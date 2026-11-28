"""Servicio de transcripción en tiempo real.

Un único hilo de reconocimiento (los motores locales no son reentrantes y
compiten por CPU) con estas reglas:

1. Los segmentos **finales** nunca se descartan y se atienden antes que los
   provisionales, en orden de llegada.
2. De los **provisionales** solo importa el más reciente de cada
   intervención: los anteriores se reemplazan sin transcribirse. Si el motor
   no los soporta (remotos: consumirían cuota) o están desactivados, se
   ignoran.
3. Un segmento ``discarded`` produce un evento final vacío para que la
   interfaz retire el texto provisional.
4. Errores: ``quota``/``network`` → un reintento con espera; ``auth``,
   ``payment``, ``unavailable`` y ``model_missing`` → fallo definitivo
   (no se cambia de motor en silencio: decide el usuario).
5. Se pasa como *prompt* el final del texto confirmado de la misma fuente
   (continuidad) y el glosario del perfil, si existe.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from ..audio.dsp import rms_dbfs
from ..audio.types import SourceKind, SpeechSegment
from ..stt.base import SpeechRecognizer, STTError, Transcript

log = logging.getLogger("unnexor.copilot.transcription")

FATAL_KINDS = frozenset({"auth", "payment", "unavailable", "model_missing"})
MIN_SPEECH_DBFS = -50.0  # por debajo: casi silencio, no se transcribe


class TranscriberState(str, Enum):
    IDLE = "idle"
    LOADING = "loading"
    TRANSCRIBING = "transcribing"
    FAILED = "failed"
    STOPPED = "stopped"


@dataclass(frozen=True)
class TranscriptEvent:
    segment_id: int
    source: SourceKind
    text: str
    final: bool
    t_start: float
    t_end: float
    confidence: float | None = None
    language: str | None = None
    engine: str = ""
    latency_s: float = 0.0  # desde que llegó el segmento hasta el texto

    @property
    def retracted(self) -> bool:
        """Final vacío: retirar el texto provisional de esta intervención."""
        return self.final and not self.text

    @property
    def low_confidence(self) -> bool:
        return self.confidence is not None and self.confidence < 0.55


@dataclass
class _Job:
    segment: SpeechSegment
    received: float


class TranscriptionService:
    def __init__(
        self,
        recognizer_factory: Callable[[], SpeechRecognizer],
        on_event: Callable[[TranscriptEvent], None],
        *,
        on_error: Callable[[STTError], None] | None = None,
        on_state: Callable[[TranscriberState], None] | None = None,
        language: str | None = None,
        partials: bool = True,
        glossary: Callable[[], str] | None = None,
        backlog_warning_s: float = 20.0,
        retry_delay_s: float = 2.0,
    ) -> None:
        self._factory = recognizer_factory
        self._on_event = on_event
        self._on_error = on_error
        self._on_state = on_state
        self.language = language
        self._partials_wanted = partials
        self._glossary = glossary
        self._backlog_warning_s = backlog_warning_s
        self._retry_delay = retry_delay_s
        self._finals: deque[_Job] = deque()
        self._partials: dict[int, _Job] = {}
        self._cond = threading.Condition()
        self._stopping = False
        self._failed = False
        self._thread: threading.Thread | None = None
        self._recognizer: SpeechRecognizer | None = None
        self._backlog_warned = False
        self._busy = False  # hay un segmento final transcribiéndose
        self.state = TranscriberState.IDLE
        self.processed = 0

    # ------------------------------------------------------------------ #
    @property
    def failed(self) -> bool:
        return self._failed

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("El servicio ya se inició")
        self._thread = threading.Thread(
            target=self._run, name="stt-service", daemon=True
        )
        self._thread.start()

    def submit(self, segment: SpeechSegment) -> None:
        """Encola un segmento (thread-safe, no bloquea)."""
        with self._cond:
            if self._stopping or self._failed:
                return
            job = _Job(segment, time.monotonic())
            if segment.final:
                self._partials.pop(segment.segment_id, None)
                self._finals.append(job)
            elif self._partials_wanted:
                self._partials[segment.segment_id] = job
            self._cond.notify()

    def drained(self) -> bool:
        """¿No queda ningún segmento final pendiente ni en curso?"""
        with self._cond:
            return not self._finals and not self._busy

    def backlog_seconds(self) -> float:
        with self._cond:
            return sum(job.segment.duration for job in self._finals)

    def stop(self, timeout: float = 30.0) -> None:
        """Termina tras transcribir los finales pendientes."""
        with self._cond:
            self._stopping = True
            self._partials.clear()
            self._cond.notify()
        if self._thread is not None:
            self._thread.join(timeout)

    # ------------------------------------------------------------------ #
    def _set_state(self, state: TranscriberState) -> None:
        self.state = state
        if self._on_state is not None:
            try:
                self._on_state(state)
            except Exception:
                log.exception("Fallo en el callback de estado STT")

    def _next_job(self) -> _Job | None:
        with self._cond:
            while not self._finals and not self._partials and not self._stopping:
                self._cond.wait(0.5)
            if self._finals:
                self._busy = True
                return self._finals.popleft()
            if self._partials and not self._stopping:
                # El provisional más reciente (mayor id = intervención más nueva).
                segment_id = max(self._partials)
                return self._partials.pop(segment_id)
            return None

    def _run(self) -> None:
        try:
            self._set_state(TranscriberState.LOADING)
            self._recognizer = self._factory()
            loader = getattr(self._recognizer, "load", None)
            if callable(loader):
                loader()
            if not self._recognizer.supports_partials:
                with self._cond:
                    self._partials_wanted = False
                    self._partials.clear()
            self._set_state(TranscriberState.IDLE)
        except STTError as exc:
            self._fail(exc)
            return
        except Exception as exc:  # nunca matar el hilo sin informar
            log.exception("No se pudo crear el motor de voz")
            self._fail(STTError(f"No se pudo iniciar el motor de voz: {exc}"))
            return
        try:
            while True:
                job = self._next_job()
                if job is None:
                    break
                self._check_backlog()
                try:
                    self._process(job)
                finally:
                    with self._cond:
                        self._busy = False
                if self._failed:
                    break
        finally:
            if self._recognizer is not None:
                try:
                    self._recognizer.close()
                except Exception:
                    log.debug("Cierre del motor con error", exc_info=True)
            if not self._failed:
                self._set_state(TranscriberState.STOPPED)

    def _process(self, job: _Job) -> None:
        segment = job.segment
        if segment.discarded or rms_dbfs(segment.samples) < MIN_SPEECH_DBFS:
            # Falsos positivos del VAD (respiración, ruido de fondo): sin voz
            # suficiente para transcribir; evita alucinaciones y cuota remota.
            self._emit_retract(segment, job)
            return
        if not segment.final and self._superseded(segment):
            return
        assert self._recognizer is not None
        self._set_state(TranscriberState.TRANSCRIBING)
        result = self._transcribe_with_retry(segment)
        self._set_state(TranscriberState.IDLE)
        if result is None:
            if segment.final:
                self._emit_retract(segment, job)
            return
        self.processed += 1
        self._emit(
            TranscriptEvent(
                segment_id=segment.segment_id,
                source=segment.source,
                text=result.text,
                final=segment.final,
                t_start=segment.t_start,
                t_end=segment.t_end,
                confidence=result.confidence,
                language=result.language,
                engine=f"{result.engine}:{result.model}",
                latency_s=round(time.monotonic() - job.received, 3),
            )
        )

    def _superseded(self, segment: SpeechSegment) -> bool:
        """¿Llegó ya el final (o un provisional más nuevo) de esta intervención?"""
        with self._cond:
            if segment.segment_id in self._partials:
                return True
            return any(j.segment.segment_id == segment.segment_id for j in self._finals)

    def _prompt(self, source: SourceKind) -> str | None:
        parts: list[str] = []
        if self._glossary is not None:
            try:
                glossary = self._glossary().strip()
            except Exception:
                glossary = ""
            if glossary:
                parts.append(glossary)
        # El texto ya confirmado NO se usa como prompt: con fragmentos sin voz
        # real Whisper "continúa" el prompt y entra en bucles ("el informe del
        # informe del…"). Solo el glosario (vocabulario) es seguro.
        del source
        return " ".join(parts) or None

    def _transcribe_with_retry(self, segment: SpeechSegment) -> Transcript | None:
        assert self._recognizer is not None
        for attempt in (1, 2):
            try:
                return self._recognizer.transcribe(
                    segment.samples,
                    language=self.language,
                    prompt=self._prompt(segment.source),
                )
            except STTError as exc:
                if exc.kind in FATAL_KINDS:
                    self._fail(exc)
                    return None
                if exc.kind == "invalid_audio":
                    return None
                if attempt == 1 and exc.kind in ("quota", "network") and segment.final:
                    delay = exc.retry_after or self._retry_delay
                    log.info("STT %s; reintento en %.1f s", exc.kind, delay)
                    with self._cond:
                        if self._cond.wait_for(lambda: self._stopping, timeout=delay):
                            return None
                    continue
                self._report(exc)
                return None
            except Exception as exc:
                log.exception("Fallo inesperado del motor de voz")
                self._report(STTError(f"Fallo del motor de voz: {exc}"))
                return None
        return None

    def _check_backlog(self) -> None:
        backlog = self.backlog_seconds()
        if backlog > self._backlog_warning_s and not self._backlog_warned:
            self._backlog_warned = True
            self._report(
                STTError(
                    f"La transcripción va con {backlog:.0f} s de retraso: el "
                    "motor elegido es lento para este equipo. Prueba uno más "
                    "ligero o un servicio remoto.",
                    "backlog",
                )
            )
        elif backlog < self._backlog_warning_s / 2:
            self._backlog_warned = False

    def _fail(self, exc: STTError) -> None:
        with self._cond:
            self._failed = True
            self._finals.clear()
            self._partials.clear()
            self._cond.notify_all()
        self._set_state(TranscriberState.FAILED)
        self._report(exc)

    def _report(self, exc: STTError) -> None:
        if self._on_error is not None:
            try:
                self._on_error(exc)
            except Exception:
                log.exception("Fallo en el callback de errores STT")

    def _emit_retract(self, segment: SpeechSegment, job: _Job) -> None:
        self._emit(
            TranscriptEvent(
                segment.segment_id,
                segment.source,
                "",
                True,
                segment.t_start,
                segment.t_end,
                latency_s=round(time.monotonic() - job.received, 3),
            )
        )

    def _emit(self, event: TranscriptEvent) -> None:
        try:
            self._on_event(event)
        except Exception:
            log.exception("Fallo en el consumidor de transcripciones")

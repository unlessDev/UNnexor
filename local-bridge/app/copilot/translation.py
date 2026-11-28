"""Traducción en tiempo real (caso de uso, sin Qt).

* Los segmentos **provisionales** solo actualizan el texto original y se
  marcan como provisionales: nunca se traducen ni se presentan como
  definitivos.
* Cada segmento **confirmado** se traduce en orden (FIFO) usando como
  contexto los dos originales anteriores.
* Los segmentos con confianza baja del reconocimiento se marcan.
* Exporta TXT y Markdown; el resumen usa la tarea ``SUMMARY`` del router.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum

from ..audio.types import SourceKind
from ..routing.executor import RoutingCancelled, RoutingError
from ..routing.router import Task
from .engine import GenerationEngine
from .prompts import language_name, summary_messages, translation_messages
from .transcription import TranscriptEvent

log = logging.getLogger("unnexor.copilot.translation")


class SegmentStatus(str, Enum):
    PROVISIONAL = "provisional"  # la persona sigue hablando
    PENDING = "pending"  # confirmado, esperando traducción
    TRANSLATING = "translating"
    DONE = "done"
    FAILED = "failed"


@dataclass(frozen=True)
class TranslationSegment:
    segment_id: int
    source: SourceKind
    t_start: float
    original: str
    status: SegmentStatus
    translation: str = ""
    language: str | None = None
    low_confidence: bool = False
    error: str = ""
    model: str = ""

    @property
    def final(self) -> bool:
        return self.status is not SegmentStatus.PROVISIONAL


def _clock(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes:02d}:{secs:02d}"


class LiveTranslator:
    def __init__(
        self,
        engine: GenerationEngine,
        *,
        target: Callable[[], str],
        source_language: Callable[[], str | None] = lambda: None,
        on_update: Callable[[TranslationSegment], None] | None = None,
        on_notice: Callable[[str], None] | None = None,
    ) -> None:
        self._engine = engine
        self._target = target
        self._source_language = source_language
        self._on_update = on_update
        self._on_notice = on_notice
        self._segments: dict[int, TranslationSegment] = {}
        self._queue: deque[int] = deque()
        self._cond = threading.Condition()
        self._running = False
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self.paused = False

    def start(self) -> None:
        with self._cond:
            if self._running:
                return
            self._running = True
        self._thread = threading.Thread(
            target=self._loop, name="copilot-translator", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        with self._cond:
            self._running = False
            self._cancel.set()
            self._cond.notify_all()
        if self._thread is not None:
            self._thread.join(timeout)

    def handle_transcript(self, event: TranscriptEvent) -> None:
        if self.paused:
            return
        with self._cond:
            if event.retracted:
                existing = self._segments.pop(event.segment_id, None)
                if existing is not None:
                    self._emit(
                        replace(existing, original="", status=SegmentStatus.FAILED)
                    )
                return
            if not event.text.strip():
                return
            current = self._segments.get(event.segment_id)
            if current is not None and current.final:
                return  # ya confirmado: los provisionales tardíos se ignoran
            segment = TranslationSegment(
                event.segment_id,
                event.source,
                event.t_start,
                event.text.strip(),
                SegmentStatus.PENDING if event.final else SegmentStatus.PROVISIONAL,
                language=event.language,
                low_confidence=event.low_confidence,
            )
            self._segments[event.segment_id] = segment
            if event.final:
                self._queue.append(event.segment_id)
                self._cond.notify_all()
        self._emit(segment)

    def segments(self) -> list[TranslationSegment]:
        with self._cond:
            return sorted(
                self._segments.values(), key=lambda s: (s.t_start, s.segment_id)
            )

    def clear(self) -> None:
        with self._cond:
            self._segments.clear()
            self._queue.clear()

    # ------------------------------------------------------------ interno
    def _loop(self) -> None:
        while True:
            with self._cond:
                while self._running and not self._queue:
                    self._cond.wait(0.5)
                if not self._running:
                    return
                segment_id = self._queue.popleft()
                segment = self._segments.get(segment_id)
                if segment is None:
                    continue
                context = " ".join(
                    s.original
                    for s in self.segments()
                    if s.final and s.t_start < segment.t_start
                )[-600:]
                segment = replace(segment, status=SegmentStatus.TRANSLATING)
                self._segments[segment_id] = segment
            self._emit(segment)
            self._translate(segment, context)

    def _translate(self, segment: TranslationSegment, context: str) -> None:
        self._cancel.clear()
        target = self._target()
        source = self._source_language() or None
        try:
            result = self._engine.generate(
                Task.TRANSLATION,
                translation_messages(segment.original, target, source, context),
                should_abort=self._cancel.is_set,
                on_notice=self._on_notice,
                temperature=0.2,
            )
            updated = replace(
                segment,
                status=SegmentStatus.DONE,
                translation=result.text.strip(),
                model=result.model_label,
            )
        except RoutingCancelled:
            return
        except RoutingError as exc:
            updated = replace(segment, status=SegmentStatus.FAILED, error=exc.message)
        except Exception as exc:  # el hilo nunca muere
            log.exception("Fallo traduciendo")
            updated = replace(segment, status=SegmentStatus.FAILED, error=str(exc))
        with self._cond:
            if segment.segment_id in self._segments:
                self._segments[segment.segment_id] = updated
        self._emit(updated)

    def _emit(self, segment: TranslationSegment) -> None:
        if self._on_update is None:
            return
        try:
            self._on_update(segment)
        except Exception:
            log.exception("Fallo en el consumidor de traducciones")

    # ------------------------------------------------------------ salidas
    def export(self, fmt: str = "md", *, title: str = "Transcripción") -> str:
        final = [s for s in self.segments() if s.final and s.original]
        target = language_name(self._target())
        if fmt == "txt":
            lines = [title, ""]
            for s in final:
                lines.append(
                    f"[{_clock(s.t_start)}] {s.source.speaker_label}: {s.original}"
                )
                if s.translation:
                    lines.append(f"    → ({target}) {s.translation}")
            return "\n".join(lines) + "\n"
        if fmt != "md":
            raise ValueError("Formato de exportación no admitido")
        lines = [f"# {title}", "", f"Traducción al {target}.", ""]
        for s in final:
            flag = " ⚠️ *(confianza baja)*" if s.low_confidence else ""
            lines.append(
                f"**[{_clock(s.t_start)}] {s.source.speaker_label}:** {s.original}{flag}  "
            )
            if s.translation:
                lines.append(f"> {s.translation}")
            elif s.status is SegmentStatus.FAILED:
                lines.append(f"> *(sin traducción: {s.error or 'error'})*")
            lines.append("")
        return "\n".join(lines)

    def summarize(
        self, language: str | None = None, on_chunk: Callable[[str], None] | None = None
    ) -> str:
        transcript = "\n".join(
            f"{s.source.speaker_label}: {s.original}"
            for s in self.segments()
            if s.final and s.original
        )
        if not transcript:
            raise ValueError("No hay contenido confirmado para resumir.")
        result = self._engine.generate(
            Task.SUMMARY,
            summary_messages(transcript, language or self._target()),
            on_chunk=on_chunk,
            on_notice=self._on_notice,
        )
        return result.text

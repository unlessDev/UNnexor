"""Asistencia en conversaciones (caso de uso, sin Qt).

Flujo: ``TranscriptEvent`` finales → turnos (se cierran tras una pausa) →
detección de preguntas del interlocutor → respuesta generada con el perfil,
la base de conocimiento y la conversación reciente.

* Una generación a la vez; las preguntas automáticas que llegan mientras
  tanto esperan (se conservan las 2 más recientes).
* No se responde dos veces a la misma pregunta en 90 s.
* Datos personales (perfil/documentos): si el perfil no permite enviarlos
  a servicios remotos, la respuesta solo usa modelos locales.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

from ..audio.types import SourceKind
from ..knowledge.profiles import ContextProfile
from ..knowledge.service import KnowledgeBase, SearchHit, build_context
from ..routing.executor import RoutingCancelled, RoutingError
from ..routing.router import Task
from .engine import GenerationEngine
from .prompts import TranscriptLine, answer_messages
from .questions import SENSITIVITY_THRESHOLDS, TurnAccumulator, score_question, similar
from .settings import CopilotSettings
from .transcription import TranscriptEvent

log = logging.getLogger("extdator.copilot.assistant")


class QAStatus(str, Enum):
    PENDING = "pending"
    GENERATING = "generating"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class QAItem:
    id: int
    question: str
    origin: str  # auto | manual | window
    created_at: float
    answer: str = ""
    status: QAStatus = QAStatus.PENDING
    model: str = ""
    error: str = ""
    useful: bool | None = None
    sources: list[str] = field(default_factory=list)
    previous_answers: list[str] = field(default_factory=list)
    score: float = 0.0


@dataclass
class AssistantCallbacks:
    on_question: Callable[[QAItem], None] | None = None
    on_answer_started: Callable[[QAItem], None] | None = None
    on_answer_chunk: Callable[[int, str], None] | None = None
    on_answer_done: Callable[[QAItem], None] | None = None
    on_notice: Callable[[str], None] | None = None


@dataclass(frozen=True)
class _Job:
    item_id: int
    mode: str  # answer | alternative | expand


class ConversationAssistant:
    MAX_TRANSCRIPT = 300

    def __init__(
        self,
        engine: GenerationEngine,
        *,
        settings: Callable[[], CopilotSettings],
        profile: Callable[[], ContextProfile | None],
        knowledge: KnowledgeBase | None = None,
        callbacks: AssistantCallbacks | None = None,
        clock: Callable[[], float] = time.monotonic,
        settle_s: float = 1.2,
    ) -> None:
        self._engine = engine
        self._settings = settings
        self._profile = profile
        self._knowledge = knowledge
        self._cb = callbacks or AssistantCallbacks()
        self._clock = clock
        self._turns = TurnAccumulator(settle_s=settle_s)
        self._lock = threading.RLock()
        self._wake = threading.Condition(self._lock)
        self._jobs: deque[_Job] = deque()
        self._items: dict[int, QAItem] = {}
        self._transcript: deque[TranscriptLine] = deque(maxlen=self.MAX_TRANSCRIPT)
        self._next_id = 1
        self._running = False
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self.paused = False

    # ------------------------------------------------------------ control
    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
        self._thread = threading.Thread(
            target=self._loop, name="copilot-assistant", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        with self._lock:
            self._running = False
            self._cancel.set()
            self._wake.notify_all()
        if self._thread is not None:
            self._thread.join(timeout)

    def cancel_current(self) -> None:
        self._cancel.set()

    # ------------------------------------------------------------ entradas
    def handle_transcript(self, event: TranscriptEvent) -> None:
        """Llamado desde el hilo STT con cada transcripción."""
        if not event.final or not event.text.strip():
            return
        with self._lock:
            self._transcript.append(
                TranscriptLine(event.source.speaker_label, event.text)
            )
            for turn in self._turns.add(
                event.source, event.text, event.t_start, event.t_end, self._clock()
            ):
                self._evaluate_turn(turn.source, turn.text)
            self._wake.notify_all()

    def ask(self, question: str) -> int:
        """Pregunta manual: siempre se responde (aunque no parezca pregunta)."""
        text = question.strip()
        if not text:
            raise ValueError("Escribe una pregunta.")
        with self._lock:
            item = self._new_item(text, "manual", 1.0)
            self._jobs.appendleft(_Job(item.id, "answer"))  # prioridad al usuario
            self._wake.notify_all()
        self._emit(self._cb.on_question, item)
        return item.id

    def ask_window(self, text: str) -> int:
        """Responde a lo escuchado en una ventana de escucha (siempre)."""
        fragment = text.strip()
        if not fragment:
            raise ValueError("No se escuchó nada en la ventana.")
        with self._lock:
            item = self._new_item(fragment, "window", 1.0)
            self._jobs.appendleft(_Job(item.id, "window"))
            self._wake.notify_all()
        self._emit(self._cb.on_question, item)
        return item.id

    def regenerate(self, item_id: int) -> None:
        self._enqueue_followup(item_id, "alternative")

    def expand(self, item_id: int) -> None:
        self._enqueue_followup(item_id, "expand")

    def mark_useful(self, item_id: int, useful: bool) -> None:
        with self._lock:
            if item_id in self._items:
                self._items[item_id].useful = useful

    def history(self) -> list[QAItem]:
        with self._lock:
            return sorted(self._items.values(), key=lambda i: i.id)

    def transcript(self) -> list[TranscriptLine]:
        with self._lock:
            return list(self._transcript)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._transcript.clear()
            self._jobs.clear()
            self._turns.flush()

    # ----------------------------------------------------------- internos
    def _enqueue_followup(self, item_id: int, mode: str) -> None:
        with self._lock:
            item = self._items.get(item_id)
            if item is None or item.status is QAStatus.GENERATING:
                return
            self._jobs.appendleft(_Job(item_id, mode))
            self._wake.notify_all()

    def _new_item(self, question: str, origin: str, score: float) -> QAItem:
        item = QAItem(self._next_id, question, origin, self._clock(), score=score)
        self._items[item.id] = item
        self._next_id += 1
        return item

    def _evaluate_turn(self, source: SourceKind, text: str) -> None:
        settings = self._settings()
        if self.paused or not settings.auto_answer:
            return
        if source is SourceKind.MICROPHONE and not settings.answer_microphone_questions:
            return
        result = score_question(text)
        if not result.is_question(SENSITIVITY_THRESHOLDS[settings.sensitivity]):
            return
        now = self._clock()
        for item in self._items.values():
            if now - item.created_at < 90 and similar(item.question, text):
                return
        item = self._new_item(text, "auto", result.score)
        auto_pending = [
            j for j in self._jobs if self._items[j.item_id].origin == "auto"
        ]
        if len(auto_pending) >= 2:
            self._jobs.remove(auto_pending[0])
            self._items[auto_pending[0].item_id].status = QAStatus.CANCELLED
        self._jobs.append(_Job(item.id, "answer"))
        self._emit(self._cb.on_question, item)

    def _loop(self) -> None:
        while True:
            with self._lock:
                if not self._running:
                    return
                for turn in self._turns.due(self._clock()):
                    self._evaluate_turn(turn.source, turn.text)
                job = self._jobs.popleft() if self._jobs else None
                if job is None:
                    self._wake.wait(0.2)
                    continue
            self._run_job(job)

    def _knowledge_for(
        self, question: str, profile: ContextProfile | None
    ) -> list[SearchHit]:
        if (
            self._knowledge is None
            or profile is None
            or not self._settings().use_knowledge
        ):
            return []
        try:
            return self._knowledge.search(profile.id, question, k=4)
        except Exception:
            log.exception("Fallo consultando la base de conocimiento")
            return []

    def _run_job(self, job: _Job) -> None:
        with self._lock:
            item = self._items.get(job.item_id)
            if item is None or item.status is QAStatus.CANCELLED:
                return
            previous = item.answer
            if job.mode != "answer" and previous:
                item.previous_answers.append(previous)
            item.status = QAStatus.GENERATING
            item.answer = ""
            item.error = ""
            transcript = list(self._transcript)
        profile = self._profile()
        hits = self._knowledge_for(item.question, profile)
        knowledge = build_context(hits)
        item.sources = [hit.source for hit in hits]
        personal = bool(knowledge) or bool(
            profile and (profile.about.strip() or profile.role.strip())
        )
        local_only = personal and not (profile and profile.allow_remote_documents)
        messages = answer_messages(
            item.question,
            profile,
            transcript=transcript,
            knowledge=knowledge,
            mode=job.mode,
            previous_answer=previous,
        )
        self._cancel.clear()
        self._emit(self._cb.on_answer_started, item)

        def on_chunk(delta: str) -> None:
            with self._lock:
                item.answer += delta
            if self._cb.on_answer_chunk is not None:
                try:
                    self._cb.on_answer_chunk(item.id, delta)
                except Exception:
                    log.exception("Fallo en on_answer_chunk")

        try:
            result = self._engine.generate(
                Task.ANSWER,
                messages,
                sensitive=personal,
                local_only=local_only,
                on_chunk=on_chunk,
                should_abort=self._cancel.is_set,
                on_notice=self._cb.on_notice,
            )
            with self._lock:
                item.answer = result.text
                item.model = result.model_label
                item.status = QAStatus.DONE
        except RoutingCancelled:
            with self._lock:
                item.status = QAStatus.CANCELLED
        except RoutingError as exc:
            with self._lock:
                item.status = QAStatus.FAILED
                item.error = exc.message
                if local_only:
                    item.error += (
                        " (Tu perfil no permite enviar datos personales a "
                        "servicios remotos: se necesita un modelo local.)"
                    )
        except Exception as exc:  # el hilo nunca debe morir
            log.exception("Fallo generando la respuesta")
            with self._lock:
                item.status = QAStatus.FAILED
                item.error = str(exc)
        self._emit(self._cb.on_answer_done, item)

    @staticmethod
    def _emit(callback: Callable[[QAItem], None] | None, item: QAItem) -> None:
        if callback is None:
            return
        try:
            callback(item)
        except Exception:
            log.exception("Fallo en un callback del asistente")

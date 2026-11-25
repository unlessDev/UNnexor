"""ViewModel del copiloto: sesión, servicios y señales para las vistas.

Los servicios (captura, STT, asistente, traductor, desafíos) corren en sus
propios hilos y avisan por callbacks; aquí se convierten en señales Qt (las
emisiones desde otros hilos llegan a la GUI con conexión en cola).
"""

from __future__ import annotations

import base64
import copy
import logging
import os
import tempfile
import threading
import time
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, Signal

from ...audio.capture import CaptureState, CaptureStatus, SoundCardBackend
from ...audio.pipeline import AudioPipeline, SourceSelection
from ...audio.types import SourceKind
from ...config import Config
from ...copilot.assistant import AssistantCallbacks, ConversationAssistant, QAItem
from ...copilot.challenges import ChallengeResult, ChallengeSolver
from ...copilot.engine import RoutedEngine
from ...copilot.settings import CopilotSettings, SettingsRepository
from ...copilot.transcription import (
    TranscriberState,
    TranscriptEvent,
    TranscriptionService,
)
from ...copilot.translation import LiveTranslator
from ...knowledge.embeddings import OllamaEmbedder
from ...knowledge.profiles import ContextProfile, ProfileStore
from ...knowledge.service import KnowledgeBase
from ...ollama_client import OllamaClient
from ...providers.consent import ConsentStore
from ...providers.usage import UsageLedger
from ...routing.router import RoutingPolicy, Task
from ...stt.base import STTError
from ...stt.catalog import get_spec
from ...stt.factory import create_recognizer, engine_status
from .tasks import run_async

log = logging.getLogger("extdator.copilot.ui")

MODES = ("conversation", "translation")

STATE_TEXT = {
    CaptureState.STARTING: "Abriendo dispositivo…",
    CaptureState.CAPTURING: "Capturando audio",
    CaptureState.PAUSED: "Pausado",
    CaptureState.RECONNECTING: "Reconectando…",
    CaptureState.STOPPED: "Captura detenida",
    CaptureState.FAILED: "Error de captura",
}
STT_TEXT = {
    TranscriberState.IDLE: "Escuchando",
    TranscriberState.LOADING: "Cargando motor de voz…",
    TranscriberState.TRANSCRIBING: "Transcribiendo",
    TranscriberState.FAILED: "Error del motor de voz",
    TranscriberState.STOPPED: "Transcripción detenida",
}


def _has_key(provider_id: str) -> bool:
    try:
        from ...providers.manager import get_key

        return bool(get_key(provider_id))
    except Exception:
        return False


class CopilotController(QObject):
    transcript = Signal(object)  # TranscriptEvent
    question = Signal(object)  # QAItem
    answerStarted = Signal(object)
    answerChunk = Signal(int, str)
    answerDone = Signal(object)
    translationUpdated = Signal(object)  # TranslationSegment
    challengeStarted = Signal(str)
    challengeChunk = Signal(str)
    challengeDone = Signal(object)  # ChallengeResult
    challengeFailed = Signal(str)
    status = Signal(str, str)  # (componente, texto)
    level = Signal(str, float, float)  # (fuente, dBFS, prob. de voz)
    notice = Signal(str)
    error = Signal(str)
    sessionChanged = Signal(str)  # idle | conversation | translation
    pausedChanged = Signal(bool)
    settingsChanged = Signal()

    def __init__(
        self,
        cfg: Config,
        *,
        settings_repo: SettingsRepository | None = None,
        profiles: ProfileStore | None = None,
        knowledge: KnowledgeBase | None = None,
        consents: ConsentStore | None = None,
        ledger: UsageLedger | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        copilot_cfg = copy.copy(cfg)
        copilot_cfg.ollama_think = False  # respuestas en vivo: sin razonamiento
        copilot_cfg.ollama_keep_alive = "15m"
        self.cfg = copilot_cfg
        self.settings_repo = settings_repo or SettingsRepository()
        self.settings: CopilotSettings = self.settings_repo.load()
        self.profiles = profiles or ProfileStore()
        self.consents = consents or ConsentStore()
        self.ledger = ledger or UsageLedger()
        self.ollama = OllamaClient(copilot_cfg)
        self.embedder = OllamaEmbedder(base_url=copilot_cfg.ollama_host)
        self.knowledge = knowledge or KnowledgeBase(embedder=self.embedder)
        self._hardware: dict[str, Any] | None = None
        self.engine = RoutedEngine(
            self.ollama,
            policy=self.policy,
            remote_models=lambda: self.settings.remote_models,
            has_key=_has_key,
            hardware=lambda: self._hardware,
            ledger=self.ledger,
        )
        self.assistant = ConversationAssistant(
            self.engine,
            settings=lambda: self.settings,
            profile=self.active_profile,
            knowledge=self.knowledge,
            callbacks=AssistantCallbacks(
                on_question=self.question.emit,
                on_answer_started=self._on_answer_started,
                on_answer_chunk=self.answerChunk.emit,
                on_answer_done=self._on_answer_done,
                on_notice=self.notice.emit,
            ),
        )
        self.translator = LiveTranslator(
            self.engine,
            target=lambda: self.settings.translation_target,
            source_language=lambda: self.settings.translation_source or None,
            on_update=self.translationUpdated.emit,
            on_notice=self.notice.emit,
        )
        self.solver = ChallengeSolver(
            self.engine, ocr=self._ocr_images, language=self._answer_language
        )
        self.mode = "idle"
        self.paused = False
        self._pipeline: AudioPipeline | None = None
        self._stt: TranscriptionService | None = None
        self._lock = threading.Lock()
        self.assistant.start()
        self.translator.start()

    # ------------------------------------------------------------ ajustes
    def policy(self) -> RoutingPolicy:
        return self.settings.policy(
            self.consents.providers_with("remote_data"),
            self.consents.providers_with("costs"),
            self.consents.providers_with("documents"),
        )

    def save_settings(self, settings: CopilotSettings) -> None:
        self.settings_repo.save(settings)
        self.settings = settings
        self.engine.invalidate()
        self.settingsChanged.emit()

    def set_hardware(self, hw: dict[str, Any] | None) -> None:
        self._hardware = hw
        self.engine.invalidate()

    def active_profile(self) -> ContextProfile | None:
        profile_id = self.settings.active_profile
        return self.profiles.get(profile_id) if profile_id else None

    def _answer_language(self) -> str:
        profile = self.active_profile()
        return profile.language if profile else "es"

    # ------------------------------------------------------------ sesión
    def stt_ready(self) -> tuple[bool, str]:
        try:
            status = engine_status(get_spec(self.settings.stt_engine))
        except KeyError:
            return False, "Motor de voz desconocido: elige otro en 'Motores y modelos'."
        return status.ready, status.reason

    def start_session(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"Modo desconocido: {mode}")
        if self.mode != "idle":
            self.stop_session()
        sources: list[SourceSelection] = []
        if self.settings.capture_system:
            sources.append(
                SourceSelection(SourceKind.SYSTEM, self.settings.system_device or None)
            )
        if self.settings.capture_microphone:
            sources.append(
                SourceSelection(
                    SourceKind.MICROPHONE, self.settings.microphone_device or None
                )
            )
        if not sources:
            raise ValueError(
                "Elige al menos una fuente: audio del sistema o micrófono."
            )
        ready, reason = self.stt_ready()
        if not ready:
            raise ValueError(f"El motor de voz no está listo: {reason}")
        engine_key = self.settings.stt_engine
        language = (
            self.settings.translation_source
            if mode == "translation"
            else self.settings.stt_language
        ) or None
        profile = self.active_profile()
        self._stt = TranscriptionService(
            lambda: create_recognizer(engine_key),
            self._on_transcript,
            on_error=self._on_stt_error,
            on_state=lambda st: self.status.emit("stt", STT_TEXT.get(st, st.value)),
            language=language,
            partials=self.settings.show_partials,
            glossary=(lambda: profile.glossary) if profile else None,
        )
        self._pipeline = AudioPipeline(
            SoundCardBackend(),
            sources,
            self._stt.submit,
            self._on_capture_status,
            on_level=lambda kind, db, prob: self.level.emit(kind.value, db, prob),
        )
        self.mode = mode
        self.paused = False
        self.assistant.paused = False
        self.translator.paused = False
        self._stt.start()
        self._pipeline.start()
        self.sessionChanged.emit(mode)
        task = Task.ANSWER if mode == "conversation" else Task.TRANSLATION
        run_async(
            lambda: self.engine.warm_up(task),
            on_done=lambda msg: self.status.emit("llm", str(msg)),
            on_error=lambda msg: self.status.emit("llm", msg),
        )

    def pause(self) -> None:
        if self.mode == "idle" or self.paused or self._pipeline is None:
            return
        self._pipeline.pause()
        self.paused = True
        self.assistant.paused = True
        self.translator.paused = True
        self.pausedChanged.emit(True)

    def resume(self) -> None:
        if self.mode == "idle" or not self.paused or self._pipeline is None:
            return
        self._pipeline.resume()
        self.paused = False
        self.assistant.paused = False
        self.translator.paused = False
        self.pausedChanged.emit(False)

    def toggle_pause(self) -> None:
        self.resume() if self.paused else self.pause()

    def stop_session(self) -> None:
        pipeline, stt = self._pipeline, self._stt
        self._pipeline = self._stt = None
        if pipeline is not None:
            pipeline.stop()
        if stt is not None:
            stt.stop(timeout=15)
        self.mode = "idle"
        self.paused = False
        self.sessionChanged.emit("idle")
        self.status.emit("capture", "Finalizado")

    def shutdown(self) -> None:
        self.stop_session()
        self.solver.cancel()
        self.assistant.stop()
        self.translator.stop()
        self.embedder.close()
        self.ollama.close()

    # ---------------------------------------------------------- callbacks
    def _on_transcript(self, event: TranscriptEvent) -> None:
        self.transcript.emit(event)
        if self.mode == "conversation":
            self.assistant.handle_transcript(event)
        elif self.mode == "translation":
            self.translator.handle_transcript(event)

    def _on_capture_status(self, status: CaptureStatus) -> None:
        text = STATE_TEXT.get(status.state, status.state.value)
        if status.message:
            text = f"{text}: {status.message}"
        label = status.source.label
        self.status.emit("capture", f"{label} · {text}")
        if status.state is CaptureState.FAILED:
            self.error.emit(f"{label}: {status.message or 'la captura falló'}")

    def _on_stt_error(self, exc: STTError) -> None:
        if exc.kind == "backlog":
            self.notice.emit(exc.message)
        else:
            self.error.emit(exc.message)

    def _on_answer_started(self, item: QAItem) -> None:
        self.status.emit("llm", "Generando respuesta…")
        self.answerStarted.emit(item)

    def _on_answer_done(self, item: QAItem) -> None:
        self.status.emit(
            "llm", "Respuesta lista" if item.answer else item.error or "Sin respuesta"
        )
        self.answerDone.emit(item)

    # ----------------------------------------------------------- desafíos
    def _ocr_images(self, images_b64: list[str]) -> str:
        from ... import ocr_manager

        engine = ocr_manager.recommend()
        if not engine:
            return ""
        texts: list[str] = []
        for b64 in images_b64:
            handle, path = tempfile.mkstemp(suffix=".png", prefix="extdator_challenge_")
            try:
                with os.fdopen(handle, "wb") as out:
                    out.write(base64.b64decode(b64))
                texts.append(ocr_manager.transcribe(path, engine=engine).text)
            finally:
                try:
                    os.unlink(path)  # privacidad: la captura no queda en disco
                except OSError:
                    pass
        ocr_manager.release_engine()
        return "\n\n".join(t for t in texts if t.strip())

    def solve_challenge(self, images_b64: list[str], instruction: str) -> None:
        if self.solver.busy:
            self.notice.emit("Ya hay un análisis en curso.")
            return
        self.status.emit("llm", "Procesando imagen…")
        self.challengeStarted.emit(instruction)

        def work() -> ChallengeResult:
            return self.solver.solve(
                images_b64,
                instruction,
                on_chunk=self.challengeChunk.emit,
                on_notice=self.notice.emit,
            )

        def done(result: Any) -> None:
            self.status.emit("llm", f"Análisis listo ({result.model})")
            self.challengeDone.emit(result)

        def failed(message: str) -> None:
            self.status.emit("llm", "Error al analizar")
            self.challengeFailed.emit(message)

        run_async(work, on_done=done, on_error=failed)

    # ------------------------------------------------------ compatibilidad
    def assess_current_workload(self) -> dict[str, Any]:
        """Equipo + carga combinada de la configuración actual (bloqueante)."""
        from ... import ocr_manager
        from ...compat.workload import (
            WorkloadItem,
            assess_workload,
            ocr_item,
            ollama_item,
            stt_item,
        )
        from ...hardware import detect

        hw = detect().to_dict()
        self.set_hardware(hw)
        items: list[WorkloadItem] = []
        spec = get_spec(self.settings.stt_engine)
        if spec.is_local:
            items.append(stt_item(spec.label, spec.ram_mb))
        try:
            installed = {m.get("name"): m for m in self.ollama.installed_models_full()}
        except Exception:
            installed = {}
        used: set[str] = set()
        vision_ok = False
        for task, kind in ((Task.ANSWER, "llm"), (Task.VISION, "vision")):
            primary = self.engine.decide(task).primary
            if primary is None:
                continue
            if task is Task.VISION:
                vision_ok = True
            if not primary.local or primary.model in used:
                continue
            entry = installed.get(primary.model)
            if entry is None:
                continue
            used.add(primary.model)
            details = entry.get("details") or {}
            items.append(
                ollama_item(
                    primary.model,
                    entry.get("size"),
                    details.get("parameter_size"),
                    kind,
                )
            )
        ocr_engine = None if vision_ok else ocr_manager.recommend()
        if ocr_engine:
            items.append(ocr_item(ocr_engine))
        return {"hw": hw, "items": items, "assessment": assess_workload(hw, items)}

    def run_benchmark(self, kind: str) -> dict[str, Any]:
        """Medición consentida (bloqueante: llamar en segundo plano)."""
        from ...compat.benchmark import benchmark_llm, benchmark_stt

        if kind == "stt":
            spec = get_spec(self.settings.stt_engine)
            if not spec.is_local:
                raise ValueError(
                    "El motor de voz elegido es remoto: no hay nada que medir en este equipo."
                )
            result = benchmark_stt(spec.model)
            key = f"stt:{spec.model}"
        else:
            primary = self.engine.decide(Task.ANSWER).primary
            if primary is None or not primary.local:
                raise ValueError("El modelo de respuesta elegido no es local.")
            result = benchmark_llm(self.ollama, primary.model)
            key = f"llm:{primary.model}"
        settings = self.settings
        settings.benchmarks[key] = result
        self.save_settings(settings)
        return result

    # ------------------------------------------------------------ utilidades
    def describe_routes(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for task in (Task.ANSWER, Task.TRANSLATION, Task.VISION, Task.SUMMARY):
            out[task.value] = self.engine.decide(task).explain()
        return out


def wait_until(predicate: Callable[[], bool], timeout: float = 5.0) -> bool:
    """Utilidad para pruebas: espera activa breve sin bloquear eventos Qt."""
    from PySide6.QtCore import QCoreApplication

    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    return predicate()

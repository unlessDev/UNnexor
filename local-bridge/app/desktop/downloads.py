"""Gestor único de descargas de modelos (Ollama y Whisper local).

Antes cada diálogo creaba sus propios ``PullWorker`` y los detenía al
cerrarse: cerrar «Modelos de IA» cancelaba la descarga, dos ventanas podían
descargar el mismo modelo a la vez y «Recomendar configuración» no podía
descargar nada. ``DownloadManager`` centraliza:

* **Cola en serie**: una descarga activa a la vez (no compiten por el ancho
  de banda ni por el disco); el resto espera en orden.
* **Sin duplicados**: pedir un modelo ya en cola o en curso no lo repite.
* **Progreso real** (``ollama_pull.PullTracker``) emitido como
  ``PullSnapshot`` inmutable, limitado a ~8 actualizaciones/s para no
  saturar la interfaz.
* **Verificación**: al terminar una descarga de Ollama se comprueba que el
  modelo aparece en ``/api/tags`` antes de informar éxito.
* **Cancelación** por trabajo y al cerrar la aplicación.

El consentimiento lo piden las ventanas antes de llamar a ``enqueue``:
este módulo nunca inicia una descarga por su cuenta.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from PySide6.QtCore import QObject, QThread, Signal

from ..ollama_client import GenerationCancelled, OllamaClient, OllamaError
from ..ollama_pull import (
    CANCELLED,
    COMPLETED,
    FAILED,
    FINALIZING,
    PREPARING,
    PullSnapshot,
    PullTracker,
)

log = logging.getLogger("unnexor.downloads")

EMIT_INTERVAL_S = 0.12

KIND_OLLAMA = "ollama"
KIND_WHISPER = "whisper"


@dataclass(frozen=True)
class DownloadJob:
    kind: str  # ollama | whisper
    name: str  # nombre del modelo de Ollama o del modelo Whisper
    label: str = ""
    expected_bytes: int = 0  # tamaño aproximado (Whisper: no hay total real)

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.name}"

    @property
    def title(self) -> str:
        return self.label or self.name


def _model_present(installed: List[dict], name: str) -> bool:
    wanted = name if ":" in name else f"{name}:latest"
    for entry in installed:
        current = str(entry.get("name") or entry.get("model") or "")
        if current == name or current == wanted:
            return True
    return False


class _DownloadThread(QThread):
    progress = Signal(object)  # PullSnapshot
    done = Signal(object)  # PullSnapshot final (COMPLETED | CANCELLED | FAILED)

    def __init__(self, ollama: OllamaClient, job: DownloadJob, parent=None) -> None:
        super().__init__(parent)
        self._ollama = ollama
        self.job = job
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        tracker = PullTracker(self.job.key)
        last_emit = 0.0

        def emit(snapshot: PullSnapshot, force: bool = False) -> None:
            nonlocal last_emit
            now = time.monotonic()
            if force or now - last_emit >= EMIT_INTERVAL_S:
                last_emit = now
                self.progress.emit(snapshot)

        emit(tracker.update("pulling manifest"), force=True)
        try:
            if self.job.kind == KIND_WHISPER:
                self._run_whisper(tracker, emit)
            else:
                self._run_ollama(tracker, emit)
        except GenerationCancelled:
            self.done.emit(tracker.finish(CANCELLED, "Descarga cancelada."))
            return
        except OllamaError as exc:
            self.done.emit(tracker.finish(FAILED, exc.message))
            return
        except Exception as exc:  # nunca tumbar la aplicación
            log.exception("Fallo inesperado descargando %s", self.job.key)
            self.done.emit(tracker.finish(FAILED, str(exc) or exc.__class__.__name__))
            return
        self.done.emit(tracker.finish(COMPLETED))

    # ------------------------------------------------------------------
    def _run_ollama(self, tracker: PullTracker, emit) -> None:
        def on_line(obj: dict) -> None:
            snapshot = tracker.update(
                str(obj.get("status", "")),
                obj.get("total"),
                obj.get("completed"),
                str(obj.get("digest", "")),
            )
            # Los cambios de fase se emiten siempre; el avance, limitado.
            emit(snapshot, force=snapshot.phase != tracker_phase[0])
            tracker_phase[0] = snapshot.phase

        tracker_phase = [PREPARING]
        self._ollama.pull_stream(
            self.job.name, on_line=on_line, should_abort=lambda: self._cancel
        )
        emit(tracker.update("verifying installation"), force=True)
        installed = self._ollama.installed_models_full()
        if not _model_present(installed, self.job.name):
            raise OllamaError(
                f"Ollama terminó la descarga pero '{self.job.name}' no aparece "
                "entre los modelos instalados."
            )

    def _run_whisper(self, tracker: PullTracker, emit) -> None:
        from ..stt import faster_whisper_engine as fw
        from ..stt.base import STTError

        expected = max(1, self.job.expected_bytes)

        def on_progress(mb: float) -> None:
            if self._cancel:
                return  # huggingface_hub no admite cancelación a mitad
            done = int(mb * 1_048_576)
            emit(tracker.update("downloading", expected, min(done, expected - 1), "w"))

        try:
            fw.download_model(self.job.name, on_progress=on_progress)
        except STTError as exc:
            raise OllamaError(str(exc)) from exc
        emit(tracker.update("writing manifest"), force=True)
        if not fw.is_installed(self.job.name):
            raise OllamaError(f"El modelo de voz '{self.job.name}' quedó incompleto.")
        if self._cancel:
            raise GenerationCancelled()


class DownloadManager(QObject):
    """Cola de descargas compartida por todas las ventanas."""

    jobQueued = Signal(object)  # DownloadJob
    jobProgress = Signal(object)  # PullSnapshot
    jobFinished = Signal(object)  # PullSnapshot final
    idle = Signal()  # cola vacía

    _instance: Optional["DownloadManager"] = None

    def __init__(self, ollama: OllamaClient, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._ollama = ollama
        self._queue: List[DownloadJob] = []
        self._thread: Optional[_DownloadThread] = None
        self._last: Dict[str, PullSnapshot] = {}

    # ------------------------------------------------------------ singleton
    @classmethod
    def instance(cls, ollama: Optional[OllamaClient] = None) -> "DownloadManager":
        if cls._instance is None:
            if ollama is None:
                raise RuntimeError("DownloadManager sin inicializar")
            cls._instance = DownloadManager(ollama)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        if cls._instance is not None:
            cls._instance.shutdown()
        cls._instance = None

    # ------------------------------------------------------------ consulta
    def active_job(self) -> Optional[DownloadJob]:
        return self._thread.job if self._thread is not None else None

    def pending(self) -> List[DownloadJob]:
        return list(self._queue)

    def is_busy(self, kind: str, name: str) -> bool:
        key = f"{kind}:{name}"
        active = self.active_job()
        return (active is not None and active.key == key) or any(
            j.key == key for j in self._queue
        )

    def last_snapshot(self, kind: str, name: str) -> Optional[PullSnapshot]:
        return self._last.get(f"{kind}:{name}")

    # ------------------------------------------------------------ acciones
    def enqueue(self, job: DownloadJob) -> bool:
        """Añade la descarga (tras el consentimiento). False si ya estaba."""
        if self.is_busy(job.kind, job.name):
            return False
        self._queue.append(job)
        self._last.pop(job.key, None)
        log.info("Descarga en cola: %s", job.key)
        self.jobQueued.emit(job)
        self._start_next()
        return True

    def cancel(self, kind: str, name: str) -> None:
        key = f"{kind}:{name}"
        for job in list(self._queue):
            if job.key == key:
                self._queue.remove(job)
                snapshot = PullTracker(key).finish(CANCELLED, "Descarga cancelada.")
                self._last[key] = snapshot
                self.jobFinished.emit(snapshot)
        if self._thread is not None and self._thread.job.key == key:
            self._thread.cancel()

    def cancel_all(self) -> None:
        for job in list(self._queue):
            self.cancel(job.kind, job.name)
        if self._thread is not None:
            self._thread.cancel()

    def shutdown(self, wait_ms: int = 3000) -> None:
        self._queue.clear()
        if self._thread is not None:
            self._thread.cancel()
            if not self._thread.wait(wait_ms):
                log.warning("La descarga en curso no terminó a tiempo al cerrar.")

    # ------------------------------------------------------------ interno
    def _start_next(self) -> None:
        if self._thread is not None or not self._queue:
            if self._thread is None and not self._queue:
                self.idle.emit()
            return
        job = self._queue.pop(0)
        thread = _DownloadThread(self._ollama, job, self)
        thread.progress.connect(self._on_progress)
        thread.done.connect(self._on_done)
        thread.finished.connect(self._on_thread_finished)
        self._thread = thread
        log.info("Descarga iniciada: %s", job.key)
        thread.start()

    def _on_progress(self, snapshot: PullSnapshot) -> None:
        self._last[snapshot.model] = snapshot
        self.jobProgress.emit(snapshot)

    def _on_done(self, snapshot: PullSnapshot) -> None:
        self._last[snapshot.model] = snapshot
        level = logging.INFO if snapshot.phase == COMPLETED else logging.WARNING
        log.log(level, "Descarga %s: %s", snapshot.model, snapshot.describe())
        self.jobFinished.emit(snapshot)

    def _on_thread_finished(self) -> None:
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.deleteLater()
        self._start_next()


__all__ = [
    "COMPLETED",
    "CANCELLED",
    "FAILED",
    "FINALIZING",
    "DownloadJob",
    "DownloadManager",
    "KIND_OLLAMA",
    "KIND_WHISPER",
]

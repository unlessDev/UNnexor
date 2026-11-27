"""Workers (QThread) del puente: generación con Ollama y sondeo de estado.

* ``GenerationWorker`` — único consumidor de la cola (``Bus``). Mantiene el
  orden de recepción de las selecciones, hace el streaming a la GUI vía
  señales Qt (conexiones en cola entre hilos) y respeta peticiones de
  detener/limpiar entre fragmentos.
* ``StatusWorker`` — sondea Ollama cada pocos segundos para el indicador de
  conexión y puede listar modelos a petición.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, QThread, Signal

from .. import ocr_manager
from ..config import Config
from ..conversation import Conversation
from ..diagnostics import write_diagnostics_archive
from ..hardware import detect as detect_hardware
from ..hardware import gpu_acceleration_active
from ..model_catalog import recommended_catalog
from ..model_evaluator import assess_model
from ..ocr_manager import OCREngineError, OCRNotAvailable
from ..ollama_client import (
    GenerationCancelled,
    ModelNotFoundError,
    OllamaClient,
    OllamaError,
)
from ..queue_manager import (
    Bus,
    ClearConversation,
    ConfigUpdate,
    ImageRequest,
    RuntimeState,
    SelectionRequest,
)

log = logging.getLogger("extdator.worker")


class GenerationWorker(QThread):
    """Consume la cola y genera respuestas con Ollama (streaming)."""

    queryStarted = Signal(str)
    chunkReceived = Signal(str)
    generationFinished = Signal(dict)
    generationFailed = Signal(str, str)  # (mensaje, tipo) tipo: "model" | "general"
    generationCancelled = Signal()
    conversationCleared = Signal()
    configApplied = Signal(dict)
    workerStopped = Signal()

    def __init__(
        self,
        cfg: Config,
        bus: Bus,
        state: RuntimeState,
        ollama: OllamaClient,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._cfg = cfg
        self._bus = bus
        self._state = state
        self._ollama = ollama
        self._conversation = Conversation(
            system_prompt=cfg.ollama_system_prompt,
            max_messages=cfg.max_history_messages,
        )
        self._running = True
        state.set_model(cfg.ollama_model)

    # ------------------------------------------------------------------
    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        log.info("Worker de generación iniciado.")
        while self._running:
            event = self._bus.consume(timeout=0.25)
            if event is None:
                if self._state.consume_clear():
                    self._do_clear()
                continue
            if isinstance(event, SelectionRequest):
                self._handle_selection(event)
            elif isinstance(event, ImageRequest):
                self._handle_image(event)
            elif isinstance(event, ClearConversation):
                self._do_clear()
            elif isinstance(event, ConfigUpdate):
                self._apply_config(event.changes)
            else:  # pragma: no cover
                log.warning("Evento desconocido en la cola: %r", event)
        log.info("Worker de generación detenido.")
        self.workerStopped.emit()

    # ------------------------------------------------------------------
    def _handle_selection(self, request: SelectionRequest) -> None:
        if self._state.consume_clear():
            self._do_clear()

        self._conversation.add_user(request.prompt())
        self._state.set_history(len(self._conversation))
        self._state.set_generating(True)
        self.queryStarted.emit(request.display())

        def on_chunk(delta: str) -> None:
            self.chunkReceived.emit(delta)

        def should_abort() -> bool:
            return self._state.stop_requested() or self._state.clear_requested()

        try:
            with self._state.lock():
                provider_id, remote_model = self._state.provider()
                model = self._cfg.ollama_model
                temperature = self._cfg.ollama_temperature
            if provider_id and provider_id != "ollama":
                text, stats = self._generate_with_provider(
                    provider_id, remote_model, on_chunk, should_abort
                )
            else:
                text, stats = self._ollama.chat_stream(
                    self._conversation.payload(),
                    model=model,
                    temperature=temperature,
                    on_chunk=on_chunk,
                    should_abort=should_abort,
                )
            self._conversation.add_assistant(text)
            self._state.set_history(len(self._conversation))
            self.generationFinished.emit(stats)
        except GenerationCancelled:
            if self._state.clear_requested():
                self._state.consume_clear()
                self._do_clear()
            else:
                self.generationCancelled.emit()
        except OllamaError as exc:
            # No contaminar el historial con un turno fallido.
            self._conversation.drop_last_user()
            self._state.set_history(len(self._conversation))
            kind = "model" if isinstance(exc, ModelNotFoundError) else "general"
            self.generationFailed.emit(exc.message, kind)
        except Exception as exc:  # red de seguridad: el worker nunca debe morir
            log.exception("Error inesperado durante la generación")
            self._conversation.drop_last_user()
            self._state.set_history(len(self._conversation))
            self.generationFailed.emit(f"Error interno del puente: {exc}", "general")
        finally:
            self._state.set_generating(False)
            self._state.reset_stop()

    # ------------------------------------------------------------------
    def _generate_with_provider(
        self, provider_id: str, remote_model: str, on_chunk, should_abort
    ):
        """Genera con un proveedor remoto (adaptador OpenAI-compatible)."""
        if not remote_model:
            raise OllamaError(  # reutiliza el tipo de error para la GUI
                "No hay modelo remoto seleccionado. Elige uno en 'Modelos'."
            )
        from ..providers.manager import ProviderError, get_client_for

        try:
            client = get_client_for(provider_id, timeout=self._cfg.ollama_timeout)
        except ProviderError as exc:
            raise OllamaError(str(exc)) from exc
        # El historial local se envía completo: los proveedores remotos no
        # mantienen estado conversacional entre peticiones.
        return client.chat_stream(
            self._conversation.payload(),
            model=remote_model,
            temperature=self._cfg.ollama_temperature,
            on_chunk=on_chunk,
            should_abort=should_abort,
        )

    # ------------------------------------------------------------------
    def _handle_image(self, request: ImageRequest) -> None:
        """Captura de pantalla → modelo multimodal de Ollama (una sola vez)."""
        if self._state.consume_clear():
            self._do_clear()
        with self._state.lock():
            model = self._cfg.ollama_model

        self._state.set_generating(True)
        label = "📷 captura de pantalla"
        self.queryStarted.emit(
            f"{label} — {request.instruction[:120]}"
            + ("…" if len(request.instruction) > 120 else "")
        )

        def on_chunk(delta: str) -> None:
            self.chunkReceived.emit(delta)

        def should_abort() -> bool:
            return self._state.stop_requested() or self._state.clear_requested()

        try:
            capabilities: List[str] = []
            try:
                capabilities = self._ollama.show(model).get("capabilities", [])
            except OllamaError:
                pass
            if "vision" not in capabilities:
                vision = self._list_vision_models()
                hint = f"El modelo activo '{model}' no procesa imágenes. " + (
                    f"Modelos con visión instalados: {', '.join(vision)}. "
                    if vision
                    else "Ningún modelo con visión está instalado: descarga "
                    "uno como 'qwen2.5vl:7b' o 'gemma3:4b' desde "
                    "'Modelos' (botón 🤖)."
                )
                self.generationFailed.emit(hint, "model")
                return
            messages: List[Dict[str, Any]] = (
                [{"role": "system", "content": self._conversation.system_prompt}]
                if self._conversation.system_prompt
                else []
            )
            messages.append(
                {
                    "role": "user",
                    "content": request.instruction,
                    "images": [request.image_b64],
                }
            )
            text, stats = self._ollama.chat_stream(
                messages, model=model, on_chunk=on_chunk, should_abort=should_abort
            )
            # Historial sin la imagen (memoria): queda el texto del turno.
            self._conversation.add_user(
                request.instruction + "\n[imagen adjunta: captura de pantalla]"
            )
            self._conversation.add_assistant(text)
            self._state.set_history(len(self._conversation))
            self.generationFinished.emit(stats)
        except GenerationCancelled:
            if self._state.clear_requested():
                self._state.consume_clear()
                self._do_clear()
            else:
                self.generationCancelled.emit()
        except OllamaError as exc:
            self.generationFailed.emit(exc.message, "general")
        except Exception as exc:
            log.exception("Error inesperado procesando imagen")
            self.generationFailed.emit(
                f"Error interno procesando la captura: {exc}", "general"
            )
        finally:
            self._state.set_generating(False)
            self._state.reset_stop()

    def _list_vision_models(self) -> List[str]:
        """Nombres de modelos instalados con capacidad 'vision'."""
        vision: List[str] = []
        try:
            for entry in self._ollama.installed_models_full():
                name = entry.get("name", "")
                try:
                    caps = self._ollama.show(name).get("capabilities", [])
                except OllamaError:
                    continue
                if "vision" in caps:
                    vision.append(name)
        except OllamaError:
            pass
        return vision

    # ------------------------------------------------------------------
    def _do_clear(self) -> None:
        self._conversation.clear()
        self._state.set_history(0)
        log.info("Conversación reiniciada.")
        self.conversationCleared.emit()

    def _apply_config(self, changes: dict) -> None:
        if "system_prompt" in changes:
            self._conversation.system_prompt = changes["system_prompt"]
        if "model" in changes:
            self._state.set_model(changes["model"])
        if "provider" in changes:
            self._state.set_provider(
                changes["provider"], changes.get("remote_model", "")
            )
        self.configApplied.emit(changes)


class StatusWorker(QThread):
    """Sondea Ollama para el indicador de conexión y lista modelos a demanda."""

    statusChanged = Signal(bool, str, bool)  # (alcanzable, detalle, modelo_ok)
    modelsReady = Signal(list)

    INTERVAL_S = 4.0

    def __init__(
        self, cfg: Config, ollama: OllamaClient, parent: Optional[QObject] = None
    ) -> None:
        super().__init__(parent)
        self._cfg = cfg
        self._ollama = ollama
        self._running = True
        self._models_requested = False

    def stop(self) -> None:
        self._running = False

    def request_models(self) -> None:
        self._models_requested = True

    def run(self) -> None:
        while self._running:
            reachable, detail = self._ollama.is_reachable(timeout=2.0)
            models: list[str] = []
            model_ok = False
            if reachable:
                try:
                    models = self._ollama.list_models()
                    configured = self._cfg.ollama_model
                    base = configured.split(":")[0]
                    model_ok = any(
                        name == configured or name.split(":")[0] == base
                        for name in models
                    )
                except OllamaError as exc:
                    detail = exc.message
            if self._models_requested:
                self._models_requested = False
                self.modelsReady.emit(models)
            self.statusChanged.emit(reachable, detail, model_ok)
            # Sueño interrumpible: responde a stop() rápido.
            deadline = time.monotonic() + self.INTERVAL_S
            while self._running and time.monotonic() < deadline:
                time.sleep(0.2)


class HardwareWorker(QThread):
    """Detección de hardware + modelos instalados + evaluación de compatibilidad.

    Todo el trabajo pesado (registro, psutil, /api/show por modelo) ocurre
    fuera del hilo de la GUI.
    """

    ready = Signal(dict)  # resultado completo
    failed = Signal(str)

    def __init__(
        self,
        ollama: OllamaClient,
        configured_model: str = "",
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._ollama = ollama
        self._configured = configured_model

    def run(self) -> None:
        try:
            info = detect_hardware()
            hw = info.to_dict()
            gpu_active = gpu_acceleration_active(self._ollama)
            info.ollama_gpu_active = gpu_active
            hw["ollama_gpu_active"] = gpu_active
            # Dato de terreno: si Ollama corre solo con CPU, la VRAM no cuenta.
            if gpu_active is False and hw.get("gpus"):
                hw["gpus"] = []
                hw.setdefault("notes", []).append(
                    "Ollama está usando CPU (no la GPU): evaluación en modo "
                    "solo-RAM."
                )

            installed: List[Dict[str, Any]] = []
            try:
                for entry in self._ollama.installed_models_full():
                    details = entry.get("details") or {}
                    capabilities: List[str] = []
                    try:
                        capabilities = self._ollama.show(entry.get("name", "")).get(
                            "capabilities", []
                        )
                    except OllamaError:
                        pass
                    assessment = assess_model(
                        model_name=entry.get("name", ""),
                        size_bytes=entry.get("size"),
                        parameter_size=details.get("parameter_size"),
                        hw=hw,
                        installed=True,
                    )
                    installed.append(
                        {
                            "name": entry.get("name", ""),
                            "size_bytes": entry.get("size"),
                            "modified_at": entry.get("modified_at", ""),
                            "parameter_size": details.get("parameter_size", ""),
                            "quantization": details.get("quantization_level", ""),
                            "capabilities": capabilities,
                            "assessment": assessment.to_dict(),
                        }
                    )
            except OllamaError as exc:
                self.failed.emit(exc.message)
                return

            disk_free_gb = info.disk_free_gb
            catalog: List[Dict[str, Any]] = []
            installed_names = {row["name"].split(":")[0] for row in installed}
            for item in recommended_catalog():
                if item["name"].split(":")[0] in installed_names:
                    continue  # ya está instalado: aparece en la otra sección
                assessment = assess_model(
                    model_name=item["name"],
                    size_bytes=item["size_bytes"],
                    parameter_size=f"{item['params_b']}B",
                    hw=hw,
                    installed=False,
                    disk_free_gb=disk_free_gb,
                )
                catalog.append({**item, "assessment": assessment.to_dict()})

            self.ready.emit(
                {
                    "hardware": hw,
                    "installed": installed,
                    "catalog": catalog,
                    "configured": self._configured,
                }
            )
        except Exception as exc:  # nunca tumbar la GUI
            log.exception("Fallo cargando modelos/hardware")
            self.failed.emit(f"Error al evaluar el equipo: {exc}")


class OCRWorker(QThread):
    """Transcripción OCR de una captura (motor local, hilo aparte).

    Al terminar (con éxito o con error) borra la imagen temporal y libera
    la memoria del motor: los modelos OCR no permanecen cargados cuando
    no se usan.
    """

    transcribed = Signal(str, str)  # (texto/Markdown, etiqueta del motor)
    failed = Signal(str)

    def __init__(
        self,
        image_path: str,
        engine: Optional[str] = None,
        lang: str = "es",
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._path = image_path
        self._engine = engine
        self._lang = lang
        self.stop_requested = False

    def run(self) -> None:
        try:
            result = ocr_manager.transcribe(
                self._path, engine=self._engine, lang=self._lang
            )
            self.transcribed.emit(result.text, result.engine_label)
        except (OCREngineError, OCRNotAvailable) as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            log.exception("Fallo inesperado en OCRWorker")
            self.failed.emit(f"Error interno del OCR: {exc}")
        finally:
            try:
                os.unlink(self._path)  # privacidad: borrar la captura
            except OSError:
                log.warning("No se pudo borrar la captura temporal %s", self._path)
            # Los pesos OCR no permanecen en RAM entre capturas.
            ocr_manager.release_engine()


class PullWorker(QThread):
    """Descarga de un modelo con progreso (POST /api/pull en streaming)."""

    progress = Signal(str, str, float)  # (modelo, estado, fracción 0-1)
    succeeded = Signal(str)
    failed = Signal(str, str)  # (modelo, mensaje)

    def __init__(
        self, ollama: OllamaClient, model: str, parent: Optional[QObject] = None
    ) -> None:
        super().__init__(parent)
        self._ollama = ollama
        self._model = model
        self._running = True

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        last_status, last_frac = "iniciando…", 0.0

        def on_progress(
            status: str, total: Optional[int], completed: Optional[int]
        ) -> None:
            nonlocal last_status, last_frac
            last_status = status
            if total and completed is not None and total > 0:
                last_frac = min(1.0, completed / total)
            self.progress.emit(self._model, last_status, last_frac)

        try:
            self._ollama.pull_stream(
                self._model,
                on_progress=on_progress,
                should_abort=lambda: not self._running,
            )
            self.succeeded.emit(self._model)
        except GenerationCancelled:
            self.failed.emit(self._model, "Descarga cancelada.")
        except OllamaError as exc:
            self.failed.emit(self._model, exc.message)


class DiagnosticsWorker(QThread):
    """Genera el ZIP de diagnóstico fuera del hilo de la GUI."""

    bundleReady = Signal(str)
    bundleFailed = Signal(str)

    def __init__(
        self,
        target: Path | str,
        cfg: Config,
        state: Any,
        bus: Any,
        pairing: Any,
        ollama: Optional[OllamaClient],
        parent: Optional[QObject] = None,
        *,
        check_ollama: bool = True,
        log_dir: Optional[Path | str] = None,
    ) -> None:
        super().__init__(parent)
        self._target = Path(target)
        self._cfg = cfg
        self._state = state
        self._bus = bus
        self._pairing = pairing
        self._ollama = ollama
        self._check_ollama = check_ollama
        self._log_dir = Path(log_dir) if log_dir is not None else None

    def run(self) -> None:
        try:
            result = write_diagnostics_archive(
                self._target,
                self._cfg,
                state=self._state,
                bus=self._bus,
                pairing=self._pairing,
                ollama=self._ollama,
                log_dir=self._log_dir,
                check_ollama=self._check_ollama,
            )
        except Exception as exc:
            log.exception("No se pudo generar el diagnóstico")
            message = str(exc).strip() or exc.__class__.__name__
            self.bundleFailed.emit(message)
            return
        self.bundleReady.emit(str(result))

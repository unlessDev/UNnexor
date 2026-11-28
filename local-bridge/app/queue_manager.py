"""Comunicación entre el servidor HTTP y la aplicación de escritorio.

* ``Bus``: cola thread-safe de eventos (selecciones, limpiar, config).
  La publíca el hilo de uvicorn y la consume el QThread worker.
* ``RuntimeState``: banderas compartidas con sincronización explícita
  (generando / detener / limpiar). Nunca se comparte estado mutable sin lock.
"""

from __future__ import annotations

import queue
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class SelectionRequest:
    """Texto seleccionado enviado por la extensión."""

    text: str
    source_url: str = ""
    received_at: str = ""  # ISO-8601 informado por el cliente
    # Pregunta opcional del usuario sobre el texto ("Consultar" en la
    # extensión). Vacía = envío directo (comportamiento histórico).
    question: str = ""

    @property
    def mode(self) -> str:
        """``"ask"`` (Consultar) si hay pregunta; ``"direct"`` si no."""
        return "ask" if self.question.strip() else "direct"

    def prompt(self) -> str:
        """Mensaje que recibe el modelo.

        * Enviar directamente: el texto seleccionado tal cual (histórico).
        * Consultar: la consulta del usuario es la tarea y la selección es
          su contexto; el modelo debe atender la consulta, no comentar el
          texto.
        """
        question = self.question.strip()
        if not question:
            return self.text
        return (
            "CONSULTA DEL USUARIO (pregunta, orden o instrucción):\n"
            f"{question}\n\n"
            "TEXTO SELECCIONADO EN LA PÁGINA (contexto de la consulta):\n"
            f'"""\n{self.text}\n"""\n\n'
            "Atiende la consulta del usuario usando el texto seleccionado como "
            "contexto. Si es una orden (resumir, traducir, corregir, explicar…), "
            "ejecútala sobre ese texto; si es una pregunta, respóndela. Si pide "
            "otro idioma o formato, respétalo aunque el texto esté en otro. Si la "
            "consulta se refiere a algo dicho antes en esta conversación, usa "
            "ese contexto también."
        )

    def display(self) -> str:
        """Versión breve para el historial del panel."""
        question = self.question.strip()
        if not question:
            return self.text
        excerpt = " ".join(self.text.split())
        if len(excerpt) > 280:
            excerpt = excerpt[:279] + "…"
        return f"{question}\n\n› {excerpt}"


@dataclass(frozen=True)
class ClearConversation:
    """Orden de iniciar una conversación nueva."""


@dataclass(frozen=True)
class ConfigUpdate:
    """Cambios de configuración en caliente (POST /api/config)."""

    changes: Dict[str, Any]


@dataclass(frozen=True)
class ImageRequest:
    """Captura de pantalla enviada a un modelo multimodal."""

    image_b64: str
    instruction: str
    source_url: str = ""
    received_at: str = ""


class Bus:
    """Cola FIFO thread-safe con bloqueo por timeout en el consumidor."""

    def __init__(self, maxsize: int = 64) -> None:
        self._q: queue.Queue[Any] = queue.Queue(maxsize=maxsize)

    def publish(self, event: Any) -> bool:
        try:
            self._q.put_nowait(event)
            return True
        except queue.Full:
            return False

    def consume(self, timeout: float = 0.2) -> Optional[Any]:
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    def qsize(self) -> int:
        return self._q.qsize()


class RuntimeState:
    """Estado compartido servidor ↔ worker ↔ GUI, protegido con un lock."""

    def __init__(self, model: str = "") -> None:
        # RLock: permite re-entrada (p. ej. update_config toma lock() y
        # llama a set_model() dentro).
        self._lock = threading.RLock()
        self._generating = False
        self._stop_requested = False
        self._clear_requested = False
        self._model = model
        self._provider_id = "ollama"  # "ollama" o id de proveedor remoto
        self._remote_model = ""  # modelo del proveedor remoto activo
        self._history_count = 0
        self.started_at = time.time()

    @contextmanager
    def lock(self):
        with self._lock:
            yield

    # --- generación -------------------------------------------------
    def set_generating(self, value: bool) -> None:
        with self._lock:
            self._generating = value

    def is_generating(self) -> bool:
        with self._lock:
            return self._generating

    # --- detener generación ----------------------------------------
    def request_stop(self) -> None:
        with self._lock:
            self._stop_requested = True

    def stop_requested(self) -> bool:
        with self._lock:
            return self._stop_requested

    def reset_stop(self) -> None:
        with self._lock:
            self._stop_requested = False

    # --- limpiar conversación --------------------------------------
    def request_clear(self) -> None:
        with self._lock:
            self._clear_requested = True

    def clear_requested(self) -> bool:
        """Consulta no destructiva (consume_clear() es la que resetea)."""
        with self._lock:
            return self._clear_requested

    def consume_clear(self) -> bool:
        """Devuelve True una única vez por cada petición de limpieza."""
        with self._lock:
            requested = self._clear_requested
            self._clear_requested = False
            return requested

    # --- modelo / historial ----------------------------------------
    def set_model(self, model: str) -> None:
        with self._lock:
            self._model = model

    def model_name(self) -> str:
        with self._lock:
            return self._model

    def set_provider(self, provider_id: str, remote_model: str = "") -> None:
        """Proveedor activo: 'ollama' o un id de proveedor de API remoto."""
        with self._lock:
            self._provider_id = provider_id or "ollama"
            self._remote_model = remote_model

    def provider(self) -> tuple[str, str]:
        with self._lock:
            return self._provider_id, self._remote_model

    def set_history(self, count: int) -> None:
        with self._lock:
            self._history_count = count

    def history_count(self) -> int:
        with self._lock:
            return self._history_count

    def uptime_seconds(self) -> int:
        return int(time.time() - self.started_at)

"""Ventanas de escucha del copiloto de conversación.

Problema que resuelve: con escucha continua, cuando el interlocutor habla
mucho tiempo seguido la pregunta queda enterrada entre explicaciones y el
detector (que evalúa turno a turno) no la identifica, o la identifica a
medias.

Con una *ventana de escucha* el usuario decide qué fragmento importa:

* **Temporizada**: al pulsar «Escuchar» (o el atajo) se captura durante N
  segundos; al terminar, todo lo escuchado se envía junto al modelo, que
  identifica la pregunta dentro del fragmento.
* **Manual**: el atajo inicia la escucha y el mismo atajo la termina (o el
  de cancelar la descarta).

Fuera de la ventana la captura está en pausa: no se acumula audio en
memoria ni se transcribe nada. Las fuentes (audio del sistema y/o
micrófono) son las mismas de la sesión: la ventana nunca activa el
micrófono por su cuenta.

Este módulo es lógica pura (sin Qt ni audio) para poder probarlo.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

MIN_SECONDS = 5
MAX_SECONDS = 600
PRESET_SECONDS = (10, 20, 30, 60, 120)
MAX_WINDOW_CHARS = 8000  # límite del texto enviado al modelo


class ListenMode(str, Enum):
    CONTINUOUS = "continuous"  # comportamiento clásico: detección por turnos
    TIMED = "timed"
    MANUAL = "manual"


class WindowState(str, Enum):
    IDLE = "idle"  # captura en pausa, esperando al usuario
    LISTENING = "listening"
    PROCESSING = "processing"  # captura cerrada, terminando de transcribir


@dataclass(frozen=True)
class WindowLine:
    speaker: str
    text: str


class ListeningWindow:
    """Acumula el texto final transcrito durante una ventana de escucha."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self.state = WindowState.IDLE
        self.duration_s: float | None = None
        self.started_at = 0.0
        self.ended_at = 0.0
        self._lines: list[WindowLine] = []

    # ------------------------------------------------------------------
    def begin(self, duration_s: float | None) -> None:
        """Abre una ventana (``None`` = manual, sin límite de tiempo)."""
        with self._lock:
            if self.state is not WindowState.IDLE:
                raise RuntimeError("Ya hay una escucha en curso.")
            if duration_s is not None and not (
                MIN_SECONDS <= duration_s <= MAX_SECONDS
            ):
                raise ValueError(
                    f"La duración debe estar entre {MIN_SECONDS} y {MAX_SECONDS} s."
                )
            self.duration_s = duration_s
            self.started_at = self._clock()
            self.ended_at = 0.0
            self._lines = []
            self.state = WindowState.LISTENING

    def add(self, speaker: str, text: str) -> bool:
        """Añade texto final; se acepta mientras se escucha o se procesa."""
        text = " ".join(text.split())
        if not text:
            return False
        with self._lock:
            if self.state is WindowState.IDLE:
                return False
            self._lines.append(WindowLine(speaker, text))
            return True

    def end(self) -> None:
        """Cierra la captura; aún pueden llegar transcripciones pendientes."""
        with self._lock:
            if self.state is WindowState.LISTENING:
                self.state = WindowState.PROCESSING
                self.ended_at = self._clock()

    def finish(self) -> str:
        """Devuelve el fragmento escuchado y vuelve a reposo."""
        with self._lock:
            text = self._render()
            self._reset()
            return text

    def cancel(self) -> None:
        with self._lock:
            self._reset()

    # ------------------------------------------------------------------
    def remaining(self) -> float | None:
        """Segundos que faltan (ventana temporizada en curso) o ``None``."""
        with self._lock:
            if self.state is not WindowState.LISTENING or self.duration_s is None:
                return None
            return max(0.0, self.duration_s - (self._clock() - self.started_at))

    def expired(self) -> bool:
        left = self.remaining()
        return left is not None and left <= 0.0

    def elapsed(self) -> float:
        with self._lock:
            if self.state is WindowState.IDLE:
                return 0.0
            end = self.ended_at or self._clock()
            return max(0.0, end - self.started_at)

    def preview(self) -> str:
        with self._lock:
            return self._render()

    # ------------------------------------------------------------------
    def _render(self) -> str:
        if not self._lines:
            return ""
        speakers = {line.speaker for line in self._lines}
        if len(speakers) == 1:
            text = " ".join(line.text for line in self._lines)
        else:
            text = "\n".join(f"{line.speaker}: {line.text}" for line in self._lines)
        if len(text) > MAX_WINDOW_CHARS:
            # Lo más reciente es lo más probable que contenga la pregunta.
            text = "…" + text[-(MAX_WINDOW_CHARS - 1) :]
        return text

    def _reset(self) -> None:
        self.state = WindowState.IDLE
        self.duration_s = None
        self._lines = []
        self.ended_at = 0.0


def format_seconds(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    minutes, secs = divmod(seconds, 60)
    return f"{minutes}:{secs:02d}" if minutes else f"{secs} s"

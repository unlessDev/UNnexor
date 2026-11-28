"""Progreso real de ``ollama pull`` (``POST /api/pull`` en streaming).

Ollama informa el avance **por capa** (``digest``): cada línea trae el
``total`` y el ``completed`` de una sola capa. Un modelo tiene una capa
grande (los pesos) y varias pequeñas (plantilla, licencia, parámetros).
Calcular la fracción con la capa en curso hacía que la barra saltara de
100 % a 0 % y viceversa; ``PullTracker`` agrega todas las capas:

    porcentaje = Σ bytes completados / Σ bytes totales × 100

Reglas de honestidad (ver ``docs/MODELS_CATALOG.md``):

* Sin un total conocido la fracción es ``None`` (barra indeterminada).
* El porcentaje nunca llega a 100 % antes de que Ollama confirme
  ``success``; durante la verificación se limita a 99 %.
* La velocidad se calcula sobre una ventana deslizante de bytes reales.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Dict, Optional, Tuple

# Fases normalizadas (la interfaz las traduce con PHASE_LABELS).
PREPARING = "preparing"
DOWNLOADING = "downloading"
VERIFYING = "verifying"
FINALIZING = "finalizing"
COMPLETED = "completed"
CANCELLED = "cancelled"
FAILED = "failed"

PHASE_LABELS = {
    PREPARING: "Preparando descarga",
    DOWNLOADING: "Descargando",
    VERIFYING: "Verificando",
    FINALIZING: "Finalizando instalación",
    COMPLETED: "Completado",
    CANCELLED: "Cancelado",
    FAILED: "Error",
}
TERMINAL_PHASES = (COMPLETED, CANCELLED, FAILED)

SPEED_WINDOW_S = 4.0


def phase_for_status(status: str) -> str:
    """Traduce el ``status`` de Ollama a una fase normalizada."""
    text = (status or "").strip().lower()
    if text == "success":
        return COMPLETED
    if text.startswith("pulling manifest") or not text:
        return PREPARING
    if text.startswith("pulling") or text.startswith("downloading"):
        return DOWNLOADING
    if text.startswith("verifying"):
        return VERIFYING
    if text.startswith("writing") or text.startswith("removing") or "manifest" in text:
        return FINALIZING
    return DOWNLOADING


def format_bytes(value: Optional[float]) -> str:
    if value is None:
        return "?"
    units = ("B", "KB", "MB", "GB", "TB")
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.0f} {unit}" if unit in ("B", "KB") else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"  # pragma: no cover


@dataclass(frozen=True)
class PullSnapshot:
    """Estado inmutable de una descarga, apto para enviarlo entre hilos."""

    model: str
    phase: str
    status: str  # texto original de Ollama
    completed: int
    total: int  # 0 = desconocido
    fraction: Optional[float]  # None = indeterminado
    speed_bps: Optional[float]
    layers_done: int
    layers_total: int
    error: str = ""

    @property
    def percent(self) -> Optional[int]:
        return None if self.fraction is None else int(self.fraction * 100)

    @property
    def phase_label(self) -> str:
        return PHASE_LABELS.get(self.phase, self.phase)

    def describe(self) -> str:
        """Línea legible: «Descargando · 1.2 GB de 4.7 GB · 25 % · 8.3 MB/s»."""
        parts = [self.phase_label]
        if self.phase == FAILED and self.error:
            return f"Error: {self.error}"
        if self.total:
            parts.append(
                f"{format_bytes(self.completed)} de {format_bytes(self.total)}"
            )
        elif self.completed:
            parts.append(f"{format_bytes(self.completed)} descargados")
        if self.percent is not None and self.phase not in (CANCELLED,):
            parts.append(f"{self.percent} %")
        if self.speed_bps and self.phase == DOWNLOADING:
            parts.append(f"{format_bytes(self.speed_bps)}/s")
        if self.layers_total > 1 and self.phase == DOWNLOADING:
            parts.append(
                f"capa {min(self.layers_done + 1, self.layers_total)}/{self.layers_total}"
            )
        return " · ".join(parts)


@dataclass
class PullTracker:
    """Agrega las líneas de ``/api/pull`` en un ``PullSnapshot`` coherente."""

    model: str
    clock: Callable[[], float] = time.monotonic
    _layers: Dict[str, Tuple[int, int]] = field(default_factory=dict)
    _samples: Deque[Tuple[float, int]] = field(default_factory=deque)
    _phase: str = PREPARING
    _status: str = ""
    _error: str = ""

    # ------------------------------------------------------------------
    def update(
        self,
        status: str,
        total: Optional[int] = None,
        completed: Optional[int] = None,
        digest: str = "",
    ) -> PullSnapshot:
        self._status = status or self._status
        phase = phase_for_status(status)
        if self._phase not in TERMINAL_PHASES:
            self._phase = phase
        if total and total > 0:
            key = digest or status or f"layer{len(self._layers)}"
            done = int(completed or 0)
            previous = self._layers.get(key, (0, int(total)))[0]
            # Ollama puede reenviar un valor menor al reanudar: nunca retroceder.
            self._layers[key] = (int(total), max(previous, min(done, int(total))))
        now = self.clock()
        self._samples.append((now, self.completed_bytes))
        while self._samples and now - self._samples[0][0] > SPEED_WINDOW_S:
            self._samples.popleft()
        return self.snapshot()

    def finish(self, phase: str, error: str = "") -> PullSnapshot:
        self._phase = phase
        self._error = error
        if phase == COMPLETED:
            # Al confirmar success, todas las capas están completas.
            self._layers = {k: (t, t) for k, (t, _d) in self._layers.items()}
        return self.snapshot()

    # ------------------------------------------------------------------
    @property
    def total_bytes(self) -> int:
        return sum(t for t, _d in self._layers.values())

    @property
    def completed_bytes(self) -> int:
        return sum(d for _t, d in self._layers.values())

    def speed(self) -> Optional[float]:
        if len(self._samples) < 2:
            return None
        (t0, b0), (t1, b1) = self._samples[0], self._samples[-1]
        if t1 - t0 < 0.5 or b1 < b0:
            return None
        return (b1 - b0) / (t1 - t0)

    def snapshot(self) -> PullSnapshot:
        total = self.total_bytes
        done = self.completed_bytes
        fraction: Optional[float]
        if self._phase == COMPLETED:
            fraction = 1.0
        elif total > 0:
            fraction = min(done / total, 0.99)
        else:
            fraction = None
        layers_done = sum(1 for t, d in self._layers.values() if d >= t)
        return PullSnapshot(
            model=self.model,
            phase=self._phase,
            status=self._status,
            completed=done,
            total=total,
            fraction=fraction,
            speed_bps=self.speed(),
            layers_done=layers_done,
            layers_total=len(self._layers),
            error=self._error,
        )

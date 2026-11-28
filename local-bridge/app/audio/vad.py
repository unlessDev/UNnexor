"""Detección de actividad de voz (VAD) en streaming.

Dos implementaciones del mismo puerto ``VoiceActivityDetector``:

* ``SileroVAD`` — red Silero v6 (ONNX, ~1,2 MB) incluida en el paquete
  ``faster-whisper``; no requiere descargas. Ventanas de 512 muestras a
  16 kHz (32 ms) con 64 muestras de contexto y estado LSTM persistente.
* ``EnergyVAD`` — respaldo sin modelos: energía por trama de 30 ms frente a
  un suelo de ruido adaptativo. Menos preciso con música o ruido de fondo.

``create_vad("auto")`` elige Silero si está disponible.
"""

from __future__ import annotations

import logging
import math
import os
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from .dsp import SILENCE_DBFS, rms_dbfs
from .types import SAMPLE_RATE, FloatArray

log = logging.getLogger("unnexor.audio.vad")


class VADUnavailable(RuntimeError):
    """El detector solicitado no puede cargarse en este equipo."""


class VoiceActivityDetector(Protocol):
    name: str
    frame_samples: int

    def speech_probability(self, frame: FloatArray) -> float:
        """Probabilidad (0–1) de voz en una trama de ``frame_samples``."""
        ...

    def reset(self) -> None: ...


class EnergyVAD:
    """VAD por energía con suelo de ruido adaptativo."""

    name = "energy"

    def __init__(
        self,
        frame_ms: int = 30,
        margin_db: float = 10.0,
        min_floor_db: float = -60.0,
        rise_per_frame_db: float = 0.05,
    ) -> None:
        self.frame_samples = SAMPLE_RATE * frame_ms // 1000
        self._margin = margin_db
        self._min_floor = min_floor_db
        self._rise = rise_per_frame_db
        self._floor = min_floor_db

    def reset(self) -> None:
        self._floor = self._min_floor

    def speech_probability(self, frame: FloatArray) -> float:
        level = rms_dbfs(frame)
        if level <= SILENCE_DBFS:
            return 0.0
        # El suelo baja de inmediato ante tramas más silenciosas y sube muy
        # despacio: así la voz sostenida no se "absorbe" como ruido.
        if level < self._floor:
            self._floor = max(self._min_floor, level)
        else:
            self._floor = min(level, self._floor + self._rise)
        excess = level - (max(self._floor, self._min_floor) + self._margin)
        return 1.0 / (1.0 + math.exp(-excess / 2.0))


def silero_model_path() -> Path | None:
    """Ruta del ONNX de Silero que distribuye faster-whisper, si existe."""
    override = os.environ.get(
        "UNNEXOR_SILERO_VAD", os.environ.get("EXTDATOR_SILERO_VAD", "")
    ).strip()
    if override:
        path = Path(override)
        return path if path.is_file() else None
    try:
        from faster_whisper.utils import get_assets_path
    except Exception:  # paquete opcional ausente o roto
        return None
    assets = Path(get_assets_path())
    candidates = sorted(assets.glob("silero_vad*.onnx"), reverse=True)
    return candidates[0] if candidates else None


class SileroVAD:
    """Silero VAD v6 en streaming (ventanas de 512 muestras a 16 kHz)."""

    name = "silero"
    frame_samples = 512
    _CONTEXT = 64

    def __init__(self, model_path: Path | None = None) -> None:
        path = model_path or silero_model_path()
        if path is None:
            raise VADUnavailable(
                "Silero VAD no está disponible (requiere faster-whisper)."
            )
        try:
            import onnxruntime
        except ImportError as exc:  # pragma: no cover - depende del entorno
            raise VADUnavailable("onnxruntime no está instalado.") from exc
        options = onnxruntime.SessionOptions()
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 1
        options.log_severity_level = 4
        try:
            self._session: Any = onnxruntime.InferenceSession(
                str(path), providers=["CPUExecutionProvider"], sess_options=options
            )
        except Exception as exc:
            raise VADUnavailable(f"No se pudo cargar Silero VAD: {exc}") from exc
        names = {item.name for item in self._session.get_inputs()}
        if not {"input", "h", "c"} <= names:
            raise VADUnavailable(
                f"Modelo Silero con entradas inesperadas: {sorted(names)}"
            )
        self.reset()

    def reset(self) -> None:
        self._h = np.zeros((1, 1, 128), dtype=np.float32)
        self._c = np.zeros((1, 1, 128), dtype=np.float32)
        self._context = np.zeros(self._CONTEXT, dtype=np.float32)

    def speech_probability(self, frame: FloatArray) -> float:
        if frame.size != self.frame_samples:
            raise ValueError(f"Silero espera tramas de {self.frame_samples} muestras")
        window = np.concatenate([self._context, frame]).astype(np.float32)
        probs, self._h, self._c = self._session.run(
            None, {"input": window[np.newaxis, :], "h": self._h, "c": self._c}
        )
        self._context = frame[-self._CONTEXT :].astype(np.float32)
        return float(np.asarray(probs).ravel()[0])


def create_vad(prefer: str = "auto") -> VoiceActivityDetector:
    """``auto`` → Silero si se puede cargar; si no, energía."""
    if prefer in ("auto", "silero"):
        try:
            return SileroVAD()
        except VADUnavailable as exc:
            if prefer == "silero":
                raise
            log.info("Silero VAD no disponible (%s); se usa VAD por energía.", exc)
    return EnergyVAD()

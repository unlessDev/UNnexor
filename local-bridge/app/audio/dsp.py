"""Procesado de señal mínimo y sin dependencias más allá de NumPy.

* ``to_mono``: mezcla de canales (WASAPI graba basura en mono: se captura
  en estéreo y se mezcla aquí).
* ``StreamingResampler``: filtro paso bajo FIR + interpolación con fase
  continua entre bloques, sin artefactos en los bordes. Para razones
  enteras (48 kHz → 16 kHz) equivale a una decimación exacta.
* ``rms_dbfs`` / ``normalize``: niveles para indicadores y ganancia
  conservadora de fragmentos muy bajos antes del reconocimiento.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import numpy.typing as npt

from .types import FloatArray

SILENCE_DBFS = -100.0


def to_mono(data: npt.NDArray[Any]) -> FloatArray:
    """Convierte ``(frames,)`` o ``(frames, canales)`` a mono ``float32``."""
    arr = np.asarray(data, dtype=np.float32)
    if arr.ndim == 1:
        return arr
    if arr.ndim == 2:
        if arr.shape[1] == 1:
            return arr[:, 0].copy()
        return arr.mean(axis=1, dtype=np.float32)
    raise ValueError(f"Forma de audio no soportada: {arr.shape}")


def lowpass_kernel(cutoff: float, taps: int) -> FloatArray:
    """FIR paso bajo por ventana (Hamming). ``cutoff`` normalizado a fs (0–0.5)."""
    if not 0 < cutoff < 0.5:
        raise ValueError("cutoff debe estar entre 0 y 0.5")
    if taps % 2 == 0:
        taps += 1
    n = np.arange(taps) - (taps - 1) / 2
    kernel = 2 * cutoff * np.sinc(2 * cutoff * n) * np.hamming(taps)
    kernel /= kernel.sum()
    return kernel.astype(np.float32)


class StreamingResampler:
    """Remuestreo bloque a bloque con estado entre llamadas."""

    def __init__(self, src_rate: int, dst_rate: int, taps: int = 63) -> None:
        if src_rate <= 0 or dst_rate <= 0:
            raise ValueError("Las frecuencias deben ser positivas")
        self.src_rate = src_rate
        self.dst_rate = dst_rate
        self._step = src_rate / dst_rate
        self._kernel: FloatArray | None = None
        if dst_rate < src_rate:
            # Corte al 90 % del Nyquist de destino para evitar aliasing.
            self._kernel = lowpass_kernel(0.45 * dst_rate / src_rate, taps)
        history = 0 if self._kernel is None else len(self._kernel) - 1
        self._history: FloatArray = np.zeros(history, dtype=np.float32)
        self._tail: FloatArray = np.zeros(0, dtype=np.float32)
        self._pos = 0.0

    @property
    def passthrough(self) -> bool:
        return self.src_rate == self.dst_rate

    def reset(self) -> None:
        self._history[:] = 0
        self._tail = np.zeros(0, dtype=np.float32)
        self._pos = 0.0

    def process(self, block: FloatArray) -> FloatArray:
        block = np.asarray(block, dtype=np.float32)
        if self.passthrough or block.size == 0:
            return block.copy()
        filtered = block
        if self._kernel is not None:
            padded = np.concatenate([self._history, block])
            filtered = np.convolve(padded, self._kernel, mode="valid").astype(
                np.float32
            )
            if self._history.size:
                self._history = padded[-self._history.size :].copy()
        buf = np.concatenate([self._tail, filtered])
        last_index = buf.size - 1
        if last_index < 1:
            self._tail = buf
            return np.zeros(0, dtype=np.float32)
        count = max(0, math.ceil((last_index - self._pos) / self._step))
        if count == 0:
            self._pos -= last_index
            self._tail = buf[-1:].copy()
            return np.zeros(0, dtype=np.float32)
        positions = self._pos + self._step * np.arange(count, dtype=np.float64)
        out = np.interp(positions, np.arange(buf.size), buf).astype(np.float32)
        # El siguiente búfer empieza en buf[-1]: la posición se re-referencia.
        self._pos = float(positions[-1] + self._step - last_index)
        self._tail = buf[-1:].copy()
        return out


def rms_dbfs(samples: FloatArray) -> float:
    """Nivel RMS en dBFS (``SILENCE_DBFS`` para silencio digital)."""
    if samples.size == 0:
        return SILENCE_DBFS
    rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
    if rms <= 1e-10:
        return SILENCE_DBFS
    return max(SILENCE_DBFS, 20.0 * math.log10(rms))


def normalize(
    samples: FloatArray, target_peak: float = 0.9, max_gain: float = 8.0
) -> FloatArray:
    """Ganancia de pico limitada: sube voces muy bajas sin amplificar ruido
    de forma desmedida. Nunca atenúa una señal que ya está en rango."""
    if samples.size == 0:
        return samples
    peak = float(np.max(np.abs(samples)))
    if peak <= 1e-6:
        return samples
    gain = min(max_gain, target_peak / peak)
    if gain <= 1.0:
        return samples
    return (samples * gain).astype(np.float32)

"""Codificación WAV PCM16 en memoria (sin archivos temporales)."""

from __future__ import annotations

import io
import wave

import numpy as np

from .types import SAMPLE_RATE, FloatArray


def to_pcm16(samples: FloatArray) -> bytes:
    clipped = np.clip(samples, -1.0, 1.0)
    return (clipped * 32767.0).astype("<i2").tobytes()


def encode_wav(samples: FloatArray, sample_rate: int = SAMPLE_RATE) -> bytes:
    """WAV mono PCM16: formato aceptado por Groq, OpenAI y Deepgram."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(to_pcm16(samples))
    return buffer.getvalue()


def decode_wav(data: bytes) -> tuple[FloatArray, int]:
    """Lee WAV PCM16 (mono o estéreo) a mono ``float32``."""
    with wave.open(io.BytesIO(data), "rb") as handle:
        if handle.getsampwidth() != 2:
            raise ValueError("Solo se admite WAV PCM de 16 bits")
        channels = handle.getnchannels()
        rate = handle.getframerate()
        raw = handle.readframes(handle.getnframes())
    pcm = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        pcm = pcm.reshape(-1, channels).mean(axis=1, dtype=np.float32)
    return pcm.astype(np.float32), rate

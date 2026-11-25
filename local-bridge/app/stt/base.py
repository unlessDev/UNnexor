"""Puerto de reconocimiento de voz y tipos de resultado."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from ..audio.types import FloatArray


class STTMode(str, Enum):
    """Modalidad REAL que ofrece el motor (no se finge streaming)."""

    STREAMING = "streaming"  # flujo continuo con resultados parciales nativos
    CHUNKED = "chunked"  # se envían fragmentos de voz ya segmentados
    BATCH = "batch"  # archivos completos, latencia alta

    @property
    def label(self) -> str:
        return {
            STTMode.STREAMING: "Streaming real",
            STTMode.CHUNKED: "Procesamiento por fragmentos",
            STTMode.BATCH: "Transcripción por lotes",
        }[self]


class STTError(RuntimeError):
    """Error con mensaje para el usuario.

    ``kind``: auth · quota · payment · network · model_missing ·
    unavailable · invalid_audio · general.
    """

    def __init__(
        self, message: str, kind: str = "general", retry_after: float | None = None
    ) -> None:
        super().__init__(message)
        self.message = message
        self.kind = kind
        self.retry_after = retry_after


@dataclass(frozen=True)
class TranscriptPiece:
    text: str
    start: float
    end: float
    confidence: float | None = None  # 0–1 si el motor lo informa


@dataclass(frozen=True)
class Transcript:
    text: str
    engine: str
    model: str
    audio_seconds: float
    elapsed_seconds: float
    language: str | None = None
    language_probability: float | None = None
    confidence: float | None = None
    pieces: tuple[TranscriptPiece, ...] = field(default_factory=tuple)
    filtered: bool = False  # se descartó como alucinación / no-voz

    @property
    def real_time_factor(self) -> float | None:
        """Tiempo de proceso / duración del audio (< 1 = más rápido que real)."""
        if self.audio_seconds <= 0:
            return None
        return self.elapsed_seconds / self.audio_seconds

    @property
    def low_confidence(self) -> bool:
        return self.confidence is not None and self.confidence < 0.55


class SpeechRecognizer(Protocol):
    engine_id: str
    model: str
    is_remote: bool
    supports_partials: bool  # ¿conviene transcribir fragmentos provisionales?

    def transcribe(
        self,
        audio: FloatArray,
        *,
        language: str | None = None,
        prompt: str | None = None,
    ) -> Transcript: ...

    def close(self) -> None: ...

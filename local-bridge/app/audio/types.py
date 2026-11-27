"""Tipos del dominio de audio (sin dependencias de Qt ni de backends)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias

import numpy as np
import numpy.typing as npt

SAMPLE_RATE = 16_000
"""Frecuencia interna (Hz): la que esperan Whisper y la mayoría de motores."""

FloatArray: TypeAlias = npt.NDArray[np.float32]


class SourceKind(str, Enum):
    """Origen del audio. Determina quién habla en la conversación."""

    SYSTEM = "system"  # audio reproducido por el equipo (los demás)
    MICROPHONE = "microphone"  # la voz del usuario

    @property
    def label(self) -> str:
        return "Audio del sistema" if self is SourceKind.SYSTEM else "Micrófono"

    @property
    def speaker_label(self) -> str:
        """Etiqueta de interlocutor para transcripciones."""
        return "Interlocutor" if self is SourceKind.SYSTEM else "Tú"


@dataclass(frozen=True)
class AudioDevice:
    """Dispositivo de captura tal como lo expone el backend."""

    id: str
    name: str
    kind: SourceKind
    is_default: bool = False
    channels: int = 2


@dataclass(frozen=True)
class AudioChunk:
    """Bloque ya normalizado: mono, ``float32``, 16 kHz."""

    samples: FloatArray
    source: SourceKind
    t_start: float  # segundos desde el inicio de la sesión

    @property
    def duration(self) -> float:
        return len(self.samples) / SAMPLE_RATE


@dataclass(frozen=True)
class SpeechSegment:
    """Intervención detectada por el segmentador.

    ``final=False`` indica un fragmento provisional: la persona sigue
    hablando y el texto puede cambiar. Cada ``segment_id`` termina siempre
    con exactamente un segmento ``final=True`` (salvo que se descarte por
    ser demasiado corto, en cuyo caso se emite ``discarded=True``).
    """

    segment_id: int
    source: SourceKind
    samples: FloatArray
    t_start: float
    t_end: float
    final: bool
    discarded: bool = False

    @property
    def duration(self) -> float:
        return len(self.samples) / SAMPLE_RATE

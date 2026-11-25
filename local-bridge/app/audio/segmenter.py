"""Segmentación del flujo de audio en intervenciones.

Máquina de estados por fuente:

* **Silencio**: se guarda un pre-roll (para no cortar el inicio de la
  palabra). Tras ``start_frames`` tramas con voz se abre una intervención.
* **Voz**: se acumula audio. Cada ``partial_interval_s`` se emite una copia
  *provisional* (para transcripción progresiva). Tras ``end_silence_ms`` de
  silencio se emite la intervención *final*. Si dura más de
  ``max_segment_s`` se corta (final) y continúa en una intervención nueva,
  para que la latencia no crezca sin límite en monólogos largos.

Las intervenciones más cortas que ``min_segment_ms`` se descartan (toses,
clics) emitiendo un segmento ``discarded=True`` para cerrar provisionales.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np

from .types import SAMPLE_RATE, AudioChunk, FloatArray, SourceKind, SpeechSegment
from .vad import VoiceActivityDetector


@dataclass(frozen=True)
class SegmenterConfig:
    start_threshold: float = 0.5
    end_threshold: float = 0.35
    start_frames: int = 3
    end_silence_ms: int = 700
    pre_roll_ms: int = 300
    min_segment_ms: int = 350
    max_segment_s: float = 20.0
    partial_interval_s: float = 1.5
    trailing_silence_ms: int = 200

    def __post_init__(self) -> None:
        if not 0 < self.end_threshold <= self.start_threshold < 1:
            raise ValueError("Umbrales de VAD incoherentes")
        if self.max_segment_s <= 1 or self.partial_interval_s <= 0:
            raise ValueError("Duraciones de segmento inválidas")


class Segmenter:
    """Convierte bloques de audio de una fuente en ``SpeechSegment``."""

    def __init__(
        self,
        vad: VoiceActivityDetector,
        source: SourceKind,
        config: SegmenterConfig | None = None,
        first_segment_id: int = 1,
    ) -> None:
        self._vad = vad
        self._source = source
        self._cfg = config or SegmenterConfig()
        self._frame = vad.frame_samples
        self._frame_s = self._frame / SAMPLE_RATE
        self._pending: FloatArray = np.zeros(0, dtype=np.float32)
        self._pending_t = 0.0
        pre_frames = max(1, round(self._cfg.pre_roll_ms / 1000 / self._frame_s))
        self._pre_roll: deque[FloatArray] = deque(maxlen=pre_frames)
        self._candidate: list[FloatArray] = []
        self._speech: list[FloatArray] = []
        self._in_speech = False
        self._silence_frames = 0
        self._segment_start = 0.0
        self._last_partial_len = 0
        self._next_id = first_segment_id
        self._current_id = 0
        self._voiced_frames = 0
        self.last_probability = 0.0

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    def reset(self) -> None:
        self._vad.reset()
        self._pending = np.zeros(0, dtype=np.float32)
        self._pre_roll.clear()
        self._candidate.clear()
        self._speech.clear()
        self._in_speech = False
        self._silence_frames = 0
        self._voiced_frames = 0
        self._last_partial_len = 0

    # ------------------------------------------------------------------ #
    def feed(self, chunk: AudioChunk) -> list[SpeechSegment]:
        """Procesa un bloque (de cualquier tamaño) y devuelve eventos."""
        if self._pending.size == 0:
            self._pending_t = chunk.t_start
        self._pending = np.concatenate([self._pending, chunk.samples])
        events: list[SpeechSegment] = []
        offset = 0
        while self._pending.size - offset >= self._frame:
            frame = self._pending[offset : offset + self._frame]
            frame_t = self._pending_t + offset / SAMPLE_RATE
            offset += self._frame
            event = self._process_frame(frame, frame_t)
            if event is not None:
                events.extend(event)
        self._pending = self._pending[offset:]
        self._pending_t += offset / SAMPLE_RATE
        return events

    def flush(self) -> list[SpeechSegment]:
        """Cierra la intervención en curso (al pausar o detener)."""
        if not self._in_speech:
            self._candidate.clear()
            return []
        return self._finish(force_end=True)

    # ------------------------------------------------------------------ #
    def _process_frame(self, frame: FloatArray, frame_t: float) -> list[SpeechSegment]:
        prob = self._vad.speech_probability(frame)
        self.last_probability = prob
        cfg = self._cfg
        if not self._in_speech:
            if prob >= cfg.start_threshold:
                self._candidate.append(frame.copy())
                if len(self._candidate) >= cfg.start_frames:
                    self._open(frame_t)
            else:
                # Un candidato fallido vuelve al pre-roll.
                for pending in self._candidate:
                    self._pre_roll.append(pending)
                self._candidate.clear()
                self._pre_roll.append(frame.copy())
            return []

        self._speech.append(frame.copy())
        if prob < cfg.end_threshold:
            self._silence_frames += 1
        else:
            self._silence_frames = 0
            self._voiced_frames += 1
        silence_ms = self._silence_frames * self._frame_s * 1000
        if silence_ms >= cfg.end_silence_ms:
            return self._finish(force_end=False)
        if self._duration() >= cfg.max_segment_s:
            events = self._finish(force_end=True)
            # Continúa hablando: se abre otra intervención sin pre-roll.
            self._open(frame_t + self._frame_s, with_pre_roll=False)
            return events
        if (
            self._duration() - self._last_partial_len * self._frame_s
            >= cfg.partial_interval_s
        ):
            self._last_partial_len = len(self._speech)
            return [self._segment(final=False)]
        return []

    def _open(self, frame_t: float, with_pre_roll: bool = True) -> None:
        pre = list(self._pre_roll) if with_pre_roll else []
        self._pre_roll.clear()
        self._voiced_frames = len(self._candidate)
        self._speech = pre + self._candidate
        self._candidate = []
        self._in_speech = True
        self._silence_frames = 0
        self._last_partial_len = 0
        frames_before = len(self._speech)
        self._segment_start = max(0.0, frame_t - (frames_before - 1) * self._frame_s)
        self._current_id = self._next_id
        self._next_id += 1

    def _duration(self) -> float:
        return len(self._speech) * self._frame_s

    def _finish(self, force_end: bool) -> list[SpeechSegment]:
        cfg = self._cfg
        if not force_end:
            # Se conserva un poco de silencio final; el resto se recorta.
            keep = round(cfg.trailing_silence_ms / 1000 / self._frame_s)
            drop = max(0, self._silence_frames - keep)
            if drop:
                tail = self._speech[-drop:]
                del self._speech[-drop:]
                for frame in tail:
                    self._pre_roll.append(frame)
        # Solo cuentan las tramas con voz (ni pre-roll ni silencio final).
        voiced_ms = self._voiced_frames * self._frame_s * 1000
        discarded = voiced_ms < cfg.min_segment_ms
        segment = self._segment(final=True, discarded=discarded)
        self._speech = []
        self._in_speech = False
        self._silence_frames = 0
        self._voiced_frames = 0
        self._last_partial_len = 0
        return [segment]

    def _segment(self, final: bool, discarded: bool = False) -> SpeechSegment:
        samples = (
            np.concatenate(self._speech)
            if self._speech
            else np.zeros(0, dtype=np.float32)
        )
        return SpeechSegment(
            segment_id=self._current_id,
            source=self._source,
            samples=samples,
            t_start=self._segment_start,
            t_end=self._segment_start + len(samples) / SAMPLE_RATE,
            final=final,
            discarded=discarded,
        )

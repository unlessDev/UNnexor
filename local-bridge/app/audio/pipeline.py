"""Orquestación de captura + segmentación para una o varias fuentes.

Hilos:

* un ``CaptureWorker`` por fuente (sistema y/o micrófono);
* un hilo de segmentación que consume una cola acotada de ``AudioChunk``,
  ejecuta un segmentador por fuente (cada uno con su VAD: el VAD tiene
  estado) y entrega ``SpeechSegment`` con identificadores globales.

Los callbacks se invocan desde hilos de trabajo; la capa Qt los convierte
en señales (conexiones en cola). Nada de esto bloquea la interfaz.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace

from .capture import CaptureBackend, CaptureState, CaptureStatus, CaptureWorker
from .dsp import rms_dbfs
from .segmenter import Segmenter, SegmenterConfig
from .types import AudioChunk, SourceKind, SpeechSegment
from .vad import VoiceActivityDetector, create_vad

log = logging.getLogger("extdator.audio.pipeline")

LevelCallback = Callable[[SourceKind, float, float], None]


@dataclass(frozen=True)
class SourceSelection:
    kind: SourceKind
    device_id: str | None = None  # None = dispositivo por defecto


class AudioPipeline:
    """Captura simultánea de varias fuentes con segmentación por voz."""

    _STOP = object()

    def __init__(
        self,
        backend: CaptureBackend,
        sources: list[SourceSelection],
        on_segment: Callable[[SpeechSegment], None],
        on_status: Callable[[CaptureStatus], None],
        *,
        on_level: LevelCallback | None = None,
        vad_factory: Callable[[], VoiceActivityDetector] = create_vad,
        segmenter_config: SegmenterConfig | None = None,
        level_interval_s: float = 0.1,
        max_queue_chunks: int = 600,
    ) -> None:
        if not sources:
            raise ValueError("Selecciona al menos una fuente de audio")
        kinds = [source.kind for source in sources]
        if len(set(kinds)) != len(kinds):
            raise ValueError("Cada tipo de fuente solo puede elegirse una vez")
        self._backend = backend
        self._sources = list(sources)
        self._on_segment = on_segment
        self._on_status = on_status
        self._on_level = on_level
        self._vad_factory = vad_factory
        self._seg_cfg = segmenter_config
        self._level_interval = level_interval_s
        self._queue: queue.Queue[object] = queue.Queue(maxsize=max_queue_chunks)
        self._workers: list[CaptureWorker] = []
        self._thread: threading.Thread | None = None
        self._segmenters: dict[SourceKind, Segmenter] = {}
        self._ids: dict[tuple[SourceKind, int], int] = {}
        self._next_id = 1
        self._last_level: dict[SourceKind, float] = {}
        self._dropped = 0
        self._lock = threading.Lock()
        self._origin = time.monotonic()
        self.vad_name = ""

    # ------------------------------------------------------------------ #
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def dropped_chunks(self) -> int:
        return self._dropped

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("La captura ya se inició")
        for source in self._sources:
            vad = self._vad_factory()
            self.vad_name = vad.name
            self._segmenters[source.kind] = Segmenter(vad, source.kind, self._seg_cfg)
        self._thread = threading.Thread(
            target=self._segment_loop, name="audio-segmenter", daemon=True
        )
        self._thread.start()
        for source in self._sources:
            worker = CaptureWorker(
                self._backend,
                source.kind,
                self._enqueue,
                self._on_status,
                device_id=source.device_id,
                clock_origin=self._origin,
            )
            self._workers.append(worker)
            worker.start()

    def pause(self) -> None:
        for worker in self._workers:
            worker.pause()
        self._queue.put(("flush",))

    def resume(self) -> None:
        for worker in self._workers:
            worker.resume()

    def stop(self, timeout: float = 5.0) -> None:
        """Detiene la captura, cierra intervenciones y libera dispositivos."""
        for worker in self._workers:
            worker.stop()
        for worker in self._workers:
            worker.join(timeout)
            if worker.is_alive():
                log.warning("El hilo %s no terminó a tiempo", worker.name)
        if self._thread is not None:
            self._put_blocking(self._STOP)
            self._thread.join(timeout)
        self._workers.clear()

    # ------------------------------------------------------------------ #
    def _enqueue(self, chunk: AudioChunk) -> None:
        try:
            self._queue.put_nowait(chunk)
        except queue.Full:
            # Nunca bloquear al hilo de captura: se descarta lo más antiguo.
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            self._dropped += 1
            try:
                self._queue.put_nowait(chunk)
            except queue.Full:
                pass

    def _put_blocking(self, item: object) -> None:
        while True:
            try:
                self._queue.put(item, timeout=0.5)
                return
            except queue.Full:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    pass

    def _segment_loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is self._STOP or (
                isinstance(item, tuple) and item and item[0] == "flush"
            ):
                for active in self._segmenters.values():
                    self._dispatch(active.flush())
                if item is self._STOP:
                    return
                continue
            if not isinstance(item, AudioChunk):
                continue
            current = self._segmenters.get(item.source)
            if current is None:
                continue
            try:
                self._dispatch(current.feed(item))
            except Exception:
                log.exception("Fallo segmentando audio de %s", item.source.value)
            self._report_level(item, current.last_probability)

    def _dispatch(self, segments: list[SpeechSegment]) -> None:
        for segment in segments:
            key = (segment.source, segment.segment_id)
            with self._lock:
                global_id = self._ids.get(key)
                if global_id is None:
                    global_id = self._next_id
                    self._next_id += 1
                    self._ids[key] = global_id
                if segment.final:
                    self._ids.pop(key, None)
            try:
                self._on_segment(replace(segment, segment_id=global_id))
            except Exception:
                log.exception("Fallo en el consumidor de segmentos")

    def _report_level(self, chunk: AudioChunk, probability: float) -> None:
        if self._on_level is None:
            return
        now = time.monotonic()
        if now - self._last_level.get(chunk.source, 0.0) < self._level_interval:
            return
        self._last_level[chunk.source] = now
        try:
            self._on_level(chunk.source, rms_dbfs(chunk.samples), probability)
        except Exception:
            log.exception("Fallo en el indicador de nivel")


def failed_sources(statuses: dict[SourceKind, CaptureStatus]) -> list[SourceKind]:
    """Fuentes cuya captura terminó en error definitivo."""
    return [kind for kind, st in statuses.items() if st.state is CaptureState.FAILED]

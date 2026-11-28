"""Captura de audio: puerto, adaptador SoundCard y worker por fuente.

Windows (prioritario): SoundCard usa WASAPI. El audio del sistema se graba
en modo *loopback* del altavoz por defecto (lo que suena en el equipo) y el
micrófono es una fuente independiente y opcional.

Notas verificadas en la documentación de SoundCard y en este equipo:

* WASAPI "graba basura" si se pide un solo canal: se captura en estéreo y
  se mezcla a mono en ``dsp.to_mono``.
* SoundCard inicializa COM (MTA) al importarse; en cada hilo de captura se
  importa primero y luego se llama a ``CoInitializeEx`` (equilibrado con
  ``CoUninitialize``). Importarlo con COM ya inicializado en el hilo falla.
* En loopback sin reproducción, WASAPI no entrega paquetes y SoundCard
  rellena con ceros en tiempo real (silencio, no bloqueo).

El worker nunca toca Qt: publica ``AudioChunk`` y estados mediante
callbacks. La capa de presentación los traduce a señales.
"""

from __future__ import annotations

import contextlib
import logging
import sys
import threading
import time
import warnings
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

import numpy as np

from .dsp import StreamingResampler, to_mono
from .types import SAMPLE_RATE, AudioChunk, AudioDevice, FloatArray, SourceKind

log = logging.getLogger("unnexor.audio.capture")

CAPTURE_RATE = 48_000
CAPTURE_CHANNELS = 2


class CaptureError(RuntimeError):
    """Error de captura con mensaje orientado al usuario."""

    def __init__(self, message: str, kind: str = "backend") -> None:
        super().__init__(message)
        self.message = message
        self.kind = kind  # unavailable | device | backend


class CaptureStream(Protocol):
    samplerate: int
    channels: int

    def read(self, frames: int) -> Any:
        """Devuelve ``(frames, channels)`` en ``float32``."""
        ...

    def close(self) -> None: ...


class CaptureBackend(Protocol):
    name: str

    def thread_scope(self) -> contextlib.AbstractContextManager[None]:
        """Prepara el hilo actual para usar el backend (p. ej. COM)."""
        ...

    def available(self) -> tuple[bool, str]: ...

    def list_devices(self) -> list[AudioDevice]: ...

    def open(
        self,
        kind: SourceKind,
        device_id: str | None,
        samplerate: int,
        channels: int,
    ) -> CaptureStream: ...


# --------------------------------------------------------------------- #
# Adaptador SoundCard
# --------------------------------------------------------------------- #
@contextlib.contextmanager
def _thread_com() -> Iterator[None]:
    """Inicializa COM (MTA) en el hilo actual en Windows y lo libera."""
    if sys.platform != "win32":
        yield
        return
    import ctypes

    ole32 = ctypes.windll.ole32  # type: ignore[attr-defined,unused-ignore]
    hr = ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED
    try:
        yield
    finally:
        if hr in (0, 1):  # S_OK / S_FALSE: hay que equilibrar
            ole32.CoUninitialize()


def _import_soundcard() -> Any:
    # Aviso benigno de SoundCard al abrir loopback sin reproducción (WASAPI
    # no entrega paquetes y se rellenan ceros). Filtro acotado a ese mensaje.
    warnings.filterwarnings("ignore", message="data discontinuity in recording")
    try:
        import soundcard
    except Exception as exc:  # ImportError o fallo de inicialización nativa
        raise CaptureError(
            "La captura de audio no está disponible en esta instalación "
            f"({exc.__class__.__name__}).",
            "unavailable",
        ) from exc
    return soundcard


class _SoundCardStream:
    def __init__(self, recorder: Any, samplerate: int, channels: int) -> None:
        self._recorder = recorder
        self.samplerate = samplerate
        self.channels = channels
        self._open = True

    def read(self, frames: int) -> Any:
        return self._recorder.record(numframes=frames)

    def close(self) -> None:
        if self._open:
            self._open = False
            try:
                self._recorder.__exit__(None, None, None)
            except Exception:  # liberar nunca debe propagar
                log.debug("Cierre del grabador con error", exc_info=True)


class SoundCardBackend:
    """WASAPI (Windows), PulseAudio (Linux) o CoreAudio (macOS)."""

    name = "soundcard"

    @contextlib.contextmanager
    def thread_scope(self) -> Iterator[None]:
        # Orden obligatorio: importar (SoundCard inicializa COM al importarse)
        # y solo después inicializar COM en el hilo.
        _import_soundcard()
        with _thread_com():
            yield

    def available(self) -> tuple[bool, str]:
        try:
            with self.thread_scope():
                _import_soundcard().default_speaker()
        except CaptureError as exc:
            return False, exc.message
        except Exception as exc:
            return False, f"Sin dispositivos de audio utilizables ({exc})."
        return True, ""

    def list_devices(self) -> list[AudioDevice]:
        with self.thread_scope():
            sc = _import_soundcard()
            devices: list[AudioDevice] = []
            try:
                default_speaker = sc.default_speaker()
            except Exception:
                default_speaker = None
            try:
                default_mic = sc.default_microphone()
            except Exception:
                default_mic = None
            for mic in sc.all_microphones(include_loopback=True):
                loop = bool(getattr(mic, "isloopback", False))
                kind = SourceKind.SYSTEM if loop else SourceKind.MICROPHONE
                if loop:
                    is_default = default_speaker is not None and (
                        mic.name == default_speaker.name
                    )
                else:
                    is_default = default_mic is not None and mic.id == default_mic.id
                devices.append(
                    AudioDevice(
                        id=str(mic.id),
                        name=str(mic.name),
                        kind=kind,
                        is_default=is_default,
                        channels=int(getattr(mic, "channels", 2) or 2),
                    )
                )
            return devices

    def open(
        self,
        kind: SourceKind,
        device_id: str | None,
        samplerate: int,
        channels: int,
    ) -> CaptureStream:
        sc = _import_soundcard()
        try:
            if kind is SourceKind.SYSTEM:
                if device_id:
                    mic = sc.get_microphone(id=device_id, include_loopback=True)
                else:
                    speaker = sc.default_speaker()
                    mic = sc.get_microphone(id=str(speaker.name), include_loopback=True)
            else:
                mic = (
                    sc.get_microphone(id=device_id)
                    if device_id
                    else sc.default_microphone()
                )
            recorder = mic.recorder(samplerate=samplerate, channels=channels)
            recorder.__enter__()
        except IndexError as exc:
            raise CaptureError(
                "No se encontró el dispositivo de audio seleccionado.", "device"
            ) from exc
        except Exception as exc:
            raise CaptureError(
                f"No se pudo abrir el dispositivo de audio: {exc}", "device"
            ) from exc
        return _SoundCardStream(recorder, samplerate, channels)


# --------------------------------------------------------------------- #
# Worker por fuente
# --------------------------------------------------------------------- #
class CaptureState(str, Enum):
    STARTING = "starting"
    CAPTURING = "capturing"
    PAUSED = "paused"
    RECONNECTING = "reconnecting"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(frozen=True)
class CaptureStatus:
    source: SourceKind
    state: CaptureState
    message: str = ""
    level_dbfs: float | None = None


class CaptureWorker(threading.Thread):
    """Lee una fuente en bloques de ``block_ms`` y entrega ``AudioChunk``.

    * La pausa mantiene el dispositivo abierto y descarta el audio, para
      reanudar al instante sin perder la sincronía del búfer de WASAPI.
    * Ante errores recuperables reintenta con espera exponencial
      (0,5 → 8 s) hasta ``max_retries``; luego informa ``FAILED``.
    * ``stop()`` es idempotente y el dispositivo se libera siempre.
    """

    def __init__(
        self,
        backend: CaptureBackend,
        source: SourceKind,
        on_chunk: Callable[[AudioChunk], None],
        on_status: Callable[[CaptureStatus], None],
        *,
        device_id: str | None = None,
        clock_origin: float | None = None,
        block_ms: int = 100,
        max_retries: int = 5,
    ) -> None:
        super().__init__(name=f"capture-{source.value}", daemon=True)
        self._backend = backend
        self.source = source
        self._on_chunk = on_chunk
        self._on_status = on_status
        self._device_id = device_id
        self._origin = time.monotonic() if clock_origin is None else clock_origin
        self._block_ms = block_ms
        self._max_retries = max_retries
        self._stop_event = threading.Event()
        self._paused = threading.Event()

    # Control (thread-safe) ---------------------------------------------
    def stop(self) -> None:
        self._stop_event.set()

    def pause(self) -> None:
        self._paused.set()
        self._emit(CaptureState.PAUSED)

    def resume(self) -> None:
        self._paused.clear()
        self._emit(CaptureState.CAPTURING)

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    # ------------------------------------------------------------------ #
    def run(self) -> None:
        try:
            with self._backend.thread_scope():
                failure = self._run_loop()
        except CaptureError as exc:  # backend no disponible en este equipo
            failure = exc.message
        if failure is None:
            self._emit(CaptureState.STOPPED)
        else:
            self._emit(CaptureState.FAILED, failure)

    def _run_loop(self) -> str | None:
        """Bucle de apertura/lectura/reconexión. Devuelve el error final o
        ``None`` si terminó porque se pidió detener."""
        attempt = 0
        while not self._stop_event.is_set():
            self._emit(
                CaptureState.STARTING if attempt == 0 else CaptureState.RECONNECTING
            )
            try:
                stream = self._backend.open(
                    self.source, self._device_id, CAPTURE_RATE, CAPTURE_CHANNELS
                )
            except CaptureError as exc:
                if exc.kind == "unavailable" or not self._backoff(attempt, exc):
                    return exc.message
                attempt += 1
                continue
            try:
                self._emit(
                    CaptureState.PAUSED if self.paused else CaptureState.CAPTURING
                )
                self._read_loop(stream)
                attempt = 0
            except Exception as exc:  # dispositivo desconectado, etc.
                log.warning("Captura %s interrumpida: %s", self.source.value, exc)
                error = CaptureError(f"Se perdió el dispositivo: {exc}", "device")
                if not self._backoff(attempt, error):
                    return error.message
                attempt += 1
            finally:
                stream.close()
        return None

    def _read_loop(self, stream: CaptureStream) -> None:
        frames = stream.samplerate * self._block_ms // 1000
        resampler = StreamingResampler(stream.samplerate, SAMPLE_RATE)
        while not self._stop_event.is_set():
            raw = stream.read(frames)
            read_end = time.monotonic() - self._origin
            if self.paused:
                resampler.reset()
                continue
            mono = to_mono(np.asarray(raw))
            samples: FloatArray = resampler.process(mono)
            if samples.size == 0:
                continue
            chunk = AudioChunk(
                samples=samples,
                source=self.source,
                t_start=max(0.0, read_end - len(samples) / SAMPLE_RATE),
            )
            self._on_chunk(chunk)

    def _backoff(self, attempt: int, error: CaptureError) -> bool:
        if attempt >= self._max_retries:
            return False
        delay = min(8.0, 0.5 * (2**attempt))
        self._emit(CaptureState.RECONNECTING, f"{error.message} Reintentando…")
        return not self._stop_event.wait(delay)

    def _emit(self, state: CaptureState, message: str = "") -> None:
        try:
            self._on_status(CaptureStatus(self.source, state, message))
        except Exception:  # un consumidor roto no debe matar la captura
            log.exception("Fallo en el callback de estado de captura")

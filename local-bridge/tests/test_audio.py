"""Pruebas del paquete de audio: DSP, VAD, segmentación, captura y pipeline.

La captura real (WASAPI) se valida en ``scripts/audio_e2e.py``; aquí se usan
backends simulados para cubrir reconexión, pausa y liberación sin hardware.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Iterator

import numpy as np
import pytest

from app.audio import dsp
from app.audio.capture import CaptureError, CaptureState, CaptureStatus, CaptureWorker
from app.audio.pipeline import AudioPipeline, SourceSelection
from app.audio.segmenter import Segmenter, SegmenterConfig
from app.audio.types import SAMPLE_RATE, AudioChunk, AudioDevice, SourceKind
from app.audio.vad import EnergyVAD, SileroVAD, VADUnavailable, create_vad
from app.audio.wav import decode_wav, encode_wav


def tone(freq: float, seconds: float, rate: int = SAMPLE_RATE, amp: float = 0.4):
    t = np.arange(int(seconds * rate)) / rate
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def silence(seconds: float, rate: int = SAMPLE_RATE):
    return np.zeros(int(seconds * rate), dtype=np.float32)


def dominant_freq(samples, rate: int) -> float:
    spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
    return float(np.fft.rfftfreq(len(samples), 1 / rate)[np.argmax(spectrum)])


# --------------------------------------------------------------------- DSP
def test_to_mono_averages_channels_and_keeps_mono():
    stereo = np.array([[1.0, 0.0], [0.5, 0.5]], dtype=np.float32)
    assert np.allclose(dsp.to_mono(stereo), [0.5, 0.5])
    mono = np.array([0.1, 0.2], dtype=np.float32)
    assert np.allclose(dsp.to_mono(mono), mono)
    with pytest.raises(ValueError):
        dsp.to_mono(np.zeros((2, 2, 2)))


@pytest.mark.parametrize("src", [48_000, 44_100, 32_000])
def test_streaming_resampler_preserves_tone_and_length(src):
    signal = tone(440, 2.0, rate=src)
    resampler = dsp.StreamingResampler(src, SAMPLE_RATE)
    sizes = [479, 4800, 1, 1000, 333]
    out, pos, i = [], 0, 0
    while pos < len(signal):
        size = sizes[i % len(sizes)]
        out.append(resampler.process(signal[pos : pos + size]))
        pos += size
        i += 1
    result = np.concatenate(out)
    expected = len(signal) * SAMPLE_RATE / src
    assert abs(len(result) - expected) <= 2
    assert abs(dominant_freq(result, SAMPLE_RATE) - 440) < 3
    steady = result[2000:-2000]
    assert 0.35 < np.max(np.abs(steady)) < 0.45


def test_streaming_equals_one_shot():
    signal = tone(300, 1.0, rate=48_000) + tone(1200, 1.0, rate=48_000, amp=0.1)
    one = dsp.StreamingResampler(48_000, SAMPLE_RATE).process(signal)
    streamer = dsp.StreamingResampler(48_000, SAMPLE_RATE)
    pieces = [streamer.process(block) for block in np.array_split(signal, 37)]
    assert np.allclose(np.concatenate(pieces), one, atol=1e-5)


def test_resampler_attenuates_aliasing_tones():
    # 10 kHz no cabe en 16 kHz (Nyquist 8 kHz): debe atenuarse, no plegarse.
    alias = dsp.StreamingResampler(48_000, SAMPLE_RATE).process(
        tone(10_000, 1.0, 48_000)
    )
    assert dsp.rms_dbfs(alias[1000:]) < -40


def test_passthrough_and_levels():
    block = tone(200, 0.1)
    assert np.array_equal(dsp.StreamingResampler(16_000, 16_000).process(block), block)
    assert dsp.rms_dbfs(silence(0.1)) == dsp.SILENCE_DBFS
    assert -12 < dsp.rms_dbfs(tone(200, 0.5, amp=0.4)) < -10


def test_normalize_boosts_quiet_but_never_attenuates():
    quiet = tone(200, 0.2, amp=0.05)
    boosted = dsp.normalize(quiet)
    assert np.max(np.abs(boosted)) == pytest.approx(0.4, rel=0.05)  # máx. x8
    loud = tone(200, 0.2, amp=0.95)
    assert np.array_equal(dsp.normalize(loud), loud)


def test_wav_roundtrip():
    original = tone(440, 0.3)
    decoded, rate = decode_wav(encode_wav(original))
    assert rate == SAMPLE_RATE
    assert np.allclose(decoded, original, atol=1e-3)


# --------------------------------------------------------------------- VAD
def frames(samples, size):
    for start in range(0, len(samples) - size + 1, size):
        yield samples[start : start + size]


def test_energy_vad_separates_silence_and_signal():
    vad = EnergyVAD()
    noise = (np.random.default_rng(0).normal(0, 0.001, SAMPLE_RATE)).astype(np.float32)
    quiet = [vad.speech_probability(f) for f in frames(noise, vad.frame_samples)]
    loud = [
        vad.speech_probability(f) for f in frames(tone(300, 0.5), vad.frame_samples)
    ]
    assert max(quiet) < 0.2
    assert min(loud) > 0.9


def test_silero_vad_streaming_api():
    try:
        vad = SileroVAD()
    except VADUnavailable:
        pytest.skip("Silero VAD no disponible (faster-whisper no instalado)")
    probs = [vad.speech_probability(f) for f in frames(silence(1.0), 512)]
    assert max(probs) < 0.2
    with pytest.raises(ValueError):
        vad.speech_probability(silence(0.01))
    vad.reset()


def test_create_vad_falls_back_to_energy(monkeypatch):
    from app.audio import vad as vad_module

    monkeypatch.setattr(vad_module, "silero_model_path", lambda: None)
    assert isinstance(create_vad("auto"), EnergyVAD)
    with pytest.raises(VADUnavailable):
        create_vad("silero")


# --------------------------------------------------------------- Segmenter
class ThresholdVAD:
    """VAD determinista: voz si el pico supera 0,05."""

    name = "threshold"
    frame_samples = 480

    def speech_probability(self, frame) -> float:
        return 1.0 if float(np.max(np.abs(frame))) > 0.05 else 0.0

    def reset(self) -> None:
        pass


def feed_all(segmenter: Segmenter, samples, block: int = 1600):
    events, t = [], 0.0
    for start in range(0, len(samples), block):
        piece = samples[start : start + block]
        events += segmenter.feed(AudioChunk(piece, SourceKind.SYSTEM, t))
        t += len(piece) / SAMPLE_RATE
    return events


def test_segmenter_emits_one_final_with_pre_roll_and_trimmed_silence():
    seg = Segmenter(ThresholdVAD(), SourceKind.SYSTEM)
    audio = np.concatenate([silence(1.0), tone(250, 1.2), silence(1.5)])
    events = feed_all(seg, audio)
    finals = [e for e in events if e.final]
    assert len(finals) == 1
    final = finals[0]
    assert not final.discarded
    assert final.t_start == pytest.approx(0.7, abs=0.05)  # 300 ms de pre-roll
    # 0,3 pre-roll + 1,2 voz + 0,2 de silencio final conservado
    assert final.duration == pytest.approx(1.7, abs=0.05)
    assert final.source is SourceKind.SYSTEM


def test_segmenter_emits_provisional_partials_for_long_speech():
    seg = Segmenter(ThresholdVAD(), SourceKind.SYSTEM)
    events = feed_all(seg, np.concatenate([tone(250, 3.4), silence(1.0)]))
    partials = [e for e in events if not e.final]
    assert len(partials) == 2  # a 1,5 s y a 3,0 s
    assert partials[0].duration < partials[1].duration
    assert {e.segment_id for e in events} == {1}
    assert events[-1].final


def test_segmenter_cuts_monologues_at_max_duration():
    config = SegmenterConfig(max_segment_s=2.0, partial_interval_s=10)
    seg = Segmenter(ThresholdVAD(), SourceKind.SYSTEM, config)
    events = feed_all(seg, np.concatenate([tone(250, 5.0), silence(1.0)]))
    finals = [e for e in events if e.final]
    assert [e.segment_id for e in finals] == [1, 2, 3]
    assert all(f.duration <= 2.05 for f in finals)


def test_segmenter_discards_short_blips_and_flushes():
    seg = Segmenter(ThresholdVAD(), SourceKind.MICROPHONE)
    events = feed_all(seg, np.concatenate([silence(0.5), tone(250, 0.15), silence(1)]))
    assert len(events) == 1 and events[0].discarded
    feed_all(seg, tone(250, 0.6))
    assert seg.in_speech
    flushed = seg.flush()
    assert len(flushed) == 1 and flushed[0].final and not flushed[0].discarded
    assert seg.flush() == []


def test_segmenter_config_validation():
    with pytest.raises(ValueError):
        SegmenterConfig(start_threshold=0.3, end_threshold=0.5)


# ------------------------------------------------------------ Captura
class FakeStream:
    def __init__(self, script, rate=48_000):
        self.samplerate = rate
        self.channels = 2
        self._script = script
        self.closed = False

    def read(self, frames):
        action = self._script.pop(0) if self._script else "tone"
        time.sleep(0.001)
        if action == "error":
            raise OSError("dispositivo desconectado")
        sig = tone(300, frames / self.samplerate, rate=self.samplerate)
        return np.stack([sig, sig], axis=1)

    def close(self):
        self.closed = True


class FakeBackend:
    name = "fake"

    def __init__(self, streams=None, open_errors=None):
        self.streams = list(streams or [])
        self.open_errors = list(open_errors or [])
        self.opened: list[FakeStream] = []

    @contextlib.contextmanager
    def thread_scope(self) -> Iterator[None]:
        yield

    def available(self):
        return True, ""

    def list_devices(self):
        return [AudioDevice("d1", "Altavoz", SourceKind.SYSTEM, True)]

    def open(self, kind, device_id, samplerate, channels):
        if self.open_errors:
            raise self.open_errors.pop(0)
        stream = self.streams.pop(0) if self.streams else FakeStream([])
        self.opened.append(stream)
        return stream


def run_worker(backend, until, **kwargs):
    chunks, statuses = [], []
    worker = CaptureWorker(
        backend, SourceKind.SYSTEM, chunks.append, statuses.append, **kwargs
    )
    worker._backoff_base = 0  # type: ignore[attr-defined]
    worker.start()
    deadline = time.monotonic() + 5
    while not until(chunks, statuses) and time.monotonic() < deadline:
        time.sleep(0.01)
    worker.stop()
    worker.join(5)
    assert not worker.is_alive()
    return chunks, statuses


def test_worker_resamples_to_16k_mono_and_releases_device():
    backend = FakeBackend()
    chunks, statuses = run_worker(backend, lambda c, s: len(c) >= 3)
    assert all(chunk.samples.ndim == 1 for chunk in chunks)
    assert len(chunks[1].samples) == 1600  # 100 ms a 16 kHz
    assert statuses[-1].state is CaptureState.STOPPED
    assert backend.opened[0].closed


def test_worker_reconnects_after_device_error(monkeypatch):
    monkeypatch.setattr(CaptureWorker, "_backoff", lambda self, a, e: True)
    backend = FakeBackend(streams=[FakeStream(["tone", "error"]), FakeStream([])])
    chunks, statuses = run_worker(backend, lambda c, s: len(c) >= 4)
    states = [s.state for s in statuses]
    assert CaptureState.RECONNECTING in states
    assert len(backend.opened) == 2
    assert all(stream.closed for stream in backend.opened)


def test_worker_fails_when_backend_unavailable():
    backend = FakeBackend(open_errors=[CaptureError("sin audio", "unavailable")])
    _, statuses = run_worker(
        backend, lambda c, s: any(x.state is CaptureState.FAILED for x in s)
    )
    assert statuses[-1].state is CaptureState.FAILED
    assert statuses[-1].message == "sin audio"


def test_worker_gives_up_after_max_retries(monkeypatch):
    monkeypatch.setattr("app.audio.capture.time.sleep", lambda _s: None)
    errors = [CaptureError("ocupado", "device") for _ in range(10)]
    backend = FakeBackend(open_errors=errors)
    worker = CaptureWorker(
        backend, SourceKind.SYSTEM, lambda c: None, lambda s: None, max_retries=2
    )
    monkeypatch.setattr(worker._stop_event, "wait", lambda _t: False)
    statuses: list[CaptureStatus] = []
    worker._on_status = statuses.append
    worker.run()
    assert statuses[-1].state is CaptureState.FAILED
    assert len(backend.open_errors) == 7  # 1 intento + 2 reintentos


def test_worker_pause_drops_audio_and_resume_restores():
    backend = FakeBackend()
    chunks: list[AudioChunk] = []
    statuses: list[CaptureStatus] = []
    worker = CaptureWorker(backend, SourceKind.SYSTEM, chunks.append, statuses.append)
    worker.pause()
    worker.start()
    time.sleep(0.3)
    assert chunks == []
    worker.resume()
    deadline = time.monotonic() + 3
    while not chunks and time.monotonic() < deadline:
        time.sleep(0.01)
    worker.stop()
    worker.join(5)
    assert chunks
    assert CaptureState.PAUSED in [s.state for s in statuses]


# ------------------------------------------------------------ Pipeline
def test_pipeline_two_sources_assigns_global_ids_and_flushes_on_stop():
    segments, statuses, levels = [], [], []
    lock = threading.Lock()

    def on_segment(segment):
        with lock:
            segments.append(segment)

    pipeline = AudioPipeline(
        FakeBackend(),
        [SourceSelection(SourceKind.SYSTEM), SourceSelection(SourceKind.MICROPHONE)],
        on_segment,
        statuses.append,
        on_level=lambda kind, db, p: levels.append((kind, db, p)),
        vad_factory=ThresholdVAD,
    )
    pipeline.start()
    time.sleep(0.8)
    pipeline.stop()
    finals = [s for s in segments if s.final]
    assert {s.source for s in finals} == {SourceKind.SYSTEM, SourceKind.MICROPHONE}
    assert len({s.segment_id for s in finals}) == len(finals)
    assert levels and all(db > -20 for _, db, _ in levels)
    assert not pipeline.running


def test_pipeline_rejects_duplicate_or_empty_sources():
    with pytest.raises(ValueError):
        AudioPipeline(FakeBackend(), [], lambda s: None, lambda s: None)
    duplicated = [
        SourceSelection(SourceKind.SYSTEM),
        SourceSelection(SourceKind.SYSTEM),
    ]
    with pytest.raises(ValueError):
        AudioPipeline(FakeBackend(), duplicated, lambda s: None, lambda s: None)

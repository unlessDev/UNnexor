"""Pruebas del servicio de transcripción con un motor simulado."""

from __future__ import annotations

import threading
import time

import numpy as np

from app.audio.types import SAMPLE_RATE, SourceKind, SpeechSegment
from app.copilot.transcription import TranscriberState, TranscriptionService
from app.stt.base import STTError, Transcript


def segment(
    seg_id: int,
    final: bool,
    seconds: float = 1.0,
    discarded: bool = False,
    source: SourceKind = SourceKind.SYSTEM,
) -> SpeechSegment:
    samples = np.full(int(seconds * SAMPLE_RATE), seg_id / 100, dtype=np.float32)
    return SpeechSegment(seg_id, source, samples, 0.0, seconds, final, discarded)


class FakeRecognizer:
    engine_id = "fake"
    model = "m"
    is_remote = False

    def __init__(self, supports_partials=True, delay=0.0, errors=None):
        self.supports_partials = supports_partials
        self.delay = delay
        self.errors = list(errors or [])
        self.calls: list[tuple[int, str | None]] = []
        self.closed = False
        self.gate = threading.Event()
        self.gate.set()

    def transcribe(self, audio, *, language=None, prompt=None):
        self.gate.wait(5)
        seg_id = round(float(audio[0]) * 100)
        self.calls.append((seg_id, prompt))
        if self.errors:
            raise self.errors.pop(0)
        time.sleep(self.delay)
        return Transcript(
            text=f"texto {seg_id}",
            engine="fake",
            model="m",
            audio_seconds=len(audio) / SAMPLE_RATE,
            elapsed_seconds=self.delay,
            language=language,
            confidence=0.9,
        )

    def close(self):
        self.closed = True


def run_service(recognizer, segments, *, wait_for=None, **kwargs):
    events, errors, states = [], [], []
    service = TranscriptionService(
        lambda: recognizer,
        events.append,
        on_error=errors.append,
        on_state=states.append,
        retry_delay_s=0.01,
        **kwargs,
    )
    service.start()
    for seg in segments:
        service.submit(seg)
    deadline = time.monotonic() + 5
    while wait_for and not wait_for(events, errors) and time.monotonic() < deadline:
        time.sleep(0.01)
    service.stop()
    return service, events, errors, states


def test_finals_are_never_dropped_and_partials_are_superseded():
    recognizer = FakeRecognizer()
    recognizer.gate.clear()  # bloquea el motor mientras se encola todo
    events, errors = [], []
    service = TranscriptionService(
        lambda: recognizer, events.append, on_error=errors.append
    )
    service.start()
    time.sleep(0.05)
    service.submit(segment(9, final=False))  # entra al motor y queda bloqueado
    time.sleep(0.05)
    for seg in (
        segment(1, False),
        segment(1, False, 2),
        segment(2, False),
        segment(3, True),
        segment(4, True),
    ):
        service.submit(seg)
    recognizer.gate.set()
    service.stop()
    finals = [e.segment_id for e in events if e.final]
    assert finals == [3, 4]
    transcribed = [c[0] for c in recognizer.calls]
    # Tras el bloqueo: primero los finales, luego solo el provisional más nuevo.
    assert transcribed[1:3] == [3, 4]
    assert 1 not in transcribed  # provisionales viejos reemplazados sin transcribir
    assert recognizer.closed and service.state is TranscriberState.STOPPED


def test_final_cancels_pending_partial_of_same_segment():
    recognizer = FakeRecognizer()
    _, events, _, _ = run_service(
        recognizer,
        [segment(5, False), segment(5, True)],
        wait_for=lambda ev, _e: any(x.final for x in ev),
    )
    assert [e.final for e in events if e.segment_id == 5][-1] is True


def test_partials_ignored_when_engine_does_not_support_them():
    recognizer = FakeRecognizer(supports_partials=False)
    _, events, _, _ = run_service(
        recognizer,
        [segment(1, False), segment(1, True)],
        wait_for=lambda ev, _e: any(x.final for x in ev),
    )
    assert [(e.segment_id, e.final) for e in events] == [(1, True)]


def test_discarded_segment_emits_retraction():
    _, events, _, _ = run_service(
        FakeRecognizer(),
        [segment(7, True, discarded=True)],
        wait_for=lambda ev, _e: bool(ev),
    )
    assert events[0].retracted and events[0].segment_id == 7


def test_fatal_error_stops_service_and_ignores_new_segments():
    recognizer = FakeRecognizer(errors=[STTError("clave mala", "auth")])
    service, events, errors, states = run_service(
        recognizer,
        [segment(1, True), segment(2, True)],
        wait_for=lambda _ev, err: bool(err),
    )
    assert service.failed
    assert errors[0].kind == "auth"
    assert TranscriberState.FAILED in states
    service.submit(segment(3, True))
    assert all(e.segment_id != 3 for e in events)


def test_quota_error_is_retried_once():
    recognizer = FakeRecognizer(errors=[STTError("429", "quota", retry_after=0.01)])
    _, events, errors, _ = run_service(
        recognizer, [segment(1, True)], wait_for=lambda ev, _e: bool(ev)
    )
    assert events[0].text == "texto 1" and not errors
    assert len(recognizer.calls) == 2


def test_network_error_twice_reports_and_retracts():
    recognizer = FakeRecognizer(errors=[STTError("red", "network")] * 2)
    _, events, errors, _ = run_service(
        recognizer, [segment(1, True)], wait_for=lambda ev, _e: bool(ev)
    )
    assert errors and errors[0].kind == "network"
    assert events[0].retracted


def test_prompt_carries_confirmed_text_and_glossary_per_source():
    recognizer = FakeRecognizer()
    run_service(
        recognizer,
        [
            segment(1, True),
            segment(2, True),
            segment(3, True, source=SourceKind.MICROPHONE),
        ],
        wait_for=lambda ev, _e: len(ev) == 3,
        glossary=lambda: "Kubernetes, Terraform",
    )
    prompts = {seg_id: prompt for seg_id, prompt in recognizer.calls}
    assert prompts[1] == "Kubernetes, Terraform"
    assert prompts[2] == "Kubernetes, Terraform texto 1"
    assert prompts[3] == "Kubernetes, Terraform"  # otra fuente, otro contexto


def test_backlog_warning_when_engine_is_too_slow():
    recognizer = FakeRecognizer(delay=0.05)
    _, _, errors, _ = run_service(
        recognizer,
        [segment(i, True, seconds=3) for i in range(1, 12)],
        wait_for=lambda _ev, err: bool(err),
        backlog_warning_s=10,
    )
    assert any(e.kind == "backlog" for e in errors)


def test_factory_failure_is_reported():
    events, errors, states = [], [], []

    def boom():
        raise STTError("modelo ausente", "model_missing")

    service = TranscriptionService(
        boom, events.append, on_error=errors.append, on_state=states.append
    )
    service.start()
    service.stop()
    assert service.failed and errors[0].kind == "model_missing"
    assert states[-1] is TranscriberState.FAILED

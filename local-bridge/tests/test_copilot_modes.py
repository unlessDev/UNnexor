"""Traducción en tiempo real y desafíos en pantalla (motor simulado)."""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from app.audio.types import SourceKind
from app.copilot.challenges import ChallengeSolver, ChangeDetector, fingerprint, hamming
from app.copilot.engine import GenerationResult
from app.copilot.transcription import TranscriptEvent
from app.copilot.translation import LiveTranslator, SegmentStatus
from app.providers.access import AccessTier
from app.routing.executor import RoutingError
from app.routing.router import Candidate, RouteDecision, Task


class Engine:
    def __init__(self, vision=True, text=True, fail_on=None):
        self.vision, self.text, self.fail_on = vision, text, fail_on
        self.calls = []
        self.gate = threading.Event()
        self.gate.set()

    def decide(self, task, sensitive=False, local_only=False):
        ok = self.vision if task is Task.VISION else self.text
        cand = Candidate(
            "ollama", "m", True, AccessTier.LOCAL, frozenset({"chat", "vision"})
        )
        return RouteDecision(task, cand if ok else None)

    def generate(
        self,
        task,
        messages,
        *,
        sensitive=False,
        on_chunk=None,
        should_abort=None,
        on_notice=None,
        temperature=None,
        local_only=False,
    ):
        self.gate.wait(5)
        self.calls.append((task, messages))
        user = messages[-1]["content"]
        if self.fail_on and task is Task.TRANSLATION and self.fail_on in str(user):
            raise RoutingError("modelo caído")
        if task is Task.TRANSLATION:
            text = "EN: " + str(user).split("Texto a traducir:\n")[-1]
        else:
            text = f"resultado {task.value}"
        if on_chunk:
            on_chunk(text)
        return GenerationResult(text, "m (local)", False, {})


def event(seg, text, final=True, t=0.0, conf=0.9):
    return TranscriptEvent(
        seg, SourceKind.SYSTEM, text, final, t, t + 1, confidence=conf
    )


def wait(pred, timeout=5):
    deadline = time.monotonic() + timeout
    while not pred() and time.monotonic() < deadline:
        time.sleep(0.01)
    return pred()


# ---------------------------------------------------------------- traducción
def test_provisional_is_never_translated_and_finals_keep_order():
    engine = Engine()
    engine.gate.clear()
    updates = []
    tr = LiveTranslator(engine, target=lambda: "en", on_update=updates.append)
    tr.start()
    tr.handle_transcript(event(1, "Hola a", final=False))
    assert tr.segments()[0].status is SegmentStatus.PROVISIONAL
    tr.handle_transcript(event(1, "Hola a todos", t=0))
    tr.handle_transcript(event(2, "Empezamos", t=2))
    tr.handle_transcript(event(1, "Hola a tod", final=False))  # tardío: se ignora
    engine.gate.set()
    assert wait(lambda: all(s.status is SegmentStatus.DONE for s in tr.segments()))
    segs = tr.segments()
    assert [s.original for s in segs] == ["Hola a todos", "Empezamos"]
    assert segs[1].translation == "EN: Empezamos"
    assert "Hola a todos" in str(engine.calls[1][1][-1]["content"])  # contexto previo
    assert all(task is Task.TRANSLATION for task, _ in engine.calls)
    assert len(engine.calls) == 2
    tr.stop()


def test_failures_low_confidence_retraction_and_exports():
    engine = Engine(fail_on="rompe")
    tr = LiveTranslator(engine, target=lambda: "en")
    tr.start()
    tr.handle_transcript(event(1, "Bien dicho", conf=0.3))
    tr.handle_transcript(event(2, "esto rompe", t=1))
    tr.handle_transcript(event(3, "ruido", final=False, t=2))
    tr.handle_transcript(TranscriptEvent(3, SourceKind.SYSTEM, "", True, 2, 3))
    assert wait(
        lambda: [s.status for s in tr.segments()]
        == [SegmentStatus.DONE, SegmentStatus.FAILED]
    )
    md = tr.export("md", title="Reunión")
    assert md.startswith("# Reunión") and "confianza baja" in md
    assert "> EN: Bien dicho" in md and "sin traducción: modelo caído" in md
    txt = tr.export("txt")
    assert (
        "[00:00] Interlocutor: Bien dicho" in txt and "→ (inglés) EN: Bien dicho" in txt
    )
    with pytest.raises(ValueError):
        tr.export("pdf")
    assert tr.summarize() == "resultado summary"
    tr.clear()
    with pytest.raises(ValueError):
        tr.summarize()
    tr.stop()


def test_paused_translator_ignores_input():
    tr = LiveTranslator(Engine(), target=lambda: "en")
    tr.paused = True
    tr.handle_transcript(event(1, "Hola"))
    assert tr.segments() == []


# ------------------------------------------------------------------ desafíos
def test_fingerprint_detects_relevant_changes_only():
    rng = np.random.default_rng(1)
    base = rng.integers(0, 255, (300, 400)).astype(np.float32)
    noisy = base + rng.normal(0, 2, base.shape)
    changed = base.copy()
    changed[:, :200] = 255 - changed[:, :200]
    a, b, c = fingerprint(base), fingerprint(noisy), fingerprint(changed)
    assert hamming(a, b) <= 6 < hamming(a, c)
    detector = ChangeDetector()
    assert detector.should_process(a)
    assert not detector.should_process(b)
    assert detector.should_process(c)
    with pytest.raises(ValueError):
        fingerprint(np.zeros((3, 3)))


def test_solver_prefers_vision_then_ocr_then_explains():
    engine = Engine(vision=True)
    solver = ChallengeSolver(engine, ocr=lambda imgs: "2 + 2 = ?")
    notices = []
    result = solver.solve(["QUJD"], "Resuelve", on_notice=notices.append)
    assert result.method == "vision" and engine.calls[-1][0] is Task.VISION
    assert engine.calls[-1][1][-1]["images"] == ["QUJD"]
    assert "Visión" in notices[0]

    engine = Engine(vision=False)
    solver = ChallengeSolver(engine, ocr=lambda imgs: "2 + 2 = ?")
    result = solver.solve(["QUJD"])
    assert result.method == "ocr" and engine.calls[-1][0] is Task.CHALLENGE
    assert "2 + 2 = ?" in engine.calls[-1][1][-1]["content"]

    solver = ChallengeSolver(Engine(vision=False), ocr=lambda imgs: "  ")
    with pytest.raises(RoutingError, match="OCR no reconoció"):
        solver.solve(["QUJD"])
    solver = ChallengeSolver(Engine(vision=False), ocr=None)
    assert solver.plan()[0] == "none"
    with pytest.raises(RoutingError, match="modelo con visión"):
        solver.solve(["QUJD"])
    with pytest.raises(ValueError):
        solver.solve([])


def test_solver_rejects_concurrent_runs():
    engine = Engine()
    engine.gate.clear()
    solver = ChallengeSolver(engine)
    worker = threading.Thread(target=lambda: solver.solve(["A"]))
    worker.start()
    assert wait(lambda: solver.busy)
    with pytest.raises(RuntimeError):
        solver.solve(["B"])
    engine.gate.set()
    worker.join(5)
    assert not solver.busy

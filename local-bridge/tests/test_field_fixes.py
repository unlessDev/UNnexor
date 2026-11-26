"""Regresiones de los problemas reportados en uso real (2026-11-25):
memoria de Ollama, bucles de transcripción, historial que desaparecía,
redimensionado y minimizado de ventanas sin marco."""

from __future__ import annotations

import json

import httpx
import numpy as np
import pytest

from app.audio.types import SAMPLE_RATE, SourceKind, SpeechSegment
from app.copilot.transcription import TranscriptionService
from app.ollama_client import (
    OllamaClient,
    OllamaResponseError,
    is_resource_error,
)
from app.stt.base import Transcript
from app.stt.hallucinations import collapse_repetitions
from tests.conftest import make_config

OOM = (
    "llama-server reported out-of-memory during startup: failed to allocate "
    "Vulkan0 buffer of size 375840768"
)


def ndjson(*objs: dict) -> bytes:
    return "\n".join(json.dumps(o) for o in objs).encode()


class FakeOllama:
    """Ollama simulado: el primer /api/chat falla por memoria."""

    def __init__(self, failures: int = 1, fail_mid_stream: bool = False) -> None:
        self.failures = failures
        self.fail_mid_stream = fail_mid_stream
        self.unloaded: list[str] = []
        self.chats = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/ps":
            return httpx.Response(
                200, json={"models": [{"name": "qwen3:8b"}, {"name": "qwen2.5vl:7b"}]}
            )
        if path == "/api/generate":
            body = json.loads(request.content)
            if body.get("keep_alive") == 0:
                self.unloaded.append(body["model"])
                return httpx.Response(200, json={"done": True})
            if self.failures:
                self.failures -= 1
                return httpx.Response(500, json={"error": OOM})
            return httpx.Response(200, json={"done": True})
        if path == "/api/chat":
            self.chats += 1
            if self.fail_mid_stream:
                return httpx.Response(
                    200,
                    content=ndjson(
                        {"message": {"content": "Hola"}},
                        {
                            "error": "wsarecv: An existing connection was forcibly closed"
                        },
                    ),
                )
            if self.failures:
                self.failures -= 1
                return httpx.Response(500, json={"error": OOM})
            return httpx.Response(
                200, content=ndjson({"message": {"content": "ok"}}, {"done": True})
            )
        return httpx.Response(404)


def client(fake: FakeOllama) -> OllamaClient:
    return OllamaClient(make_config(), transport=httpx.MockTransport(fake))


def test_resource_errors_are_recognized():
    assert is_resource_error(OOM)
    assert is_resource_error(
        "read tcp ... wsarecv: An existing connection was forcibly closed"
    )
    assert is_resource_error("vk::Device::allocateMemory: ErrorOutOfDeviceMemory")
    assert not is_resource_error("model 'x' not found")


def test_oom_unloads_other_models_and_retries_once():
    fake = FakeOllama(failures=1)
    text, _ = client(fake).chat_stream(
        [{"role": "user", "content": "x"}], model="qwen2.5vl:7b"
    )
    assert text == "ok"
    assert fake.unloaded == ["qwen3:8b"]  # no descarga el que se va a usar
    assert fake.chats == 2


def test_persistent_oom_gives_actionable_message():
    fake = FakeOllama(failures=5)
    with pytest.raises(OllamaResponseError) as info:
        client(fake).chat_stream([{"role": "user", "content": "x"}], model="qwen3:8b")
    message = info.value.message
    assert "memoria" in message and "qwen2.5vl:7b" in message and "num_ctx" in message
    assert fake.chats == 2  # un solo reintento


def test_no_retry_after_partial_output():
    fake = FakeOllama(failures=0, fail_mid_stream=True)
    chunks: list[str] = []
    with pytest.raises(OllamaResponseError):
        client(fake).chat_stream(
            [{"role": "user", "content": "x"}], model="m", on_chunk=chunks.append
        )
    assert chunks == ["Hola"] and fake.chats == 1 and fake.unloaded == []


def test_preload_recovers_from_oom():
    fake = FakeOllama(failures=1)
    assert client(fake).preload("qwen2.5-coder:7b") >= 0
    assert set(fake.unloaded) == {"qwen3:8b", "qwen2.5vl:7b"}


# ------------------------------------------------------- bucles de STT
def test_collapse_repetitions():
    assert (
        collapse_repetitions(
            "El informe del informe del informe del informe del trimestre."
        )
        == "El informe del informe del trimestre."
    )
    assert collapse_repetitions("Muy muy bien, gracias.") == "Muy muy bien, gracias."
    assert collapse_repetitions("a b c a b c a b c a b c fin") == "a b c a b c fin"


class CountingRecognizer:
    engine_id = "fake"
    model = "m"
    is_remote = False
    supports_partials = True

    def __init__(self) -> None:
        self.calls: list[str | None] = []

    def transcribe(self, audio, *, language=None, prompt=None):
        self.calls.append(prompt)
        return Transcript("hola", "fake", "m", len(audio) / SAMPLE_RATE, 0.1)

    def close(self) -> None:
        pass


def test_near_silent_segments_are_not_transcribed_and_no_previous_text_prompt():
    recognizer = CountingRecognizer()
    events = []
    service = TranscriptionService(
        lambda: recognizer, events.append, glossary=lambda: "Kubernetes"
    )
    service.start()
    quiet = (np.random.default_rng(0).normal(0, 0.0005, SAMPLE_RATE)).astype(np.float32)
    loud = (0.2 * np.sin(np.arange(SAMPLE_RATE) / 5)).astype(np.float32)
    service.submit(SpeechSegment(1, SourceKind.SYSTEM, quiet, 0, 1, True))
    service.submit(SpeechSegment(2, SourceKind.SYSTEM, loud, 1, 2, True))
    service.submit(SpeechSegment(3, SourceKind.SYSTEM, loud, 2, 3, True))
    service.stop()
    assert [e.retracted for e in events] == [True, False, False]
    assert recognizer.calls == ["Kubernetes", "Kubernetes"]  # nunca el texto previo


# ------------------------------------------------------------ interfaz
def test_fit_for_vision_limits_size(qapp):
    from PySide6.QtGui import QPixmap

    from app.desktop.screenshot import (
        VISION_MAX_PIXELS,
        VISION_MAX_SIDE,
        fit_for_vision,
    )

    big = fit_for_vision(QPixmap(2560, 1440))
    assert max(big.width(), big.height()) <= VISION_MAX_SIDE
    assert big.width() * big.height() <= VISION_MAX_PIXELS
    small = QPixmap(640, 480)
    assert fit_for_vision(small).size() == small.size()


def test_overlay_keeps_history(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    from app.copilot.assistant import QAItem, QAStatus
    from app.desktop.copilot import overlay as overlay_module
    from app.desktop.widgets import AssistantMessage

    ini = str(tmp_path / "o.ini")
    monkeypatch.setattr(
        overlay_module,
        "QSettings",
        lambda *_a: QSettings(ini, QSettings.Format.IniFormat),
    )
    overlay = overlay_module.CopilotOverlay()
    for number in (1, 2):
        item = QAItem(number, f"pregunta {number}", "auto", 0.0)
        overlay.begin_answer(item)
        item.answer = f"respuesta {number}"
        item.status = QAStatus.DONE
        overlay.append_answer(number, item.answer)
        overlay.finish_answer(item)
    overlay.begin_text("🧩 desafío")
    overlay.append_text("x = 3")
    overlay.finish_text("x = 3")
    texts = [m.text() for m in overlay._container.findChildren(AssistantMessage)]
    assert texts == ["respuesta 1", "respuesta 2", "x = 3"]  # nada desaparece
    overlay.clear_history()
    qapp.processEvents()
    overlay.close()


def test_frameless_edges_and_cursors(qapp):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtWidgets import QWidget

    from app.desktop.frameless import cursor_for, edges_at

    widget = QWidget()
    widget.setGeometry(100, 100, 400, 300)
    frame = widget.frameGeometry()
    assert edges_at(widget, frame.center()) == Qt.Edge(0)
    assert (
        edges_at(widget, QPoint(frame.left() + 2, frame.center().y()))
        == Qt.Edge.LeftEdge
    )
    corner = edges_at(widget, QPoint(frame.right() - 1, frame.bottom() - 1))
    assert cursor_for(corner) == Qt.CursorShape.SizeFDiagCursor
    assert edges_at(widget, QPoint(10, 10)) == Qt.Edge(0)


def test_frameless_press_on_child_at_edge_starts_system_resize(qapp, monkeypatch):
    """La franja del borde la cubren widgets hijos: la pulsación debe
    iniciar el redimensionado igualmente."""
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

    from app.desktop.frameless import FramelessResizer

    window = QWidget(None, Qt.WindowType.FramelessWindowHint)
    layout = QVBoxLayout(window)
    layout.setContentsMargins(0, 0, 0, 0)
    child = QLabel("contenido")
    layout.addWidget(child)
    window.setGeometry(200, 200, 300, 200)
    window.show()
    qapp.processEvents()
    requested = []

    class Handle:
        def startSystemResize(self, edges):
            requested.append(edges)
            return True

    monkeypatch.setattr(window, "windowHandle", lambda: Handle())
    resizer = FramelessResizer()
    resizer.manage(window)
    frame = window.frameGeometry()
    global_pos = QPointF(frame.right() - 1, frame.center().y())
    press = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(child.width() - 1, 10),
        global_pos,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    try:
        assert resizer.eventFilter(child, press) is True
        assert requested == [Qt.Edge.RightEdge]
    finally:
        resizer.shutdown()  # no dejar filtros/temporizadores a otras pruebas
        window.close()

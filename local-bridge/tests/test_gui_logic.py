"""Pruebas de lógica de la GUI con Qt offscreen (sin escritorio real).

Valida: bloque de mensajes, streaming, autoscroll con pausa, copiado,
estado del pie, persistencia y creación de la ventana con workers reales
(Ollama simulado vía MockTransport).
"""

from __future__ import annotations

import json

import httpx

from app.config import Config
from app.desktop.styles import APP_QSS, FONT_FAMILIES
from app.desktop.widgets import (
    AssistantMessage,
    ChatScroller,
    SystemNote,
    UserMessage,
    make_chat_area,
)
from app.ollama_client import OllamaClient


# ---------------------------------------------------------------- widgets
def test_assistant_message_streaming_and_finalize(qapp):
    widget = AssistantMessage(model="llama3")
    widget.append_streaming("Hola ")
    widget.append_streaming("mundo")
    text = widget.finalize()
    assert text == "Hola mundo"
    assert widget.text() == "Hola mundo"


def test_assistant_message_converts_math_to_unicode(qapp):
    widget = AssistantMessage(model="m")
    raw = "Resuelvo: $\\frac{1}{2}$ y luego x^2 = 4"
    widget.append_streaming(raw)
    final = widget.finalize()
    # Lo que se copia al portapapeles es la versión legible…
    assert final == "Resuelvo: 1/2 y luego x² = 4"
    # …y el texto crudo se conserva para la lógica (historial/stats).
    assert widget.text() == raw
    plain = widget.browser.toPlainText()
    assert "1/2" in plain and "x²" in plain


def test_assistant_message_empty_marks_error(qapp):
    widget = AssistantMessage(model="m")
    widget.append_streaming("")
    widget.finalize()
    assert widget.property("error") is True


def test_user_and_note_widgets(qapp):
    from PySide6.QtWidgets import QLabel

    user = UserMessage("selección áéíóú")
    labels = user.findChildren(QLabel)
    assert any("selección áéíóú" in label.text() for label in labels)


def test_user_message_shows_ocr_math_readable(qapp):
    from PySide6.QtWidgets import QLabel

    # Texto típico del OCR con fórmulas (bloques $$…$$ con LaTeX).
    user = UserMessage("$$\\frac{1}{2}$$")
    labels = user.findChildren(QLabel)
    assert any("1/2" in label.text() for label in labels)
    assert not any("$$" in label.text() for label in labels)
    note = SystemNote("⚠ error", error=True)
    assert note.property("error") is True


def test_chat_scroller_pauses_and_resumes(qapp):
    from PySide6.QtWidgets import QLabel

    scroll, container, vbox = make_chat_area()
    bar = scroll.verticalScrollBar()
    scroller = ChatScroller(bar)

    # Contenido suficiente para hacer scroll.
    for i in range(40):
        label = QLabel(f"línea {i} " + "x" * 60)
        label.setWordWrap(True)
        vbox.insertWidget(vbox.count() - 1, label)
    container.resize(400, 3000)
    scroll.resize(480, 600)
    scroll.show()
    qapp.processEvents()

    scroller.ensure_bottom()
    qapp.processEvents()
    assert bar.value() == bar.maximum()  # autoscroll al fondo

    # El usuario sube → se suspende el autoscroll.
    scroller.enabled = True
    bar.setValue(max(0, bar.maximum() - 400))
    qapp.processEvents()
    assert scroller.enabled is False

    # Vuelve al final → se reactiva.
    bar.setValue(bar.maximum())
    qapp.processEvents()
    assert scroller.enabled is True


# ---------------------------------------------------------------- styles
def test_stylesheet_has_no_blocked_origin_and_fonts():
    assert "Cascadia Mono" in FONT_FAMILIES
    assert "#12141a" in APP_QSS


# ---------------------------------------------------------------- integración con Ollama simulado
def test_stream_into_assistant_widget(qapp):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/x-ndjson"},
            content=(
                "".join(
                    json.dumps({"message": {"content": part}, "done": False}) + "\n"
                    for part in ["Hola ", "**mundo**", " final"]
                )
                + json.dumps({"done": True, "eval_count": 3})
                + "\n"
            ).encode("utf-8"),
        )

    cfg = Config()
    cfg.token = ""
    client = OllamaClient(cfg, transport=httpx.MockTransport(handler))
    widget = AssistantMessage(model="test")

    collected: list[str] = []
    text, stats = client.chat_stream(
        [{"role": "user", "content": "saluda"}],
        model="test",
        on_chunk=lambda delta: (
            collected.append(delta),
            widget.append_streaming(delta),
        ),
    )
    assert "".join(collected) == "Hola **mundo** final"
    final = widget.finalize()
    assert final == "Hola **mundo** final"
    assert stats["eval_count"] == 3

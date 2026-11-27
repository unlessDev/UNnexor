"""Control del tiempo de escucha del copiloto y «Siempre visible» real."""

from __future__ import annotations

import pytest

from app.audio.types import SourceKind
from app.copilot.listening import ListeningWindow, WindowState, format_seconds
from app.copilot.prompts import answer_messages
from app.copilot.transcription import TranscriptEvent
from app.desktop.copilot.hotkeys import find_conflicts
from tests.test_copilot_ui import window  # noqa: F401 - fixture reutilizada


# ------------------------------------------------------- lógica pura
class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_timed_window_counts_down_and_collects_text():
    clock = Clock()
    win = ListeningWindow(clock)
    win.begin(20)
    assert win.state is WindowState.LISTENING and win.remaining() == 20
    assert win.add("Interlocutor", "Bueno, te cuento el contexto del proyecto")
    clock.now += 12
    assert win.remaining() == pytest.approx(8)
    win.add("Interlocutor", "¿cómo lo resolverías?")
    clock.now += 9
    assert win.expired()
    win.end()
    assert win.state is WindowState.PROCESSING
    win.add("Interlocutor", "Y en cuánto tiempo.")  # llega tarde: se acepta
    text = win.finish()
    assert text.endswith("¿cómo lo resolverías? Y en cuánto tiempo.")
    assert win.state is WindowState.IDLE and not win.add("X", "fuera de ventana")


def test_manual_window_has_no_limit_and_validates_duration():
    win = ListeningWindow(Clock())
    win.begin(None)
    assert win.remaining() is None and not win.expired()
    with pytest.raises(RuntimeError):
        win.begin(None)
    win.cancel()
    with pytest.raises(ValueError):
        win.begin(2)
    assert format_seconds(75) == "1:15" and format_seconds(9) == "9 s"


def test_window_prompt_asks_to_find_the_question():
    messages = answer_messages(
        "larga explicación… ¿y tú qué harías?", None, mode="window"
    )
    assert "Identifica la pregunta" in messages[-1]["content"]


# ------------------------------------------------------- atajos
def test_hotkey_conflicts_are_detected():
    problems = find_conflicts(
        {
            "escuchar": "Ctrl+Alt+Shift+L",
            "cancelar escucha": "ctrl+alt+shift+l",
            "desafío": "Win+L",
            "preguntar": "Ctrl+Alt+Shift+A",
            "vacío": "",
        }
    )
    assert len(problems) == 2
    assert any("misma combinación" in p for p in problems)
    assert any("bloquear el equipo" in p for p in problems)
    assert find_conflicts({"a": "Ctrl+Alt+Shift+L", "b": "Ctrl+Alt+Shift+X"}) == []


# ------------------------------------------------------- controlador
class FakePipeline:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def pause(self) -> None:
        self.calls.append("pause")

    def resume(self) -> None:
        self.calls.append("resume")

    def stop(self) -> None:
        self.calls.append("stop")


class FakeSTT:
    def __init__(self) -> None:
        self.pending = True

    def drained(self) -> bool:
        return not self.pending

    def stop(self, timeout: float = 0) -> None:
        pass


def _event(text: str, final: bool = True) -> TranscriptEvent:
    return TranscriptEvent(1, SourceKind.SYSTEM, text, final, 0, 1)


def _prepare(window, mode: str):  # noqa: F811
    c = window.controller
    c.settings.listen_mode = mode
    c.settings.listen_seconds = 10
    c.mode = "conversation"
    c._pipeline = FakePipeline()
    c._stt = FakeSTT()
    asked: list[str] = []
    c.assistant.ask_window = lambda text: asked.append(text) or 1
    auto: list[TranscriptEvent] = []
    c.assistant.handle_transcript = auto.append
    states: list[str] = []
    c.listeningChanged.connect(lambda state, _text: states.append(state))
    return c, asked, auto, states


def test_timed_listening_flow(window):  # noqa: F811
    c, asked, auto, states = _prepare(window, "timed")
    c.begin_listening()
    assert c._pipeline.calls == ["resume"] and states[-1] == "listening"
    c._on_transcript(_event("Te explico el problema con detalle."))
    c._on_transcript(_event("parcial", final=False))  # los provisionales no cuentan
    c._on_transcript(_event("¿Qué propondrías?"))
    assert auto == []  # sin detección turno a turno en modo ventana
    c.window.started_at -= 11  # se agotó la duración
    c._window_tick()
    assert c.window.state is WindowState.PROCESSING
    assert c._pipeline.calls[-1] == "pause"  # captura cerrada: no acumula audio
    c._window_tick()  # STT aún transcribiendo: espera
    assert asked == []
    c._stt.pending = False
    c._window_tick()
    assert asked == ["Te explico el problema con detalle. ¿Qué propondrías?"]
    assert states[-1] == "idle"


def test_manual_listening_toggle_and_cancel(window):  # noqa: F811
    c, asked, _auto, states = _prepare(window, "manual")
    c.toggle_listening()  # atajo: empieza
    assert c.window.state is WindowState.LISTENING and c.window.remaining() is None
    c._on_transcript(_event("Algo que no quiero enviar"))
    c.cancel_listening()
    assert c.window.state is WindowState.IDLE and asked == []
    assert c._pipeline.calls[-1] == "pause"
    c.toggle_listening()
    c._on_transcript(_event("¿Puedes resumirlo?"))
    c.toggle_listening()  # mismo atajo: termina
    c._stt.pending = False
    c._window_tick()
    assert asked == ["¿Puedes resumirlo?"]


def test_empty_window_sends_nothing(window):  # noqa: F811
    c, asked, _auto, states = _prepare(window, "timed")
    c.begin_listening()
    c.end_listening()
    c._stt.pending = False
    c._window_tick()
    assert asked == [] and states[-1] == "idle"


def test_continuous_mode_keeps_classic_behaviour(window):  # noqa: F811
    c, asked, auto, _states = _prepare(window, "continuous")
    c._on_transcript(_event("¿Qué es CQRS?"))
    assert len(auto) == 1 and asked == []
    with pytest.raises(ValueError):
        c.begin_listening()


def test_conversation_page_listen_controls(window):  # noqa: F811
    page = window.conversation
    page.listen_mode.setCurrentIndex(page.listen_mode.findData("timed"))
    assert window.controller.settings.listen_mode == "timed"
    assert page.listen_seconds.isEnabled() and not page.auto.isEnabled()
    page._apply_preset(page.listen_presets.findData(60))
    assert window.controller.settings.listen_seconds == 60
    page.listen_mode.setCurrentIndex(page.listen_mode.findData("continuous"))
    assert page.listen_panel.isHidden() and page.auto.isEnabled()


# ------------------------------------------------------- siempre visible
def test_topmost_is_real_and_reflected(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QWidget

    from app.desktop import topmost

    ini = str(tmp_path / "pins.ini")
    monkeypatch.setattr(
        topmost, "QSettings", lambda *_a: QSettings(ini, QSettings.Format.IniFormat)
    )
    widget = QWidget()
    widget.show()
    qapp.processEvents()
    handle_before = int(widget.winId())
    assert topmost.set_topmost(widget, True) is True
    assert topmost.is_topmost(widget)
    assert topmost.set_topmost(widget, False) is False
    assert not topmost.is_topmost(widget)
    if topmost.native_available():
        assert int(widget.winId()) == handle_before  # sin recrear la ventana
    pref = topmost.PinPreference("prueba", False)
    assert pref.load() is False
    pref.save(True)
    assert topmost.PinPreference("prueba", False).load() is True
    widget.close()


def test_overlay_pin_button_follows_real_state(window, qapp):  # noqa: F811
    from app.desktop import topmost

    overlay = window.overlay
    overlay.show()
    qapp.processEvents()
    saved: list[bool] = []
    overlay.pinChanged.connect(saved.append)
    overlay._pin.setChecked(False)
    assert saved == [False] and not topmost.is_topmost(overlay)
    overlay._pin.setChecked(True)
    assert saved == [False, True] and topmost.is_topmost(overlay)
    assert window.controller.settings.overlay_on_top is True
    overlay.close()

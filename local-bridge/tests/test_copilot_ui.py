"""Interfaz del copiloto (offscreen): construcción, navegación y flujo de
señales. Sin red (Ollama apunta a un puerto cerrado) ni captura real."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="PySide6 no instalada")

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QMessageBox

from app.audio.types import SourceKind
from app.copilot.assistant import QAItem, QAStatus
from app.copilot.challenges import ChallengeResult
from app.copilot.settings import SettingsRepository
from app.copilot.transcription import TranscriptEvent
from app.copilot.translation import SegmentStatus, TranslationSegment
from app.knowledge.profiles import ProfileStore
from app.knowledge.service import KnowledgeBase
from app.providers.consent import ConsentStore
from app.providers.usage import UsageLedger
from tests.conftest import make_config


@pytest.fixture()
def window(qapp, tmp_path, monkeypatch):
    from app.desktop.copilot import overlay as overlay_module
    from app.desktop.copilot.controller import CopilotController
    from app.desktop.copilot.window import CopilotWindow

    monkeypatch.setenv("EXTDATOR_MODELS_DIR", str(tmp_path / "models"))
    ini = str(tmp_path / "overlay.ini")
    monkeypatch.setattr(
        overlay_module,
        "QSettings",
        lambda *_a: QSettings(ini, QSettings.Format.IniFormat),
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    cfg = make_config(ollama_host="http://127.0.0.1:9")  # puerto cerrado: sin red
    controller = CopilotController(
        cfg,
        settings_repo=SettingsRepository(tmp_path / "settings.json"),
        profiles=ProfileStore(tmp_path / "profiles"),
        knowledge=KnowledgeBase(tmp_path / "kb.sqlite3"),
        consents=ConsentStore(tmp_path / "consents.json"),
        ledger=UsageLedger(tmp_path / "usage.json"),
    )
    win = CopilotWindow(cfg, controller, register_hotkeys=False)
    yield win
    win.shutdown()


def test_all_pages_build_and_navigate(window):
    from app.desktop.copilot.controller import wait_until

    for row in range(window.nav.count()):
        window.nav.setCurrentRow(row)
        assert window.stack.currentIndex() == row
    assert wait_until(
        lambda: "Consultando" not in window.engines.routes.toPlainText(), 10
    )
    assert window.engines.table.rowCount() >= 13  # proveedores remotos del catálogo
    window.open_page("translation")
    assert window.stack.currentWidget() is window.translation


def test_start_without_sources_or_engine_is_rejected(window):
    s = window.controller.settings
    s.capture_system = False
    s.capture_microphone = False
    with pytest.raises(ValueError, match="fuente"):
        window.controller.start_session("conversation")
    s.capture_system = True
    with pytest.raises(ValueError, match="motor de voz no está listo"):
        window.controller.start_session("conversation")  # modelo no descargado
    assert window.controller.mode == "idle"


def test_conversation_flow_updates_page_and_overlay(window):
    c = window.controller
    c.mode = "conversation"
    event = TranscriptEvent(
        1, SourceKind.SYSTEM, "¿Qué es CQRS?", True, 0, 1, confidence=0.4
    )
    c.transcript.emit(event)
    assert "¿Qué es CQRS?" in window.conversation.transcript.toPlainText()
    assert "⚠️" in window.conversation.transcript.toPlainText()  # confianza baja
    item = QAItem(1, "¿Qué es CQRS?", "auto", 0.0)
    c.question.emit(item)
    item.status = QAStatus.GENERATING
    c.answerStarted.emit(item)
    c.answerChunk.emit(1, "Separa lecturas ")
    item.answer = "Separa lecturas y escrituras."
    item.status = QAStatus.DONE
    item.model = "qwen3:8b (local)"
    c.answerDone.emit(item)
    assert window.conversation.questions.count() == 1
    assert window.conversation.questions.item(0).text().startswith("✅")
    assert window.conversation.alt.isEnabled()
    assert window.overlay._alt.isEnabled()
    assert "Sugerencia lista" in window.overlay._status.text()
    c.mode = "idle"


def test_translation_view_distinguishes_provisional(window):
    c = window.controller
    c.translator.handle_transcript(
        TranscriptEvent(1, SourceKind.SYSTEM, "Hola a", False, 0, 1)
    )
    window.translation.refresh_view()
    assert "Hola a …" in window.translation.view.toPlainText()
    segment = TranslationSegment(
        2, SourceKind.SYSTEM, 2.0, "Adiós", SegmentStatus.DONE, "Bye"
    )
    c.mode = "translation"
    c.translationUpdated.emit(segment)
    from app.desktop.widgets import AssistantMessage

    shown = window.overlay._container.findChildren(AssistantMessage)
    assert any("Bye" in message.text() for message in shown)
    c.mode = "idle"


def test_challenge_signals_reach_page_and_overlay(window):
    c = window.controller
    c.challengeStarted.emit("Resuelve")
    c.challengeChunk.emit("## Identificación del problema\n")
    c.challengeDone.emit(
        ChallengeResult(
            "## Identificación del problema\nx = 3", "vision", "qwen2.5vl:7b (local)"
        )
    )
    assert "x = 3" in window.challenges.current_text()
    assert "visión" in window.challenges.plan.text()
    assert window.overlay.isVisible()


def test_profile_save_activate_and_consent_toggle(window):
    page = window.profiles
    page.new_profile()
    page.name.setText("Entrevista")
    page.role.setText("Backend")
    page.activate_profile()
    profiles = window.controller.profiles.list()
    assert [p.name for p in profiles] == ["Entrevista"]
    assert window.controller.settings.active_profile == profiles[0].id
    combo = window.conversation.profile
    assert combo.findData(profiles[0].id) >= 0
    # Permiso de envío de datos para Groq (diálogo confirmado por la prueba).
    table = window.engines.table
    window.engines._fill_consents()
    row = next(
        r
        for r in range(table.rowCount())
        if table.item(r, 0).data(Qt.ItemDataRole.UserRole) == "groq"
    )
    table.item(row, 3).setCheckState(Qt.CheckState.Checked)
    assert window.controller.consents.has("groq", "remote_data")
    assert table.item(row, 4).text() == "no aplica"  # gratuito con límites
    window.privacy.revoke_all()
    assert not window.controller.consents.has("groq", "remote_data")


def test_hotkey_parsing():
    from app.desktop.copilot.hotkeys import (
        MOD_ALT,
        MOD_CONTROL,
        MOD_SHIFT,
        HotkeyError,
        parse_hotkey,
    )

    assert parse_hotkey("Ctrl+Alt+Shift+C") == (
        MOD_CONTROL | MOD_ALT | MOD_SHIFT,
        ord("C"),
    )
    assert parse_hotkey("ctrl+f5")[1] == 0x74
    for bad in ("C", "Shift+C", "Ctrl+Hyper+C", "Ctrl+Ñ+"):
        with pytest.raises(HotkeyError):
            parse_hotkey(bad)

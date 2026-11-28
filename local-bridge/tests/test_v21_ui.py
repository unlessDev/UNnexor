"""Pruebas de interfaz de 2.1.0: cierre real del panel del copiloto,
respuestas en primera persona, ventanas de modelos y recomendación con
descargas consentidas, y dimensionado según la pantalla."""

from __future__ import annotations

from typing import Any

import pytest
from PySide6.QtCore import QObject, QSize, Signal
from PySide6.QtWidgets import QMessageBox, QWidget

from app.compat.advisor import Recommendation
from app.copilot.prompts import answer_messages, clean_spoken_answer
from app.ollama_pull import COMPLETED, FAILED, PullTracker
from tests.test_copilot_ui import window  # noqa: F401 (fixture reutilizada)


# ------------------------------------------------- panel flotante / copiloto
def test_overlay_has_close_button_and_closing_stops_session(window):  # noqa: F811
    from PySide6.QtWidgets import QPushButton

    overlay = window.overlay
    closers = [b for b in overlay.findChildren(QPushButton) if b.text() == "✕"]
    assert closers, "el panel flotante debe poder cerrarse, no solo minimizarse"
    stopped: list[bool] = []
    window.controller.mode = "conversation"
    window.controller.stop_session = lambda: stopped.append(True)  # type: ignore
    overlay.show()
    closers[0].click()
    assert stopped == [True]
    assert not overlay.isVisible()
    # La aplicación sigue: la ventana del copiloto es utilizable y el panel
    # puede volver a mostrarse con la siguiente sesión.
    window.controller.mode = "idle"
    overlay.show()
    assert overlay.isVisible()


def test_overlay_shutdown_does_not_reemit_close(window):  # noqa: F811
    emitted: list[bool] = []
    window.overlay.closeRequested.connect(lambda: emitted.append(True))
    window.overlay.show()
    window.overlay.prepare_shutdown()
    window.overlay.close()
    assert emitted == []


def test_spoken_answer_is_distinct_and_sanitized(window):  # noqa: F811
    import time

    from app.copilot.assistant import QAItem
    from app.desktop.widgets import AssistantMessage

    overlay = window.overlay
    item = QAItem(id=7, question="¿Qué es CQRS?", origin="auto", created_at=time.time())
    overlay.begin_answer(item)
    overlay.append_answer(7, "Podrías responder que CQRS separa lecturas y escrituras.")
    item.answer = clean_spoken_answer(
        "Podrías responder que CQRS separa lecturas y escrituras."
    )
    item.model = "m"
    overlay.finish_answer(item)
    answers = overlay._container.findChildren(AssistantMessage)
    assert answers[-1].property("spoken") is True
    assert answers[-1].text() == "CQRS separa lecturas y escrituras."


def test_first_person_prompt_forbids_advisor_phrasing():
    messages = answer_messages(
        "¿Cuál es la diferencia entre función y procedimiento?", None
    )
    system, user = messages[0]["content"], messages[1]["content"]
    assert "primera persona" in system
    assert "Podrías responder" in system and "PROHIBIDO" in system
    assert "no te autoriza a inventar" in system.lower() or "NO te autoriza" in system
    assert "Sugiere la respuesta" not in user
    assert "Contéstale" in user


@pytest.mark.parametrize(
    "raw, expected",
    [
        (
            "Podrías responder que una función devuelve un valor.",
            "Una función devuelve un valor.",
        ),
        (
            "Una posible respuesta sería: «Claro, te lo explico.»",
            "Claro, te lo explico.",
        ),
        (
            "Te recomiendo que digas algo como: me interesa el puesto.",
            "Me interesa el puesto.",
        ),
        ("Respuesta sugerida.", "Respuesta sugerida."),
        ("Una función devuelve un valor.", "Una función devuelve un valor."),
    ],
)
def test_clean_spoken_answer(raw, expected):
    assert clean_spoken_answer(raw) == expected


def test_transcript_line_html_distinguishes_partial():
    from app.desktop.copilot.common import transcript_line_html

    final = transcript_line_html("Interlocutor", "Hola <b>")
    partial = transcript_line_html("Interlocutor", "y luego", partial=True)
    assert "INTERLOCUTOR" in final and "&lt;b&gt;" in final
    assert "transcribiendo" in partial and "italic" in partial
    assert "transcribiendo" not in final


# ------------------------------------------------------- tamaño de ventanas
def test_fit_to_screen_never_exceeds_available_area(qapp):
    from PySide6.QtGui import QGuiApplication

    from app.desktop.ui_kit import fit_to_screen

    widget = QWidget()
    area = QGuiApplication.primaryScreen().availableGeometry()
    size = fit_to_screen(widget, QSize(10_000, 10_000), QSize(400, 300))
    assert size.width() <= area.width() and size.height() <= area.height()
    assert widget.minimumWidth() <= size.width()
    small = fit_to_screen(QWidget(), QSize(500, 400))
    assert (small.width(), small.height()) == (500, 400)


# ------------------------------------------------------------ modelos de IA
class FakeDownloads(QObject):
    jobProgress = Signal(object)
    jobFinished = Signal(object)
    jobQueued = Signal(object)
    idle = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.jobs: list[Any] = []

    def enqueue(self, job) -> bool:
        if any(j.key == job.key for j in self.jobs):
            return False
        self.jobs.append(job)
        return True

    def active_job(self):
        return self.jobs[0] if self.jobs else None

    def pending(self):
        return self.jobs[1:]

    def is_busy(self, kind, name):
        return any(j.key == f"{kind}:{name}" for j in self.jobs)

    def last_snapshot(self, kind, name):
        return None

    def cancel(self, kind, name):
        pass

    def finish(self, key: str, phase: str, error: str = "") -> None:
        self.jobs = [j for j in self.jobs if j.key != key]
        self.jobFinished.emit(PullTracker(key).finish(phase, error))


def _assessment(tier: str) -> dict:
    return {
        "tier": tier,
        "color": "#3ddc97",
        "emoji": "🟢",
        "reasons": ["Cabe completo en GPU."],
        "explanation": "ok",
        "metrics": {"disk_needed_gb": 6},
    }


def test_model_row_keeps_action_visible_when_narrow(qapp):
    from app.desktop.dialogs import ACTION_COLUMN_WIDTH, ModelRow

    row = ModelRow(
        name="qwen3:8b",
        meta="5 GB",
        explanation="Descripción muy larga " * 40,
        tier="perfecto",
        action="Descargar",
        enabled=True,
    )
    row.resize(520, 200)
    row.show()
    qapp.processEvents()
    assert row.button.isVisible()
    assert row.button.geometry().right() <= row.width()
    assert row.button.parentWidget().width() == ACTION_COLUMN_WIDTH
    tracker = PullTracker("ollama:qwen3:8b")
    row.apply_snapshot(tracker.update("pulling abc", 100, 40, "abc"))
    assert row.progress_view.isVisible() and row.progress.value() == 400
    row.apply_snapshot(tracker.finish(COMPLETED))
    assert row.button.text() == "Seleccionar" and row.installed


def test_models_panel_download_requires_consent(qapp, monkeypatch):
    from app.desktop.dialogs import ModelsPanel

    downloads = FakeDownloads()
    panel = ModelsPanel(None, "qwen3:8b", downloads=downloads)  # type: ignore[arg-type]
    panel._populate(
        {
            "installed": [
                {
                    "name": "qwen3:8b",
                    "assessment": _assessment("perfecto"),
                    "capabilities": ["completion"],
                },
                {
                    "name": "nomic-embed-text:latest",
                    "assessment": _assessment("perfecto"),
                    "capabilities": ["embedding"],
                },
            ],
            "catalog": [
                {
                    "name": "gemma3:4b",
                    "assessment": _assessment("perfecto"),
                    "size_gb": 3.3,
                    "params_b": 4,
                    "family": "gemma",
                    "description": "Ligero con visión",
                }
            ],
            "hardware": {"cpu_name": "CPU  de   prueba", "ram_total_mb": 16384},
        }
    )
    assert "CPU de prueba" in panel._status.text()
    # Un modelo de embeddings no se ofrece como modelo de chat.
    assert not panel._rows["nomic-embed-text:latest"].button.isEnabled()
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No),
    )
    panel._pull("gemma3:4b")
    assert downloads.jobs == []  # sin consentimiento no empieza nada
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )
    panel._pull("gemma3:4b")
    assert [j.name for j in downloads.jobs] == ["gemma3:4b"]
    downloads.finish("ollama:gemma3:4b", COMPLETED)
    assert panel._rows["gemma3:4b"].button.text() == "Seleccionar"
    panel.shutdown()


# ------------------------------------------------- recomendación con descargas
def _rec(feature, choice, *, kind="", name="", size=0, engine="Ollama"):
    return Recommendation(
        feature=feature,
        title=feature,
        engine=engine,
        choice=choice,
        tier="perfecto",
        params={"temperature": 0.4, "num_ctx": 8192},
        download="ollama pull x" if kind else "",
        download_kind=kind,
        download_name=name,
        download_bytes=size,
    )


def test_recommendation_downloads_after_consent_and_applies_on_success(
    qapp, tmp_path, monkeypatch
):
    from app.copilot.settings import CopilotSettings
    from app.desktop import recommend_dialog
    from tests.conftest import make_config

    monkeypatch.setattr(recommend_dialog.paths, "data_dir", lambda: tmp_path)
    answers = [QMessageBox.StandardButton.No]
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: answers[0])
    )
    cfg = make_config()
    cfg.ollama_model = "viejo:7b"
    applied: list[dict] = []
    settings = CopilotSettings()
    downloads = FakeDownloads()
    dialog = recommend_dialog.RecommendationDialog(
        cfg,
        ollama=None,  # type: ignore[arg-type]
        apply_main=applied.append,
        copilot_settings=lambda: settings,
        save_copilot=lambda _s: None,
        downloads=downloads,  # type: ignore[arg-type]
    )
    size = 4_700_000_000
    recs = [
        _rec("chat", "qwen2.5:7b", kind="ollama", name="qwen2.5:7b", size=size),
        _rec("conversation", "qwen2.5:7b", kind="ollama", name="qwen2.5:7b", size=size),
        _rec("translation", "phi4-mini:3.8b"),
    ]
    dialog._on_done(
        {
            "profile": "medium",
            "profile_label": "Consumo medio",
            "recommendations": recs,
            "acceleration": [],
            "never_recommended": ["EXIGENTE", "NO COMPATIBLE"],
            "hardware": {"disk_free_gb": 50},
            "installed_names": ["phi4-mini:3.8b"],
            "profile_tiers": {
                "low": "perfecto",
                "medium": "regular",
                "high": "imposible",
            },
        }
    )
    medium_chip = dialog._profile_cards["medium"].chip
    assert medium_chip is not None and "aceptable" in medium_chip.text()
    plan = dialog._plan()
    assert len(plan.unique_jobs()) == 1 and plan.download_bytes == size

    dialog.apply()  # el usuario dice «No»
    assert downloads.jobs == [] and applied == []
    assert "No se aplicó nada" in dialog._status.text()

    answers[0] = QMessageBox.StandardButton.Yes
    dialog.apply()
    # Lo disponible se aplica ya; lo que requiere descarga, después.
    assert settings.pinned.get("translation") == "ollama:phi4-mini:3.8b"
    assert applied == []
    assert [j.name for j in downloads.jobs] == ["qwen2.5:7b"]  # una sola vez

    downloads.finish("ollama:qwen2.5:7b", FAILED, "sin conexión")
    assert applied == [] and not dialog._retry.isHidden()
    assert "no terminaron" in dialog._status.text()

    dialog.retry_failed()
    assert [j.name for j in downloads.jobs] == ["qwen2.5:7b"]
    downloads.finish("ollama:qwen2.5:7b", COMPLETED)
    assert applied[-1]["ollama_model"] == "qwen2.5:7b"
    assert settings.pinned["answer"] == "ollama:qwen2.5:7b"
    assert "Todo instalado" in dialog._status.text()
    assert (tmp_path / "config_backup.json").is_file()
    dialog.done(0)


def test_profile_tier_uses_worst_ollama_tier():
    from app.desktop.recommend_dialog import profile_tier

    recs = [_rec("chat", "a"), _rec("vision", "b")]
    recs[1].tier = "alto"
    recs.append(_rec("stt", "small", engine="faster-whisper"))
    recs[2].tier = "imposible"  # no es de Ollama: no cuenta
    assert profile_tier(recs) == "alto"

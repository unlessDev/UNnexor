"""Comportamiento del panel principal y del diálogo de ajustes (offscreen).

Cubren código tocado por la limpieza de tipado: bordes de redimensionado
como ``Qt.Edge`` y el guardado de ``num_ctx``/``num_predict``.
"""

from __future__ import annotations

import httpx
import pytest

pytest.importorskip("PySide6", reason="PySide6 no instalada")

from PySide6.QtCore import QEvent, QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QMouseEvent

from app.ollama_client import OllamaClient
from app.queue_manager import Bus, RuntimeState
from tests.conftest import make_config


def _mouse(kind: QEvent.Type, local: QPoint, origin: QPoint) -> QMouseEvent:
    global_pos = QPointF(origin.x() + local.x(), origin.y() + local.y())
    return QMouseEvent(
        kind,
        QPointF(local),
        global_pos,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


@pytest.fixture()
def window(qapp, tmp_path, monkeypatch):
    from app.desktop import window as window_module

    ini = str(tmp_path / "bridge.ini")
    # QSettings(org, app) usa siempre el registro: se aísla en un INI temporal.
    monkeypatch.setattr(
        window_module,
        "QSettings",
        lambda *_args: QSettings(ini, QSettings.Format.IniFormat),
    )
    cfg = make_config(always_on_top=False, hover_scroll_hook=False)
    ollama = OllamaClient(
        cfg, transport=httpx.MockTransport(lambda _r: httpx.Response(503))
    )
    instance = window_module.BridgeWindow(
        cfg, Bus(), RuntimeState(model="m"), ollama, show_onboarding=False
    )
    instance.setGeometry(100, 100, 500, 500)
    yield instance
    instance.stop_workers()
    instance.close()
    ollama.close()


def test_edges_are_detected_as_qt_edge_flags(window) -> None:
    assert window._edge_at(QPoint(250, 250)) == Qt.Edge(0)
    assert window._edge_at(QPoint(2, 250)) == Qt.Edge.LeftEdge
    corner = window._edge_at(QPoint(498, 498))
    assert corner & Qt.Edge.RightEdge
    assert corner & Qt.Edge.BottomEdge


def test_dragging_bottom_right_corner_resizes(window) -> None:
    origin = window.geometry().topLeft()
    start = QPoint(498, 498)
    window.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, start, origin))
    assert window._resize_origin is not None
    window.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, QPoint(538, 518), origin))
    window.mouseReleaseEvent(
        _mouse(QEvent.Type.MouseButtonRelease, QPoint(538, 518), origin)
    )
    assert window._resize_origin is None
    assert window.width() == 540
    assert window.height() == 520


def test_settings_dialog_saves_context_and_prediction(qapp) -> None:
    from app.desktop.dialogs import SettingsDialog

    cfg = make_config()
    cfg.ollama_extra_options = {"top_k": 40}
    dialog = SettingsDialog(cfg)
    emitted: list[dict] = []
    dialog.settingsChanged.connect(emitted.append)
    dialog._widgets["_num_ctx"].setValue(4096)
    dialog._widgets["_num_predict"].setValue(1024)
    dialog._save()
    assert emitted, "El diálogo no emitió cambios"
    extra = emitted[0]["ollama_extra_options"]
    assert extra == {"top_k": 40, "num_ctx": 4096, "num_predict": 1024}
    assert "_num_ctx" not in emitted[0]

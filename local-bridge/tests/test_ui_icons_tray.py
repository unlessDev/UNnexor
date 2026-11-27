"""Iconos vectoriales propios, barra de título simplificada y bandeja."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")


def test_every_icon_renders(qapp):
    from app.desktop import icons

    for name in icons.PATHS:
        pixmap = icons.pixmap(name, icons.NORMAL, 24)
        assert not pixmap.isNull()
        assert not icons.icon(name, checkable=True).isNull()


def test_titlebar_uses_vector_icons_and_hides_models(qapp):
    from PySide6.QtWidgets import QToolButton

    from app.desktop.widgets import TitleBar

    bar = TitleBar("qwen3:8b")
    buttons = bar.findChildren(QToolButton)
    assert buttons and all(not b.icon().isNull() for b in buttons)
    assert all(b.text() == "" for b in buttons)  # sin emojis
    assert bar._models.isHidden()  # Modelos de IA vive en el menú
    bar.deleteLater()


def test_tray_toggles_panel(qapp):
    from PySide6.QtWidgets import QWidget

    from app.desktop.tray import AppTray
    from app.main import app_icon

    window = QWidget()
    window.show()
    tray = AppTray(window, app_icon())
    texts = [a.text() for a in tray.contextMenu().actions() if a.text()]
    assert "Salir de UNnexor" in texts and "Copiloto en tiempo real" in texts
    tray.toggle_panel()
    assert window.isMinimized()
    tray.toggle_panel()
    assert not window.isMinimized()
    tray.deleteLater()
    window.deleteLater()
    qapp.sendPostedEvents(None, 52)

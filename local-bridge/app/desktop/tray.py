"""Icono de UNnexor en el área de notificación (centro de todas las ventanas).

Con el panel, el copiloto, su panel flotante, la consola y los diálogos, el
usuario podía perder de vista dónde estaba cada cosa. Desde este icono:

* clic: muestra u oculta (minimiza) el panel principal;
* menú: abre el copiloto, la recomendación de configuración, los modelos,
  la consola del puente o sale de UNnexor (cierre ordenado).

La primera vez se muestra un aviso breve explicando dónde está.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from . import icons

FIRST_RUN_KEY = "tray/introduced"


class AppTray(QSystemTrayIcon):
    def __init__(self, window: Any, app_icon) -> None:  # BridgeWindow
        super().__init__(app_icon, window)
        self._window = window
        self.setToolTip("UNnexor AI Bridge")
        menu = QMenu()
        self._menu = menu  # referencia viva

        def add(icon_name: str, text: str, slot) -> None:
            action = menu.addAction(icons.menu_icon(icon_name), text)
            action.triggered.connect(slot)

        add("window", "Mostrar / ocultar el panel", self.toggle_panel)
        add("home", "Centro de control", lambda: window.open_control_center())
        add("copilot", "Copiloto en tiempo real", lambda: window.open_copilot())
        add("capture", "Capturar una región…", lambda: window._start_capture())
        menu.addSeparator()
        add(
            "sparkles",
            "Recomendar configuración…",
            lambda: window.open_recommendations(),
        )
        add("models", "Modelos de IA…", lambda: window.open_control_center("models"))
        add("settings", "Ajustes…", lambda: window._open_settings())
        add("terminal", "Consola del puente", lambda: window.toggle_log_console())
        menu.addSeparator()
        add("quit", "Salir de UNnexor", window.close)
        self.setContextMenu(menu)
        self.activated.connect(self._on_activated)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.toggle_panel()

    def toggle_panel(self) -> None:
        window = self._window
        if window.isVisible() and not window.isMinimized():
            window.showMinimized()
        else:
            window.showNormal()
            window.raise_()

    def introduce(self) -> None:
        """Aviso único: dónde encontrar UNnexor."""
        settings = QSettings("UNnexor", "Bridge")
        if str(settings.value(FIRST_RUN_KEY, "")).lower() in ("true", "1"):
            return
        settings.setValue(FIRST_RUN_KEY, True)
        if self.supportsMessages():
            self.showMessage(
                "UNnexor está activo",
                "Desde este icono abres el panel, el copiloto, los modelos y la "
                "consola, o sales de UNnexor.",
                QSystemTrayIcon.MessageIcon.Information,
                6000,
            )

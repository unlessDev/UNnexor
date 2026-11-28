"""«Siempre visible» real para las ventanas de UNnexor.

Problema que corrige: el botón 📌 cambiaba la bandera de Qt
``WindowStaysOnTopHint``. Qt aplica ese cambio **recreando la ventana
nativa** (nuevo HWND), con lo que se perdían los estilos reforzados
(``WS_EX_NOACTIVATE``/``WS_EX_APPWINDOW``) y el hook de rueda seguía
apuntando al HWND viejo; además el botón arrancaba siempre marcado sin
mirar el estado real.

Aquí, en Windows, se usa ``SetWindowPos(HWND_TOPMOST / HWND_NOTOPMOST)``
sobre el mismo HWND (sin recrearlo) y el estado se lee del propio sistema
(``WS_EX_TOPMOST``), así el botón refleja siempre lo que hace la ventana.
En otros sistemas se recurre a la bandera de Qt.

Cada ventana guarda su preferencia por separado (``QSettings``), de modo
que el usuario decide cuáles quedan por encima y cuáles no.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from typing import Any

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QWidget

log = logging.getLogger("unnexor.desktop.topmost")

IS_WINDOWS = sys.platform == "win32"
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_NOOWNERZORDER = 0x0200
WS_EX_TOPMOST = 0x00000008
GWL_EXSTYLE = -20

_user32: Any = None
if IS_WINDOWS:  # pragma: no branch
    try:
        import ctypes.wintypes as wt

        _user32 = ctypes.windll.user32
        _user32.SetWindowPos.argtypes = [
            wt.HWND,
            wt.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wt.UINT,
        ]
        _user32.SetWindowPos.restype = wt.BOOL
        _user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        _user32.GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
    except (AttributeError, OSError):  # pragma: no cover
        _user32 = None

SETTINGS_GROUP = ("UNnexor", "WindowPins")


def native_available() -> bool:
    """¿Hay HWND reales? (no con la plataforma Qt ``offscreen`` de las pruebas)."""
    return _user32 is not None and QGuiApplication.platformName() == "windows"


def _hwnd(widget: QWidget) -> int:
    if not native_available():
        return 0
    try:
        return (
            int(widget.winId())
            if widget.testAttribute(Qt.WidgetAttribute.WA_WState_Created)
            else 0
        )
    except RuntimeError:  # objeto C++ destruido
        return 0


def is_topmost(widget: QWidget) -> bool:
    """Estado REAL de la ventana (lo que ve el sistema operativo)."""
    hwnd = _hwnd(widget)
    if _user32 is not None and hwnd:
        style = _user32.GetWindowLongPtrW(ctypes.c_void_p(hwnd), GWL_EXSTYLE)
        return bool(style & WS_EX_TOPMOST)
    return bool(widget.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)


def set_topmost(widget: QWidget, enabled: bool) -> bool:
    """Coloca la ventana encima de las demás (o la devuelve a la normalidad).

    Devuelve el estado real tras aplicar el cambio.
    """
    hwnd = _hwnd(widget)
    if _user32 is not None and hwnd:
        ok = _user32.SetWindowPos(
            ctypes.c_void_p(hwnd),
            ctypes.c_void_p(HWND_TOPMOST if enabled else HWND_NOTOPMOST),
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER,
        )
        if not ok:
            log.warning("SetWindowPos falló al cambiar «Siempre visible».")
        return is_topmost(widget)
    visible = widget.isVisible()
    widget.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)
    if visible:
        widget.show()
    return is_topmost(widget)


class PinPreference:
    """Preferencia «Siempre visible» de UNA ventana, persistente."""

    def __init__(self, key: str, default: bool) -> None:
        self.key = key
        self.default = default
        self._settings = QSettings(*SETTINGS_GROUP)

    def load(self) -> bool:
        value = self._settings.value(self.key, None)
        if value is None:
            return self.default
        return str(value).lower() in ("true", "1")

    def save(self, enabled: bool) -> None:
        self._settings.setValue(self.key, bool(enabled))
        self._settings.sync()

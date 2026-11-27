"""Iconos vectoriales de UNnexor (trazos propios, 24×24, sin licencias de terceros).

Sustituyen a los emojis de la interfaz, que cada versión de Windows dibuja
distinto y que no siguen la paleta de la marca. Cada icono se genera a partir
de un trazo SVG y se colorea en tiempo de ejecución: gris en reposo, blanco
al pasar el ratón y cian cuando el botón está activado.
"""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap

NORMAL = "#8a93a5"
HOVER = "#e8eaf2"
ACTIVE = "#00e5ff"
DANGER = "#ff6b6b"

# Contenido interior de <svg viewBox="0 0 24 24"> (trazos, sin relleno).
PATHS: dict[str, str] = {
    "pin": '<path d="M9 3h6l-1 6 4 4H6l4-4z"/><path d="M12 13v8"/>',
    "copy": '<rect x="8" y="8" width="12" height="12" rx="2"/>'
    '<path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/>',
    "new": '<path d="M4 12a8 8 0 1 0 2.3-5.6"/><path d="M4 4v4h4"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2"/>',
    "capture": '<path d="M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3'
    'a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3"/><circle cx="12" cy="12" r="3"/>',
    "copilot": '<path d="M12 3a3 3 0 0 1 3 3v6a3 3 0 0 1-6 0V6a3 3 0 0 1 3-3z"/>'
    '<path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
    "menu": '<path d="M4 7h16M4 12h16M4 17h16"/>',
    "minimize": '<path d="M5 12h14"/>',
    "close": '<path d="M6 6l12 12M18 6L6 18"/>',
    "models": '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4M15 2v4'
    'M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/>',
    "sparkles": '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/>'
    '<path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>',
    "providers": '<path d="M9 7V3M15 7V3M7 7h10v4a5 5 0 0 1-10 0z"/><path d="M12 16v5"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3'
    'M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1"/>',
    "link": '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/>'
    '<path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
    "rocket": '<path d="M12 15l-3-3c1-5 4-8 10-9-1 6-4 9-9 10z"/>'
    '<path d="M9 12H5l3-4h4M12 15v4l4-3v-4"/>',
    "diagnostics": '<path d="M3 12h4l2-6 4 12 2-6h6"/>',
    "terminal": '<rect x="3" y="4" width="18" height="16" rx="2"/>'
    '<path d="M7 9l3 3-3 3M13 15h4"/>',
    "window": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18"/>',
    "home": '<path d="M3 11l9-7 9 7"/><path d="M5 10v10h14V10"/>',
    "globe": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18'
    'M12 3a14 14 0 0 0 0 18"/>',
    "puzzle": '<path d="M5 8h3a2 2 0 1 1 4 0h3v3a2 2 0 1 1 0 4v4H5v-4a2 2 0 1 0 0-4z"/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
    "shield": '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/>',
    "quit": '<path d="M12 3v9"/><path d="M6.3 7.5a8 8 0 1 0 11.4 0"/>',
}


def _svg(name: str, color: str, width: float = 2.0) -> bytes:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        f'stroke="{color}" stroke-width="{width}" stroke-linecap="round" '
        f'stroke-linejoin="round">{PATHS[name]}</svg>'
    ).encode()


def pixmap(name: str, color: str, size: int = 32) -> QPixmap:
    from PySide6.QtSvg import QSvgRenderer

    renderer = QSvgRenderer(QByteArray(_svg(name, color)))
    image = QPixmap(size, size)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return image


@lru_cache(maxsize=128)
def icon(name: str, color: str = NORMAL, checkable: bool = False) -> QIcon:
    """Icono con estados: reposo, hover (Active) y activado (On)."""
    result = QIcon()
    result.addPixmap(pixmap(name, color), QIcon.Mode.Normal, QIcon.State.Off)
    result.addPixmap(pixmap(name, HOVER), QIcon.Mode.Active, QIcon.State.Off)
    if checkable:
        result.addPixmap(pixmap(name, ACTIVE), QIcon.Mode.Normal, QIcon.State.On)
        result.addPixmap(pixmap(name, ACTIVE), QIcon.Mode.Active, QIcon.State.On)
    return result


def menu_icon(name: str) -> QIcon:
    return icon(name, "#c9cde0")

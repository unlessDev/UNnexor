"""Identidad visual de UNnexor en la interfaz de escritorio.

La X se dibuja desde ``assets/unnexor-x.svg`` (el mismo SVG maestro que
``branding/unnexor-x.svg``, generado por ``scripts/make_brand_assets.py``),
así se ve nítida en cualquier escala de pantalla. Si QtSvg no estuviera
disponible se usa el PNG del icono.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QLabel, QWidget

from .. import paths

log = logging.getLogger("unnexor.desktop.brand")

APP_NAME = "UNnexor"
TAGLINE = "AI Bridge"
AUTHOR = "UnlessDev"


def logo_pixmap(height: int, device_pixel_ratio: float = 1.0) -> QPixmap:
    """La X de UNnexor con la altura lógica indicada (fondo transparente)."""
    svg_path = paths.resource_path("assets/unnexor-x.svg")
    try:
        from PySide6.QtSvg import QSvgRenderer

        renderer = QSvgRenderer(str(svg_path))
        if renderer.isValid():
            view = renderer.viewBoxF()
            ratio = view.width() / view.height() if view.height() else 1.6
            width = max(1, round(height * ratio))
            pixmap = QPixmap(
                round(width * device_pixel_ratio), round(height * device_pixel_ratio)
            )
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            renderer.render(painter, QRectF(0, 0, pixmap.width(), pixmap.height()))
            painter.end()
            pixmap.setDevicePixelRatio(device_pixel_ratio)
            return pixmap
    except Exception as exc:  # pragma: no cover - entorno sin QtSvg
        log.debug("No se pudo renderizar el SVG del logo: %s", exc)
    fallback = QPixmap(str(paths.resource_path("assets/unnexor.png")))
    if fallback.isNull():
        return fallback
    return fallback.scaledToHeight(
        round(height * device_pixel_ratio),
        Qt.TransformationMode.SmoothTransformation,
    )


class LogoLabel(QLabel):
    """Etiqueta con la X de UNnexor (se adapta a la escala del monitor)."""

    def __init__(self, height: int = 14, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._logo_height = height
        self.setObjectName("brandLogo")
        self.setToolTip(f"{APP_NAME} {TAGLINE} · by {AUTHOR}")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._refresh()

    def _refresh(self) -> None:
        self.setPixmap(logo_pixmap(self._logo_height, self.devicePixelRatioF()))

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == event.Type.ScreenChangeInternal:
            self._refresh()

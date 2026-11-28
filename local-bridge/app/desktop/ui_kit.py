"""Componentes visuales compartidos por las ventanas de UNnexor.

Un único lenguaje visual para diálogos y páginas (antes cada ventana tenía
su propia hoja de estilos, tamaños fijos y textos que se cortaban):

* ``DIALOG_QSS``       — hoja de estilos común (tipografía, botones, campos).
* ``fit_to_screen``    — tamaño inicial proporcional a la pantalla real,
  nunca mayor que el área disponible y centrado sobre la ventana padre.
* ``wrap_label``       — etiqueta con ajuste de línea y altura correcta.
* ``TierChip``         — clasificación de consumo con la MISMA escala y
  colores que el catálogo de modelos (``model_evaluator.TIER_META``); el
  color acompaña siempre a un texto, nunca lo sustituye.
* ``ChoiceCard``       — tarjeta seleccionable (fondo, borde y ✓ visibles).
* ``DownloadProgress`` — barra + detalle honestos a partir de ``PullSnapshot``.
* ``scrollable``       — envuelve contenido largo en un área con scroll.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..model_evaluator import TIER_META, TIER_SIN_EVALUAR
from ..ollama_pull import COMPLETED, FAILED, PullSnapshot
from .styles import ACCENT, BORDER, MUTED, PANEL, TEXT

BG = "#12141a"
FIELD_BG = "#0e1016"
SELECTED_BG = "#211d3d"
UI_FONT = '"Segoe UI", "Segoe UI Variable", "Inter", Arial, sans-serif'

DIALOG_QSS = f"""
QDialog, QMainWindow, #pageRoot {{ background: {BG}; color: {TEXT}; }}
QWidget {{ font-family: {UI_FONT}; font-size: 13px; }}
QLabel {{ background: transparent; color: {TEXT}; }}
QLabel[role="title"] {{ font-size: 18px; font-weight: 700; color: #f2f3f8; }}
QLabel[role="section"] {{ font-size: 12px; font-weight: 700; color: {ACCENT};
    letter-spacing: 1px; padding-top: 6px; }}
QLabel[role="muted"] {{ color: {MUTED}; font-size: 12px; }}
QLabel[role="error"] {{ color: #ff8a8a; }}
QLineEdit, QPlainTextEdit, QTextBrowser, QSpinBox, QDoubleSpinBox {{
    background: {FIELD_BG}; color: {TEXT}; border: 1px solid {BORDER};
    border-radius: 6px; padding: 6px 8px; selection-background-color: #3a2bff; }}
QComboBox {{ background: #20242e; color: {TEXT}; border: 1px solid {BORDER};
    border-radius: 6px; padding: 5px 8px; min-height: 22px; }}
QComboBox QAbstractItemView {{ background: #1b1f28; color: {TEXT};
    selection-background-color: #2f2a6b; }}
QPushButton {{ background: #20242e; color: {TEXT}; border: 1px solid {BORDER};
    border-radius: 6px; padding: 7px 14px; min-height: 20px; }}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:disabled {{ color: #5b6374; border-color: #22262f; }}
QPushButton[kind="primary"] {{ background: #3b2fd6; border-color: #5a4cff;
    color: white; font-weight: 600; }}
QPushButton[kind="primary"]:hover {{ background: #4a3df0; }}
QPushButton[kind="danger"] {{ border-color: #7a3340; color: #ffb3b3; }}
QProgressBar {{ background: {FIELD_BG}; border: 1px solid {BORDER};
    border-radius: 5px; height: 12px; text-align: center; color: {TEXT};
    font-size: 11px; }}
QProgressBar::chunk {{ background: #5a4cff; border-radius: 4px; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ width: 10px; background: transparent; }}
QScrollBar::handle:vertical {{ background: #2a3140; border-radius: 5px;
    min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QFrame#card {{ background: {PANEL}; border: 1px solid {BORDER};
    border-radius: 10px; }}
QListWidget#nav {{ background: #0f1116; border: none; border-right: 1px solid
    {BORDER}; color: #c9cde0; font-size: 13px; padding-top: 8px; }}
QListWidget#nav::item {{ padding: 10px 14px; border-radius: 6px; margin: 1px 8px; }}
QListWidget#nav::item:selected {{ background: {SELECTED_BG}; color: white;
    border-left: 3px solid {ACCENT}; }}
QListWidget#nav::item:hover:!selected {{ background: #171a22; }}
"""


def fit_to_screen(
    widget: QWidget,
    preferred: QSize,
    minimum: Optional[QSize] = None,
    *,
    anchor: Optional[QWidget] = None,
) -> QSize:
    """Tamaño inicial adaptado a la pantalla (≤ 92 % del área útil)."""
    screen = None
    if anchor is not None and anchor.screen() is not None:
        screen = anchor.screen()
    if screen is None:
        screen = widget.screen() or QGuiApplication.primaryScreen()
    area = screen.availableGeometry() if screen is not None else None
    width, height = preferred.width(), preferred.height()
    if area is not None:
        width = min(width, int(area.width() * 0.92))
        height = min(height, int(area.height() * 0.92))
    if minimum is not None:
        width = max(width, min(minimum.width(), area.width() if area else width))
        height = max(height, min(minimum.height(), area.height() if area else height))
        widget.setMinimumSize(
            min(minimum.width(), width), min(minimum.height(), height)
        )
    widget.resize(width, height)
    if area is not None:
        center = (
            anchor.frameGeometry().center()
            if anchor is not None and anchor.isVisible()
            else area.center()
        )
        x = max(area.left(), min(center.x() - width // 2, area.right() - width))
        y = max(area.top(), min(center.y() - height // 2, area.bottom() - height))
        widget.move(x, y)
    return QSize(width, height)


def wrap_label(text: str = "", role: str = "", *, selectable: bool = False) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
    if role:
        label.setProperty("role", role)
    if selectable:
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def scrollable(content: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setWidget(content)
    return area


def tier_style(tier: str) -> tuple[str, str, str]:
    """(emoji, texto, color) de la escala común de consumo/compatibilidad."""
    return TIER_META.get(tier, TIER_META[TIER_SIN_EVALUAR])


class TierChip(QLabel):
    """Píldora «🟢 ÓPTIMO» con el color semántico como refuerzo del texto."""

    def __init__(self, tier: str, text: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.set_tier(tier, text)

    def set_tier(self, tier: str, text: str = "") -> None:
        emoji, label, color = tier_style(tier)
        self.setText(f"{emoji} {text or label}")
        self.setToolTip(f"Clasificación: {label}")
        self.setStyleSheet(
            f"QLabel {{ color: {color}; border: 1px solid {color};"
            " border-radius: 9px; padding: 1px 8px; font-size: 11px;"
            " font-weight: 700; background: rgba(255,255,255,0.03); }"
        )
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)


class ChoiceCard(QFrame):
    """Tarjeta seleccionable: el estado elegido se ve sin depender del color."""

    clicked = Signal()

    def __init__(
        self,
        title: str,
        description: str,
        *,
        tier: str = "",
        tier_text: str = "",
        subtitle: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("choiceCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(title)
        self._selected = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)
        top = QHBoxLayout()
        self._check = QLabel("○")
        self._check.setFixedWidth(22)
        self._title = QLabel(title)
        self._title.setStyleSheet("font-size: 15px; font-weight: 700;")
        top.addWidget(self._check)
        top.addWidget(self._title, 1)
        layout.addLayout(top)
        if subtitle:
            sub = wrap_label(f"<b>{subtitle}</b>")
            layout.addWidget(sub)
        self.chip: Optional[TierChip] = None
        if tier:
            self.chip = TierChip(tier, tier_text)
            layout.addWidget(self.chip)
        self._desc = wrap_label(description, "muted")
        layout.addWidget(self._desc)
        layout.addStretch(1)
        self.setMinimumWidth(200)
        self._restyle()

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self.setAccessibleDescription("seleccionado" if selected else "")
        self._restyle()

    def is_selected(self) -> bool:
        return self._selected

    def set_tier(self, tier: str, text: str = "") -> None:
        if self.chip is not None:
            self.chip.set_tier(tier, text)

    def _restyle(self) -> None:
        if self._selected:
            self._check.setText("✓")
            self._check.setStyleSheet(
                f"color: white; background: {ACCENT}; border-radius: 11px;"
                " font-weight: 900; qproperty-alignment: AlignCenter;"
                " min-height: 22px;"
            )
            self.setStyleSheet(
                f"QFrame#choiceCard {{ background: {SELECTED_BG};"
                f" border: 2px solid {ACCENT}; border-radius: 12px; }}"
            )
        else:
            self._check.setText("○")
            self._check.setStyleSheet(f"color: {MUTED}; font-size: 16px;")
            self.setStyleSheet(
                f"QFrame#choiceCard {{ background: {PANEL};"
                f" border: 1px solid {BORDER}; border-radius: 12px; }}"
                f"QFrame#choiceCard:hover {{ border-color: #4a4f63; }}"
            )

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.clicked.emit()
            return
        super().keyPressEvent(event)


class DownloadProgress(QWidget):
    """Progreso de una descarga: barra real o indeterminada + detalle."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(True)
        self.detail = wrap_label("", "muted")
        layout.addWidget(self.bar)
        layout.addWidget(self.detail)

    def apply(self, snapshot: PullSnapshot) -> None:
        if snapshot.fraction is None:
            self.bar.setRange(0, 0)  # indeterminado: no se inventa porcentaje
        else:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(snapshot.fraction * 1000))
            self.bar.setFormat(f"{snapshot.fraction * 100:.0f} %")
        if snapshot.phase == FAILED:
            self.bar.setRange(0, 1)
            self.bar.setValue(0)
            self.bar.setFormat("Error")
        if snapshot.phase == COMPLETED:
            self.bar.setRange(0, 1)
            self.bar.setValue(1)
            self.bar.setFormat("100 %")
        self.detail.setText(snapshot.describe())
        self.detail.setProperty(
            "role", "error" if snapshot.phase == FAILED else "muted"
        )
        style = self.detail.style()
        if style is not None:
            style.unpolish(self.detail)
            style.polish(self.detail)


def card(parent: Optional[QWidget] = None) -> QFrame:
    frame = QFrame(parent)
    frame.setObjectName("card")
    return frame


def section(text: str) -> QLabel:
    label = QLabel(text.upper())
    label.setProperty("role", "section")
    return label

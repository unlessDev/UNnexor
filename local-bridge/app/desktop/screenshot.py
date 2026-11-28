"""Captura de región de pantalla: overlay de selección + vista previa.

Flujo (FASE 7):
    botón 📷 → overlay translúcido por pantalla → arrastrar rectángulo
    → se ocultan los overlays → (120 ms) → grab de la región real
    → ``ScreenshotPreviewDialog`` (instrucción + método) → envío.

Detalles de DPI/multimonitor:
  * Un overlay independiente por ``QScreen`` (geometría nativa, no
    maximizado: cubre también la barra de tareas).
  * El rectángulo se manipula en coordenadas lógicas (device-independent)
    relativas a la pantalla; al capturar se multiplica por el
    ``devicePixelRatio`` del grab resultante. Así funciona con escalado
    100 %/125 %/150 % y con monitores mezclados.
  * Los overlays se cierran ANTES de capturar (nunca aparecen en la imagen).
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QObject, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen, QPixmap, QScreen
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .styles import ACCENT, BORDER, MUTED, PANEL, TEXT
from .ui_kit import fit_to_screen


# --------------------------------------------------------------------- #
# Overlay de selección (uno por pantalla)
# --------------------------------------------------------------------- #
class RegionOverlay(QWidget):
    """Superficie translúcida sobre una pantalla para arrastrar el rectángulo."""

    selectionFinished = Signal(object, str)  # (QRect|None, screenName)

    def __init__(self, screen: QScreen) -> None:
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool  # sin botón en la barra de tareas
        )
        super().__init__(None, flags)
        self._screen = screen
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._origin: Optional[QPoint] = None
        self._current_point: Optional[QPoint] = None
        self._selecting = False

    def show_full(self) -> None:
        self.setGeometry(self._screen.geometry())
        self.show()
        self.activateWindow()  # recibe teclado solo durante la selección

    # ------------------------------------------------------------------ #
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        # Oscurecer toda la pantalla...
        painter.fillRect(self.rect(), QColor(6, 8, 12, 140))
        rect = self._selected_rect()
        if rect is not None:
            # ...dejar la región elegida nítida y con borde acento.
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            painter.fillRect(rect, Qt.GlobalColor.transparent)
            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_SourceOver
            )
            painter.setPen(QPen(QColor(ACCENT), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect)
            size_label = f"{rect.width()} × {rect.height()} px"
            painter.setPen(QPen(QColor(TEXT), 1))
            painter.drawText(
                rect.adjusted(0, -20, 0, 0), Qt.AlignmentFlag.AlignBottom, size_label
            )

    def _selected_rect(self) -> Optional[QRect]:
        if (
            self._selecting
            and self._origin is not None
            and self._current_point is not None
        ):
            return QRect(self._origin, self._current_point).normalized()
        return None

    # ------------------------------------------------------------------ #
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._origin = event.position().toPoint()
            self._current_point = self._origin
            self._selecting = True
            self.update()
        elif event.button() == Qt.MouseButton.RightButton:
            self._finish(None)

    def mouseMoveEvent(self, event) -> None:
        if self._selecting:
            self._current_point = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if not self._selecting:
            return
        self._current_point = event.position().toPoint()
        rect = self._selected_rect()
        self._selecting = False
        # Rechazar selecciones minúsculas (clic accidental).
        if rect is None or rect.width() < 12 or rect.height() < 12:
            self._finish(None)
        else:
            self._finish(rect)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self._finish(None)

    # ------------------------------------------------------------------ #
    def _finish(self, rect: Optional[QRect]) -> None:
        name = self._screen.name()
        self.close()
        self.selectionFinished.emit(rect, name)


# --------------------------------------------------------------------- #
# Orquestador multimonitor + captura
# --------------------------------------------------------------------- #
class RegionSelector(QObject):
    """Abre overlays en todas las pantallas y captura la región elegida."""

    captured = Signal(QPixmap)
    cancelled = Signal()

    # Espera tras ocultar los overlays para que desaparezcan del frame real.
    HIDE_DELAY_MS = 150

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._overlays: List[RegionOverlay] = []
        self._result_rect: Optional[QRect] = None
        self._result_screen: Optional[QScreen] = None

    @property
    def last_rect(self) -> Optional[QRect]:
        """Región lógica elegida (para volver a capturarla periódicamente)."""
        return self._result_rect

    @property
    def last_screen(self) -> Optional[QScreen]:
        return self._result_screen

    def start(self) -> None:
        screens = QGuiApplication.screens()
        if not screens:
            self.cancelled.emit()
            return
        for screen in screens:
            overlay = RegionOverlay(screen)
            overlay.selectionFinished.connect(self._on_selection)
            self._overlays.append(overlay)
            overlay.show_full()

    def _on_selection(self, rect: object, screen_name: str) -> None:
        if self._result_screen is not None:
            return  # ya se resolvió en otra pantalla
        self._result_rect = rect if isinstance(rect, QRect) else None
        if rect is not None:
            self._result_screen = self._screen_by_name(screen_name)
        self._close_all()

    def _screen_by_name(self, name: str) -> Optional[QScreen]:
        for screen in QGuiApplication.screens():
            if screen.name() == name:
                return screen
        return None

    def _close_all(self) -> None:
        for overlay in list(self._overlays):
            if overlay.isVisible():
                overlay.close()
        self._overlays.clear()
        if self._result_rect is None or self._result_screen is None:
            QTimer.singleShot(self.HIDE_DELAY_MS, self.cancelled.emit)
        else:
            QTimer.singleShot(self.HIDE_DELAY_MS, self._grab)

    def _grab(self) -> None:
        screen = self._result_screen
        rect = self._result_rect
        if screen is None or rect is None:
            self.cancelled.emit()
            return
        full = screen.grabWindow(0)  # escritorio completo de ESA pantalla
        dpr = full.devicePixelRatio() or 1.0
        # Coordenadas lógicas → píxeles físicos (escalado de Windows).
        device_rect = QRect(
            int(rect.x() * dpr),
            int(rect.y() * dpr),
            int(rect.width() * dpr),
            int(rect.height() * dpr),
        )
        final = full.copy(device_rect)
        self.captured.emit(final)


# --------------------------------------------------------------------- #
# Editor de instrucciones: Enter envía, Shift+Enter inserta salto
# --------------------------------------------------------------------- #
class InstructionEdit(QPlainTextEdit):
    submitted = Signal()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
            event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self.submitted.emit()
            return
        super().keyPressEvent(event)


# --------------------------------------------------------------------- #
# Vista previa
# --------------------------------------------------------------------- #
class ScreenshotPreviewDialog(QDialog):
    """Confirmación humana antes de procesar la captura.

    Ofrece: imagen, dimensiones/peso, instrucción (Enter = enviar,
    Shift+Enter = salto de línea) y método de procesamiento
    (visión multimodal / OCR local). ``Repetir`` reinicia la selección.
    """

    repeatRequested = Signal()

    def __init__(
        self,
        pixmap: QPixmap,
        vision_available: bool,
        ocr_available: bool,
        model_name: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Vista previa de la captura — UNnexor")
        fit_to_screen(self, QSize(680, 680), QSize(480, 480), anchor=parent)
        self.setStyleSheet(
            f"QDialog {{ background: #12141a; color: {TEXT}; }}"
            f"QLabel {{ background: transparent; color: {TEXT}; }}"
            f"QPlainTextEdit {{ background: #0e1016; color: {TEXT};"
            f" border: 1px solid {BORDER}; border-radius: 6px; padding: 5px; }}"
            f"QComboBox {{ background: #20242e; color: {TEXT};"
            f" border: 1px solid {BORDER}; border-radius: 6px; padding: 4px; }}"
            f"QPushButton {{ background: #20242e; color: {TEXT};"
            f" border: 1px solid {BORDER}; border-radius: 6px; padding: 6px 12px; }}"
            f"QPushButton:default {{ border-color: {ACCENT}; }}"
            f"QPushButton:hover {{ border-color: {ACCENT}; }}"
        )
        self._pixmap = pixmap
        self._instruction = ""

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        title = QLabel("Captura lista. Describe qué quieres hacer con ella:")
        title.setStyleSheet(f"color: {TEXT}; font-weight: 700;")
        layout.addWidget(title)

        # Imagen escalada para caber (sin deformar).
        max_w, max_h = 640, 300
        scaled = pixmap.scaled(
            max_w,
            max_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        image = QLabel()
        image.setPixmap(scaled)
        image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        image.setStyleSheet(
            f"border: 1px solid {BORDER}; background: {PANEL}; padding: 4px;"
        )
        layout.addWidget(image)

        # En Qt6 QPixmap no expone sizeInBytes(); el peso en memoria se
        # calcula desde el QImage subyacente (4 bytes/px con premultiplied).
        kb = max(1, int(pixmap.toImage().sizeInBytes() / 1024))
        meta = QLabel(
            f"Dimensiones: {pixmap.width()} × {pixmap.height()} px · ~{kb} KB · "
            f"Modelo: {model_name or '—'}"
        )
        meta.setStyleSheet(f"color: {MUTED}; font-size: 10px;")
        layout.addWidget(meta)

        # --- Método de procesamiento ----------------------------------- #
        method_row = QHBoxLayout()
        method_label = QLabel("Procesar con:")
        method_label.setStyleSheet(f"color: {MUTED};")
        self._method = QComboBox()
        if vision_available:
            self._method.addItem(
                "Visión — el modelo multimodal analiza la imagen", "vision"
            )
        if ocr_available:
            self._method.addItem(
                "OCR local — transcripción Markdown/LaTeX (sin salir del equipo)",
                "ocr",
            )
        if self._method.count() == 0:
            self._method.addItem(
                "Sin método disponible: instala un modelo con visión (🤖)", "none"
            )
        method_row.addWidget(method_label)
        method_row.addWidget(self._method, 1)
        layout.addLayout(method_row)

        # --- Instrucción ------------------------------------------------ #
        self._edit = InstructionEdit()
        self._edit.setPlaceholderText(
            "Ej.: «Resuelve el ejercicio paso a paso y explica el resultado»\n"
            "Enter = enviar · Shift+Enter = nueva línea · Esc = cancelar"
        )
        self._edit.setFixedHeight(72)
        layout.addWidget(self._edit, 1)

        buttons = QHBoxLayout()
        repeat = QPushButton("Repetir")
        repeat.setToolTip("Volver a seleccionar la región")
        repeat.clicked.connect(self._repeat)
        cancel = QPushButton("Cancelar")
        cancel.clicked.connect(self.reject)
        self._send = QPushButton("Enviar")
        self._send.setDefault(True)
        self._send.clicked.connect(self._send_now)
        buttons.addWidget(repeat)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(self._send)
        layout.addLayout(buttons)

        self._edit.submitted.connect(self._send_now)
        self._method.currentIndexChanged.connect(self._update_send_enabled)
        self._update_send_enabled()

    # ------------------------------------------------------------------ #
    def _update_send_enabled(self) -> None:
        method = self._method.currentData()
        self._send.setEnabled(method in ("vision", "ocr"))

    def _send_now(self) -> None:
        method = self._method.currentData()
        if method not in ("vision", "ocr"):
            return
        self._instruction = self._edit.toPlainText().strip()
        if not self._instruction:
            self._edit.setPlaceholderText(
                "Escribe una instrucción antes de enviar (Enter para enviar)."
            )
            self._edit.setFocus()
            return
        self.accept()

    def _repeat(self) -> None:
        self.repeatRequested.emit()
        self.reject()

    # ------------------------------------------------------------------ #
    def instruction(self) -> str:
        return self._instruction

    def method(self) -> str:
        data = self._method.currentData()
        return data if isinstance(data, str) else "none"


# Límite para modelos con visión: los tokens de imagen crecen con los píxeles
# y con ellos los búferes de cómputo de la GPU; capturas de 1600+ px agotaban
# la VRAM de una GPU de 8 GB (qwen2.5vl:7b, 2026-11-25).
VISION_MAX_SIDE = 1280
VISION_MAX_PIXELS = 1_200_000


def fit_for_vision(pixmap: QPixmap) -> QPixmap:
    """Reduce la captura (sin agrandarla) para enviarla a un modelo de visión."""
    width, height = pixmap.width(), pixmap.height()
    if width <= 0 or height <= 0:
        return pixmap
    scale = min(1.0, VISION_MAX_SIDE / max(width, height))
    scale = min(scale, (VISION_MAX_PIXELS / (width * height)) ** 0.5)
    if scale >= 1.0:
        return pixmap
    return pixmap.scaled(
        max(1, int(width * scale)),
        max(1, int(height * scale)),
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )

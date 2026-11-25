"""Widgets reutilizables de la ventana flotante.

* ``IconButton``    — botones de la barra de título (glifos unicode).
* ``TitleBar``      — barra arrastrable con modelo, estado y controles.
* ``UserMessage`` / ``AssistantMessage`` / ``SystemNote`` — bloques de chat.
* ``ChatScroller``  — autoscroll con pausa/reanudación según el usuario.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QScrollBar,
    QSizePolicy,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..math_unicode import to_human_math

FONT_FAMILIES = ["Segoe UI Emoji", "Segoe UI Symbol", "Segoe UI", "Arial"]


def _now_hhmm() -> str:
    return datetime.now().strftime("%H:%M")


class IconButton(QToolButton):
    """Botón de icono (glifo unicode) para la barra de título."""

    def __init__(
        self,
        glyph: str,
        tooltip: str,
        object_name: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        font = QFont()
        font.setFamilies(FONT_FAMILIES)
        font.setPointSize(11)
        self.setFont(font)
        self.setText(glyph)
        self.setToolTip(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        if object_name:
            self.setObjectName(object_name)


class TitleBar(QWidget):
    """Barra de título: arrastre manual (sin activar la ventana) + controles."""

    pinToggled = Signal(bool)
    minimizeClicked = Signal()
    closeClicked = Signal()
    copyClicked = Signal()
    clearClicked = Signal()
    stopClicked = Signal()
    menuClicked = Signal()
    modelsClicked = Signal()
    screenshotClicked = Signal()
    copilotClicked = Signal()

    HEIGHT = 40

    def __init__(self, model_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("titleBar")
        self.setFixedHeight(self.HEIGHT)

        self._drag_offset = None
        self._dragging = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 8, 6)
        layout.setSpacing(6)

        self.dot = QLabel("●")
        self.dot.setObjectName("connDot")
        self.dot.setToolTip("Estado de conexión con Ollama")

        self.title = QLabel("EXTDATOR")
        self.title.setObjectName("titleLabel")

        self.model = QLabel(model_name)
        self.model.setObjectName("modelLabel")

        layout.addWidget(self.dot)
        layout.addWidget(self.title)
        layout.addWidget(self.model)
        layout.addStretch(1)

        self._pin = IconButton(
            "📌", "Siempre visible (encima de las demás ventanas)", "pinBtn"
        )
        self._pin.setCheckable(True)
        self._pin.setChecked(True)
        self._pin.toggled.connect(self.pinToggled)
        self._copy = IconButton("⧉", "Copiar última respuesta")
        self._copy.clicked.connect(self.copyClicked)
        self._clear = IconButton("⟲", "Nueva conversación")
        self._clear.clicked.connect(self.clearClicked)
        self._stop = IconButton("■", "Detener generación")
        self._stop.clicked.connect(self.stopClicked)
        self._stop.setVisible(False)
        self._models = IconButton("🤖", "Modelos de IA (elegir, evaluar, descargar)")
        self._models.clicked.connect(self.modelsClicked)
        self._shot = IconButton("📷", "Capturar región de pantalla (OCR o visión)")
        self._shot.clicked.connect(self.screenshotClicked)
        self._copilot = IconButton(
            "🧭", "Copiloto en tiempo real (conversación, traducción, desafíos)"
        )
        self._copilot.clicked.connect(self.copilotClicked)
        self._menu = IconButton("⚙", "Opciones (opacidad, copiar, reiniciar)")
        self._menu.clicked.connect(self.menuClicked)
        self._min = IconButton("—", "Minimizar")
        self._min.clicked.connect(self.minimizeClicked)
        self._close = IconButton("✕", "Cerrar", "closeBtn")
        self._close.clicked.connect(self.closeClicked)

        for button in (
            self._pin,
            self._copy,
            self._clear,
            self._stop,
            self._models,
            self._shot,
            self._copilot,
            self._menu,
            self._min,
            self._close,
        ):
            layout.addWidget(button)

    # ------------------------------------------------------------------
    def set_status(self, ok: bool | None, tooltip: str) -> None:
        """None = desconocido, True = conectado, False = sin conexión."""
        if ok is None:
            self.dot.setStyleSheet("color: #8a93a5;")
        elif ok:
            self.dot.setStyleSheet("color: #3ddc97;")
        else:
            self.dot.setStyleSheet("color: #ff6b6b;")
        self.dot.setToolTip(tooltip)

    def set_model(self, name: str) -> None:
        self.model.setText(name)

    def set_generating(self, generating: bool) -> None:
        self._stop.setVisible(generating)

    # ------------------------------------------------------------------
    # Arrastre manual: nunca activa la ventana (mantiene el foco del navegador).
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            child = self.childAt(event.position().toPoint())
            if isinstance(child, QToolButton):
                super().mousePressEvent(event)
                return
            self._dragging = True
            self._drag_offset = (
                event.globalPosition().toPoint()
                - self.window().frameGeometry().topLeft()
            )

    def mouseMoveEvent(self, event) -> None:
        if self._dragging and self._drag_offset is not None:
            self.window().move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event) -> None:
        was_dragging = self._dragging
        self._dragging = False
        self._drag_offset = None
        super().mouseReleaseEvent(event)
        if was_dragging:
            window = self.window()
            if hasattr(window, "on_window_moved"):
                window.on_window_moved()

    def mouseDoubleClickEvent(self, event) -> None:
        super().mouseDoubleClickEvent(event)


# ---------------------------------------------------------------------- #
class MessageHeader(QFrame):
    def __init__(
        self, role: str, model: str = "", parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 4)
        layout.setSpacing(8)
        role_label = QLabel(role)
        role_label.setObjectName("msgRole")
        layout.addWidget(role_label)
        if model:
            model_label = QLabel(model)
            model_label.setObjectName("msgTime")
            layout.addWidget(model_label)
        layout.addStretch(1)
        time_label = QLabel(_now_hhmm())
        time_label.setObjectName("msgTime")
        layout.addWidget(time_label)


class UserMessage(QFrame):
    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("userMessage")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)
        layout.addWidget(MessageHeader("TÚ"))
        # Las consultas pueden traer LaTeX (p. ej. el resultado del OCR
        # con fórmulas): se muestra en matemática Unicode legible.
        content = QLabel(to_human_math(text))
        content.setObjectName("msgContent")
        content.setWordWrap(True)
        content.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(content)


class SystemNote(QFrame):
    def __init__(
        self, text: str, error: bool = False, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("noteMessage")
        if error:
            self.setProperty("error", True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        content = QLabel(text)
        content.setObjectName("msgContent")
        content.setWordWrap(True)
        layout.addWidget(content)


class AssistantMessage(QFrame):
    """Respuesta del modelo: texto progresivo + Markdown final.

    Durante el streaming se muestra el texto ya convertido a matemática
    Unicode (re-render coalescido); al terminar se renderiza con
    Markdown (código, listas, títulos) sobre el texto convertido, de
    modo que el LaTeX crudo nunca es lo que el usuario lee o copia.
    """

    def __init__(
        self, model: str = "", parent: QWidget | None = None, role: str = "OLLAMA"
    ) -> None:
        super().__init__(parent)
        self.setObjectName("assistantMessage")
        self._parts: list[str] = []
        self._final_text = ""
        self._last_shown = ""
        self._stream_pending = False
        self._finalized = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)
        layout.addWidget(MessageHeader(role, model))

        self.browser = QTextBrowser()
        self.browser.setObjectName("assistantText")
        self.browser.setOpenExternalLinks(True)
        self.browser.setOpenLinks(False)
        self.browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.browser.setFrameShape(QFrame.Shape.NoFrame)
        self.browser.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.browser.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        layout.addWidget(self.browser)

        self._document().contentsChange.connect(self._schedule_height_update)
        self._height_pending = False

    def _document(self):
        return self.browser.document()

    # ------------------------------------------------------------------
    def append_streaming(self, delta: str) -> None:
        """Acumula el fragmento y agenda un re-render Unicode.

        La conversión se hace sobre el texto acumulado (un comando LaTeX
        puede llegar partido entre fragmentos) y coalescida con
        ``QTimer.singleShot(0)``: varios fragmentos del mismo ciclo de
        eventos producen un solo re-render.
        """
        self._parts.append(delta)
        if self._stream_pending:
            return
        self._stream_pending = True
        QTimer.singleShot(0, self, self._render_streaming)

    def _render_streaming(self) -> None:
        self._stream_pending = False
        if self._finalized:
            return  # finalize ya dejó el texto definitivo (Markdown)
        converted = to_human_math("".join(self._parts))
        if converted == self._last_shown:
            return
        self._last_shown = converted
        self.browser.setPlainText(converted)
        # contentsChange ya agenda la actualización de altura.

    def finalize(self) -> str:
        """Renderiza con Markdown (ya en Unicode) y devuelve el texto.

        El valor devuelto es el que copia el botón de portapapeles: se
        devuelve la versión legible, no el LaTeX crudo.
        """
        self._final_text = "".join(self._parts)
        self._finalized = True
        converted = to_human_math(self._final_text)
        self._last_shown = converted
        if self._final_text.strip():
            self.browser.setMarkdown(converted)
        else:
            self.browser.setPlainText("(respuesta vacía)")
            self.setProperty("error", True)
        self._update_height()
        return converted

    def text(self) -> str:
        return self._final_text or "".join(self._parts)

    def mark_error(self) -> None:
        self.setProperty("error", True)
        style = self.style()
        if style is not None:
            style.unpolish(self)
            style.polish(self)

    # ------------------------------------------------------------------
    def resizeEvent(self, event) -> None:
        """Recalcula la altura cuando cambia el ancho (el texto se re-envuelve)."""
        super().resizeEvent(event)
        self._schedule_height_update()

    def _schedule_height_update(self) -> None:
        if self._height_pending:
            return
        self._height_pending = True
        QTimer.singleShot(0, self, self._update_height)

    def _update_height(self) -> None:
        self._height_pending = False
        doc = self._document()
        height = int(doc.size().height()) + 2 * int(doc.documentMargin()) + 6
        self.browser.setFixedHeight(max(24, height))


# ---------------------------------------------------------------------- #
class ChatScroller(QObject):
    """Autoscroll con detección de intención del usuario.

    * Mientras el usuario está al final → autoscroll activo.
    * Si sube para leer → se suspende hasta que vuelva al final.
    """

    AT_BOTTOM_TOLERANCE = 12

    def __init__(self, bar: QScrollBar, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bar = bar
        self.enabled = True
        self._programmatic = False
        self._pending = False
        bar.valueChanged.connect(self._on_value_changed)

    def _on_value_changed(self, value: int) -> None:
        if self._programmatic:
            return
        self.enabled = (self._bar.maximum() - value) <= self.AT_BOTTOM_TOLERANCE

    def ensure_bottom(self) -> None:
        if not self.enabled or self._pending:
            return
        self._pending = True
        QTimer.singleShot(0, self._apply)

    def _apply(self) -> None:
        self._pending = False
        if not self.enabled:
            return
        self._programmatic = True
        try:
            self._bar.setValue(self._bar.maximum())
        finally:
            self._programmatic = False


def make_chat_area() -> tuple[QScrollArea, QWidget, QVBoxLayout]:
    """Crea el área de chat (scroll externo + contenedor vertical)."""
    scroll = QScrollArea()
    scroll.setObjectName("chatArea")
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    container = QWidget()
    vbox = QVBoxLayout(container)
    vbox.setContentsMargins(12, 12, 6, 12)
    vbox.setSpacing(10)
    vbox.addStretch(1)
    scroll.setWidget(container)
    return scroll, container, vbox

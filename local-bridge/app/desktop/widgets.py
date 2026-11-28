"""Widgets reutilizables de la ventana flotante.

* ``IconButton``    — botones de la barra de título (glifos unicode).
* ``TitleBar``      — barra arrastrable con modelo, estado y controles.
* ``UserMessage`` / ``AssistantMessage`` / ``SystemNote`` — bloques de chat.
* ``ChatScroller``  — autoscroll con pausa/reanudación según el usuario.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QScrollBar,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..math_unicode import to_human_math
from .brand import LogoLabel
from .rich_text import RichTextBrowser
from .styles import TEXT as TEXT_COLOR

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
        from . import icons

        if glyph in icons.PATHS:
            # Icono vectorial propio (coherente en cualquier Windows).
            self.setIcon(icons.icon(glyph, checkable=object_name == "pinBtn"))
            self.setIconSize(QSize(16, 16))
        else:
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

        self.logo = LogoLabel(13)
        self.title = QLabel("UNnexor")
        self.title.setObjectName("titleLabel")

        self.model = QLabel(model_name)
        self.model.setObjectName("modelLabel")
        # Con poco ancho cede espacio el modelo, nunca el nombre de la app.
        self.model.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.model.setMinimumWidth(0)
        self.title.setMinimumWidth(self.title.sizeHint().width() + 4)

        layout.addWidget(self.dot)
        layout.addWidget(self.logo)
        layout.addWidget(self.title)
        layout.addWidget(self.model)
        layout.addStretch(1)

        self._pin = IconButton(
            "pin", "Siempre visible (encima de las demás ventanas)", "pinBtn"
        )
        self._pin.setCheckable(True)
        self._pin.toggled.connect(self.pinToggled)
        self._copy = IconButton("copy", "Copiar la última respuesta")
        self._copy.clicked.connect(self.copyClicked)
        self._clear = IconButton("new", "Nueva conversación")
        self._clear.clicked.connect(self.clearClicked)
        self._stop = IconButton("stop", "Detener la generación")
        self._stop.clicked.connect(self.stopClicked)
        self._stop.setVisible(False)
        self._models = IconButton(
            "models", "Modelos de IA (elegir, evaluar, descargar)"
        )
        self._models.clicked.connect(self.modelsClicked)
        self._models.setVisible(False)  # en el menú ☰ → Modelos de IA
        self._shot = IconButton(
            "capture", "Capturar una región de la pantalla (visión u OCR)"
        )
        self._shot.clicked.connect(self.screenshotClicked)
        self._copilot = IconButton(
            "copilot", "Copiloto en tiempo real (conversación, traducción, desafíos)"
        )
        self._copilot.clicked.connect(self.copilotClicked)
        self._menu = IconButton("menu", "Menú: configuración, herramientas y ventana")
        self._menu.clicked.connect(self.menuClicked)
        self._min = IconButton("minimize", "Minimizar a la barra de tareas")
        self._min.clicked.connect(self.minimizeClicked)
        self._close = IconButton("close", "Cerrar UNnexor", "closeBtn")
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

    def set_pinned(self, pinned: bool) -> None:
        """Refleja el estado real de «Siempre visible» sin reemitir la señal."""
        self._pin.blockSignals(True)
        self._pin.setChecked(pinned)
        self._pin.blockSignals(False)
        self._pin.setToolTip(
            "Siempre visible: ACTIVADO (clic para desactivar)"
            if pinned
            else "Siempre visible: desactivado (clic para fijar encima)"
        )

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
    def __init__(
        self, text: str, parent: QWidget | None = None, *, role: str = "TÚ"
    ) -> None:
        super().__init__(parent)
        self.setObjectName("userMessage")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)
        layout.addWidget(MessageHeader(role))
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
    """Respuesta del modelo con Markdown, fórmulas y código resaltado.

    El renderizado (``rich_text.RichRenderer``) se aplica también durante
    el streaming, coalescido cada ``STREAM_RENDER_MS`` para no bloquear la
    interfaz; las respuestas muy largas se muestran como texto durante la
    generación y se componen una sola vez al terminar. ``finalize`` devuelve
    la versión legible (fórmulas en Unicode) que copia el portapapeles.
    """

    STREAM_RENDER_MS = 140
    STREAM_RICH_LIMIT = 24_000  # caracteres

    def __init__(
        self,
        model: str = "",
        parent: QWidget | None = None,
        role: str = "OLLAMA",
        *,
        font_px: float = 14.0,
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

        self.browser = RichTextBrowser()
        self.browser.setObjectName("assistantText")
        self.browser.set_text_style(TEXT_COLOR, font_px)
        self.browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Las líneas de código largas no se parten: barra horizontal si hace falta.
        self.browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.browser.setFrameShape(QFrame.Shape.NoFrame)
        self.browser.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self.browser.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.browser.codeCopied.connect(self._on_code_copied)
        layout.addWidget(self.browser)

        self._document().contentsChange.connect(self._schedule_height_update)
        self._height_pending = False

    def _document(self):
        return self.browser.document()

    # ------------------------------------------------------------------
    def append_streaming(self, delta: str) -> None:
        """Acumula el fragmento y agenda un re-render coalescido."""
        self._parts.append(delta)
        if self._stream_pending:
            return
        self._stream_pending = True
        QTimer.singleShot(self.STREAM_RENDER_MS, self, self._render_streaming)

    def _render_streaming(self) -> None:
        self._stream_pending = False
        if self._finalized:
            return  # finalize ya dejó el texto definitivo
        text = "".join(self._parts)
        if text == self._last_shown:
            return
        self._last_shown = text
        if len(text) > self.STREAM_RICH_LIMIT:
            self.browser.setPlainText(to_human_math(text))
        else:
            self._render(text)

    def _render(self, text: str) -> None:
        try:
            self.browser.set_rich_text(text)
        except Exception:  # nunca perder la respuesta por un fallo de formato
            self.browser.setPlainText(to_human_math(text))

    def finalize(self) -> str:
        """Compone la respuesta definitiva y devuelve su versión legible."""
        self._final_text = "".join(self._parts)
        self._finalized = True
        self._last_shown = self._final_text
        if self._final_text.strip():
            self._render(self._final_text)
        else:
            self.browser.setPlainText("(respuesta vacía)")
            self.setProperty("error", True)
        self._update_height()
        return to_human_math(self._final_text)

    def text(self) -> str:
        return self._final_text or "".join(self._parts)

    def replace_text(self, text: str) -> str:
        """Sustituye el contenido (p. ej. respuesta saneada) y la finaliza."""
        self._parts = [text]
        self._finalized = False
        return self.finalize()

    def mark_error(self) -> None:
        self.setProperty("error", True)
        style = self.style()
        if style is not None:
            style.unpolish(self)
            style.polish(self)

    def _on_code_copied(self, _index: int) -> None:
        self.browser.setToolTip("Código copiado al portapapeles ✓")
        QTimer.singleShot(2500, self, lambda: self.browser.setToolTip(""))

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
        bar = self.browser.horizontalScrollBar()
        if bar is not None and doc.size().width() > self.browser.viewport().width() + 1:
            height += bar.sizeHint().height()
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

"""Panel flotante del copiloto.

* Siempre visible (configurable), sin marco, arrastrable y redimensionable.
* ``WS_EX_NOACTIVATE``: no roba el foco de la aplicación de la reunión.
* Muestra el estado (capturando, transcribiendo, generando, pausado…),
  la transcripción reciente y la sugerencia o traducción en curso.
* Es una ventana normal y visible: no se oculta de capturas de pantalla
  ni de la compartición de pantalla (ADR-0005).
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QSettings, Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizeGrip,
    QVBoxLayout,
    QWidget,
)

from ...copilot.assistant import QAItem
from ...copilot.transcription import TranscriptEvent
from ...copilot.translation import SegmentStatus, TranslationSegment
from ..frameless import enable_frameless_resize
from ..styles import ACCENT, AMBER, BORDER, GREEN, MESSAGE_QSS, MUTED, PANEL, RED, TEXT
from ..topmost import IS_WINDOWS, set_topmost
from ..widgets import AssistantMessage, ChatScroller, UserMessage, make_chat_area
from ..window_manager import apply_noactivate
from .common import transcript_line_html

MAX_HISTORY = 80  # widgets conservados (≈ 40 preguntas con su respuesta)

OVERLAY_QSS = f"""
#overlayRoot {{ background: #12141a; border: 1px solid {BORDER}; border-radius: 10px; }}
#overlayBar {{ background: {PANEL}; border-bottom: 1px solid {BORDER};
              border-top-left-radius: 10px; border-top-right-radius: 10px; }}
#overlayStatus {{ color: {MUTED}; font-size: 11px; }}
#overlayHeard {{ background: #161a24; border-bottom: 1px solid {BORDER}; }}
#overlayHeardTitle {{ color: {AMBER}; font-size: 11px; font-weight: 700;
              letter-spacing: 1px; }}
#overlayTranscript {{ color: {TEXT}; font-size: 14px; }}
#overlayQuestion {{ color: {ACCENT}; font-weight: 700; padding: 2px 8px; }}
#overlayClose:hover {{ background: #5a1f2a; border-color: {RED}; }}
QPushButton {{ background: #1d212b; color: {TEXT}; border: 1px solid {BORDER};
              border-radius: 5px; padding: 3px 8px; font-size: 11px; }}
QPushButton:hover {{ border-color: {ACCENT}; }}
QLabel {{ background: transparent; color: {TEXT}; }}
""" + MESSAGE_QSS


class CopilotOverlay(QWidget):
    pauseClicked = Signal()
    stopClicked = Signal()
    alternativeClicked = Signal(int)
    expandClicked = Signal(int)
    openMainClicked = Signal()
    pinChanged = Signal(bool)  # el usuario cambió «Siempre visible»
    listenClicked = Signal()  # 🎙 iniciar / terminar la escucha
    # El usuario cerró el panel (✕ o Alt+F4): la ventana del copiloto
    # finaliza la escucha en curso y libera el audio; la app sigue abierta.
    closeRequested = Signal()

    def __init__(self, on_top: bool = True, opacity: int = 92) -> None:
        flags = (
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        # Sin Qt.Tool: así tiene botón en la barra de tareas y al minimizarlo
        # no desaparece (WS_EX_APPWINDOW se refuerza en showEvent).
        # En Windows «Siempre visible» se aplica sobre el HWND (ver
        # desktop/topmost.py): la bandera de Qt recrearía la ventana.
        if on_top and not IS_WINDOWS:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        super().__init__(None, flags)
        self._on_top = on_top
        self.setWindowTitle("UNnexor · Copiloto")
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMinimumSize(320, 220)
        self.setStyleSheet(OVERLAY_QSS)
        self._drag: QPoint | None = None
        self._current_item: int | None = None
        self._answer: AssistantMessage | None = None
        self._recent: list[str] = []
        self._settings = QSettings("UNnexor", "CopilotOverlay")
        self._compact = False
        self._shutting_down = False

        root = QFrame(self)
        root.setObjectName("overlayRoot")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        bar = QFrame()
        bar.setObjectName("overlayBar")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(8, 4, 6, 4)
        self._dot = QLabel("●")
        self._dot.setStyleSheet(f"color: {MUTED};")
        self._status = QLabel("Preparado")
        self._status.setObjectName("overlayStatus")
        bar_layout.addWidget(self._dot)
        bar_layout.addWidget(self._status, 1)
        self._listen = QPushButton("🎙")
        self._listen.setToolTip("Escuchar ahora / terminar la escucha")
        self._listen.clicked.connect(self.listenClicked)
        self._listen.setVisible(False)
        self._pause = QPushButton("⏸")
        self._pause.setToolTip("Pausar / reanudar la captura")
        self._pause.clicked.connect(self.pauseClicked)
        self._stop = QPushButton("■")
        self._stop.setToolTip("Finalizar la sesión")
        self._stop.clicked.connect(self.stopClicked)
        self._compact_btn = QPushButton("▁")
        self._compact_btn.setToolTip("Contraer / expandir el panel")
        self._compact_btn.clicked.connect(self.toggle_compact)
        main = QPushButton("⧉")
        main.setToolTip("Abrir la ventana del copiloto")
        main.clicked.connect(self.openMainClicked)
        clear = QPushButton("🗑")
        clear.setToolTip("Vaciar el historial del panel")
        clear.clicked.connect(self.clear_history)
        self._pin = QPushButton("📌")
        self._pin.setCheckable(True)
        self._pin.setObjectName("overlayPin")
        self._pin.toggled.connect(self._on_pin_toggled)
        minimize = QPushButton("—")
        minimize.setToolTip("Minimizar a la barra de tareas")
        minimize.clicked.connect(self.showMinimized)
        close = QPushButton("✕")
        close.setObjectName("overlayClose")
        close.setToolTip(
            "Cerrar el panel (finaliza la escucha en curso; UNnexor sigue abierto)"
        )
        close.clicked.connect(self.close)
        for button in (
            self._listen,
            self._pause,
            self._stop,
            clear,
            self._compact_btn,
            main,
            self._pin,
            minimize,
            close,
        ):
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            bar_layout.addWidget(button)
        layout.addWidget(bar)

        self._body = QWidget()
        body = QVBoxLayout(self._body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        heard = QFrame()
        heard.setObjectName("overlayHeard")
        heard_layout = QVBoxLayout(heard)
        heard_layout.setContentsMargins(10, 6, 10, 8)
        heard_layout.setSpacing(2)
        self._heard_title = QLabel("🗣 LO QUE SE ESTÁ DICIENDO")
        self._heard_title.setObjectName("overlayHeardTitle")
        heard_layout.addWidget(self._heard_title)
        self._transcript = QLabel("")
        self._transcript.setObjectName("overlayTranscript")
        self._transcript.setWordWrap(True)
        self._transcript.setTextFormat(Qt.TextFormat.RichText)
        self._transcript.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        from PySide6.QtWidgets import QScrollArea

        self._heard_scroll = QScrollArea()
        self._heard_scroll.setWidgetResizable(True)
        self._heard_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._heard_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._heard_scroll.setStyleSheet("background: transparent;")
        self._heard_scroll.setWidget(self._transcript)
        heard_layout.addWidget(self._heard_scroll)
        self._heard = heard
        heard.setVisible(False)
        body.addWidget(heard)
        self._question = QLabel("")
        self._question.setObjectName("overlayQuestion")
        self._question.setWordWrap(True)
        body.addWidget(self._question)
        self._scroll, self._container, self._vbox = make_chat_area()
        self._scroller = ChatScroller(self._scroll.verticalScrollBar(), self)
        body.addWidget(self._scroll, 1)
        actions = QHBoxLayout()
        actions.setContentsMargins(8, 4, 8, 6)
        self._alt = QPushButton("↻ Otra respuesta")
        self._alt.clicked.connect(self._emit_alternative)
        self._more = QPushButton("＋ Ampliar")
        self._more.clicked.connect(self._emit_expand)
        for button in (self._alt, self._more):
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setEnabled(False)
            actions.addWidget(button)
        actions.addStretch(1)
        grip = QSizeGrip(self)
        actions.addWidget(
            grip, 0, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight
        )
        body.addLayout(actions)
        layout.addWidget(self._body, 1)

        self.setWindowOpacity(max(0.4, min(1.0, opacity / 100)))
        geometry = self._settings.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        else:
            self.resize(460, 520)
        # Redimensionado desde cualquier borde (además del agarre inferior).
        enable_frameless_resize(self)

    # ------------------------------------------------------------ ventana
    def showEvent(self, event) -> None:
        super().showEvent(event)
        try:
            apply_noactivate(int(self.winId()))
        except Exception:  # pragma: no cover - solo Windows
            pass
        self.set_on_top(self._on_top)

    def closeEvent(self, event) -> None:
        self._settings.setValue("geometry", self.saveGeometry())
        super().closeEvent(event)
        if not self._shutting_down:
            self.closeRequested.emit()

    def prepare_shutdown(self) -> None:
        """Cierre de la aplicación: no reemitir ``closeRequested``."""
        self._shutting_down = True

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() < 34:
            self._drag = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag is not None:
            self.move(event.globalPosition().toPoint() - self._drag)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag is not None:
            self._drag = None
            self._settings.setValue("geometry", self.saveGeometry())
        super().mouseReleaseEvent(event)

    def set_on_top(self, on_top: bool) -> None:
        """Aplica «Siempre visible» y refleja el estado real en el 📌."""
        # El valor pedido se guarda ANTES: sin HWND nativo, set_topmost
        # vuelve a mostrar la ventana y showEvent lo reaplica.
        self._on_top = on_top
        if self.isVisible():
            self._on_top = set_topmost(self, on_top)
        self._sync_pin()

    def is_on_top(self) -> bool:
        return self._on_top

    def _sync_pin(self) -> None:
        self._pin.blockSignals(True)
        self._pin.setChecked(self._on_top)
        self._pin.blockSignals(False)
        self._pin.setToolTip(
            "Siempre visible: ACTIVADO (clic para desactivar)"
            if self._on_top
            else "Siempre visible: desactivado (clic para fijar encima)"
        )

    def _on_pin_toggled(self, checked: bool) -> None:
        self.set_on_top(checked)
        self.pinChanged.emit(self._on_top)

    def set_opacity(self, value: int) -> None:
        self.setWindowOpacity(max(0.4, min(1.0, value / 100)))

    def toggle_compact(self) -> None:
        self._compact = not self._compact
        self._body.setVisible(not self._compact)
        if self._compact:
            self._expanded_height = self.height()
            self.setMinimumHeight(0)
            self.resize(self.width(), 38)
        else:
            self.setMinimumHeight(220)
            self.resize(self.width(), getattr(self, "_expanded_height", 360))

    # ------------------------------------------------------------- estado
    def set_status(self, text: str, kind: str = "info") -> None:
        color = {
            "capture": GREEN,
            "busy": AMBER,
            "error": RED,
            "listening": RED,
            "paused": MUTED,
        }.get(kind, ACCENT)
        self._dot.setStyleSheet(f"color: {color};")
        self._status.setText(text)

    def set_listen_available(self, available: bool) -> None:
        self._listen.setVisible(available)
        self._pause.setVisible(not available)

    def set_listening(self, state: str, text: str) -> None:
        self._listen.setText("■" if state == "listening" else "🎙")
        self._listen.setEnabled(state != "processing")
        if text:
            kind = {"listening": "listening", "processing": "busy"}.get(state, "paused")
            self.set_status(text, kind)

    def set_paused(self, paused: bool) -> None:
        self._pause.setText("▶" if paused else "⏸")
        if paused:
            self.set_status("Pausado", "paused")

    # ------------------------------------------------------ conversación
    def show_transcript(self, event: TranscriptEvent) -> None:
        """Últimas frases: las definitivas en claro y la parcial en cursiva."""
        if not event.text:
            return
        line = transcript_line_html(event.source.speaker_label, event.text)
        if event.final:
            self._recent.append(line)
            self._recent = self._recent[-3:]
            shown = list(self._recent)
        else:
            shown = self._recent[-2:] + [
                transcript_line_html(
                    event.source.speaker_label, event.text, partial=True
                )
            ]
        self._heard.setVisible(True)
        self._transcript.setText("".join(shown))
        self._fit_heard()

    def _fit_heard(self) -> None:
        """La transcripción ocupa como mucho ~40 % del panel; lo último, visible."""
        limit = max(90, int(self.height() * 0.4))
        wanted = self._transcript.heightForWidth(self._heard_scroll.viewport().width())
        self._heard_scroll.setFixedHeight(max(40, min(limit, wanted + 4)))
        bar = self._heard_scroll.verticalScrollBar()
        if bar is not None:
            bar.setValue(bar.maximum())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._heard.isVisible():
            self._fit_heard()

    def _append(self, widget: QWidget) -> None:
        """Añade al historial (nunca borra lo anterior; conserva los últimos
        ``MAX_HISTORY`` elementos). El autodesplazamiento se pausa si el
        usuario subió a leer."""
        self._vbox.insertWidget(self._vbox.count() - 1, widget)
        while self._vbox.count() - 1 > MAX_HISTORY:
            old = self._vbox.takeAt(0)
            stale = old.widget() if old is not None else None
            if stale is not None and stale is not self._answer:
                stale.deleteLater()
        self._scroller.ensure_bottom()

    def begin_answer(self, item: QAItem) -> None:
        self._current_item = item.id
        self._question.setText("")
        self._append(UserMessage(item.question, role="INTERLOCUTOR PREGUNTA"))
        self._answer = AssistantMessage(
            item.model or "",
            role="TU RESPUESTA · para decir en voz alta",
            font_px=15.0,
        )
        self._answer.setProperty("spoken", True)
        self._append(self._answer)
        self._alt.setEnabled(False)
        self._more.setEnabled(False)
        self.set_status("Generando respuesta…", "busy")

    def append_answer(self, item_id: int, delta: str) -> None:
        if item_id == self._current_item and self._answer is not None:
            self._answer.append_streaming(delta)
            self._scroller.ensure_bottom()

    def finish_answer(self, item: QAItem) -> None:
        if item.id != self._current_item or self._answer is None:
            return
        if item.answer:
            # La respuesta final ya viene saneada (sin «Podrías responder…»).
            if item.answer.strip() != self._answer.text().strip():
                self._answer.replace_text(item.answer)
            else:
                self._answer.finalize()
            self.set_status(f"Sugerencia lista · {item.model}", "capture")
        else:
            self._answer.mark_error()
            self.set_status(item.error or "Sin respuesta", "error")
        self._alt.setEnabled(bool(item.answer))
        self._more.setEnabled(bool(item.answer))

    # --------------------------------------------------------- traducción
    def show_translation(self, segment: TranslationSegment) -> None:
        self._current_item = None
        self._question.setText("")
        if segment.status is SegmentStatus.PROVISIONAL:
            self._heard.setVisible(True)
            self._transcript.setText(
                transcript_line_html("Original", segment.original, partial=True)
            )
            return
        if segment.status is SegmentStatus.DONE:
            widget = AssistantMessage("traducción", role="COPILOTO")
            widget.append_streaming(f"{segment.translation}\n\n_{segment.original}_")
            widget.finalize()
            self._append(widget)
            self._transcript.setText("")

    def begin_text(self, title: str) -> None:
        """Nueva entrada en streaming (p. ej. un desafío) sin borrar las previas."""
        self._current_item = None
        self._question.setText(title)
        self._append(UserMessage(title))
        self._answer = AssistantMessage("copiloto", role="COPILOTO")
        self._append(self._answer)

    def append_text(self, delta: str) -> None:
        if self._answer is not None:
            self._answer.append_streaming(delta)
            self._scroller.ensure_bottom()

    def finish_text(self, text: str, error: bool = False) -> None:
        if self._answer is None:
            self.begin_text("copiloto")
        assert self._answer is not None
        if self._answer.text().strip() != text.strip():
            self._answer.append_streaming(
                text[len(self._answer.text()) :]
                if text.startswith(self._answer.text())
                else "\n\n" + text
            )
        if error:
            self._answer.mark_error()
        else:
            self._answer.finalize()

    def show_text(self, title: str, text: str) -> None:
        self.begin_text(title)
        self.finish_text(text)

    def clear_history(self) -> None:
        self._clear_answers()
        self._question.setText("")
        self._current_item = None
        self._alt.setEnabled(False)
        self._more.setEnabled(False)

    def _clear_answers(self) -> None:
        while self._vbox.count() > 1:
            item = self._vbox.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        self._answer = None

    def _emit_alternative(self) -> None:
        if self._current_item is not None:
            self.alternativeClicked.emit(self._current_item)

    def _emit_expand(self) -> None:
        if self._current_item is not None:
            self.expandClicked.emit(self._current_item)

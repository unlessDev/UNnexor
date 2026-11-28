"""Consola del puente integrada en la aplicación (opcional).

Sustituye a la ventana de terminal que mostraba las peticiones HTTP: los
registros se capturan siempre en memoria (los últimos ``MAX_LINES``) y en
``bridge.log``, pero la ventana solo aparece si el usuario la abre desde el
menú ⚙ o marca "Mostrar al iniciar". Oculta no tiene ventana ni icono en la
barra de tareas y no afecta al servidor.
"""

from __future__ import annotations

import logging
import os
import threading
from collections import deque

from PySide6.QtCore import QObject, QSettings, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont, QGuiApplication, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import paths
from ..banner import render as render_banner
from .brand import LogoLabel
from .ui_kit import fit_to_screen

MAX_LINES = 3000
_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


class _Emitter(QObject):
    line = Signal(str)


class LogBuffer(logging.Handler):
    """Handler en memoria; emite cada línea nueva como señal Qt."""

    def __init__(self, max_lines: int = MAX_LINES) -> None:
        super().__init__(level=logging.DEBUG)
        self.setFormatter(logging.Formatter(_FORMAT, "%H:%M:%S"))
        self._lines: deque[str] = deque(maxlen=max_lines)
        self._lock = threading.Lock()
        self.emitter = _Emitter()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = self.format(record)
        except Exception:  # pragma: no cover - nunca romper el logging
            return
        with self._lock:
            self._lines.append(text)
        try:
            self.emitter.line.emit(text)
        except RuntimeError:  # pragma: no cover - QObject destruido al salir
            pass

    def snapshot(self) -> list[str]:
        with self._lock:
            return list(self._lines)


_buffer: LogBuffer | None = None


def install_log_buffer() -> LogBuffer:
    """Engancha (una vez) el buffer al logger raíz."""
    global _buffer
    if _buffer is None:
        _buffer = LogBuffer()
        logging.getLogger().addHandler(_buffer)
    return _buffer


class LogConsoleWindow(QWidget):
    """Ventana de registros: peticiones HTTP, estado de Ollama, errores."""

    SETTINGS_KEY = "logConsole/showAtStart"

    def __init__(self, buffer: LogBuffer, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("UNnexor · Consola del puente")
        fit_to_screen(self, QSize(940, 580), QSize(560, 320))
        self.setObjectName("logConsole")
        self.setStyleSheet(
            "#logConsole { background: #0d0f16; }"
            "QLabel { color: #c9cde0; }"
            "QPlainTextEdit { background: #07080d; color: #cfd3e6;"
            " border: 1px solid #262b36; border-radius: 6px;"
            " selection-background-color: #3a2bff; }"
            "QLineEdit { background: #11131d; color: #e8eaf2;"
            " border: 1px solid #262b36; border-radius: 6px; padding: 4px 8px; }"
            "QPushButton { background: #1b1e2b; color: #e8eaf2;"
            " border: 1px solid #262b36; border-radius: 6px; padding: 5px 10px; }"
            "QPushButton:hover { border-color: #00e5ff; }"
            "QCheckBox { color: #c9cde0; }"
        )
        self._buffer = buffer
        self._settings = QSettings("UNnexor", "Bridge")
        self._filter = ""

        header = QHBoxLayout()
        header.addWidget(LogoLabel(14))
        title = QLabel("<b>UNnexor AI Bridge</b> · registros en vivo")
        header.addWidget(title)
        header.addStretch(1)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Filtrar (p. ej. HTTP, ollama, error)…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_filter)
        self._search.setFixedWidth(260)
        header.addWidget(self._search)

        self._view = QPlainTextEdit()
        self._view.setReadOnly(True)
        self._view.setMaximumBlockCount(MAX_LINES + 40)
        self._view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        font = QFont()
        font.setFamilies(["Cascadia Mono", "Consolas", "Courier New"])
        font.setPointSize(9)
        self._view.setFont(font)

        footer = QHBoxLayout()
        self._follow = QCheckBox("Seguir el final")
        self._follow.setChecked(True)
        self._at_start = QCheckBox("Mostrar al iniciar UNnexor")
        self._at_start.setChecked(self.show_at_start())
        self._at_start.toggled.connect(
            lambda on: self._settings.setValue(self.SETTINGS_KEY, bool(on))
        )
        copy = QPushButton("Copiar")
        copy.clicked.connect(self._copy)
        clear = QPushButton("Limpiar vista")
        clear.clicked.connect(self._view.clear)
        folder = QPushButton("Carpeta de logs")
        folder.clicked.connect(self._open_folder)
        hide = QPushButton("Ocultar")
        hide.setToolTip("Oculta la consola; el puente sigue funcionando.")
        hide.clicked.connect(self.hide)
        footer.addWidget(self._follow)
        footer.addWidget(self._at_start)
        footer.addStretch(1)
        for button in (copy, clear, folder, hide):
            footer.addWidget(button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.addLayout(header)
        layout.addWidget(self._view, 1)
        layout.addLayout(footer)

        self._reload()
        buffer.emitter.line.connect(self._append)

    # ------------------------------------------------------------------
    def show_at_start(self) -> bool:
        value = self._settings.value(self.SETTINGS_KEY, False)
        return str(value).lower() in ("true", "1")

    def _matches(self, line: str) -> bool:
        return not self._filter or self._filter in line.lower()

    def _reload(self) -> None:
        self._view.clear()
        if not self._filter:
            self._view.appendPlainText(render_banner(color=False))
        for line in self._buffer.snapshot():
            if self._matches(line):
                self._view.appendPlainText(line)
        self._scroll_end()

    def _on_filter(self, text: str) -> None:
        self._filter = text.strip().lower()
        self._reload()

    def _append(self, line: str) -> None:
        if not self.isVisible() or not self._matches(line):
            return
        self._view.appendPlainText(line)
        if self._follow.isChecked():
            self._scroll_end()

    def _scroll_end(self) -> None:
        self._view.moveCursor(QTextCursor.MoveOperation.End)
        bar = self._view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self._view.toPlainText())

    def _open_folder(self) -> None:
        folder = paths.logs_dir()
        os.makedirs(folder, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def showEvent(self, event) -> None:
        # Al reabrirla se vuelca lo ocurrido mientras estaba oculta.
        self._reload()
        super().showEvent(event)

    def toggle(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()
            self.activateWindow()

"""Estilos y utilidades compartidas por las vistas del copiloto."""

from __future__ import annotations

import html

from PySide6.QtGui import QPdfWriter, QTextDocument
from PySide6.QtWidgets import QComboBox, QFrame, QLabel, QWidget

from ..styles import ACCENT, BORDER, GREEN, MUTED, PANEL, RED, TEXT

LANGUAGES = [
    ("", "Detección automática"),
    ("es", "Español"),
    ("en", "Inglés"),
    ("pt", "Portugués"),
    ("fr", "Francés"),
    ("de", "Alemán"),
    ("it", "Italiano"),
]

COPILOT_QSS = f"""
QMainWindow, #copilotRoot {{ background: #12141a; }}
QWidget {{ color: {TEXT}; font-size: 12px; }}
QLabel#pageTitle {{ font-size: 17px; font-weight: 700; color: #e8eaf2; }}
QLabel#hint {{ color: {MUTED}; font-size: 11px; }}
QLabel#statusLine {{ color: {MUTED}; font-size: 11px; }}
QListWidget#nav {{ background: {PANEL}; border: none; border-right: 1px solid {BORDER};
                   font-size: 13px; padding-top: 8px; }}
QListWidget#nav::item {{ padding: 9px 12px; }}
QListWidget#nav::item:selected {{ background: #232838; color: {ACCENT}; }}
QGroupBox {{ border: 1px solid {BORDER}; border-radius: 8px; margin-top: 14px;
             padding: 10px; background: {PANEL}; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {ACCENT}; }}
QPushButton {{ background: #1d212b; border: 1px solid {BORDER}; border-radius: 6px;
               padding: 6px 12px; }}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:disabled {{ color: #555c6b; }}
QPushButton#primary {{ background: {ACCENT}; color: #0e0f14; font-weight: 700; border: none; }}
QPushButton#primary:disabled {{ background: #2c2840; color: #6f6a8a; }}
QPushButton#danger {{ border-color: {RED}; color: {RED}; }}
QLineEdit, QPlainTextEdit, QTextBrowser, QComboBox, QSpinBox, QListWidget {{
    background: #0f1116; border: 1px solid {BORDER}; border-radius: 6px; padding: 4px; }}
QProgressBar {{ background: #0f1116; border: 1px solid {BORDER}; border-radius: 4px;
                height: 8px; text-align: center; }}
QProgressBar::chunk {{ background: {GREEN}; border-radius: 4px; }}
QFrame#card {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 10px; }}
QFrame#card:hover {{ border-color: {ACCENT}; }}
QCheckBox {{ spacing: 6px; }}
"""


def title(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("pageTitle")
    return label


def hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("hint")
    label.setWordWrap(True)
    return label


def separator() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet(f"color: {BORDER};")
    return line


def language_combo(
    include_auto: bool = True, parent: QWidget | None = None
) -> QComboBox:
    combo = QComboBox(parent)
    for code, name in LANGUAGES:
        if code or include_auto:
            combo.addItem(name, code)
    return combo


def select_data(combo: QComboBox, value: object) -> None:
    index = combo.findData(value)
    if index >= 0:
        combo.setCurrentIndex(index)


def escape(text: str) -> str:
    return html.escape(text).replace("\n", "<br>")


def export_pdf(markdown: str, path: str) -> None:
    """Markdown → PDF con el motor de texto de Qt (sin dependencias)."""
    document = QTextDocument()
    document.setMarkdown(markdown)
    writer = QPdfWriter(path)
    writer.setTitle("Extdator")
    document.print_(writer)

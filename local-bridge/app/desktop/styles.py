"""Estilos de la ventana flotante (estética terminal oscura y minimalista)."""

from __future__ import annotations

# Paleta compartida por la ventana y los diálogos (source of truth visual).
BACKGROUND = "#12141a"
PANEL = "#171a21"
BORDER = "#262b36"
TEXT = "#d6dae2"
MUTED = "#8a93a5"
ACCENT = "#8b7cff"
GREEN = "#3ddc97"
RED = "#ff6b6b"
AMBER = "#ffd166"
ORANGE = "#ff9f43"

# Fuentes monoespaciadas preferidas; Qt usa la primera disponible.
FONT_FAMILIES = [
    "Cascadia Mono",
    "Consolas",
    "DejaVu Sans Mono",
    "Courier New",
]

APP_QSS = """
* {
    font-family: "Cascadia Mono", "Consolas", "DejaVu Sans Mono", monospace;
    outline: none;
}
QMainWindow, #rootCentral {
    background: #12141a;
    border: 1px solid #262b36;
}
QLabel {
    background: transparent;
    color: #d6dae2;
}
#titleBar {
    background: #171a21;
    border-bottom: 1px solid #262b36;
}
#titleLabel {
    color: #e8eaf2;
    font-weight: 700;
    font-size: 12px;
    letter-spacing: 1px;
}
#modelLabel {
    color: #8a93a5;
    font-size: 11px;
}
#connDot {
    font-size: 11px;
}
#chatArea, #chatArea > QWidget > QWidget {
    background: transparent;
    border: none;
}
#userMessage, #assistantMessage, #noteMessage {
    border-radius: 8px;
    border: 1px solid #262b36;
}
#userMessage {
    background: #1a1e29;
    border-left: 3px solid #8b7cff;
}
#assistantMessage {
    background: #151820;
    border-left: 3px solid #3ddc97;
}
#assistantMessage[spoken="true"] {
    background: #14201b;
    border: 1px solid #2b5a45;
    border-left: 4px solid #3ddc97;
}
#assistantMessage[error="true"] {
    border-left: 3px solid #ff6b6b;
}
#noteMessage {
    background: transparent;
    border: 1px dashed #262b36;
}
#noteMessage[error="true"] {
    border: 1px solid rgba(255, 107, 107, 0.55);
    background: rgba(255, 107, 107, 0.08);
}
#msgRole {
    color: #8b7cff;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
}
#assistantMessage #msgRole { color: #3ddc97; }
#msgTime {
    color: #6b7385;
    font-size: 10px;
}
#msgContent {
    color: #d6dae2;
    font-size: 12px;
}
#assistantText {
    background: transparent;
    color: #d6dae2;
    border: none;
    font-size: 12px;
}
QToolButton {
    background: transparent;
    border: none;
    border-radius: 6px;
    color: #8a93a5;
    font-size: 13px;
    padding: 2px;
    min-width: 26px;
    min-height: 24px;
}
QToolButton:hover {
    background: #232838;
    color: #e8eaf2;
}
QToolButton#closeBtn:hover {
    background: #a33a3a;
    color: #ffffff;
}
QToolButton#pinBtn:checked {
    color: #00e5ff;
    background: #1f2147;
    border: 1px solid #3a2bff;
}
QMenu {
    background: #171a21;
    border: 1px solid #262b36;
    padding: 6px;
}
QMenu::item {
    color: #d6dae2;
    padding: 6px 18px;
    border-radius: 5px;
}
QMenu::item:selected {
    background: #262b3a;
}
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #2a3140;
    border-radius: 5px;
    min-height: 24px;
}
QScrollBar::handle:vertical:hover { background: #39445a; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal { height: 0; }
#footer {
    color: #8a93a5;
    font-size: 11px;
    padding: 4px 12px 6px;
    background: #101218;
    border-top: 1px solid #1d212b;
}
QSlider::groove:horizontal {
    height: 4px; background: #262b36; border-radius: 2px;
}
QSlider::handle:horizontal {
    width: 12px; height: 12px; margin: -4px 0;
    background: #8b7cff; border-radius: 6px;
}
QToolTip {
    background: #171a21; color: #d6dae2;
    border: 1px solid #262b36; padding: 4px;
}
"""

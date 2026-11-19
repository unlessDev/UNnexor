"""Vista previa: LaTeX crudo vs. matemática Unicode legible.

Genera un PNG con la misma respuesta matemática renderizada de las dos
formas (la anterior, con LaTeX crudo, y la actual, convertida a Unicode)
para verificar a ojo que el panel se lee y se puede copiar a mano.

Uso::

    .venv/Scripts/python.exe scripts/math_render_preview.py [salida.png]

En Windows usa el plugin ``windows`` (el ``offscreen`` dibuja cajas en
lugar de glifos); en otros sistemas, ``offscreen``. Sin argumento escribe
el PNG en %TEMP% y también imprime el texto plano convertido (lo que
recibe el botón de copiar).
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_PLATFORM", "windows")
else:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QLabel,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from app.desktop.styles import APP_QSS  # noqa: E402
from app.desktop.widgets import AssistantMessage, UserMessage  # noqa: E402

ANSWER = """\
## Resolución paso a paso

Partimos de la ecuación $x^2 - 5x + 6 = 0$ (con $a = 1$, $b = -5$ y
$c = 6$) y aplicamos la fórmula general:

$$x = \\frac{-b \\pm \\sqrt{b^2 - 4ac}}{2a}$$

Sustituyendo los coeficientes:

$$x = \\frac{5 \\pm \\sqrt{25 - 24}}{2} = \\frac{5 \\pm 1}{2}$$

De donde se obtienen las dos soluciones:

$$x_1 = \\frac{5 + 1}{2} = 3, \\qquad x_2 = \\frac{5 - 1}{2} = 2$$

**Verificación:** $3^2 - 5 \\cdot 3 + 6 = 9 - 15 + 6 = 0$ ✓
"""

OCR_QUERY = "$$\\mathrm{x}^{\\Lambda} 2+\\mathrm{y}^{\\Lambda} 2=2 5$$"


def _section(title: str) -> QLabel:
    label = QLabel(title)
    label.setStyleSheet("font-weight: 600; padding: 6px 4px 2px 4px; color: #cfd6e4;")
    return label


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv or ["preview"])
    root = QWidget()
    root.setStyleSheet(APP_QSS)
    root.resize(880, 900)
    layout = QVBoxLayout(root)
    layout.setContentsMargins(16, 12, 16, 12)
    layout.setSpacing(6)

    layout.addWidget(_section("ANTES — LaTeX crudo (lo que se veía)"))
    before = QTextBrowser()
    before.setMarkdown(ANSWER)  # ruta de render anterior
    before.setFixedHeight(265)
    layout.addWidget(before)

    layout.addWidget(_section("DESPUÉS — matemática Unicode (legible)"))
    after = AssistantMessage("qwen3:8b")
    layout.addWidget(after)

    layout.addWidget(_section("Consulta OCR con LaTeX (burbuja del usuario)"))
    layout.addWidget(UserMessage(OCR_QUERY))
    layout.addStretch(1)

    # Sin ventana visible pero con layout real (el plugin de Windows
    # resuelve las fuentes; un widget oculto no calcula el documento).
    root.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    root.show()
    app.processEvents()

    for i in range(0, len(ANSWER), 40):  # fragmentos como el streaming real
        after.append_streaming(ANSWER[i : i + 40])
    converted = after.finalize()
    app.processEvents()

    root.adjustSize()
    root.resize(880, root.sizeHint().height())
    app.processEvents()

    out = (
        sys.argv[1]
        if len(sys.argv) > 1
        else os.path.join(tempfile.gettempdir(), "extdator_math_preview.png")
    )
    if not root.grab().save(out, "PNG"):
        print("ERROR: no se pudo guardar", out)
        return 1
    print("PNG:", out, f"({os.path.getsize(out)} bytes)")
    print("--- texto que recibe el portapapeles ---")
    print(converted)
    print("--- fin ---")
    return 0


if __name__ == "__main__":
    sys.exit(main())

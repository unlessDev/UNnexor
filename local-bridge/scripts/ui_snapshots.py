"""Capturas de las ventanas rediseñadas con datos reales (verificación visual).

Uso (desde local-bridge/):
    .venv\\Scripts\\python.exe scripts\\ui_snapshots.py [carpeta_salida]

Abre, con el Ollama y el hardware reales del equipo, el centro de control,
la ventana «Modelos de IA», «Recomendar configuración», el panel flotante
del copiloto (con transcripción y respuesta de ejemplo) y una respuesta con
fórmulas y código; las captura en dos tamaños (amplio y compacto) y guarda
PNG. No descarga ni cambia nada: solo lee el estado.
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QApplication, QWidget

SAMPLE = (
    "## Fórmula general\n\nPara $ax^2+bx+c=0$:\n\n"
    "$$x = \\frac{-b \\pm \\sqrt{b^2-4ac}}{2a}$$\n\n"
    "Sistema: \\begin{cases} 2x + y = 5 \\\\ x - y = 1 \\end{cases}\n\n"
    "```python\ndef raices(a, b, c):\n    d = (b**2 - 4*a*c) ** 0.5\n"
    "    return (-b + d) / (2*a), (-b - d) / (2*a)\n```\n"
)


def pump(app: QApplication, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


def grab(widget: QWidget, out: Path, name: str, sizes: list[QSize], app) -> None:
    for size in sizes:
        widget.resize(size)
        pump(app, 0.6)
        path = out / f"{name}-{size.width()}x{size.height()}.png"
        widget.grab().save(str(path))
        print("captura:", path)


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp())
    out.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv)
    from app.config import load_config
    from app.desktop.copilot.overlay import CopilotOverlay
    from app.desktop.dialogs import ModelSelectorDialog
    from app.desktop.recommend_dialog import RecommendationDialog
    from app.desktop.widgets import AssistantMessage
    from app.ollama_client import OllamaClient
    from app.paths import env_file

    cfg = load_config(env_file())
    ollama = OllamaClient(cfg)

    models = ModelSelectorDialog(ollama, cfg.ollama_model)
    models.show()
    pump(app, 12)
    grab(models, out, "modelos", [QSize(1000, 760), QSize(720, 520)], app)
    models.close()

    rec = RecommendationDialog(cfg, ollama, apply_main=lambda _c: None)
    rec.show()
    pump(app, 20)
    grab(rec, out, "recomendar", [QSize(1060, 820), QSize(720, 560)], app)
    rec.done(0)

    from app.audio.types import SourceKind
    from app.copilot.assistant import QAItem
    from app.copilot.transcription import TranscriptEvent

    overlay = CopilotOverlay(on_top=False)
    overlay.show()
    for index, (text, final) in enumerate(
        (
            ("Buenas, gracias por venir.", True),
            (
                "¿Podrías explicarme la diferencia entre una función y un "
                "procedimiento?",
                True,
            ),
            ("y si tienes un ejemplo", False),
        )
    ):
        overlay.show_transcript(
            TranscriptEvent(index, SourceKind.SYSTEM, text, final, 0.0, 1.0)
        )
    item = QAItem(
        id=1,
        question="¿Diferencia entre función y procedimiento?",
        origin="auto",
        created_at=time.time(),
    )
    overlay.begin_answer(item)
    answer = (
        "Una función devuelve un valor como resultado de su ejecución, mientras "
        "que un procedimiento ejecuta una serie de instrucciones sin que tenga que "
        "devolver nada. La diferencia está en el propósito y en cómo se usa el resultado."
    )
    overlay.append_answer(1, answer)
    item.answer, item.model = answer, "qwen3:8b"
    overlay.finish_answer(item)
    grab(overlay, out, "copiloto-panel", [QSize(460, 520), QSize(360, 420)], app)
    overlay.prepare_shutdown()
    overlay.close()

    message = AssistantMessage(model="qwen3:8b")
    message.setStyleSheet("background:#151820;")
    message.append_streaming(SAMPLE)
    message.finalize()
    message.show()
    grab(message, out, "respuesta", [QSize(560, 520), QSize(380, 620)], app)
    print("Capturas en", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

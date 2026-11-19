"""Smoke test GUI offscreen: abre los diálogos nuevos con QApplication real.

Verifica FASE 11 (integración) sin pantalla: los diálogos se construyen,
se muestran, procesan eventos y se cierran sin excepciones. No es una
prueba de interacción humana: es una prueba de que el código Qt no truena.

Uso:
    QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe scripts/gui_smoke.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Ofscreen ANTES de importar PySide6.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QCoreApplication, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.config import Config  # noqa: E402
from app.ollama_client import OllamaClient  # noqa: E402
from app.pairing import PairingManager  # noqa: E402
from app.desktop.dialogs import (  # noqa: E402
    ModelSelectorDialog,
    PairingDialog,
    SettingsDialog,
)
from app.desktop.providers_dialog import ProvidersDialog  # noqa: E402
from app.desktop.screenshot import ScreenshotPreviewDialog  # noqa: E402
from PySide6.QtGui import QPixmap, QColor, QPainter  # noqa: E402

FAILURES: list[str] = []


def check(name: str, fn) -> None:
    try:
        fn()
        print(f"  [ok] {name}")
    except Exception as exc:  # noqa: BLE001
        FAILURES.append(f"{name}: {exc!r}")
        print(f"  [FAIL] {name}: {exc!r}")


def exercise(dialog) -> None:
    """show + processEvents + close, con timeout de seguridad."""
    dialog.show()
    for _ in range(5):
        QCoreApplication.processEvents()
    dialog.close()


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    cfg = Config()
    ollama = OllamaClient(cfg)
    pairing = PairingManager(cfg.token or "smoke-token")

    print("Smoke test GUI (offscreen) — Extdator")

    def model_selector():
        dlg = ModelSelectorDialog(ollama, current_model=cfg.ollama_model)
        exercise(dlg)
        # Deja morir el HardwareWorker para no colgar el proceso.
        worker = getattr(dlg, "_hardware_worker", None)
        if worker is not None and worker.isRunning():
            worker.wait(3000)

    def settings_dialog():
        dlg = SettingsDialog(cfg)
        exercise(dlg)

    def providers_dialog():
        dlg = ProvidersDialog()
        exercise(dlg)

    def pairing_dialog():
        dlg = PairingDialog(pairing)
        exercise(dlg)
        dlg._timer.stop()

    def preview_dialog():
        pixmap = QPixmap(200, 140)
        pixmap.fill(QColor("#171a21"))
        painter = QPainter(pixmap)
        painter.drawText(10, 60, "x^2 = 9  =>  x = ±3")
        painter.end()
        dlg = ScreenshotPreviewDialog(
            pixmap,
            vision_available=False,
            ocr_available=False,
            model_name=cfg.ollama_model,
        )
        exercise(dlg)

    check("ModelSelectorDialog", model_selector)
    check("SettingsDialog", settings_dialog)
    check("ProvidersDialog", providers_dialog)
    check("PairingDialog", pairing_dialog)
    check("ScreenshotPreviewDialog", preview_dialog)

    # Un temporizador para salir limpio (los QThread QThreadPool pueden
    # retener el evento loop un instante).
    QTimer.singleShot(200, app.quit)
    app.exec()

    if FAILURES:
        print(f"\n{len(FAILURES)} fallo(s):")
        for f in FAILURES:
            print(" -", f)
        return 1
    print("\nTodos los diálogos abrieron y cerraron sin excepciones.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

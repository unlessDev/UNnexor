"""Prueba real de la interfaz del copiloto (Windows, ventanas visibles).

Abre la ventana del copiloto, inicia una sesión de conversación con el
botón de la página, reproduce una pregunta TTS por los altavoces y espera
la sugerencia en el panel flotante. Guarda capturas de la ventana y del
panel en la carpeta indicada. Ajustes y perfil en un directorio temporal.

Uso:
    .venv\\Scripts\\python.exe scripts\\copilot_ui_e2e.py --out %TEMP%\\copilot_ui
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtWidgets import QApplication
from tts_samples import synthesize_lines

from app.config import Config
from app.copilot.settings import SettingsRepository
from app.desktop.copilot.controller import CopilotController
from app.desktop.copilot.window import CopilotWindow
from app.knowledge.profiles import ContextProfile, Detail, ProfileStore
from app.knowledge.service import KnowledgeBase
from app.providers.consent import ConsentStore
from app.providers.usage import UsageLedger

QUESTION = [
    "Bueno, empecemos.",
    "Cuéntame sobre un proyecto en el que hayas tenido que aprender una "
    "tecnología nueva muy rápido.",
]


def pump(app: QApplication, seconds: float, until=lambda: False) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if until():
            return True
        time.sleep(0.02)
    return until()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model", default="qwen3:8b")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    app = QApplication(sys.argv)
    tmp = Path(tempfile.mkdtemp(prefix="unnexor_ui_e2e_"))
    repo = SettingsRepository(tmp / "settings.json")
    settings = repo.load()
    settings.stt_engine = "faster-whisper:base"
    settings.capture_system = True
    settings.capture_microphone = False
    settings.strategy = "selected_only"
    settings.pinned = {"answer": f"ollama:{args.model}"}
    profiles = ProfileStore(tmp / "profiles")
    profile = profiles.save(
        ContextProfile(
            name="Entrevista de prueba",
            role="Desarrolladora backend",
            about="Aprendí Go en dos semanas para reescribir un servicio de "
            "notificaciones con alta concurrencia.",
            detail=Detail.BRIEF,
        )
    )
    settings.active_profile = profile.id
    repo.save(settings)
    cfg = Config()
    controller = CopilotController(
        cfg,
        settings_repo=repo,
        profiles=profiles,
        knowledge=KnowledgeBase(tmp / "kb.sqlite3"),
        consents=ConsentStore(tmp / "consents.json"),
        ledger=UsageLedger(tmp / "usage.json"),
    )
    window = CopilotWindow(cfg, controller, register_hotkeys=True)
    window.show()
    window.open_page("conversation")
    pump(app, 1.5)
    done: list[object] = []
    controller.answerDone.connect(done.append)
    window.conversation.start()
    print("sesión:", controller.mode, "| atajos con problemas:", window.hotkey_problems)
    pump(app, 18)  # carga del motor de voz y precarga del modelo
    with tempfile.TemporaryDirectory() as wav_dir:
        wav = Path(wav_dir) / "q.wav"
        synthesize_lines(QUESTION, "es", wav)
        import winsound

        player = threading.Thread(
            target=winsound.PlaySound, args=(str(wav), winsound.SND_FILENAME)
        )
        started = time.monotonic()
        player.start()
        ok = pump(app, 150, until=lambda: bool(done))
        player.join()
    elapsed = time.monotonic() - started
    pump(app, 1.0)
    window.grab().save(str(args.out / "copilot_window.png"))
    window.overlay.grab().save(str(args.out / "copilot_overlay.png"))
    item = done[0] if done else None
    print("respuesta recibida:", ok, f"({elapsed:.1f} s desde el inicio del audio)")
    if item is not None:
        print("pregunta:", item.question.encode("ascii", "backslashreplace").decode())  # type: ignore[attr-defined]
        print("modelo:", item.model, "| estado:", item.status.value)  # type: ignore[attr-defined]
        print("respuesta:", item.answer[:300].encode("ascii", "backslashreplace").decode())  # type: ignore[attr-defined]
    print(
        "transcripción:",
        window.conversation.transcript.toPlainText()
        .encode("ascii", "backslashreplace")
        .decode(),
    )
    controller.stop_session()
    pump(app, 1.0)
    window.shutdown()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

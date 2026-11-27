"""Prueba real de la escucha temporizada del copiloto (Windows + Ollama).

Un monólogo TTS largo (contexto + pregunta al final) se reproduce por los
altavoces mientras el copiloto escucha durante una ventana temporizada con
el controlador real de la aplicación: captura WASAPI loopback, Silero VAD,
Whisper local, cierre de la ventana, espera del STT y respuesta del modelo
LOCAL de Ollama. Sin datos personales ni proveedores remotos.

Uso:
    .venv\\Scripts\\python.exe scripts\\listen_window_e2e.py --seconds 30
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tts_samples import synthesize_lines

MONOLOGUE = [
    "Bueno, antes de nada te pongo en contexto sobre el equipo.",
    "Somos cinco personas, trabajamos con Python y desplegamos en la nube "
    "cada dos semanas, y últimamente tenemos bastantes incidencias en "
    "producción que nadie detecta hasta que los clientes se quejan.",
    "Además, los registros están repartidos en varios servicios y cuesta "
    "mucho seguir una petición de principio a fin.",
    "Entonces, mi pregunta es: ¿qué harías en tu primer mes para mejorar la "
    "observabilidad del sistema?",
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=30)
    parser.add_argument("--stt", default="faster-whisper:base")
    parser.add_argument("--timeout", type=float, default=240)
    args = parser.parse_args()

    from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication

    from app.config import load_config
    from app.copilot.settings import SettingsRepository
    from app.desktop.copilot.controller import CopilotController
    from app.env_setup import ensure_env_file
    from app.knowledge.profiles import ProfileStore
    from app.knowledge.service import KnowledgeBase
    from app.providers.consent import ConsentStore
    from app.providers.usage import UsageLedger

    app = QApplication.instance() or QApplication(sys.argv[:1])
    env_path, _token, _created = ensure_env_file()
    cfg = load_config(env_path)
    tmp = Path(tempfile.mkdtemp(prefix="unnexor-listen-"))
    controller = CopilotController(
        cfg,
        settings_repo=SettingsRepository(tmp / "settings.json"),
        profiles=ProfileStore(tmp / "profiles"),
        knowledge=KnowledgeBase(tmp / "kb.sqlite3"),
        consents=ConsentStore(tmp / "consents.json"),  # sin consentimientos remotos
        ledger=UsageLedger(tmp / "usage.json"),
    )
    s = controller.settings
    s.stt_engine = args.stt
    s.stt_language = "es"
    s.capture_system = True
    s.capture_microphone = False  # nunca el micrófono en esta prueba
    s.listen_mode = "timed"
    s.listen_seconds = args.seconds
    controller.save_settings(s)

    report: dict = {"seconds": args.seconds, "states": [], "transcripts": []}
    t0 = time.monotonic()
    done = threading.Event()

    def stamp() -> float:
        return round(time.monotonic() - t0, 2)

    controller.listeningChanged.connect(
        lambda state, text: report["states"].append([stamp(), state, text])
    )
    controller.transcript.connect(
        lambda ev: ev.final
        and ev.text
        and report["transcripts"].append([stamp(), ev.text])
    )
    controller.question.connect(
        lambda item: report.update(window_text=item.question, asked_at=stamp())
    )

    def on_done(item) -> None:
        report.update(
            answer=item.answer, error=item.error, answered_at=stamp(), model=item.model
        )
        done.set()

    controller.answerDone.connect(on_done)
    controller.error.connect(lambda m: report.setdefault("errors", []).append(m))

    wav = tmp / "monologo.wav"
    synthesize_lines(MONOLOGUE, "es", wav)

    controller.start_session("conversation")
    report["pipeline_paused_while_idle"] = True
    controller.begin_listening()
    report["listening_started_at"] = stamp()

    def play() -> None:
        import winsound

        time.sleep(1.0)
        winsound.PlaySound(str(wav), winsound.SND_FILENAME)
        report["audio_finished_at"] = stamp()

    threading.Thread(target=play, daemon=True).start()

    deadline = time.monotonic() + args.timeout
    loop = QEventLoop()
    timer = QTimer()
    timer.setInterval(100)
    timer.timeout.connect(
        lambda: (done.is_set() or time.monotonic() > deadline) and loop.quit()
    )
    timer.start()
    loop.exec()
    controller.stop_session()
    controller.shutdown()
    QCoreApplication.processEvents()
    ok = bool(report.get("answer")) and "observabilidad" in (
        report.get("window_text", "").lower()
    )
    report["ok"] = ok
    print(json.dumps(report, ensure_ascii=True, indent=2))
    del app
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

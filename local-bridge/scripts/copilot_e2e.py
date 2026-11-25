"""Prueba real del copiloto de conversación (Windows + Ollama).

Una locución TTS hace de interlocutor: se reproduce por los altavoces, se
captura por WASAPI loopback, se transcribe y el asistente sugiere una
respuesta con un modelo LOCAL de Ollama. No usa audio ni datos personales.

Uso:
    .venv\\Scripts\\python.exe scripts\\copilot_e2e.py --model qwen3:8b
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

from app.audio.capture import SoundCardBackend
from app.audio.pipeline import AudioPipeline, SourceSelection
from app.audio.types import SourceKind
from app.config import Config
from app.copilot.assistant import AssistantCallbacks, ConversationAssistant, QAItem
from app.copilot.engine import RoutedEngine
from app.copilot.settings import CopilotSettings
from app.copilot.transcription import TranscriptEvent, TranscriptionService
from app.knowledge.profiles import ContextProfile, Detail, Tone
from app.ollama_client import OllamaClient
from app.routing.router import RoutingPolicy, Strategy, Task
from app.stt.factory import create_recognizer

QUESTION = [
    "Buenos días, gracias por venir.",
    "Podrías contarme cuál fue tu experiencia más difícil trabajando en equipo "
    "y cómo la resolviste?",
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--stt", default="faster-whisper:base")
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()

    cfg = Config()
    cfg.ollama_think = False  # respuestas rápidas: sin razonamiento visible
    cfg.ollama_keep_alive = "10m"
    ollama = OllamaClient(cfg)
    settings = CopilotSettings(strategy=Strategy.LOCAL_FIRST.value)
    profile = ContextProfile(
        name="Prueba",
        role="Desarrollador backend",
        about="Cinco años con Python. Coordiné un equipo de cuatro personas en "
        "una migración a microservicios con plazos ajustados.",
        goal="Entrevista para un puesto de líder técnico.",
        tone=Tone.PROFESSIONAL,
        detail=Detail.BRIEF,
    )
    engine = RoutedEngine(
        ollama,
        policy=lambda: RoutingPolicy(
            Strategy.SELECTED_ONLY,
            pinned={Task.ANSWER: f"ollama:{args.model}"},
            selected=frozenset({f"ollama:{args.model}"}),
        ),
        remote_models=lambda: {},
        has_key=lambda _p: False,
    )
    timeline: dict[str, float] = {}
    done = threading.Event()
    answers: list[QAItem] = []
    transcripts: list[str] = []
    started = time.perf_counter()

    def mark(name: str) -> None:
        timeline.setdefault(name, round(time.perf_counter() - started, 2))

    def on_done(item: QAItem) -> None:
        mark("answer_done")
        answers.append(item)
        done.set()

    assistant = ConversationAssistant(
        engine,
        settings=lambda: settings,
        profile=lambda: profile,
        callbacks=AssistantCallbacks(
            on_question=lambda item: mark("question_detected"),
            on_answer_chunk=lambda _i, _d: mark("first_token"),
            on_answer_done=on_done,
        ),
    )

    def on_transcript(event: TranscriptEvent) -> None:
        if event.final and event.text:
            transcripts.append(event.text)
            mark("first_final_transcript")
        assistant.handle_transcript(event)

    stt = TranscriptionService(
        lambda: create_recognizer(args.stt), on_transcript, language="es"
    )
    pipeline = AudioPipeline(
        SoundCardBackend(),
        [SourceSelection(SourceKind.SYSTEM)],
        stt.submit,
        lambda _s: None,
    )
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "q.wav"
        synthesize_lines(QUESTION, "es", wav)
        stt.start()
        assistant.start()
        pipeline.start()
        # Precarga del modelo de lenguaje al iniciar la sesión (en frío
        # puede tardar >20 s); en la aplicación se hace en segundo plano.
        warm = time.perf_counter()
        warm_msg = engine.warm_up(Task.ANSWER)
        timeline["warm_up_s"] = round(time.perf_counter() - warm, 2)
        print("precarga:", warm_msg.encode("ascii", "backslashreplace").decode())
        time.sleep(1.5)  # carga del modelo de voz y apertura del dispositivo
        import winsound

        started = time.perf_counter()
        winsound.PlaySound(str(wav), winsound.SND_FILENAME)
        mark("audio_finished")
        done.wait(args.timeout)
    pipeline.stop()
    stt.stop()
    assistant.stop()
    ollama.close()
    item = answers[0] if answers else None
    report = {
        "stt": args.stt,
        "model": args.model,
        "timeline_s": timeline,
        "transcripts": transcripts,
        "question": item.question if item else None,
        "status": item.status.value if item else "timeout",
        "answer_model": item.model if item else None,
        "answer": item.answer if item else None,
        "error": item.error if item else None,
    }
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0 if item and item.answer else 1


if __name__ == "__main__":
    raise SystemExit(main())

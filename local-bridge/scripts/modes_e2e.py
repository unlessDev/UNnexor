"""Prueba real de traducción en vivo y desafíos en pantalla con Ollama.

* Traducción: dos segmentos confirmados en español → inglés.
* Desafío: imagen generada con un sistema de ecuaciones → modelo con visión
  (o OCR si no hay visión). No usa capturas reales del usuario.

Uso:
    .venv\\Scripts\\python.exe scripts\\modes_e2e.py
"""

from __future__ import annotations

import base64
import importlib.util
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.audio.types import SourceKind
from app.config import Config
from app.copilot.challenges import ChallengeSolver
from app.copilot.engine import RoutedEngine
from app.copilot.transcription import TranscriptEvent
from app.copilot.translation import LiveTranslator, SegmentStatus
from app.ollama_client import OllamaClient
from app.routing.router import RoutingPolicy, Strategy

_spec = importlib.util.spec_from_file_location(
    "ocr_e2e", Path(__file__).with_name("ocr_e2e.py")
)
assert _spec and _spec.loader
_render = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_render)


def main() -> int:
    cfg = Config()
    cfg.ollama_think = False
    cfg.ollama_keep_alive = "10m"
    ollama = OllamaClient(cfg)
    engine = RoutedEngine(
        ollama,
        policy=lambda: RoutingPolicy(Strategy.QUALITY),
        remote_models=lambda: {},
        has_key=lambda _p: False,
    )
    report: dict = {}

    # ------------------------------------------------------------ traducción
    translator = LiveTranslator(engine, target=lambda: "en", source_language=lambda: "es")
    translator.start()
    started = time.perf_counter()
    for seg_id, text in enumerate(
        [
            "Buenos días a todos, empezamos la reunión con el informe del trimestre.",
            "El presupuesto aumentó un quince por ciento, sobre todo en infraestructura.",
        ],
        start=1,
    ):
        translator.handle_transcript(
            TranscriptEvent(seg_id, SourceKind.SYSTEM, text, True, seg_id * 4.0, seg_id * 4.0 + 3)
        )
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline and not all(
        s.status in (SegmentStatus.DONE, SegmentStatus.FAILED) for s in translator.segments()
    ):
        time.sleep(0.1)
    translator.stop()
    report["translation"] = {
        "seconds_total": round(time.perf_counter() - started, 2),
        "segments": [
            {"status": s.status.value, "model": s.model, "en": s.translation, "error": s.error}
            for s in translator.segments()
        ],
    }

    # --------------------------------------------------------------- desafío
    with tempfile.TemporaryDirectory() as tmp:
        image = Path(tmp) / "challenge.png"
        _render.render_math(["x^2 + y^2 = 25", "x + y = 7"], str(image))
        b64 = base64.b64encode(image.read_bytes()).decode("ascii")
    solver = ChallengeSolver(engine)
    method, explanation = solver.plan()
    started = time.perf_counter()
    first: list[float] = []
    notices: list[str] = []
    try:
        result = solver.solve(
            [b64],
            "Resuelve el sistema de ecuaciones de la imagen.",
            on_chunk=lambda _d: first.append(time.perf_counter() - started) if not first else None,
            on_notice=notices.append,
        )
        report["challenge"] = {
            "plan": explanation,
            "method": result.method,
            "model": result.model,
            "first_token_s": round(first[0], 2) if first else None,
            "seconds_total": round(time.perf_counter() - started, 2),
            "mentions_solutions": all(token in result.text for token in ("3", "4")),
            "headings": [line for line in result.text.splitlines() if line.startswith("## ")],
            "excerpt": result.text[:400],
        }
    except Exception as exc:  # se informa el fallo real
        report["challenge"] = {"plan": explanation, "method": method, "error": str(exc)}
    ollama.close()
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

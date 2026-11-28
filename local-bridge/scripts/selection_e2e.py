"""Prueba real de «Enviar directamente» y «Consultar» de punta a punta.

Recorre el mismo camino que la extensión: POST /api/selection (FastAPI con
token) → cola → ``GenerationWorker`` (QThread) → Ollama real, y comprueba
que cada envío llega con su modalidad y que el modelo responde a la
consulta (no a un envío directo). Casos: consulta informativa, técnica,
orden, consulta que requiere contexto, consulta de seguimiento y envío
directo.

Uso (con Ollama abierto):
    .venv\\Scripts\\python.exe scripts\\selection_e2e.py [modelo]
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402
from PySide6.QtCore import QCoreApplication  # noqa: E402

from app.desktop.workers import GenerationWorker  # noqa: E402
from app.ollama_client import OllamaClient  # noqa: E402
from app.queue_manager import Bus, RuntimeState  # noqa: E402
from app.server import create_app  # noqa: E402
from tests.conftest import make_config  # noqa: E402

TEXT = (
    "La fotosíntesis es el proceso por el cual las plantas convierten la luz "
    "solar, el agua y el dióxido de carbono en glucosa y oxígeno. Ocurre en los "
    "cloroplastos gracias a la clorofila."
)
CASES = [
    ("informativa", "ask", TEXT, "¿Dónde ocurre este proceso?", ["cloroplasto"]),
    ("técnica", "ask", "def f(x): return x * 2", "¿Qué devuelve f(21)?", ["42"]),
    ("orden", "ask", TEXT, "Tradúcelo al inglés en una sola frase.", ["sunlight", "carbon dioxide"]),
    ("con contexto", "ask", TEXT, "¿Qué gas se libera según el texto?", ["oxígeno"]),
    ("seguimiento", "ask", TEXT, "¿Y qué gas se consume?", ["dióxido", "co2", "co₂"]),
    ("directo", "direct", "Capital de Francia (responde con una palabra)", "", ["parís", "paris"]),
]


def main() -> int:
    model = sys.argv[1] if len(sys.argv) > 1 else "phi4-mini:3.8b"
    app = QCoreApplication(sys.argv)
    cfg = make_config(ollama_model=model, ollama_temperature=0.1)
    bus, state = Bus(), RuntimeState(model=model)
    ollama = OllamaClient(cfg)
    worker = GenerationWorker(cfg, bus, state, ollama)
    started: list[str] = []
    chunks: list[str] = []
    finished: list[dict] = []
    failed: list[str] = []
    worker.queryStarted.connect(started.append)
    worker.chunkReceived.connect(chunks.append)
    worker.generationFinished.connect(finished.append)
    worker.generationFailed.connect(lambda m, _k: failed.append(m))
    worker.start()
    client = TestClient(create_app(cfg, bus, state, ollama))
    headers = {
        "Authorization": f"Bearer {cfg.token}",
        "Origin": cfg.allowed_origins[0] if cfg.allowed_origins else "",
    }
    ok_all = True
    with client:
        for name, mode, text, question, expect in CASES:
            chunks.clear()
            before = len(finished) + len(failed)
            body = {"text": text, "mode": mode}
            if question:
                body["question"] = question
            response = client.post("/api/selection", json=body, headers=headers)
            data = response.json()
            deadline = time.monotonic() + 240
            while len(finished) + len(failed) == before and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.05)
            answer = "".join(chunks).strip()
            hit = any(word in answer.lower() for word in expect)
            mode_ok = data.get("mode") == mode and response.status_code == 202
            ok = mode_ok and hit and not failed
            ok_all &= ok
            print(
                f"[{'OK' if ok else 'FALLO'}] {name:<13} modo={data.get('mode')} "
                f"esperado={mode} · respuesta: {answer[:110]!r}"
            )
    worker.stop()
    worker.wait(5000)
    print("Historial final:", state.history_count(), "mensajes")
    print("RESULTADO:", "OK" if ok_all else "CON FALLOS", failed)
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())

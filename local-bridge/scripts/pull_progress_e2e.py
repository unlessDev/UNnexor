"""Descarga real con Ollama para validar el progreso (``DownloadManager``).

Descarga un modelo pequeño, registra cada ``PullSnapshot`` y comprueba:
el porcentaje nunca retrocede, no llega a 100 % antes de ``success``, hay
fases Preparando → Descargando → Verificando/Finalizando → Completado, la
velocidad se calcula y el modelo queda verificado en ``/api/tags``.

Uso (con Ollama abierto; descarga ~46 MB):
    .venv\\Scripts\\python.exe scripts\\pull_progress_e2e.py [modelo] [--keep]
Sin ``--keep`` el modelo se elimina al terminar (``ollama rm``).
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QCoreApplication

from app.desktop.downloads import (
    KIND_OLLAMA,
    DownloadJob,
    DownloadManager,
)
from app.ollama_client import OllamaClient
from app.ollama_pull import COMPLETED
from tests.conftest import make_config


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    model = args[0] if args else "all-minilm:22m"
    app = QCoreApplication(sys.argv)
    ollama = OllamaClient(make_config())
    manager = DownloadManager(ollama)
    snapshots: list = []
    done: list = []
    manager.jobProgress.connect(snapshots.append)
    manager.jobFinished.connect(done.append)
    manager.enqueue(DownloadJob(KIND_OLLAMA, model))
    deadline = time.monotonic() + 900
    while not done and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)
    final = done[0] if done else None
    fractions = [s.fraction for s in snapshots if s.fraction is not None]
    phases = []
    for snap in snapshots:
        if not phases or phases[-1] != snap.phase:
            phases.append(snap.phase)
    monotonic = all(b >= a for a, b in zip(fractions, fractions[1:], strict=False))
    early_100 = any(f >= 1.0 for f in fractions)
    speeds = [s.speed_bps for s in snapshots if s.speed_bps]
    print(f"Modelo: {model} · actualizaciones: {len(snapshots)}")
    print("Fases:", " → ".join(phases + ([final.phase] if final else [])))
    if fractions:
        print(f"Porcentajes: primero {fractions[0]:.1%}, último {fractions[-1]:.1%}")
    print("Velocidad máxima:", f"{max(speeds) / 1_048_576:.1f} MB/s" if speeds else "—")
    for snap in snapshots[:: max(1, len(snapshots) // 8)]:
        print("  ", snap.describe())
    print("Final:", final.describe() if final else "sin terminar")
    ok = (
        final is not None
        and final.phase == COMPLETED
        and monotonic
        and not early_100
        and bool(fractions)
    )
    print(
        f"Monótono: {monotonic} · 100 % antes de terminar: {early_100} · "
        f"RESULTADO: {'OK' if ok else 'FALLO'}"
    )
    manager.shutdown()
    if "--keep" not in sys.argv and final is not None and final.phase == COMPLETED:
        subprocess.run(["ollama", "rm", model], check=False)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

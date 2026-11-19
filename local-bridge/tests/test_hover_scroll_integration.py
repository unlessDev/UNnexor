"""Wrapper pytest de la sonda de integración de Hover Scroll.

Ejecuta la sonda en un proceso aparte (Qt necesita la plataforma real
"windows"; pytest puede estar en offscreen) y valida su JSON de salida.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="Solo aplicable a Windows"
)

PROBE = Path(__file__).resolve().parent / "integration" / "hover_scroll_probe.py"


def test_hover_scroll_and_no_activate_integration():
    completed = subprocess.run(
        [sys.executable, str(PROBE)],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(PROBE.parent.parent.parent),
    )
    output = completed.stdout.strip()
    print("--- stderr de la sonda ---")
    print(completed.stderr)
    print("--- fin stderr ---")
    try:
        json_start = output.rindex("{")
        results = json.loads(output[json_start:])
    except (ValueError, json.JSONDecodeError) as exc:
        pytest.fail(f"La sonda no imprimió JSON válido: {exc}\n{output}")
    for key, passed in results.items():
        assert passed, f"Comprobación de integración fallida: {key}"

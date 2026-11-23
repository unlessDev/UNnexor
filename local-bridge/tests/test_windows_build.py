"""Pruebas de los diagnósticos del empaquetado de escritorio."""

from __future__ import annotations

import importlib.util
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BUILD_SCRIPT = PROJECT_ROOT / "deployment" / "windows" / "build.py"

_spec = importlib.util.spec_from_file_location("windows_build", BUILD_SCRIPT)
windows_build = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(windows_build)


def test_with_log_appends_last_lines_of_bridge_log(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "bridge.log").write_text(
        "\n".join(f"linea-{index}" for index in range(60)) + "\n",
        encoding="utf-8",
    )

    message = windows_build._with_log("Auto-chequeo falló", tmp_path)

    assert message.startswith("Auto-chequeo falló\nbridge.log:\n")
    assert "linea-59" in message
    assert "linea-20" in message
    assert "linea-19" not in message


def test_with_log_without_log_keeps_original_message(tmp_path: Path) -> None:
    assert windows_build._with_log("sin log", tmp_path) == "sin log"

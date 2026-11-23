"""Pruebas del informe de auto-chequeo usado por el ejecutable empaquetado."""

from __future__ import annotations

import inspect
import json

from app import __version__, main, paths
from app.ollama_client import OLLAMA_HEALTH_TIMEOUT


def test_self_check_report_contains_paths_and_result(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    target = main.write_self_check_report({"health": {"status": "ok"}}, True)

    assert target == tmp_path / "self-check.json"
    report = json.loads(target.read_text(encoding="utf-8"))
    assert report["app"] == "extdator-bridge"
    assert report["version"] == __version__
    assert report["frozen"] is True
    assert report["data_dir"] == str(tmp_path)
    assert report["ok"] is True
    assert report["results"]["health"] == {"status": "ok"}


def test_self_check_report_records_failure(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    target = main.write_self_check_report({"error": "sin respuesta"}, False)
    assert target is not None
    report = json.loads(target.read_text(encoding="utf-8"))
    assert report["ok"] is False
    assert report["results"]["error"] == "sin respuesta"


def test_health_wait_tolerates_cold_start() -> None:
    """El arranque en frío de un EXE empaquetado no debe agotar la espera."""
    timeout = inspect.signature(main.wait_for_health).parameters["timeout"].default
    assert timeout >= 30.0


def test_health_request_timeout_covers_ollama_probe() -> None:
    """/health puede tardar lo que tarde el sondeo de Ollama; hay que esperarlo."""
    assert main.HEALTH_REQUEST_TIMEOUT > OLLAMA_HEALTH_TIMEOUT

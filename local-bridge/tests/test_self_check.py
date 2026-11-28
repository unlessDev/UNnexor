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
    assert report["app"] == "unnexor-bridge"
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


def test_server_only_runs_uvicorn_server_with_bridge_config(monkeypatch) -> None:
    """Regresión: ``--server-only`` pasaba un ``uvicorn.Config`` a
    ``uvicorn.run`` (que espera una app ASGI) e ignoraba host y puerto."""
    import uvicorn

    from app.queue_manager import Bus, RuntimeState
    from tests.conftest import make_config

    captured: dict = {}

    class FakeServer:
        def __init__(self, config) -> None:
            captured["config"] = config

        def run(self) -> None:
            captured["ran"] = True

    monkeypatch.setattr(uvicorn, "Server", FakeServer)
    cfg = make_config(host="127.0.0.1", port=5123)
    ollama = main.OllamaClient(cfg)
    try:
        assert main.run_server_only(cfg, Bus(), RuntimeState(), ollama) == 0
    finally:
        ollama.close()
    config = captured["config"]
    assert isinstance(config, uvicorn.Config)
    assert (config.host, config.port) == ("127.0.0.1", 5123)
    assert callable(config.app)
    assert captured["ran"] is True


def test_copilot_component_check_reports_failures(monkeypatch) -> None:
    import importlib

    from app.copilot import components

    real_import = importlib.import_module

    def fake_import(name: str, *args, **kwargs):
        if name == "pypdf":
            raise ImportError("No module named 'pypdf'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(components.importlib, "import_module", fake_import)
    monkeypatch.setattr(
        components, "_whisper_smoke", lambda: {"ok": True, "skipped": True}
    )
    report = components.check_components()
    assert report["pypdf"]["ok"] is False and "pypdf" in report["pypdf"]["error"]
    assert report["numpy"]["ok"] is True
    assert report["ok"] is False  # un componente ausente invalida el build

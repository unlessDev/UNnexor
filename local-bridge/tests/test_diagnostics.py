"""Pruebas del paquete de diagnóstico saneado para soporte."""

from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 no instalada")

from app import diagnostics, paths
from app.config import Config
from app.desktop import workers as desktop_workers
from app.ollama_client import OllamaError


class FakeOllama:
    def __init__(
        self,
        *,
        reachable: bool = True,
        detail: str = "0.12.3",
        models: list[str] | None = None,
        list_error: str | None = None,
    ) -> None:
        self.reachable = reachable
        self.detail = detail
        self.models = models if models is not None else ["llama3:latest"]
        self.list_error = list_error

    def is_reachable(self, timeout: float = 2.0) -> tuple[bool, str]:
        return self.reachable, self.detail

    def list_models(self) -> list[str]:
        if self.list_error:
            raise OllamaError(self.list_error)
        return self.models


class FakeState:
    def __init__(
        self,
        *,
        model: str = "llama3",
        provider: tuple[str, str] = ("ollama", ""),
        history: int = 4,
        generating: bool = False,
        uptime: int = 123,
    ) -> None:
        self.model = model
        self.provider_value = provider
        self.history = history
        self.generating = generating
        self.uptime = uptime

    def model_name(self) -> str:
        return self.model

    def provider(self) -> tuple[str, str]:
        return self.provider_value

    def history_count(self) -> int:
        return self.history

    def is_generating(self) -> bool:
        return self.generating

    def uptime_seconds(self) -> int:
        return self.uptime


class FakeBus:
    def __init__(self, pending: int = 2) -> None:
        self.pending = pending

    def qsize(self) -> int:
        return self.pending


class FakePairing:
    def __init__(self, *, confirmed: bool = False) -> None:
        self.confirmed = confirmed

    def is_confirmed(self) -> bool:
        return self.confirmed


def make_config(model: str = "llama3") -> Config:
    cfg = Config()
    cfg.token = "test-token-123"
    cfg.ollama_model = model
    return cfg


def isolate_paths(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(paths, "logs_dir", lambda: tmp_path / "logs")
    monkeypatch.setattr(paths, "settings_file", lambda: tmp_path / "user_settings.json")
    monkeypatch.setattr(paths, "providers_file", lambda: tmp_path / "providers.json")
    monkeypatch.setattr(paths, "env_file", lambda: tmp_path / ".env")


def test_redact_text_covers_secrets_and_paths() -> None:
    secret = "bridge-token-abc123"
    extension_id = "a" * 32
    text = (
        f"BRIDGE_TOKEN={secret}\n"
        "Authorization: Bearer abcdefghijklmnop\n"
        '{"api_key": "sk-super-secret-key"}\n'
        "contacto: user@example.com\n"
        f"origen: chrome-extension://{extension_id}\n"
        r"ruta: C:\Users\alice\AppData\Local\Extdator" + "\n"
        "url: https://example.com/path?token=abc123&x=1\n"
        "Selección aceptada de https://privado.example: 'texto privado'\n"
    )

    cleaned = diagnostics.redact_text(text, [secret])

    assert secret not in cleaned
    assert "abcdefghijklmnop" not in cleaned
    assert "sk-super-secret-key" not in cleaned
    assert "user@example.com" not in cleaned
    assert extension_id not in cleaned
    assert "alice" not in cleaned
    assert "token=abc123" not in cleaned
    assert "texto privado" not in cleaned
    assert diagnostics._REDACTION_MARK in cleaned


def test_snapshot_excludes_token_prompt_and_extension_id(monkeypatch, tmp_path) -> None:
    isolate_paths(monkeypatch, tmp_path)
    cfg = make_config()
    cfg.token = "super-secret-token-123"
    cfg.ollama_system_prompt = "prompt privado"
    cfg.allowed_origins = ["chrome-extension://" + "a" * 32]
    snapshot = diagnostics.collect_snapshot(
        cfg,
        state=FakeState(),
        bus=FakeBus(2),
        pairing=FakePairing(confirmed=True),
        ollama=FakeOllama(),
        log_paths=[],
    )

    dumped = json.dumps(snapshot, ensure_ascii=False)
    assert "super-secret-token-123" not in dumped
    assert "prompt privado" not in dumped
    assert "a" * 32 not in dumped
    assert snapshot["config"]["token_configured"] is True
    assert snapshot["config"]["allowed_origins_count"] == 1
    assert snapshot["pairing"]["confirmed"] is True
    assert snapshot["runtime"]["history_messages"] == 4
    assert snapshot["runtime"]["pending"] == 2
    assert snapshot["ollama"]["configured_model_installed"] is True

    statuses = {check["id"]: check["status"] for check in snapshot["checks"]}
    assert statuses["config"] == "ok"
    assert statuses["auth"] == "ok"
    assert statuses["log_privacy"] == "ok"
    assert statuses["ollama"] == "ok"
    assert statuses["model"] == "ok"
    assert statuses["logs"] == "warn"


def test_snapshot_warns_when_log_privacy_is_off_and_model_missing(
    monkeypatch, tmp_path
) -> None:
    isolate_paths(monkeypatch, tmp_path)
    cfg = make_config(model="modelo-ausente")
    cfg.log_privacy = False
    snapshot = diagnostics.collect_snapshot(
        cfg,
        ollama=FakeOllama(models=["llama3:latest"]),
        log_paths=[],
    )

    statuses = {check["id"]: check["status"] for check in snapshot["checks"]}
    assert statuses["log_privacy"] == "warn"
    assert statuses["model"] == "warn"
    assert snapshot["ollama"]["configured_model_installed"] is False


def test_snapshot_reports_ollama_listing_error(monkeypatch, tmp_path) -> None:
    isolate_paths(monkeypatch, tmp_path)
    snapshot = diagnostics.collect_snapshot(
        make_config(),
        ollama=FakeOllama(list_error="fallo al listar"),
        log_paths=[],
    )

    statuses = {check["id"]: check["status"] for check in snapshot["checks"]}
    assert statuses["ollama"] == "ok"
    assert statuses["model"] == "warn"
    assert "fallo al listar" in snapshot["ollama"]["models_error"]


def test_write_archive_sanitizes_logs_and_omits_secrets(monkeypatch, tmp_path) -> None:
    isolate_paths(monkeypatch, tmp_path)
    cfg = make_config()
    cfg.token = "token-very-secret-123"
    cfg.log_privacy = False
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "bridge.log").write_text(
        "2026-11-24 INFO extdator.server: "
        "BRIDGE_TOKEN=token-very-secret-123\n"
        "2026-11-24 INFO extdator.server: "
        "Selección aceptada de https://privado.example: 'contenido privado'\n"
        "2026-11-24 INFO extdator.main: contacto support@example.com\n"
        "2026-11-24 INFO extdator.main: ruta C:\\Users\\alice\\Extdator\n",
        encoding="utf-8",
    )
    target = tmp_path / "diagnostico.zip"

    result = diagnostics.write_diagnostics_archive(
        target,
        cfg,
        state=FakeState(),
        bus=FakeBus(0),
        pairing=FakePairing(),
        ollama=None,
        log_dir=log_dir,
        environ={"OPENAI_API_KEY": "env-secret-123"},
        now=datetime(2026, 11, 24, 10, 5, tzinfo=timezone.utc),
        check_ollama=False,
    )

    assert result == target.resolve()
    with zipfile.ZipFile(target) as archive:
        names = set(archive.namelist())
        assert {
            "LEEME.txt",
            "resumen.json",
            "diagnostico.txt",
            "logs/bridge.log",
        } <= names
        all_text = "\n".join(
            archive.read(name).decode("utf-8") for name in sorted(names)
        )
        assert "token-very-secret-123" not in all_text
        assert "env-secret-123" not in all_text
        assert "contenido privado" not in all_text
        assert "support@example.com" not in all_text
        assert "alice" not in all_text
        summary = json.loads(archive.read("resumen.json"))
        assert summary["logs"]["files_included"] == ["logs/bridge.log"]
        assert summary["logs"]["content_lines_omitted"] >= 1
        assert summary["logs"]["directory"].endswith("logs")
        assert summary["privacy"]["includes_token"] is False
        assert "AVISO: LOG_PRIVACY" in archive.read("LEEME.txt").decode("utf-8")


def test_archive_without_logs_still_contains_report(monkeypatch, tmp_path) -> None:
    isolate_paths(monkeypatch, tmp_path)
    empty_logs = tmp_path / "logs"
    empty_logs.mkdir()
    target = tmp_path / "sin-logs.zip"

    diagnostics.write_diagnostics_archive(
        target,
        make_config(),
        log_dir=empty_logs,
        check_ollama=False,
    )

    with zipfile.ZipFile(target) as archive:
        names = set(archive.namelist())
        assert "resumen.json" in names
        assert "diagnostico.txt" in names
        assert not any(name.startswith("logs/") for name in names)
        summary = json.loads(archive.read("resumen.json"))
        assert summary["logs"]["files_included"] == []
        statuses = {check["id"]: check["status"] for check in summary["checks"]}
        assert statuses["logs"] == "warn"


def test_report_and_readme_are_presentable(monkeypatch, tmp_path) -> None:
    isolate_paths(monkeypatch, tmp_path)
    snapshot = diagnostics.collect_snapshot(
        make_config(),
        ollama=FakeOllama(),
        log_paths=[],
    )
    report = diagnostics.render_report(snapshot)
    readme = diagnostics.render_readme(snapshot)

    assert "Comprobaciones" in report
    assert "Estado en memoria" in report
    assert "Extensión vinculada: desconocido" in report
    assert "None" not in report
    assert "No contiene:" in readme
    assert "token del puente: no" in readme


def test_default_archive_name_and_suffix() -> None:
    now = datetime(2026, 11, 24, 10, 5, tzinfo=timezone.utc)
    assert (
        diagnostics.default_archive_name(now)
        == "Extdator-diagnostico-20261124-1005.zip"
    )
    assert diagnostics.ensure_zip_suffix("diagnostico") == Path("diagnostico.zip")
    assert diagnostics.ensure_zip_suffix("diagnostico.ZIP") == Path("diagnostico.ZIP")


def test_diagnostics_worker_emits_ready_path(qapp, monkeypatch, tmp_path) -> None:
    isolate_paths(monkeypatch, tmp_path)
    target = tmp_path / "worker.zip"
    worker = desktop_workers.DiagnosticsWorker(
        target,
        make_config(),
        FakeState(),
        FakeBus(0),
        None,
        None,
        check_ollama=False,
        log_dir=tmp_path / "logs",
    )
    ready: list[str] = []
    failed: list[str] = []
    worker.bundleReady.connect(ready.append)
    worker.bundleFailed.connect(failed.append)

    worker.run()

    assert ready == [str(target.resolve())]
    assert failed == []
    assert target.is_file()


def test_diagnostics_worker_reports_failure(qapp, monkeypatch, tmp_path) -> None:
    def fail(*_args, **_kwargs):
        raise OSError("sin permisos")

    monkeypatch.setattr(desktop_workers, "write_diagnostics_archive", fail)
    worker = desktop_workers.DiagnosticsWorker(
        tmp_path / "fallo.zip",
        make_config(),
        FakeState(),
        FakeBus(0),
        None,
        None,
        check_ollama=False,
    )
    failed: list[str] = []
    worker.bundleFailed.connect(failed.append)

    worker.run()

    assert failed == ["sin permisos"]

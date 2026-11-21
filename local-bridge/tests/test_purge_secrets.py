"""Pruebas de la purga de secretos usada antes de desinstalar."""

from __future__ import annotations

import json
from pathlib import Path

from app import main, paths
from app.env_setup import clear_env_secrets
from app.providers import manager
from app.providers.presets import PRESETS


def test_purge_all_keys_removes_presets_orphans_and_state(monkeypatch, tmp_path):
    config_file = tmp_path / "providers.json"
    config_file.write_text(
        json.dumps(
            {
                "version": 1,
                "providers": {
                    "openai": {"connected_at": "2026-01-01", "model_count": 3},
                    "proveedor-huerfano": {"connected_at": "2026-01-02"},
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(manager, "CONFIG_FILE", config_file)
    deleted: list[str] = []
    monkeypatch.setattr(manager, "delete_key", deleted.append)

    purged = manager.purge_all_keys()

    assert set(purged) == {item["id"] for item in PRESETS} | {
        "custom",
        "proveedor-huerfano",
    }
    assert deleted == purged
    saved = json.loads(config_file.read_text(encoding="utf-8"))
    assert saved["providers"] == {}


def test_purge_all_keys_does_not_create_data_dir(monkeypatch, tmp_path):
    missing = tmp_path / "data" / "providers.json"
    monkeypatch.setattr(manager, "CONFIG_FILE", missing)
    deleted: list[str] = []
    monkeypatch.setattr(manager, "delete_key", deleted.append)

    manager.purge_all_keys()

    assert deleted  # las claves huérfanas también se intentan borrar
    assert not missing.parent.exists()


def test_clear_env_secrets_blanks_token_and_removes_api_keys(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "BRIDGE_TOKEN=token-secreto\n"
        "OLLAMA_MODEL=qwen2.5vl:7b\n"
        "OPENAI_API_KEY=sk-secreto\n"
        "GROQ_API_KEY='gsk-secreto'\n"
        "# GROQ_API_KEY=comentario conservado\n",
        encoding="utf-8",
    )

    result = clear_env_secrets(env_file)
    text = env_file.read_text(encoding="utf-8")

    assert result == {
        "found": True,
        "token_cleared": True,
        "keys_removed": ["OPENAI_API_KEY", "GROQ_API_KEY"],
        "error": "",
    }
    assert "BRIDGE_TOKEN=\n" in text
    assert "token-secreto" not in text
    assert "OPENAI_API_KEY" not in text
    active = [line for line in text.splitlines() if not line.strip().startswith("#")]
    assert not any(line.startswith("GROQ_API_KEY=") for line in active)
    assert "# GROQ_API_KEY=comentario conservado" in text
    assert "OLLAMA_MODEL=qwen2.5vl:7b" in text


def test_clear_env_secrets_missing_file_is_noop(tmp_path):
    env_file = tmp_path / "no-existe.env"

    result = clear_env_secrets(env_file)

    assert result == {
        "found": False,
        "token_cleared": False,
        "keys_removed": [],
        "error": "",
    }
    assert not env_file.exists()


def test_clear_env_secrets_read_error_is_reported(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("BRIDGE_TOKEN=token-secreto\n", encoding="utf-8")
    original_read_text = Path.read_text

    def failing_read(self, *args, **kwargs):
        if self == env_file:
            raise OSError("acceso denegado")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", failing_read)

    result = clear_env_secrets(env_file)

    assert result["found"] is True
    assert result["token_cleared"] is False
    assert result["keys_removed"] == []
    assert "acceso denegado" in result["error"]


def test_purge_secrets_cli_does_not_create_env(monkeypatch, tmp_path):
    env_file = tmp_path / "data" / ".env"
    monkeypatch.setattr(paths, "env_file", lambda: env_file)
    monkeypatch.setattr(paths, "is_frozen", lambda: False)
    monkeypatch.setattr(manager, "purge_all_keys", lambda: ["openai", "groq"])

    assert main.main(["--purge-secrets"]) == 0
    assert not env_file.exists()


def test_purge_secrets_cli_returns_error_if_env_write_fails(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    monkeypatch.setattr(paths, "env_file", lambda: env_file)
    monkeypatch.setattr(paths, "is_frozen", lambda: False)
    monkeypatch.setattr(manager, "purge_all_keys", lambda: ["openai"])
    monkeypatch.setattr(
        main,
        "clear_env_secrets",
        lambda path: {
            "found": True,
            "token_cleared": False,
            "keys_removed": [],
            "error": "no se pudo reescribir",
        },
    )

    assert main.main(["--purge-secrets"]) == 1

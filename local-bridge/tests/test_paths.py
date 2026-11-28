"""Pruebas de rutas de runtime y coherencia de versión."""

from __future__ import annotations

import json
from pathlib import Path

from app import __version__, paths


def test_development_paths_keep_historical_layout(monkeypatch):
    monkeypatch.delenv("EXTDATOR_DATA_DIR", raising=False)
    monkeypatch.delenv("UNNEXOR_DATA_DIR", raising=False)
    monkeypatch.setattr(paths, "is_frozen", lambda: False)
    assert paths.data_dir() == paths.BASE_DIR / "app" / "data"
    assert paths.logs_dir() == paths.BASE_DIR / "logs"
    assert paths.env_file() == paths.BASE_DIR / ".env"


def test_data_dir_override_is_used(monkeypatch, tmp_path):
    monkeypatch.setenv("EXTDATOR_DATA_DIR", str(tmp_path))
    assert paths.data_dir() == tmp_path
    assert paths.settings_file() == tmp_path / "user_settings.json"
    assert paths.providers_file() == tmp_path / "providers.json"


def test_frozen_paths_use_local_appdata(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("EXTDATOR_DATA_DIR", raising=False)
    monkeypatch.delenv("UNNEXOR_DATA_DIR", raising=False)
    monkeypatch.delenv("UNNEXOR_MODELS_DIR", raising=False)
    assert paths.data_dir() == tmp_path / "UNnexor"
    assert paths.logs_dir() == tmp_path / "UNnexor" / "logs"
    assert paths.env_file() == tmp_path / "UNnexor" / ".env"
    assert paths.models_dir() == tmp_path / "UNnexor" / "models"
    assert paths.legacy_data_root() == tmp_path / "Extdator"


def test_env_file_override(monkeypatch, tmp_path):
    target = tmp_path / "custom.env"
    monkeypatch.setenv("EXTDATOR_ENV_FILE", str(target))
    assert paths.env_file() == target


def test_resource_path_development():
    assert paths.resource_path(".env.example") == paths.BASE_DIR / ".env.example"


def test_manifest_version_matches_python_package():
    manifest_path = Path(paths.PROJECT_ROOT) / "browser-extension" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["version"] == __version__

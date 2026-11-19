"""Pruebas del almacenamiento centralizado de ajustes."""

from __future__ import annotations

import json

import pytest

from app.config import Config
from app.settings_store import (
    ALLOWED_KEYS,
    SettingsStore,
    SettingsValidationError,
    restart_required_for,
)


def test_load_missing_file_is_empty(tmp_path):
    store = SettingsStore(tmp_path / "user_settings.json")
    assert store.load() == {}


def test_update_persists_and_applies(tmp_path):
    store = SettingsStore(tmp_path / "user_settings.json")
    store.load()
    applied = store.update({"ollama_model": "qwen3:8b", "ollama_temperature": 0.35})
    assert set(applied) == {"ollama_model", "ollama_temperature"}

    other = SettingsStore(tmp_path / "user_settings.json")
    other.load()
    cfg = Config()
    other.apply_to(cfg)
    assert cfg.ollama_model == "qwen3:8b"
    assert cfg.ollama_temperature == 0.35


def test_unknown_key_rejected(tmp_path):
    store = SettingsStore(tmp_path / "s.json")
    store.load()
    with pytest.raises(SettingsValidationError, match="desconocido"):
        store.update({"clave_mala": "x"})
    assert store.as_dict() == {}


def test_type_and_range_validation(tmp_path):
    store = SettingsStore(tmp_path / "s.json")
    store.load()
    with pytest.raises(SettingsValidationError):
        store.update({"ollama_temperature": 3.5})
    with pytest.raises(SettingsValidationError):
        store.update({"window_opacity": "alta"})
    with pytest.raises(SettingsValidationError):
        store.update({"max_history_messages": 1})
    with pytest.raises(SettingsValidationError):
        store.update({"log_level": "RUIDO"})
    with pytest.raises(SettingsValidationError):
        store.update({"ollama_extra_options": {"x": [1, 2]}})
    with pytest.raises(SettingsValidationError):
        store.update({"ollama_model": "   "})
    assert store.as_dict() == {}


def test_valid_extra_options_and_bool(tmp_path):
    store = SettingsStore(tmp_path / "s.json")
    store.load()
    store.update(
        {
            "ollama_extra_options": {"num_ctx": 8192, "num_predict": 6144},
            "always_on_top": False,
            "log_privacy": True,
        }
    )
    assert store.get("ollama_extra_options")["num_ctx"] == 8192
    assert store.get("always_on_top") is False


def test_corrupt_file_recovered_with_backup(tmp_path):
    path = tmp_path / "user_settings.json"
    path.write_text("{esto no es json", encoding="utf-8")
    store = SettingsStore(path)
    assert store.load() == {}
    assert path.with_suffix(".json.bak").exists()


def test_unknown_keys_in_file_are_dropped(tmp_path):
    path = tmp_path / "user_settings.json"
    path.write_text(
        json.dumps({"ollama_model": "qwen3:8b", "secret_key": "x"}), encoding="utf-8"
    )
    store = SettingsStore(path)
    values = store.load()
    assert values == {"ollama_model": "qwen3:8b"}


def test_reset_clears_file(tmp_path):
    path = tmp_path / "user_settings.json"
    store = SettingsStore(path)
    store.load()
    store.update({"ollama_model": "llama3"})
    removed = store.reset()
    assert removed == {"ollama_model": "llama3"}
    assert not path.exists()
    assert store.as_dict() == {}


def test_restart_flags():
    assert "port" not in ALLOWED_KEYS or not ALLOWED_KEYS["port"]["restart"]
    assert restart_required_for(["ollama_think", "ollama_model", "ollama_host"]) == [
        "ollama_think",
        "ollama_host",
    ]
    assert restart_required_for(["ollama_temperature"]) == []


def test_invalid_saved_value_ignored_on_apply(tmp_path):
    path = tmp_path / "user_settings.json"
    path.write_text(
        json.dumps({"ollama_temperature": 99, "ollama_model": "llama3"}),
        encoding="utf-8",
    )
    store = SettingsStore(path)
    store.load()
    cfg = Config()
    applied = store.apply_to(cfg)
    assert applied == ["ollama_model"]
    assert cfg.ollama_temperature == Config.ollama_temperature  # no cambió
    assert cfg.ollama_model == "llama3"

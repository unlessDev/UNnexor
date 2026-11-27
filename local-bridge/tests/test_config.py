"""Pruebas de carga y validación de configuración."""

from __future__ import annotations

import json

import httpx
import pytest

from app.config import Config, ConfigError, load_config
from app.env_setup import ensure_env_file, generate_token
from app.ollama_client import OllamaClient


def ndjson(*objects: dict) -> bytes:
    return ("".join(json.dumps(o) + "\n" for o in objects)).encode("utf-8")


def test_defaults_are_secure(cfg):
    assert cfg.host == "127.0.0.1"
    assert cfg.auth_required is True
    assert cfg.allowed_origins == ["chrome-extension://*"]
    assert cfg.max_text_length == 32_000


def test_env_overrides(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "BRIDGE_PORT=6001\n"
        "MAX_TEXT_LENGTH=1000\n"
        "OLLAMA_MODEL=mistral\n"
        "ALWAYS_ON_TOP=0\n",
        encoding="utf-8",
    )
    cfg = load_config(env_file)
    assert cfg.port == 6001
    assert cfg.max_text_length == 1000
    assert cfg.ollama_model == "mistral"
    assert cfg.always_on_top is False


def test_invalid_bool_fails(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("AUTH_REQUIRED=quizá\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(env_file)


def test_invalid_port_fails(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("BRIDGE_PORT=99999\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(env_file)


def test_non_localhost_rejected(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("BRIDGE_HOST=0.0.0.0\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(env_file)


def test_ensure_env_file_creates_with_token(tmp_path):
    example = tmp_path / ".env.example"
    example.write_text("BRIDGE_TOKEN=\nBRIDGE_PORT=5000\n", encoding="utf-8")
    env_path = tmp_path / ".env"

    # Aseguramos que usa nuestro ejemplo simulando BASE_DIR no es necesario:
    from app import env_setup

    original = env_setup.EXAMPLE_FILE
    env_setup.EXAMPLE_FILE = example
    try:
        path, token, created = ensure_env_file(env_path)
        assert created is True
        assert token  # se generó un token
        text = path.read_text(encoding="utf-8")
        assert f"BRIDGE_TOKEN={token}" in text
        assert "BRIDGE_PORT=5000" in text

        # Segunda pasada: no regenera el token.
        path2, token2, created2 = ensure_env_file(path)
        assert created2 is False
        assert token2 is None
    finally:
        env_setup.EXAMPLE_FILE = original


def test_generate_token_is_strong():
    tokens = {generate_token() for _ in range(50)}
    assert len(tokens) == 50
    assert all(len(t) >= 32 for t in tokens)


def test_config_dataclass_defaults():
    cfg = Config()
    cfg.validate()  # no debe lanzar


# ------------------------------------------------- OLLAMA_EXTRA_OPTIONS
def test_extra_options_empty_by_default(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("BRIDGE_PORT=6001\n", encoding="utf-8")
    cfg = load_config(env_file)
    assert cfg.ollama_extra_options == {}


def test_extra_options_valid_json(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        'OLLAMA_EXTRA_OPTIONS={"num_ctx":8192,"top_k":40}\n', encoding="utf-8"
    )
    cfg = load_config(env_file)
    assert cfg.ollama_extra_options == {"num_ctx": 8192, "top_k": 40}


def test_extra_options_invalid_json_raises(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("OLLAMA_EXTRA_OPTIONS={num_ctx:8192}\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(env_file)


def test_extra_options_non_object_raises(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("OLLAMA_EXTRA_OPTIONS=[1,2]\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(env_file)


def test_extra_options_nested_value_raises(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text('OLLAMA_EXTRA_OPTIONS={"x":{"y":1}}\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(env_file)


def test_client_sends_extra_options():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["options"]["num_ctx"] == 8192
        assert body["options"]["repeat_penalty"] == 1.15
        assert body["options"]["temperature"] == 0.35
        return httpx.Response(200, content=ndjson({"done": True}))

    cfg = Config()
    cfg.token = ""
    cfg.ollama_temperature = 0.35
    cfg.ollama_extra_options = {"num_ctx": 8192, "repeat_penalty": 1.15}
    client = OllamaClient(cfg, transport=httpx.MockTransport(handler))
    client.chat_stream([{"role": "user", "content": "hola"}])


# ------------------------------------------------- OLLAMA_THINK
def test_think_unset_by_default(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("BRIDGE_PORT=6001\n", encoding="utf-8")
    cfg = load_config(env_file)
    assert cfg.ollama_think is None


def test_think_enabled_and_disabled(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("OLLAMA_THINK=1\n", encoding="utf-8")
    assert load_config(env_file).ollama_think is True
    env_file.write_text("OLLAMA_THINK=0\n", encoding="utf-8")
    assert load_config(env_file).ollama_think is False


def test_think_invalid_value_raises(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("OLLAMA_THINK=quizas\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(env_file)


def test_client_omits_think_when_unset():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "think" not in body
        return httpx.Response(200, content=ndjson({"done": True}))

    cfg = Config()
    cfg.token = ""
    client = OllamaClient(cfg, transport=httpx.MockTransport(handler))
    client.chat_stream([{"role": "user", "content": "hola"}])


def test_client_sends_think_when_configured():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if request.url.path == "/api/show":
            # Modelo con capacidad "thinking": se envía el parámetro.
            return httpx.Response(200, json={"capabilities": ["thinking"]})
        assert body["think"] is False
        return httpx.Response(200, content=ndjson({"done": True}))

    cfg = Config()
    cfg.token = ""
    cfg.ollama_think = False
    client = OllamaClient(cfg, transport=httpx.MockTransport(handler))
    client.chat_stream([{"role": "user", "content": "hola"}])


def test_client_omits_think_for_model_without_capability():
    """``think`` no se envía a modelos sin capacidad "thinking"."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion"]})
        body = json.loads(request.content)
        assert "think" not in body
        return httpx.Response(200, content=ndjson({"done": True}))

    cfg = Config()
    cfg.token = ""
    cfg.ollama_think = True
    client = OllamaClient(cfg, transport=httpx.MockTransport(handler))
    client.chat_stream([{"role": "user", "content": "hola"}])


def test_client_retries_without_think_on_400():
    """Si Ollama rechaza el ``think`` con 400, reintenta sin él una vez."""

    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/show":
            # show no reflejó la limitación (caso defensivo).
            return httpx.Response(200, json={"capabilities": ["thinking"]})
        body = json.loads(request.content)
        calls.append(body)
        if "think" in body:
            return httpx.Response(
                400, json={"error": '"mistral:latest" does not support thinking'}
            )
        return httpx.Response(200, content=ndjson({"done": True}))

    cfg = Config()
    cfg.token = ""
    cfg.ollama_think = True
    client = OllamaClient(cfg, transport=httpx.MockTransport(handler))
    text, _stats = client.chat_stream([{"role": "user", "content": "hola"}])
    assert text == ""
    assert len(calls) == 2
    assert "think" in calls[0] and "think" not in calls[1]
    # El rechazo queda cacheado: la siguiente llamada ni siquiera lo envía.
    calls.clear()
    client.chat_stream([{"role": "user", "content": "hola"}])
    assert len(calls) == 1
    assert "think" not in calls[0]


def test_client_sends_think_for_thinking_model_with_vision():
    """qwen2.5vl-style: sin "thinking" y con "vision" → sin ``think``, y el
    chat con imagen pasa (el 400 de multimodal sería otro caso)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion", "vision"]})
        body = json.loads(request.content)
        assert "think" not in body
        assert body["messages"][0]["images"] == ["aGVsbG8="]
        return httpx.Response(200, content=ndjson({"done": True}))

    cfg = Config()
    cfg.token = ""
    cfg.ollama_think = True
    client = OllamaClient(cfg, transport=httpx.MockTransport(handler))
    client.chat_stream([{"role": "user", "content": "hola", "images": ["aGVsbG8="]}])

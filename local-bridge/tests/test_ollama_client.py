"""Pruebas del cliente de Ollama con httpx.MockTransport (sin Ollama real)."""

from __future__ import annotations

import json

import httpx
import pytest

from app.config import Config
from app.ollama_client import (
    GenerationCancelled,
    ModelNotFoundError,
    OllamaClient,
    OllamaConnectionError,
    OllamaResponseError,
)


def ndjson(*objects: dict) -> bytes:
    return ("".join(json.dumps(o) + "\n" for o in objects)).encode("utf-8")


def make_client(handler) -> OllamaClient:
    cfg = Config()
    cfg.token = ""
    return OllamaClient(cfg, transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------- happy path
def test_stream_collects_chunks_and_stats():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat"
        body = json.loads(request.content)
        assert body["stream"] is True
        assert body["messages"] == [{"role": "user", "content": "saluda"}]
        assert body["model"] == "llama3"
        assert body["keep_alive"] == "5m"
        assert body["options"]["temperature"] == 0.7
        return httpx.Response(
            200,
            headers={"content-type": "application/x-ndjson"},
            content=ndjson(
                {"message": {"content": "Hola "}, "done": False},
                {"message": {"content": "mundo"}, "done": False},
                {
                    "message": {"content": ""},
                    "done": True,
                    "eval_count": 4,
                    "eval_duration": 2_000_000_000,
                },
            ),
        )

    client = make_client(handler)
    chunks: list[str] = []
    text, stats = client.chat_stream(
        [{"role": "user", "content": "saluda"}],
        model="llama3",
        on_chunk=chunks.append,
    )
    assert text == "Hola mundo"
    assert chunks == ["Hola ", "mundo"]
    assert stats["eval_count"] == 4
    assert stats["tokens_per_s"] == 2.0
    assert stats["elapsed_s"] >= 0


def test_stream_without_temperature_option():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "options" not in body
        return httpx.Response(200, content=ndjson({"done": True}))

    cfg = Config()
    cfg.ollama_temperature = None
    cfg.token = ""
    client = OllamaClient(cfg, transport=httpx.MockTransport(handler))
    client.chat_stream([{"role": "user", "content": "x"}])


# ---------------------------------------------------------------- errores
def test_model_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "model 'x' not found"})

    client = make_client(handler)
    with pytest.raises(ModelNotFoundError) as excinfo:
        client.chat_stream([{"role": "user", "content": "hola"}])
    assert "ollama pull" in excinfo.value.message


def test_error_reported_by_ollama_mid_stream():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=ndjson({"error": "cuda out of memory"}),
        )

    client = make_client(handler)
    with pytest.raises(OllamaResponseError):
        client.chat_stream([{"role": "user", "content": "hola"}])


def test_malformed_json_line():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"{no es json}\n")

    client = make_client(handler)
    with pytest.raises(OllamaResponseError):
        client.chat_stream([{"role": "user", "content": "hola"}])


def test_connection_refused_is_friendly():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = make_client(handler)
    with pytest.raises(OllamaConnectionError) as excinfo:
        client.chat_stream([{"role": "user", "content": "hola"}])
    assert "No se pudo conectar" in excinfo.value.message


def test_abort_between_chunks():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=ndjson(
                {"message": {"content": "a"}, "done": False},
                {"message": {"content": "b"}, "done": False},
                {"message": {"content": "c"}, "done": False},
            ),
        )

    client = make_client(handler)
    calls = {"n": 0}

    def should_abort() -> bool:
        calls["n"] += 1
        return calls["n"] >= 3  # aborta tras el segundo fragmento

    with pytest.raises(GenerationCancelled):
        client.chat_stream(
            [{"role": "user", "content": "hola"}], should_abort=should_abort
        )


# ---------------------------------------------------------------- info
def test_is_reachable_true():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/version"
        return httpx.Response(200, json={"version": "0.40.16"})

    client = make_client(handler)
    ok, detail = client.is_reachable()
    assert ok is True
    assert detail == "0.40.16"


def test_is_reachable_false_when_down():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    client = make_client(handler)
    ok, detail = client.is_reachable()
    assert ok is False
    assert "No se pudo conectar" in detail


def test_list_models():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "models": [{"name": "llama3:latest"}, {"name": "mistral:latest"}],
            },
        )

    client = make_client(handler)
    assert client.list_models() == ["llama3:latest", "mistral:latest"]

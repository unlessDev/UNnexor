"""Pruebas de la API HTTP con FastAPI TestClient y Ollama simulado."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Config
from app.ollama_client import OllamaClient
from app.queue_manager import Bus, ClearConversation, RuntimeState, SelectionRequest
from app.server import create_app
from tests.conftest import make_config

TOKEN = "test-token-123"
AUTH = {"Authorization": "Bearer " + TOKEN}


def ndjson(*objects: dict) -> bytes:
    return ("".join(json.dumps(o) + "\n" for o in objects)).encode("utf-8")


def ollama_stub() -> OllamaClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0-test"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={
                "models": [{"name": "llama3:latest"}, {"name": "mistral:latest"}],
            })
        return httpx.Response(200, content=ndjson({"done": True}))

    cfg = Config()
    cfg.token = ""
    return OllamaClient(cfg, transport=httpx.MockTransport(handler))


@pytest.fixture()
def app():
    cfg = make_config()
    return create_app(cfg, Bus(), RuntimeState(model="llama3"), ollama_stub()), cfg


@pytest.fixture()
def client(app):
    instance, _ = app
    with TestClient(instance) as test_client:
        yield test_client


# ---------------------------------------------------------------- health
def test_health_open_without_token(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["app"] == "extdator-bridge"
    assert body["ollama"]["reachable"] is True


# ---------------------------------------------------------------- auth
def test_selection_requires_token(client):
    response = client.post("/api/selection", json={"text": "hola"})
    assert response.status_code == 401


def test_selection_wrong_token(client):
    response = client.post(
        "/api/selection",
        json={"text": "hola"},
        headers={"Authorization": "Bearer equivocado"},
    )
    assert response.status_code == 401


def test_selection_token_in_body(app, client):
    response = client.post("/api/selection", json={"text": "hola", "token": TOKEN})
    assert response.status_code == 202


# ---------------------------------------------------------------- origin
def test_selection_rejects_web_origin(app, client):
    response = client.post(
        "/api/selection",
        json={"text": "hola"},
        headers={**AUTH, "Origin": "https://pagina-maliciosa.com"},
    )
    assert response.status_code == 403


def test_selection_accepts_extension_origin(app, client):
    response = client.post(
        "/api/selection",
        json={"text": "hola"},
        headers={**AUTH, "Origin": "chrome-extension://abcdefghijklmnop"},
    )
    assert response.status_code == 202


def test_cors_preflight_allowed(client):
    response = client.options(
        "/api/selection",
        headers={
            "Origin": "chrome-extension://abcdefghijklmnop",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert response.status_code in (200, 201)
    assert "chrome-extension://abcdefghijklmnop" in response.headers.get(
        "access-control-allow-origin", ""
    )


def test_cors_preflight_disallowed_origin(client):
    response = client.options(
        "/api/selection",
        headers={
            "Origin": "https://evil.example.com",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.headers.get("access-control-allow-origin", "") == ""


def test_status_requires_token(client):
    instance, _ = app
    text = "Hola áéíóú ñ €🚀\nsegunda línea\tcon tab"
    with TestClient(instance) as c:
        response = c.post(
            "/api/selection",
            json={"text": text, "source_url": "https://example.com/page",
                  "timestamp": "2026-01-01T00:00:00Z"},
            headers={**AUTH, "Origin": "chrome-extension://abcdefghijklmnop"},
        )
        assert response.status_code == 202
        assert response.json()["accepted"] is True


def test_selection_empty_text(app):
    instance, _ = app
    with TestClient(instance) as c:
        response = c.post("/api/selection", json={"text": "   "}, headers=AUTH)
        assert response.status_code == 422


def test_selection_too_long(app):
    instance, cfg = app
    with TestClient(instance) as c:
        response = c.post(
            "/api/selection",
            json={"text": "x" * (cfg.max_text_length + 1)},
            headers=AUTH,
        )
        assert response.status_code == 413


def test_selection_rate_limit(app):
    instance, cfg = app
    statuses = []
    with TestClient(instance) as c:
        for _ in range(cfg.rate_limit_per_minute + 3):
            response = c.post("/api/selection", json={"text": "hola"}, headers=AUTH)
            statuses.append(response.status_code)
    assert statuses.count(202) == cfg.rate_limit_per_minute
    assert statuses[-1] == 429


# ---------------------------------------------------------------- otros
def test_status_requires_token(client):
    assert client.get("/api/status").status_code == 401
    response = client.get("/api/status", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["model"] == "llama3"
    assert body["generating"] is False


def test_models_endpoint(client):
    response = client.get("/api/models", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert "llama3:latest" in body["models"]
    assert body["configured"] == "llama3"


def test_clear_endpoint(app, client):
    response = client.post("/api/clear", headers=AUTH)
    assert response.status_code == 202
    assert response.json()["accepted"] is True


def test_config_update_model(app, client):
    instance, cfg = app
    response = client.post(
        "/api/config", json={"model": "mistral", "temperature": 0.2}, headers=AUTH
    )
    assert response.status_code == 200
    assert response.json()["applied"]["model"] == "mistral"
    assert cfg.ollama_model == "mistral"
    assert cfg.ollama_temperature == 0.2


def test_config_update_invalid_temperature(client):
    response = client.post("/api/config", json={"temperature": 9.0}, headers=AUTH)
    assert response.status_code == 422


def test_config_update_empty_model(client):
    response = client.post("/api/config", json={"model": "   "}, headers=AUTH)
    assert response.status_code == 422


def test_config_requires_token(client):
    assert client.post("/api/config", json={"model": "mistral"}).status_code == 401


def test_unknown_route_404(client):
    assert client.get("/no-existe").status_code == 404

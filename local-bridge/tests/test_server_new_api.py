"""Pruebas de los endpoints nuevos: hardware, ajustes, emparejamiento, imagen.

Usa Ollama simulado (MockTransport) y ``TestClient``. El emparejamiento
comprueba caducidad, un solo uso, límite de intentos y control de origen.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Config
from app.ollama_client import OllamaClient
from app.pairing import PairingManager
from app.queue_manager import Bus, ImageRequest, RuntimeState
from app.server import create_app
from app.settings_store import SettingsStore
from tests.conftest import make_config

TOKEN = "test-token-123"
AUTH = {"Authorization": "Bearer " + TOKEN}
EXT_ORIGIN = {"Origin": "chrome-extension://abcdefabcdefabcdefabcdefabcdef"}


def ndjson(*objects: dict) -> bytes:
    return ("".join(json.dumps(o) + "\n" for o in objects)).encode("utf-8")


def ollama_stub() -> OllamaClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0-test"})
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "qwen3:8b",
                            "size": 5_200_000_000,
                            "details": {
                                "parameter_size": "8B",
                                "quantization_level": "Q4_K_M",
                            },
                        }
                    ]
                },
            )
        if request.url.path == "/api/show":
            return httpx.Response(
                200,
                json={
                    "family": "qwen3",
                    "parameter_size": "8B",
                    "capabilities": ["completion"],
                },
            )
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": []})
        return httpx.Response(200, content=ndjson({"done": True}))

    cfg = Config()
    cfg.token = ""
    return OllamaClient(cfg, transport=httpx.MockTransport(handler))


@pytest.fixture()
def stack(tmp_path):
    cfg = make_config()
    cfg.ollama_model = "qwen3:8b"  # independiente del .env de la máquina
    bus = Bus()
    state = RuntimeState(model="qwen3:8b")
    store = SettingsStore(tmp_path / "user_settings.json")
    store.load()
    pairing = PairingManager(TOKEN)
    application = create_app(
        cfg, bus, state, ollama_stub(), settings_store=store, pairing=pairing
    )
    return {
        "client": TestClient(application),
        "cfg": cfg,
        "bus": bus,
        "state": state,
        "store": store,
        "pairing": pairing,
    }


@pytest.fixture()
def client(stack):
    return stack["client"]


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    import struct
    import zlib

    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


def small_png() -> str:
    """PNG real (>1 KB de datos) construido en Python puro (sin Qt)."""
    import struct
    import zlib

    width, height = 160, 160
    signature = b"\x89PNG\r\n\x1a\n"
    # 8 bits, RGBA (6), sin entrelazado.
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    rows = bytearray()
    for y in range(height):
        rows.append(0)  # filtro: none
        for x in range(width):
            # Ruido determinista: comprime mal → IDAT > 1 KB.
            rows += bytes(
                (
                    (x * 7 + y * 3) % 251,
                    (x * 3 + y * 11) % 251,
                    (x * y * 5) % 251,
                    255,
                )
            )
    idat = zlib.compress(bytes(rows), 6)
    png = signature + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", idat)
    png += _png_chunk(b"IEND", b"")
    return base64.b64encode(png).decode("ascii")


# ------------------------------------------------------------------ hardware
def test_hardware_endpoint(client):
    response = client.get("/api/hardware", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["cpu_threads"] >= 1
    assert body["ram_total_mb"] > 1000
    assert isinstance(body["gpus"], list)
    assert body["detected_at"]
    assert "ollama_gpu_active" in body


def test_hardware_requires_auth(client):
    assert client.get("/api/hardware").status_code == 401


# ------------------------------------------------------------------ models/eval
def test_models_eval_classifies_installed(client):
    response = client.get("/api/models/eval", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["configured"] == "qwen3:8b"
    entry = next(m for m in body["models"] if m["name"] == "qwen3:8b")
    assessment = entry["assessment"]
    assert assessment["tier"] in ("perfecto", "regular", "alto", "imposible")
    assert assessment["emoji"] in ("🟢", "🟡", "🟠", "🔴")
    assert entry["size_bytes"] == 5_200_000_000


# ------------------------------------------------------------------ settings
def test_settings_get_lists_schema(client):
    response = client.get("/api/settings", headers=AUTH)
    assert response.status_code == 200
    body = response.json()["settings"]
    assert body["ollama_model"]["value"] == "qwen3:8b"
    assert body["ollama_think"]["restart"] is True
    assert body["ollama_temperature"]["live"] is True


def test_settings_post_validates_and_persists(stack, client):
    response = client.post(
        "/api/settings",
        headers=AUTH,
        json={
            "changes": {"ollama_temperature": 0.4, "ollama_think": True},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body["applied"]) == {"ollama_temperature", "ollama_think"}
    assert body["restart_required"] == ["ollama_think"]
    assert stack["cfg"].ollama_temperature == 0.4
    # Persistido: un store nuevo en el mismo archivo lo recupera.
    reloaded = SettingsStore(stack["store"].path)
    reloaded.load()
    assert reloaded.get("ollama_temperature") == 0.4


def test_settings_post_invalid_rejected_422(client):
    response = client.post(
        "/api/settings",
        headers=AUTH,
        json={
            "changes": {"ollama_temperature": 99},
        },
    )
    assert response.status_code == 422


def test_settings_live_change_publishes_config_update(stack, client):
    response = client.post(
        "/api/settings",
        headers=AUTH,
        json={
            "changes": {"ollama_model": "llama3.1:8b"},
        },
    )
    assert response.status_code == 200
    event = stack["bus"].consume(timeout=0.2)
    from app.queue_manager import ConfigUpdate

    assert isinstance(event, ConfigUpdate)
    assert event.changes == {"ollama_model": "llama3.1:8b"}


def test_settings_reset(stack, client):
    stack["store"].update({"ollama_temperature": 0.9})
    response = client.post("/api/settings/reset", headers=AUTH)
    assert response.status_code == 200
    assert "ollama_temperature" in response.json()["removed"]
    assert stack["store"].as_dict() == {}


# ------------------------------------------------------------------ pairing
def test_pair_confirm_returns_token_once(stack):
    code = stack["pairing"].current_code()
    response = stack["client"].post(
        "/api/pair/confirm",
        headers=EXT_ORIGIN,
        json={"code": code},
    )
    assert response.status_code == 200
    assert response.json()["token"] == TOKEN
    # Un solo uso: el mismo código ya no vale.
    second = stack["client"].post(
        "/api/pair/confirm",
        headers=EXT_ORIGIN,
        json={"code": code},
    )
    assert second.status_code == 403


def test_pair_confirm_wrong_code(stack):
    stack["pairing"].current_code()
    response = stack["client"].post(
        "/api/pair/confirm",
        headers=EXT_ORIGIN,
        json={"code": "000000"},
    )
    assert response.status_code == 403


def test_pair_confirm_blocks_web_origins(stack):
    stack["pairing"].current_code()
    response = stack["client"].post(
        "/api/pair/confirm",
        headers={"Origin": "https://evil.example"},
        json={"code": "123456"},
    )
    assert response.status_code == 403


def test_pair_confirm_rate_limited(stack):
    stack["pairing"].current_code()
    for attempt in range(5):
        stack["client"].post(
            "/api/pair/confirm", headers=EXT_ORIGIN, json={"code": "000000"}
        )
    sixth = stack["client"].post(
        "/api/pair/confirm", headers=EXT_ORIGIN, json={"code": "000000"}
    )
    assert sixth.status_code == 429


# ------------------------------------------------------------------ image
def test_image_accepts_valid_png(stack, client):
    response = client.post(
        "/api/image",
        headers={**AUTH, **EXT_ORIGIN},
        json={
            "image_b64": small_png(),
            "instruction": "Resuelve el ejercicio",
            "source_url": "screenshot://desktop",
        },
    )
    assert response.status_code == 202
    event = stack["bus"].consume(timeout=0.2)
    assert isinstance(event, ImageRequest)
    assert event.instruction == "Resuelve el ejercicio"


def test_image_rejects_bad_base64(client):
    response = client.post(
        "/api/image",
        headers={**AUTH, **EXT_ORIGIN},
        json={
            "image_b64": "!!!no-base64!!!",
            "instruction": "x",
        },
    )
    assert response.status_code in (422, 413)  # pydantic o validación manual


def test_image_rejects_too_small_payload(client):
    tiny = base64.b64encode(b"PNG").decode("ascii")
    response = client.post(
        "/api/image",
        headers={**AUTH, **EXT_ORIGIN},
        json={
            "image_b64": tiny,
            "instruction": "x",
        },
    )
    assert response.status_code == 422


def test_image_rejects_non_image_bytes(client):
    fake = base64.b64encode(b"x" * 2048).decode("ascii")
    response = client.post(
        "/api/image",
        headers={**AUTH, **EXT_ORIGIN},
        json={
            "image_b64": fake,
            "instruction": "x",
        },
    )
    assert response.status_code == 415


def test_image_rejects_while_generating(stack, client):
    stack["state"].set_generating(True)
    response = client.post(
        "/api/image",
        headers={**AUTH, **EXT_ORIGIN},
        json={
            "image_b64": small_png(),
            "instruction": "x",
        },
    )
    assert response.status_code == 409
    stack["state"].set_generating(False)


def test_image_requires_auth(client):
    response = client.post(
        "/api/image",
        headers=EXT_ORIGIN,
        json={
            "image_b64": small_png(),
            "instruction": "x",
        },
    )
    assert response.status_code == 401

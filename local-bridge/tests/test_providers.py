"""Pruebas del sistema de proveedores (adaptador OpenAI-compatible + gestor).

El keyring se stubbea (nunca se toca el Credential Manager real) y el
archivo de estado se redirige a un directorio temporal. Las peticiones
HTTP usan ``httpx.MockTransport``: no hay llamadas reales ni cargos.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.providers import manager
from app.providers.base import ProviderError, ProviderModel
from app.providers.openai_compat import OpenAICompatClient, _encode_message
from app.providers.presets import PRESETS, preset

BASE = "https://api.test.example/v1"
KEY = "sk-test-123"


def make_client(handler) -> OpenAICompatClient:
    return OpenAICompatClient(
        "openai",
        BASE,
        KEY,
        timeout=5.0,
        transport=httpx.MockTransport(handler),
    )


def sse(*payloads: dict) -> bytes:
    body = "".join(f"data: {json.dumps(p)}\n\n" for p in payloads) + "data: [DONE]\n\n"
    return body.encode("utf-8")


CHAT_DELTA = {"choices": [{"delta": {"content": "Hola"}}]}


# --------------------------------------------------------------------- #
# Catálogo de modelos
# --------------------------------------------------------------------- #
def test_list_models_parses_catalog():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        assert request.headers["Authorization"] == f"Bearer {KEY}"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "gpt-4o-mini", "owned_by": "openai"},
                    {"id": "gpt-4o"},
                    {"id": "", "owned_by": "x"},  # inválido: se descarta
                    "no-es-dict",  # inválido: se descarta
                ]
            },
        )

    models = make_client(handler).list_models()
    assert [m.id for m in models] == ["gpt-4o-mini", "gpt-4o"]
    assert isinstance(models[0], ProviderModel)


@pytest.mark.parametrize(
    "status,kind",
    [
        (401, "auth"),
        (403, "auth"),
        (402, "payment"),
        (429, "quota"),
    ],
)
def test_list_models_error_kinds(status, kind):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": "boom"}})

    with pytest.raises(ProviderError) as excinfo:
        make_client(handler).list_models()
    assert excinfo.value.kind == kind
    assert KEY not in str(excinfo.value)  # la clave nunca se filtra


def test_list_models_network_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(ProviderError) as excinfo:
        make_client(handler).list_models()
    assert excinfo.value.kind == "network"


# --------------------------------------------------------------------- #
# Chat en streaming (SSE)
# --------------------------------------------------------------------- #
def test_chat_stream_accumulates_and_reports():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["stream"] is True
        assert body["model"] == "test-model"
        assert body["messages"][0]["role"] == "system"
        return httpx.Response(
            200,
            content=sse(
                {"choices": [{"delta": {"content": "Hola "}}]},
                {"choices": [{"delta": {"content": "mundo"}}]},
            ),
        )

    chunks: list[str] = []
    text, stats = make_client(handler).chat_stream(
        [{"role": "system", "content": "sys"}],
        model="test-model",
        temperature=0.2,
        on_chunk=chunks.append,
    )
    assert text == "Hola mundo"
    assert chunks == ["Hola ", "mundo"]
    assert stats["provider"] == "openai"
    assert stats["chars"] == len(text)


def test_chat_stream_handles_keepalive_comments_and_deltas():
    body = (
        b": OPENROUTER PROCESSING\n\n"
        b'data: {"choices": [{"delta": {"content": "ok"}}]}\n\n'
        b"data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    text, _ = make_client(handler).chat_stream(
        [{"role": "user", "content": "hi"}], model="m"
    )
    assert text == "ok"


def test_chat_stream_abort_stops_generation():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sse(CHAT_DELTA, CHAT_DELTA))

    with pytest.raises(ProviderError) as excinfo:
        make_client(handler).chat_stream(
            [{"role": "user", "content": "hi"}],
            model="m",
            should_abort=lambda: True,
        )
    assert excinfo.value.kind == "cancelled"


def test_chat_stream_http_error_maps_to_kind():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "invalid"}})

    with pytest.raises(ProviderError) as excinfo:
        make_client(handler).chat_stream([{"role": "user", "content": "hi"}], model="m")
    assert excinfo.value.kind == "auth"


def test_image_message_encoded_to_openai_format():
    encoded = _encode_message(
        {
            "role": "user",
            "content": "Describe esto",
            "images": ["QUJD"],
        }
    )
    assert encoded["role"] == "user"
    assert isinstance(encoded["content"], list)
    assert encoded["content"][0] == {"type": "text", "text": "Describe esto"}
    assert encoded["content"][1]["type"] == "image_url"
    assert encoded["content"][1]["image_url"]["url"].endswith(";base64,QUJD")
    # Sin imágenes: formato simple
    plain = _encode_message({"role": "user", "content": "hola"})
    assert plain == {"role": "user", "content": "hola"}


# --------------------------------------------------------------------- #
# Presets (datos verificados, sin inventos)
# --------------------------------------------------------------------- #
def test_presets_are_complete_and_https():
    assert len(PRESETS) >= 12
    for preset_entry in PRESETS:
        assert preset_entry["base_url"].startswith("https://")
        assert preset_entry["docs"].startswith("https://")
        assert preset_entry["kind"]  # nunca vacío: siempre documentado
    assert preset("groq")["base_url"] == "https://api.groq.com/openai/v1"
    assert preset("desconocido") is None


# --------------------------------------------------------------------- #
# Gestor (keyring stub + config temporal)
# --------------------------------------------------------------------- #
@pytest.fixture()
def manager_env(tmp_path, monkeypatch):
    """Redirect config file and stub the keyring functions."""
    config_file = tmp_path / "providers.json"
    monkeypatch.setattr(manager, "CONFIG_FILE", config_file)
    keys: dict = {}

    def store(provider_id: str, key: str) -> None:
        keys[provider_id] = key

    def get(provider_id: str):
        return keys.get(provider_id)

    def delete(provider_id: str) -> None:
        keys.pop(provider_id, None)

    monkeypatch.setattr(manager, "store_key", store)
    monkeypatch.setattr(manager, "get_key", get)
    monkeypatch.setattr(manager, "delete_key", delete)
    return {"keys": keys, "config": config_file}


CATALOG_BODY = {"data": [{"id": f"model-{i}"} for i in range(4)]}


@pytest.fixture()
def mock_catalog(monkeypatch):
    """Parchea OpenAICompatClient para que todo /models sea un mock."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=CATALOG_BODY)

    original_init = OpenAICompatClient.__init__

    def init_patch(self, provider_id, base_url, api_key, timeout=120.0, transport=None):
        original_init(
            self,
            provider_id,
            base_url,
            api_key,
            timeout=timeout,
            transport=httpx.MockTransport(handler),
        )

    monkeypatch.setattr(manager.OpenAICompatClient, "__init__", init_patch)


def test_connect_validates_and_stores_state(manager_env, mock_catalog):
    result = manager.connect("groq", "gsk_test")
    assert result["ok"] is True
    assert result["model_count"] == 4
    assert manager_env["keys"]["groq"] == "gsk_test"
    assert "groq" in manager.connected_providers()
    state = manager.provider_state("groq")
    assert state["connected"] is True
    assert state["model_count"] == 4
    # El estado guardado NUNCA contiene la clave.
    saved = manager_env["config"].read_text(encoding="utf-8")
    assert "gsk_test" not in saved
    assert "sk" not in saved.lower()


def test_connect_invalid_key_does_not_store(manager_env):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "invalid_api_key"}})

    original_connect = manager.OpenAICompatClient

    def patched(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original_connect(*args, **kwargs)

    original_init = manager.OpenAICompatClient.__init__

    def init_patch(self, provider_id, base_url, api_key, timeout=120.0, transport=None):
        original_init(
            self,
            provider_id,
            base_url,
            api_key,
            timeout=timeout,
            transport=httpx.MockTransport(handler),
        )

    import unittest.mock as mock

    with (
        mock.patch.object(manager.OpenAICompatClient, "__init__", init_patch),
        pytest.raises(ProviderError) as excinfo,
    ):
        manager.connect("groq", "bad-key")
    assert excinfo.value.kind == "auth"
    assert manager.connected_providers() == []
    assert "groq" not in manager_env["keys"]


def test_connect_empty_key_rejected(manager_env):
    with pytest.raises(ProviderError, match="clave"):
        manager.connect("groq", "   ")
    assert manager.connected_providers() == []


def test_get_client_for_requires_saved_key(manager_env):
    with pytest.raises(ProviderError) as excinfo:
        manager.get_client_for("openai")
    assert excinfo.value.kind == "auth"


def test_disconnect_removes_everything(manager_env, mock_catalog):
    manager.connect("groq", "gsk_test")
    manager.disconnect("groq")
    assert manager.connected_providers() == []
    assert "groq" not in manager_env["keys"]
    assert manager.provider_state("groq")["connected"] is False


def test_custom_provider_roundtrip(manager_env):
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith("https://mine.example/v1")
        return httpx.Response(200, json={"data": [{"id": "m1"}]})

    original_init = manager.OpenAICompatClient.__init__
    import unittest.mock as mock

    def init_patch(self, provider_id, base_url, api_key, timeout=120.0, transport=None):
        original_init(
            self,
            provider_id,
            base_url,
            api_key,
            timeout=timeout,
            transport=httpx.MockTransport(handler),
        )

    with mock.patch.object(manager.OpenAICompatClient, "__init__", init_patch):
        manager.connect(
            "custom", "ck-1", base_url="https://mine.example/v1", label="Mi server"
        )
    assert manager.connected_providers() == ["custom"]
    client = manager.get_client_for("custom")
    assert client.base_url == "https://mine.example/v1/"


def test_migrate_from_env_moves_keys_to_keyring(manager_env, tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "BRIDGE_TOKEN=xxx\n"
        "OPENAI_API_KEY=sk-env-openai\n"
        "GROQ_API_KEY=gsk-env-groq\n"
        "RANDOM_SETTING=1\n",
        encoding="utf-8",
    )
    original_init = OpenAICompatClient.__init__
    import unittest.mock as mock

    def init_patch(self, provider_id, base_url, api_key, timeout=120.0, transport=None):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=CATALOG_BODY)

        original_init(
            self,
            provider_id,
            base_url,
            api_key,
            timeout=timeout,
            transport=httpx.MockTransport(handler),
        )

    with mock.patch.object(manager.OpenAICompatClient, "__init__", init_patch):
        migrated = manager.migrate_from_env(env)
    assert migrated == ["openai", "groq"]
    assert manager_env["keys"]["openai"] == "sk-env-openai"
    # La clave NO se elimina del .env (migración no destructiva).
    assert "OPENAI_API_KEY" in env.read_text(encoding="utf-8")


def test_available_presets_includes_connection_state(manager_env, mock_catalog):
    manager.connect("groq", "gsk_x")
    presets = manager.available_presets()
    groq = next(p for p in presets if p["id"] == "groq")
    openai = next(p for p in presets if p["id"] == "openai")
    assert groq["connected"] is True
    assert openai["connected"] is False


def test_streaming_error_body_is_read_and_reported():
    """Regresión: con un estado no tabulado (p. ej. 400) en streaming, el
    cuerpo no se leía y httpx lanzaba ResponseNotRead en lugar de un
    ProviderError con el mensaje del proveedor."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.dumps({"error": {"message": "model does not support chat"}})
        # Cuerpo como iterador = respuesta en streaming real (no precargada).
        return httpx.Response(
            400,
            content=iter([body.encode("utf-8")]),
            headers={"content-type": "application/json"},
        )

    client = make_client(handler)
    with pytest.raises(ProviderError) as info:
        client.chat_stream([{"role": "user", "content": "x"}], model="guard")
    assert "model does not support chat" in info.value.message
    assert "HTTP 400" in info.value.message

"""Catálogo unificado, adaptador de Anthropic, validación de Deepgram,
historial de uso y consentimientos. Sin red real: ``httpx.MockTransport``."""

from __future__ import annotations

import json
from datetime import date, timedelta

import httpx
import pytest

from app.providers import manager
from app.providers.access import AccessTier
from app.providers.anthropic import AnthropicClient, split_system
from app.providers.base import ProviderError
from app.providers.catalog import (
    ConnectionStatus,
    connection_status,
    get_provider,
    provider_catalog,
)
from app.providers.consent import ConsentStore
from app.providers.presets import PRESETS, SPEECH_ONLY_PROVIDER_IDS
from app.providers.usage import UsageLedger, extract_rate_limit_headers


# ------------------------------------------------------------------ presets
def test_every_preset_has_structured_access_and_protocol():
    for item in PRESETS:
        AccessTier(item["access"])  # valor válido
        assert item["protocol"] in ("openai", "anthropic", "deepgram")
        assert item["capabilities"], item["id"]
        assert item["verified_at"].startswith("2026-")
    assert SPEECH_ONLY_PROVIDER_IDS == ["deepgram"]


def test_catalog_includes_local_ollama_and_distinguishes_tiers():
    catalog = {p.id: p for p in provider_catalog()}
    assert catalog["ollama"].local and catalog["ollama"].access is AccessTier.LOCAL
    assert catalog["groq"].access is AccessTier.FREE_LIMITED
    assert catalog["openrouter"].access is AccessTier.MIXED
    assert catalog["deepgram"].capabilities == frozenset({"stt"})
    assert catalog["anthropic"].protocol == "anthropic"
    assert get_provider("nope") is None


def test_connection_status_never_overstates_access():
    groq = get_provider("groq")
    assert groq is not None
    assert connection_status(groq, has_key=False) is ConnectionStatus.NOT_CONNECTED
    status = connection_status(groq, has_key=True)
    assert status is ConnectionStatus.KEY_VALID
    assert "sin confirmar" in status.label
    assert connection_status(groq, has_key=True, confirmed=True) is (
        ConnectionStatus.MODEL_CONFIRMED
    )
    assert connection_status(groq, has_key=True, last_error_kind="payment") is (
        ConnectionStatus.FAILED
    )
    ollama = get_provider("ollama")
    assert ollama is not None
    assert connection_status(ollama, has_key=False) is ConnectionStatus.LOCAL


def test_providers_dialog_groups_follow_access_tiers():
    pytest.importorskip("PySide6")
    from app.desktop.providers_dialog import CATEGORIES

    grouped = {pid: title for title, ids in CATEGORIES for pid in ids}
    assert set(grouped) == {p["id"] for p in PRESETS}
    assert grouped["anthropic"] == "Pago por consumo"
    assert grouped["deepgram"].startswith("Créditos")


# ---------------------------------------------------------------- Anthropic
def anthropic_sse(*events: dict) -> bytes:
    out = []
    for event in events:
        out.append(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n")
    return "".join(out).encode("utf-8")


def test_anthropic_chat_stream_request_and_parsing():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = dict(request.headers)
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        body = anthropic_sse(
            {"type": "message_start", "message": {"usage": {"input_tokens": 12}}},
            {"type": "content_block_start", "index": 0},
            {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "Hola"},
            },
            {"type": "ping"},
            {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": " mundo"},
            },
            {"type": "message_delta", "usage": {"output_tokens": 3}},
            {"type": "message_stop"},
        )
        return httpx.Response(
            200,
            content=body,
            headers={"anthropic-ratelimit-requests-remaining": "49"},
        )

    client = AnthropicClient("sk-ant-x", transport=httpx.MockTransport(handler))
    chunks: list[str] = []
    text, stats = client.chat_stream(
        [
            {"role": "system", "content": "Eres breve."},
            {"role": "user", "content": "Hola", "images": ["QUJD"]},
        ],
        "claude-test",
        temperature=1.7,
        on_chunk=chunks.append,
    )
    assert text == "Hola mundo" and chunks == ["Hola", " mundo"]
    assert seen["path"] == "/v1/messages"
    assert seen["headers"]["x-api-key"] == "sk-ant-x"
    assert seen["headers"]["anthropic-version"] == "2023-06-01"
    body = seen["body"]
    assert body["system"] == "Eres breve." and body["stream"] is True
    assert body["max_tokens"] > 0 and body["temperature"] == 1.0
    content = body["messages"][0]["content"]
    assert content[0]["type"] == "image" and content[0]["source"]["data"] == "QUJD"
    assert content[1] == {"type": "text", "text": "Hola"}
    assert stats["prompt_eval_count"] == 12 and stats["eval_count"] == 3
    assert client.last_rate_limits == {"anthropic-ratelimit-requests-remaining": "49"}


def test_anthropic_list_models_paginates():
    pages = iter(
        [
            {"data": [{"id": "m1"}], "has_more": True, "last_id": "m1"},
            {"data": [{"id": "m2"}, {"no": "id"}], "has_more": False},
        ]
    )
    seen_after: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_after.append(request.url.params.get("after_id"))
        return httpx.Response(200, json=next(pages))

    client = AnthropicClient("k", transport=httpx.MockTransport(handler))
    assert [m.id for m in client.list_models()] == ["m1", "m2"]
    assert seen_after == [None, "m1"]


@pytest.mark.parametrize(
    "status,body,kind",
    [
        (401, {"error": {"message": "invalid x-api-key"}}, "auth"),
        (429, {"error": {}}, "quota"),
        (529, {"error": {}}, "quota"),
        (400, {"error": {"message": "Your credit balance is too low"}}, "payment"),
        (500, {"error": {"message": "boom"}}, "general"),
    ],
)
def test_anthropic_errors_are_typed(status, body, kind):
    client = AnthropicClient(
        "k", transport=httpx.MockTransport(lambda r: httpx.Response(status, json=body))
    )
    with pytest.raises(ProviderError) as info:
        client.chat_stream([{"role": "user", "content": "x"}], "m")
    assert info.value.kind == kind


def test_anthropic_stream_error_event_and_cancel():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=anthropic_sse(
                {
                    "type": "error",
                    "error": {"type": "overloaded_error", "message": "busy"},
                }
            ),
        )

    client = AnthropicClient("k", transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderError) as info:
        client.chat_stream([{"role": "user", "content": "x"}], "m")
    assert info.value.kind == "quota"
    with pytest.raises(ProviderError) as info:
        client.chat_stream(
            [{"role": "user", "content": "x"}], "m", should_abort=lambda: True
        )
    assert info.value.kind == "cancelled"


def test_split_system_merges_roles_and_starts_with_user():
    system, messages = split_system(
        [
            {"role": "system", "content": "A"},
            {"role": "assistant", "content": "previa"},
            {"role": "user", "content": "uno"},
            {"role": "user", "content": "dos"},
        ]
    )
    assert system == "A"
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert len(messages[-1]["content"]) == 2


# -------------------------------------------------------------- manager
@pytest.fixture()
def keyring_stub(monkeypatch, tmp_path):
    keys: dict[str, str] = {}
    monkeypatch.setattr(manager, "CONFIG_FILE", tmp_path / "providers.json")
    monkeypatch.setattr(
        manager, "store_key", lambda pid, key: keys.__setitem__(pid, key)
    )
    monkeypatch.setattr(manager, "get_key", keys.get)
    monkeypatch.setattr(manager, "delete_key", lambda pid: keys.pop(pid, None))
    return keys


def test_connect_deepgram_validates_with_projects(monkeypatch, keyring_stub):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(
            f"{request.method} {request.url} {request.headers['authorization']}"
        )
        return httpx.Response(
            200, json={"projects": [{"project_id": "p", "name": "n"}]}
        )

    real_client = httpx.Client

    def fake_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", fake_client)
    result = manager.connect("deepgram", "dg-key")
    assert result["projects"] == 1
    assert calls == ["GET https://api.deepgram.com/v1/projects Token dg-key"]
    assert keyring_stub["deepgram"] == "dg-key"
    with pytest.raises(ProviderError) as info:
        manager.get_client_for("deepgram")
    assert "solo ofrece reconocimiento de voz" in info.value.message


def test_get_client_for_anthropic_returns_native_adapter(keyring_stub):
    keyring_stub["anthropic"] = "sk-ant"
    client = manager.get_client_for("anthropic")
    assert isinstance(client, AnthropicClient)
    client.close()


# ------------------------------------------------------------ uso y límites
def test_usage_ledger_aggregates_without_content(tmp_path):
    ledger = UsageLedger(tmp_path / "usage.json")
    ledger.record(
        "groq",
        "stt",
        audio_seconds=3.5,
        rate_limits={"X-RateLimit-Remaining-Requests": "19"},
    )
    ledger.record("groq", "answer", input_tokens=100, output_tokens=40, chars_out=200)
    ledger.record("groq", "answer", errors=1, requests=1)
    totals = ledger.summary()["groq"]
    assert totals["requests"] == 3 and totals["errors"] == 1
    assert totals["audio_seconds"] == 3.5 and totals["output_tokens"] == 40
    assert ledger.by_task("groq")["answer"]["requests"] == 2
    assert ledger.rate_limits("groq")["x-ratelimit-remaining-requests"] == "19"
    raw = (tmp_path / "usage.json").read_text(encoding="utf-8")
    assert "prompt" not in raw and "text" not in raw


def test_usage_ledger_retention_and_clear(tmp_path):
    ledger = UsageLedger(tmp_path / "usage.json", retention_days=10)
    ledger.record("groq", "stt", day=date.today() - timedelta(days=40))
    ledger.record("groq", "stt")
    data = json.loads((tmp_path / "usage.json").read_text(encoding="utf-8"))
    assert list(data["days"]) == [date.today().isoformat()]
    ledger.clear()
    assert ledger.summary() == {}


def test_extract_rate_limit_headers():
    headers = httpx.Headers(
        {"x-ratelimit-remaining-tokens": "100", "retry-after": "3", "content-type": "x"}
    )
    assert extract_rate_limit_headers(headers) == {
        "x-ratelimit-remaining-tokens": "100",
        "retry-after": "3",
    }


def test_consent_store_scopes(tmp_path):
    store = ConsentStore(tmp_path / "consents.json")
    assert not store.has("groq", "remote_data")
    store.grant("groq", "remote_data")
    store.grant("groq", "costs")
    store.grant("openai", "remote_data")
    assert store.providers_with("remote_data") == {"groq", "openai"}
    assert store.providers_with("costs") == {"groq"}
    store.revoke("groq", "costs")
    assert not store.has("groq", "costs") and store.has("groq", "remote_data")
    store.revoke("openai")
    assert store.providers_with("remote_data") == {"groq"}
    with pytest.raises(ValueError):
        store.grant("groq", "everything")

"""Model Router: reglas de privacidad/coste, estrategias y ejecución con
alternativas. Candidatos y backends simulados."""

from __future__ import annotations

import json

import httpx
import pytest

from app.ollama_client import OllamaClient
from app.providers.access import AccessTier
from app.providers.base import ProviderError
from app.routing.candidates import (
    OllamaChatBackend,
    ollama_candidates,
    remote_candidates,
)
from app.routing.executor import RoutingCancelled, RoutingError, run_chat
from app.routing.router import (
    Candidate,
    RouteRequest,
    RoutingPolicy,
    Strategy,
    Task,
    route,
)
from tests.conftest import make_config

CHAT = frozenset({"chat"})
VISION = frozenset({"chat", "vision"})

LOCAL_SMALL = Candidate(
    "ollama", "qwen3:4b", True, AccessTier.LOCAL, CHAT, 2, 4, fits_hardware=True
)
LOCAL_BIG = Candidate(
    "ollama", "qwen3:8b", True, AccessTier.LOCAL, CHAT, 3, 3, fits_hardware=True
)
LOCAL_VL = Candidate(
    "ollama", "qwen2.5vl:7b", True, AccessTier.LOCAL, VISION, 3, 3, fits_hardware=True
)
LOCAL_HUGE = Candidate(
    "ollama",
    "llama:70b",
    True,
    AccessTier.LOCAL,
    CHAT,
    5,
    1,
    fits_hardware=False,
    hardware_note="requiere 40 GB",
)
GROQ = Candidate("groq", "llama-3.3-70b", False, AccessTier.FREE_LIMITED, CHAT, 4, 5)
OPENAI = Candidate("openai", "gpt-x", False, AccessTier.PAY_AS_YOU_GO, VISION, 5, 4)
ALL = [LOCAL_SMALL, LOCAL_BIG, LOCAL_VL, LOCAL_HUGE, GROQ, OPENAI]


def test_default_policy_is_local_only():
    decision = route(RouteRequest(Task.ANSWER), ALL, RoutingPolicy())
    assert decision.primary is not None and decision.primary.local
    assert all(c.local for c in decision.fallbacks)
    reasons = {r.candidate.key: r.reason for r in decision.rejected}
    assert "sin autorización" in reasons["groq:llama-3.3-70b"]
    assert reasons["ollama:llama:70b"] == "requiere 40 GB"


def test_remote_requires_data_and_cost_consent():
    policy = RoutingPolicy(
        strategy=Strategy.SPEED, remote_allowed=frozenset({"groq", "openai"})
    )
    decision = route(RouteRequest(Task.ANSWER), ALL, policy)
    assert decision.primary == GROQ  # gratuito con límites: no exige costos
    reasons = {r.candidate.key: r.reason for r in decision.rejected}
    assert "costos" in reasons["openai:gpt-x"]
    policy = RoutingPolicy(
        strategy=Strategy.QUALITY,
        remote_allowed=frozenset({"openai"}),
        cost_allowed=frozenset({"openai"}),
    )
    assert route(RouteRequest(Task.ANSWER), ALL, policy).primary == OPENAI


def test_sensitive_documents_stay_local_without_documents_consent():
    policy = RoutingPolicy(strategy=Strategy.SPEED, remote_allowed=frozenset({"groq"}))
    decision = route(RouteRequest(Task.ANSWER, sensitive=True), ALL, policy)
    assert decision.primary is not None and decision.primary.local
    assert any("documentos personales" in r.reason for r in decision.rejected)
    policy = RoutingPolicy(
        strategy=Strategy.SPEED,
        remote_allowed=frozenset({"groq"}),
        documents_allowed=frozenset({"groq"}),
    )
    assert route(RouteRequest(Task.ANSWER, sensitive=True), ALL, policy).primary == GROQ


def test_vision_task_filters_capabilities_and_explains_failure():
    decision = route(RouteRequest(Task.VISION), ALL, RoutingPolicy())
    assert decision.primary == LOCAL_VL
    none = route(RouteRequest(Task.VISION), [LOCAL_SMALL, GROQ], RoutingPolicy())
    assert not none.ok
    assert "no admite vision" in none.explain()


def test_strategies_and_pinning():
    remote = frozenset({"groq"})
    local_first = route(
        RouteRequest(Task.ANSWER), ALL, RoutingPolicy(Strategy.LOCAL_FIRST, remote)
    )
    assert local_first.primary is not None and local_first.primary.local
    assert local_first.crosses_to_remote(GROQ)
    speed = route(RouteRequest(Task.ANSWER), ALL, RoutingPolicy(Strategy.SPEED, remote))
    assert speed.primary == GROQ
    pinned = route(
        RouteRequest(Task.ANSWER),
        ALL,
        RoutingPolicy(Strategy.SPEED, remote, pinned={Task.ANSWER: LOCAL_SMALL.key}),
    )
    assert pinned.primary == LOCAL_SMALL
    selected = route(
        RouteRequest(Task.ANSWER),
        ALL,
        RoutingPolicy(
            Strategy.SELECTED_ONLY, remote, selected=frozenset({LOCAL_BIG.key})
        ),
    )
    assert selected.primary == LOCAL_BIG and selected.fallbacks == ()


def test_low_cost_prefers_local_then_free():
    policy = RoutingPolicy(
        Strategy.LOW_COST,
        frozenset({"groq", "openai"}),
        cost_allowed=frozenset({"openai"}),
    )
    decision = route(RouteRequest(Task.ANSWER), [GROQ, OPENAI], policy)
    assert decision.primary == GROQ


# ---------------------------------------------------------------- ejecutor
class Backend:
    def __init__(self, text="ok", error=None, partial=None):
        self.text, self.error, self.partial = text, error, partial
        self.closed = False

    def chat_stream(
        self, messages, model, temperature=None, on_chunk=None, should_abort=None
    ):
        if self.partial and on_chunk:
            on_chunk(self.partial)
        if self.error:
            raise self.error
        if on_chunk:
            on_chunk(self.text)
        return self.text, {"model": model}

    def close(self):
        self.closed = True


def decision_for(*cands):
    return route(
        RouteRequest(Task.ANSWER),
        list(cands),
        RoutingPolicy(Strategy.LOCAL_FIRST, frozenset({"groq"})),
    )


def test_executor_uses_fallback_and_notices_remote_crossing():
    backends = {
        LOCAL_BIG.key: Backend(error=ProviderError("Ollama caído", "network")),
        GROQ.key: Backend(text="respuesta remota"),
    }
    notices, usage = [], []
    result = run_chat(
        decision_for(LOCAL_BIG, GROQ),
        [{"role": "user", "content": "x"}],
        client_for=lambda c: backends[c.key],
        on_notice=notices.append,
        on_usage=lambda c, s, e: usage.append((c.key, e.kind if e else None)),
    )
    assert result.text == "respuesta remota" and result.used_fallback
    assert "REMOTO" in notices[0]
    assert usage == [(LOCAL_BIG.key, "network"), (GROQ.key, None)]
    assert all(b.closed for b in backends.values())


def test_executor_does_not_switch_after_partial_output():
    backends = {
        LOCAL_BIG.key: Backend(
            error=ProviderError("cortado", "network"), partial="Hola"
        ),
        GROQ.key: Backend(),
    }
    with pytest.raises(RoutingError) as info:
        run_chat(
            decision_for(LOCAL_BIG, GROQ), [], client_for=lambda c: backends[c.key]
        )
    assert "se interrumpió" in info.value.message


def test_executor_cancellation_never_falls_back():
    backends = {
        LOCAL_BIG.key: Backend(error=ProviderError("cancelada", "cancelled")),
        GROQ.key: Backend(),
    }
    with pytest.raises(RoutingCancelled):
        run_chat(
            decision_for(LOCAL_BIG, GROQ), [], client_for=lambda c: backends[c.key]
        )


def test_executor_reports_all_failures_and_empty_decision():
    backends = {LOCAL_BIG.key: Backend(error=RuntimeError("x"))}
    with pytest.raises(RoutingError) as info:
        run_chat(decision_for(LOCAL_BIG), [], client_for=lambda c: backends[c.key])
    assert info.value.attempts == ((LOCAL_BIG.key, "x"),)
    empty = route(RouteRequest(Task.VISION), [LOCAL_SMALL], RoutingPolicy())
    with pytest.raises(RoutingError):
        run_chat(empty, [], client_for=lambda c: Backend())


# ------------------------------------------------------------- candidatos
def ollama_stub(tags, caps) -> OllamaClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": tags})
        if request.url.path == "/api/show":
            name = json.loads(request.content)["model"]
            return httpx.Response(200, json={"capabilities": caps[name]})
        if request.url.path == "/api/chat":
            return httpx.Response(
                200,
                content=b'{"message":{"content":"hi"}}\n{"done":true,"eval_count":1}\n',
            )
        return httpx.Response(404)

    return OllamaClient(make_config(), transport=httpx.MockTransport(handler))


def test_ollama_candidates_use_real_capabilities_and_hardware():
    tags = [
        {
            "name": "qwen2.5vl:7b",
            "size": 6_000_000_000,
            "details": {"parameter_size": "8.3B"},
        },
        {
            "name": "nomic-embed-text:latest",
            "size": 274_000_000,
            "details": {"parameter_size": "137M"},
        },
        {
            "name": "huge:70b",
            "size": 42_000_000_000,
            "details": {"parameter_size": "70B"},
        },
    ]
    caps = {
        "qwen2.5vl:7b": ["completion", "vision"],
        "nomic-embed-text:latest": ["embedding"],
        "huge:70b": ["completion"],
    }
    hw = {"ram_available_mb": 14000, "ram_total_mb": 16000, "gpus": []}
    cands = {c.model: c for c in ollama_candidates(ollama_stub(tags, caps), hw)}
    assert cands["qwen2.5vl:7b"].capabilities == VISION
    assert cands["nomic-embed-text:latest"].capabilities == frozenset({"embeddings"})
    assert cands["huge:70b"].fits_hardware is False
    assert cands["qwen2.5vl:7b"].fits_hardware is True


def test_ollama_candidates_empty_when_ollama_down():
    def handler(request):
        raise httpx.ConnectError("down")

    client = OllamaClient(make_config(), transport=httpx.MockTransport(handler))
    assert ollama_candidates(client) == []


def test_remote_candidates_from_user_selection():
    cands = remote_candidates(
        {
            "groq": ["llama-3.3-70b-versatile", "meta-llama/llama-4-scout-17b"],
            "deepgram": ["nova-3"],
            "desconocido": ["x"],
        },
        has_key=lambda p: p == "groq",
    )
    keys = {c.key: c for c in cands}
    assert set(keys) == {
        "groq:llama-3.3-70b-versatile",
        "groq:meta-llama/llama-4-scout-17b",
    }
    assert "vision" in keys["groq:meta-llama/llama-4-scout-17b"].capabilities
    assert keys["groq:llama-3.3-70b-versatile"].capabilities == CHAT
    assert all(c.ready and c.access is AccessTier.FREE_LIMITED for c in cands)


def test_ollama_backend_maps_errors_and_streams():
    backend = OllamaChatBackend(ollama_stub([], {}))
    chunks: list[str] = []
    text, stats = backend.chat_stream(
        [{"role": "user", "content": "x"}], "m", on_chunk=chunks.append
    )
    assert text == "hi" and chunks == ["hi"]

    def down(request):
        raise httpx.ConnectError("down")

    broken = OllamaChatBackend(
        OllamaClient(make_config(), transport=httpx.MockTransport(down))
    )
    with pytest.raises(ProviderError) as info:
        broken.chat_stream([{"role": "user", "content": "x"}], "m")
    assert info.value.kind == "network"


def test_specialization_breaks_ties_by_task():
    general = Candidate(
        "ollama", "qwen3:8b", True, AccessTier.LOCAL, CHAT, 3, 3, fits_hardware=True
    )
    vision = Candidate(
        "ollama",
        "qwen2.5vl:7b",
        True,
        AccessTier.LOCAL,
        VISION,
        3,
        3,
        fits_hardware=True,
    )
    coder = Candidate(
        "ollama",
        "qwen2.5-coder:7b",
        True,
        AccessTier.LOCAL,
        frozenset({"chat", "code"}),
        3,
        3,
        fits_hardware=True,
    )
    cands = [vision, coder, general]  # el orden de la lista no debe decidir
    policy = RoutingPolicy(Strategy.QUALITY)
    assert route(RouteRequest(Task.TRANSLATION), cands, policy).primary == general
    assert route(RouteRequest(Task.ANSWER), cands, policy).primary == general
    assert route(RouteRequest(Task.CHALLENGE), cands, policy).primary == coder
    assert route(RouteRequest(Task.VISION), cands, policy).primary == vision

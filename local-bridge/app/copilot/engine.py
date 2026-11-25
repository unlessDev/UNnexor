"""Motor de generación del copiloto: enrutador + ejecutor + uso.

Une las piezas de infraestructura (candidatos de Ollama y remotos,
consentimientos, historial de uso) con el dominio (``route``/``run_chat``).
Los candidatos se cachean ``ttl_s`` segundos para no consultar
``/api/show`` en cada respuesta.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from ..ollama_client import OllamaClient, OllamaError
from ..providers.base import ProviderError
from ..providers.usage import UsageLedger
from ..routing.candidates import (
    make_client_factory,
    ollama_candidates,
    remote_candidates,
)
from ..routing.executor import run_chat
from ..routing.router import (
    Candidate,
    RouteDecision,
    RouteRequest,
    RoutingPolicy,
    Task,
    route,
)


@dataclass(frozen=True)
class GenerationResult:
    text: str
    model_label: str
    used_fallback: bool
    stats: dict[str, Any]


class GenerationEngine(Protocol):
    def generate(
        self,
        task: Task,
        messages: list[dict[str, Any]],
        *,
        sensitive: bool = False,
        on_chunk: Callable[[str], None] | None = None,
        should_abort: Callable[[], bool] | None = None,
        on_notice: Callable[[str], None] | None = None,
        temperature: float | None = None,
        local_only: bool = False,
    ) -> GenerationResult: ...

    def decide(
        self, task: Task, sensitive: bool = False, local_only: bool = False
    ) -> RouteDecision: ...


class RoutedEngine:
    def __init__(
        self,
        ollama: OllamaClient,
        *,
        policy: Callable[[], RoutingPolicy],
        remote_models: Callable[[], Mapping[str, list[str]]],
        has_key: Callable[[str], bool],
        hardware: Callable[[], Mapping[str, Any] | None] | None = None,
        ledger: UsageLedger | None = None,
        ttl_s: float = 60.0,
        client_factory: Callable[[Candidate], Any] | None = None,
    ) -> None:
        self._ollama = ollama
        self._policy = policy
        self._remote_models = remote_models
        self._has_key = has_key
        self._hardware = hardware
        self._ledger = ledger
        self._ttl = ttl_s
        self._client_for = client_factory or make_client_factory(ollama)
        self._lock = threading.Lock()
        self._cached: tuple[float, list[Candidate]] | None = None

    def warm_up(self, task: Task = Task.ANSWER, local_only: bool = False) -> str:
        """Precarga el modelo local que atendería ``task`` (bloqueante: usar
        en un hilo de trabajo al iniciar la sesión). Devuelve un resumen."""
        decision = self.decide(task, local_only=local_only)
        primary = decision.primary
        if primary is None:
            return decision.explain()
        if not primary.local:
            return f"{primary.label}: servicio remoto, no requiere carga."
        try:
            seconds = self._ollama.preload(primary.model)
        except OllamaError as exc:
            return f"No se pudo precargar {primary.label}: {exc.message}"
        return f"{primary.label} listo ({seconds:.1f} s)."

    def invalidate(self) -> None:
        with self._lock:
            self._cached = None

    def candidates(self) -> list[Candidate]:
        with self._lock:
            if self._cached and time.monotonic() - self._cached[0] < self._ttl:
                return list(self._cached[1])
        hw = self._hardware() if self._hardware else None
        found = ollama_candidates(self._ollama, hw) + remote_candidates(
            self._remote_models(), self._has_key
        )
        with self._lock:
            self._cached = (time.monotonic(), found)
        return list(found)

    def decide(
        self, task: Task, sensitive: bool = False, local_only: bool = False
    ) -> RouteDecision:
        candidates = self.candidates()
        if local_only:
            candidates = [c for c in candidates if c.local]
        return route(
            RouteRequest(task, sensitive=sensitive), candidates, self._policy()
        )

    def generate(
        self,
        task: Task,
        messages: list[dict[str, Any]],
        *,
        sensitive: bool = False,
        on_chunk: Callable[[str], None] | None = None,
        should_abort: Callable[[], bool] | None = None,
        on_notice: Callable[[str], None] | None = None,
        temperature: float | None = None,
        local_only: bool = False,
    ) -> GenerationResult:
        decision = self.decide(task, sensitive, local_only)
        backends: dict[str, Any] = {}

        def client_for(candidate: Candidate) -> Any:
            backend = self._client_for(candidate)
            backends[candidate.key] = backend
            return backend

        def usage(
            candidate: Candidate, stats: dict[str, Any], error: ProviderError | None
        ) -> None:
            if self._ledger is None or candidate.local:
                return
            client_limits = dict(
                getattr(backends.get(candidate.key), "last_rate_limits", {}) or {}
            )
            self._ledger.record(
                candidate.provider_id,
                task.value,
                errors=1 if error else 0,
                input_tokens=stats.get("prompt_eval_count"),
                output_tokens=stats.get("eval_count"),
                chars_in=sum(len(str(m.get("content", ""))) for m in messages),
                chars_out=int(stats.get("chars", 0) or 0),
                rate_limits=client_limits or None,
            )

        result = run_chat(
            decision,
            messages,
            client_for=client_for,
            on_chunk=on_chunk,
            should_abort=should_abort,
            on_notice=on_notice,
            temperature=temperature,
            on_usage=usage,
        )
        return GenerationResult(
            result.text, result.candidate.label, result.used_fallback, result.stats
        )

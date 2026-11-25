"""Ejecución de una decisión de enrutamiento para tareas de chat.

* Prueba el modelo principal y, si falla antes de emitir texto, las
  alternativas permitidas en orden. Cada cambio se comunica con
  ``on_notice`` (y se marca expresamente si pasa de local a remoto).
* Si ya se emitió texto, no se cambia de modelo (duplicaría la respuesta):
  se informa el error.
* La cancelación del usuario nunca dispara alternativas.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from ..providers.base import ProviderError
from .router import Candidate, RouteDecision


class ChatBackend(Protocol):
    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float | None = None,
        on_chunk: Callable[[str], None] | None = None,
        should_abort: Callable[[], bool] | None = None,
    ) -> tuple[str, dict[str, Any]]: ...


class RoutingError(RuntimeError):
    def __init__(
        self, message: str, attempts: tuple[tuple[str, str], ...] = ()
    ) -> None:
        super().__init__(message)
        self.message = message
        self.attempts = attempts


class RoutingCancelled(RuntimeError):
    """El usuario detuvo la generación."""


@dataclass(frozen=True)
class ExecutionResult:
    text: str
    stats: dict[str, Any]
    candidate: Candidate
    attempts: tuple[tuple[str, str], ...]  # (clave candidata, error) previos

    @property
    def used_fallback(self) -> bool:
        return bool(self.attempts)


UsageHook = Callable[[Candidate, dict[str, Any], "ProviderError | None"], None]


def run_chat(
    decision: RouteDecision,
    messages: list[dict[str, Any]],
    *,
    client_for: Callable[[Candidate], ChatBackend],
    on_chunk: Callable[[str], None] | None = None,
    should_abort: Callable[[], bool] | None = None,
    on_notice: Callable[[str], None] | None = None,
    temperature: float | None = None,
    on_usage: UsageHook | None = None,
    max_attempts: int = 3,
) -> ExecutionResult:
    if decision.primary is None:
        raise RoutingError(decision.explain())
    attempts: list[tuple[str, str]] = []
    chain = [decision.primary, *decision.fallbacks][:max_attempts]
    for index, candidate in enumerate(chain):
        emitted = False

        def forward(delta: str) -> None:
            nonlocal emitted
            emitted = True
            if on_chunk is not None:
                on_chunk(delta)

        if index > 0 and on_notice is not None:
            crossing = decision.crosses_to_remote(candidate)
            on_notice(
                f"{chain[index - 1].label} falló ({attempts[-1][1]}); se usa "
                f"{candidate.label}"
                + (" — servicio REMOTO autorizado por ti." if crossing else ".")
            )
        backend: ChatBackend | None = None
        try:
            backend = client_for(candidate)
            text, stats = backend.chat_stream(
                messages,
                candidate.model,
                temperature,
                forward,
                should_abort,
            )
        except ProviderError as exc:
            if exc.kind == "cancelled" or (should_abort is not None and should_abort()):
                raise RoutingCancelled() from exc
            if on_usage is not None:
                on_usage(candidate, {}, exc)
            attempts.append((candidate.key, exc.message))
            if emitted:
                raise RoutingError(
                    f"{candidate.label} se interrumpió: {exc.message}", tuple(attempts)
                ) from exc
            continue
        except Exception as exc:  # error no tipado del adaptador
            attempts.append((candidate.key, str(exc) or exc.__class__.__name__))
            if emitted:
                raise RoutingError(
                    f"{candidate.label} se interrumpió: {exc}", tuple(attempts)
                ) from exc
            continue
        finally:
            closer = getattr(backend, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:  # cerrar nunca debe ocultar el resultado
                    pass
        if on_usage is not None:
            on_usage(candidate, stats, None)
        return ExecutionResult(text, stats, candidate, tuple(attempts))
    detail = "; ".join(f"{key}: {error}" for key, error in attempts)
    raise RoutingError(
        f"Ningún modelo permitido pudo responder ({detail}).", tuple(attempts)
    )

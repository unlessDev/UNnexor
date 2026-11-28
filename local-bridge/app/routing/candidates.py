"""Construcción de candidatos reales para el enrutador (infraestructura).

* Ollama: modelos instalados (``/api/tags``) con sus capacidades reales
  (``/api/show``: ``completion``, ``vision``, ``embedding``…) y el encaje
  con el hardware según ``model_evaluator``.
* Remotos: proveedores con clave guardada y los modelos que el usuario
  eligió para el copiloto. Las capacidades remotas de visión se infieren
  del nombre (no hay API común que las informe) y se marcan como inferidas.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping
from typing import Any

from ..model_evaluator import (
    TIER_ALTO,
    TIER_IMPOSIBLE,
    assess_model,
    parse_parameter_count,
)
from ..ollama_client import (
    GenerationCancelled,
    ModelNotFoundError,
    OllamaClient,
    OllamaConnectionError,
    OllamaError,
)
from ..providers.access import AccessTier
from ..providers.base import ProviderError
from ..providers.catalog import get_provider
from .router import Candidate

log = logging.getLogger("unnexor.routing")

_VISION_HINTS = re.compile(
    r"(vision|-vl|vl:|llava|pixtral|gpt-4o|gpt-4\.1|gpt-5|gemini|claude|llama-4|"
    r"scout|maverick|gemma-?3|qwen2\.5-?vl|qwen3-vl|minicpm-v|moondream)",
    re.I,
)
_FAST_PROVIDERS = {"groq": 5, "cerebras": 5, "gemini": 4, "openai": 4}


def _ollama_quality_speed(params_b: float | None) -> tuple[int, int]:
    if params_b is None:
        return 3, 3
    if params_b <= 4:
        return 2, 4
    if params_b <= 9:
        return 3, 3
    if params_b <= 15:
        return 4, 2
    return 5, 1


def ollama_candidates(
    ollama: OllamaClient, hw: Mapping[str, Any] | None = None
) -> list[Candidate]:
    """Modelos instalados en Ollama como candidatos (vacío si no responde)."""
    try:
        installed = ollama.installed_models_full()
    except OllamaError as exc:
        log.info("Ollama no disponible para el enrutador: %s", exc.message)
        return []
    out: list[Candidate] = []
    for entry in installed:
        name = str(entry.get("name") or "")
        if not name:
            continue
        details = entry.get("details") or {}
        try:
            caps_raw = ollama.show(name).get("capabilities") or []
        except OllamaError:
            caps_raw = []
        caps: set[str] = set()
        if "completion" in caps_raw or not caps_raw:
            caps.add("chat")
        if "vision" in caps_raw:
            caps.add("vision")
        if "embedding" in caps_raw:
            caps.add("embeddings")
        if re.search(r"cod(?:er|e|estral)|starcoder|deepseek-coder", name, re.I):
            caps.add("code")  # especializado en programación
        params = parse_parameter_count(details.get("parameter_size"), name)
        quality, speed = _ollama_quality_speed(params)
        fits: bool | None = None
        note = ""
        if hw:
            assessment = assess_model(
                model_name=name,
                size_bytes=entry.get("size"),
                parameter_size=details.get("parameter_size"),
                hw=dict(hw),
                installed=True,
            )
            fits = assessment.tier != TIER_IMPOSIBLE
            if assessment.tier == TIER_ALTO:
                speed = max(1, speed - 1)
            note = assessment.explanation
        out.append(
            Candidate(
                provider_id="ollama",
                model=name,
                local=True,
                access=AccessTier.LOCAL,
                capabilities=frozenset(caps),
                quality=quality,
                speed=speed,
                ready=True,
                fits_hardware=fits,
                hardware_note=note,
            )
        )
    return out


def remote_candidates(
    selected_models: Mapping[str, list[str]],
    has_key: Callable[[str], bool],
) -> list[Candidate]:
    """Modelos remotos elegidos por el usuario (``{proveedor: [modelos]}``)."""
    out: list[Candidate] = []
    for provider_id, models in selected_models.items():
        info = get_provider(provider_id)
        if info is None or info.local or "chat" not in info.capabilities:
            continue
        ready = has_key(provider_id)
        for model in models:
            caps = {"chat"}
            if "vision" in info.capabilities and _VISION_HINTS.search(model):
                caps.add("vision")
            out.append(
                Candidate(
                    provider_id=provider_id,
                    model=model,
                    local=False,
                    access=info.access,
                    capabilities=frozenset(caps),
                    quality=4,
                    speed=_FAST_PROVIDERS.get(provider_id, 4),
                    ready=ready,
                )
            )
    return out


class OllamaChatBackend:
    """Adapta ``OllamaClient`` al contrato del ejecutor con errores tipados."""

    def __init__(self, ollama: OllamaClient) -> None:
        self._ollama = ollama

    def close(self) -> None:
        """El cliente de Ollama es compartido: no se cierra aquí."""

    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float | None = None,
        on_chunk: Callable[[str], None] | None = None,
        should_abort: Callable[[], bool] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        try:
            return self._ollama.chat_stream(
                messages,
                model=model,
                temperature=temperature,
                on_chunk=on_chunk,
                should_abort=should_abort,
            )
        except GenerationCancelled as exc:
            raise ProviderError("Generación cancelada.", "cancelled") from exc
        except OllamaConnectionError as exc:
            raise ProviderError(exc.message, "network") from exc
        except ModelNotFoundError as exc:
            raise ProviderError(exc.message, "model_missing") from exc
        except OllamaError as exc:
            raise ProviderError(exc.message, "general") from exc


def make_client_factory(
    ollama: OllamaClient, timeout: float = 120.0
) -> Callable[[Candidate], Any]:
    """Factoría para ``run_chat``: Ollama local o cliente remoto con clave."""

    def client_for(candidate: Candidate) -> Any:
        if candidate.local:
            return OllamaChatBackend(ollama)
        from ..providers.manager import get_client_for

        return get_client_for(candidate.provider_id, timeout=timeout)

    return client_for

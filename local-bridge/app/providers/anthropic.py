"""Adaptador nativo de la API de Anthropic (Messages API).

Protocolo (documentación oficial de Anthropic):

* Autenticación: ``x-api-key`` + ``anthropic-version: 2023-06-01``.
* Catálogo: ``GET /v1/models`` → ``{"data": [{"id", "display_name"}], ...}``
  (no consume tokens; se usa para validar la clave).
* Chat: ``POST /v1/messages`` con ``stream: true`` → SSE con eventos
  ``content_block_delta`` (``delta.type == "text_delta"``), ``message_stop``
  y ``error``. El *system prompt* va en el campo ``system`` (no es un rol) y
  ``max_tokens`` es obligatorio.
* Imágenes: bloques ``{"type": "image", "source": {"type": "base64",
  "media_type": "image/png", "data": ...}}``.

Expone la misma interfaz que ``OpenAICompatClient`` (``list_models``,
``test_connection``, ``chat_stream``) para que el resto de la aplicación no
dependa del proveedor concreto.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

import httpx

from .base import ProviderError, ProviderModel

API_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 4096

_HTTP_ERRORS: dict[int, tuple[str, str]] = {
    401: ("Clave de Anthropic inválida.", "auth"),
    403: ("La clave de Anthropic no tiene permiso para esta operación.", "auth"),
    404: ("El modelo no existe o tu cuenta no tiene acceso a él.", "auth"),
    413: ("La petición supera el tamaño permitido por Anthropic.", "general"),
    429: (
        "Límite de uso de Anthropic alcanzado (429). Espera e inténtalo de nuevo.",
        "quota",
    ),
    529: ("Anthropic está sobrecargado (529). Inténtalo en unos segundos.", "quota"),
}


class AnthropicClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.anthropic.com/v1",
        timeout: float = 120.0,
        transport: httpx.BaseTransport | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        self.provider_id = "anthropic"
        self.base_url = base_url.rstrip("/") + "/"
        self.max_tokens = max_tokens
        self.last_rate_limits: dict[str, str] = {}
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={
                "x-api-key": api_key,
                "anthropic-version": API_VERSION,
                "content-type": "application/json",
            },
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    # ------------------------------------------------------------------ #
    def list_models(self) -> list[ProviderModel]:
        models: list[ProviderModel] = []
        params: dict[str, str] = {"limit": "100"}
        for _page in range(10):  # paginación acotada
            try:
                response = self._client.get("models", params=params, timeout=15.0)
            except httpx.HTTPError as exc:
                raise _network_error(exc) from exc
            if response.status_code != 200:
                raise _http_error(response)
            payload = response.json()
            for entry in payload.get("data") or []:
                if isinstance(entry, dict) and entry.get("id"):
                    models.append(
                        ProviderModel(
                            id=str(entry["id"]),
                            provider=self.provider_id,
                            owned_by="anthropic",
                            raw=entry,
                        )
                    )
            if not payload.get("has_more") or not payload.get("last_id"):
                break
            params["after_id"] = str(payload["last_id"])
        return models

    def test_connection(self) -> dict[str, Any]:
        models = self.list_models()
        return {
            "ok": True,
            "model_count": len(models),
            "sample": [m.id for m in models[:6]],
        }

    # ------------------------------------------------------------------ #
    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float | None = None,
        on_chunk: Callable[[str], None] | None = None,
        should_abort: Callable[[], bool] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        system, converted = split_system(messages)
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": self.max_tokens,
            "messages": converted,
            "stream": True,
        }
        if system:
            payload["system"] = system
        if temperature is not None:
            payload["temperature"] = max(0.0, min(1.0, float(temperature)))
        parts: list[str] = []
        usage: dict[str, Any] = {}
        started = time.perf_counter()
        try:
            with self._client.stream("POST", "messages", json=payload) as response:
                self.last_rate_limits = {
                    k: v
                    for k, v in response.headers.items()
                    if k.lower().startswith("anthropic-ratelimit-")
                }
                if response.status_code != 200:
                    response.read()
                    raise _http_error(response)
                event = ""
                for line in response.iter_lines():
                    if should_abort is not None and should_abort():
                        raise ProviderError("Generación cancelada.", "cancelled")
                    if line.startswith("event:"):
                        event = line[len("event:") :].strip()
                        continue
                    if not line.startswith("data:"):
                        continue
                    try:
                        data = json.loads(line[len("data:") :].strip())
                    except json.JSONDecodeError as exc:
                        raise ProviderError(
                            "Anthropic envió un fragmento no válido.", "general"
                        ) from exc
                    kind = data.get("type") or event
                    if kind == "content_block_delta":
                        delta = data.get("delta") or {}
                        if delta.get("type") == "text_delta" and delta.get("text"):
                            text = str(delta["text"])
                            parts.append(text)
                            if on_chunk is not None:
                                on_chunk(text)
                    elif kind == "message_start":
                        usage.update((data.get("message") or {}).get("usage") or {})
                    elif kind == "message_delta":
                        usage.update(data.get("usage") or {})
                    elif kind == "error":
                        error = data.get("error") or {}
                        raise ProviderError(
                            f"Anthropic: {error.get('message') or 'error en el streaming'}",
                            (
                                "quota"
                                if error.get("type") == "overloaded_error"
                                else "general"
                            ),
                        )
                    elif kind == "message_stop":
                        break
        except httpx.HTTPError as exc:
            raise _network_error(exc) from exc
        text = "".join(parts)
        stats: dict[str, Any] = {
            "provider": self.provider_id,
            "model": model,
            "chars": len(text),
            "seconds": round(time.perf_counter() - started, 2),
        }
        if "input_tokens" in usage:
            stats["prompt_eval_count"] = usage["input_tokens"]
        if "output_tokens" in usage:
            stats["eval_count"] = usage["output_tokens"]
        return text, stats


def split_system(
    messages: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """Separa el *system prompt* y convierte el resto al formato Anthropic.

    Anthropic exige alternancia user/assistant empezando por ``user``: los
    mensajes consecutivos del mismo rol se fusionan.
    """
    system_parts: list[str] = []
    converted: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role", "user")
        if role == "system":
            if message.get("content"):
                system_parts.append(str(message["content"]))
            continue
        role = "assistant" if role == "assistant" else "user"
        blocks: list[dict[str, Any]] = []
        for b64 in message.get("images") or []:
            blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": b64,
                    },
                }
            )
        content = str(message.get("content") or "")
        if content:
            blocks.append({"type": "text", "text": content})
        if not blocks:
            continue
        if converted and converted[-1]["role"] == role:
            converted[-1]["content"].extend(blocks)
        else:
            converted.append({"role": role, "content": blocks})
    if converted and converted[0]["role"] != "user":
        converted.insert(
            0, {"role": "user", "content": [{"type": "text", "text": "…"}]}
        )
    return "\n\n".join(system_parts), converted


def _http_error(response: httpx.Response) -> ProviderError:
    detail = ""
    try:
        body = response.json()
        detail = str((body.get("error") or {}).get("message") or "")
    except (ValueError, AttributeError):
        pass
    if response.status_code == 400 and "credit balance" in detail.lower():
        return ProviderError(
            "Tu saldo de créditos de Anthropic es insuficiente.", "payment"
        )
    if response.status_code in _HTTP_ERRORS:
        message, kind = _HTTP_ERRORS[response.status_code]
        return ProviderError(message, kind)
    suffix = f": {detail[:200]}" if detail else ""
    return ProviderError(
        f"Anthropic devolvió HTTP {response.status_code}{suffix}", "general"
    )


def _network_error(exc: httpx.HTTPError) -> ProviderError:
    if isinstance(exc, httpx.TimeoutException):
        return ProviderError("Anthropic tardó demasiado en responder.", "network")
    return ProviderError("No se pudo conectar con Anthropic.", "network")

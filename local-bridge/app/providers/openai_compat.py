"""Adaptador genérico OpenAI-compatible (12 proveedores lo usan directamente).

Protocolo verificado en las documentaciones oficiales (2026-11-16):

* Autenticación: ``Authorization: Bearer <key>``.
* Chat: ``POST {base_url}/chat/completions`` con ``"stream": true`` → SSE
  (``data: {...}`` hasta ``data: [DONE]``; los deltas llegan en
  ``choices[0].delta.content``).
* Catálogo: ``GET {base_url}/models`` (cuando el proveedor lo expone).
* Errores típicos documentados: 401 ``invalid_api_key`` (OpenAI),
  402 créditos insuficientes y 429 límites (OpenRouter).

Este adaptador NO inventa datos: si un proveedor no expone ``/models`` o
no informa límites, se devuelven listas vacías/None y el estado queda
"acceso no verificado" para lo que la API no confirme.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx

from .base import ProviderError, ProviderModel

# Imágenes: mensaje estilo multimodal OpenAI (data URL base64).
_KIND_HTTP = {
    401: ("Clave API inválida o sin permisos para este proveedor.", "auth"),
    403: (
        "El proveedor rechazó la petición (clave sin acceso o región " "bloqueada).",
        "auth",
    ),
    402: (
        "El proveedor indica saldo/créditos insuficientes para este " "modelo.",
        "payment",
    ),
    404: (
        "El modelo solicitado no existe o no está disponible para tu " "cuenta.",
        "auth",
    ),
    413: ("La petición supera el tamaño permitido por el proveedor.", "general"),
    429: (
        "Límite de uso del proveedor alcanzado (429). Espera o usa un "
        "modelo con cuota disponible.",
        "quota",
    ),
}


class OpenAICompatClient:
    """Cliente de chat/catálogo para endpoints OpenAI-compatibles."""

    def __init__(
        self,
        provider_id: str,
        base_url: str,
        api_key: str,
        timeout: float = 120.0,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        base = base_url.rstrip("/")
        if not base.endswith("/"):
            base += "/"
        self.provider_id = provider_id
        self.base_url = base
        self._client = httpx.Client(
            base_url=base,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    # ------------------------------------------------------------------ #
    # Catálogo
    # ------------------------------------------------------------------ #
    def list_models(self) -> List[ProviderModel]:
        """Modelos realmente disponibles para la cuenta (GET /models)."""
        try:
            response = self._client.get("models", timeout=15.0)
        except httpx.HTTPError as exc:
            raise _network_error(exc) from exc
        if response.status_code in _KIND_HTTP:
            message, kind = _KIND_HTTP[response.status_code]
            raise ProviderError(message, kind)
        if response.status_code != 200:
            raise ProviderError(
                f"El proveedor devolvió HTTP {response.status_code} al "
                "consultar el catálogo de modelos.",
                "general",
            )
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise ProviderError(
                "Respuesta no válida del catálogo de modelos.", "general"
            ) from exc
        entries = payload.get("data") or []
        models: List[ProviderModel] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            model_id = entry.get("id") or ""
            if not model_id:
                continue
            models.append(
                ProviderModel(
                    id=model_id,
                    provider=self.provider_id,
                    owned_by=str(entry.get("owned_by") or entry.get("owned-by") or ""),
                    context_window=entry.get("context_window"),
                    raw=entry,
                )
            )
        return models

    def test_connection(self) -> Dict[str, Any]:
        """Valida credenciales consultando el catálogo (sin consumir tokens)."""
        models = self.list_models()
        return {
            "ok": True,
            "model_count": len(models),
            "sample": [m.id for m in models[:6]],
        }

    # ------------------------------------------------------------------ #
    # Chat en streaming (SSE)
    # ------------------------------------------------------------------ #
    def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        temperature: float | None = None,
        on_chunk: Optional[Callable[[str], None]] = None,
        should_abort: Optional[Callable[[], bool]] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """Envía la conversación y acumula el streaming SSE.

        Los mensajes pueden incluir imágenes (clave ``images`` con base64):
        se convierten al formato multimodal de OpenAI
        (``content: [text, image_url]`` con data URL).
        """
        payload: Dict[str, Any] = {
            "model": model,
            "messages": [_encode_message(m) for m in messages],
            "stream": True,
        }
        if temperature is not None:
            payload["temperature"] = temperature

        parts: List[str] = []
        started = time.perf_counter()
        try:
            with self._client.stream(
                "POST", "chat/completions", json=payload
            ) as response:
                if response.status_code in _KIND_HTTP:
                    message, kind = _KIND_HTTP[response.status_code]
                    raise ProviderError(message, kind)
                if response.status_code != 200:
                    detail = _read_error(response)
                    raise ProviderError(
                        f"El proveedor devolvió HTTP {response.status_code}: "
                        f"{detail}",
                        "general",
                    )
                for line in response.iter_lines():
                    if should_abort is not None and should_abort():
                        raise ProviderError("Generación cancelada.", "cancelled")
                    if not line or line.startswith(":"):
                        continue  # comentario keep-alive (p. ej. OpenRouter)
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:") :].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError as exc:
                        raise ProviderError(
                            "El proveedor envió un fragmento de streaming "
                            "no válido.",
                            "general",
                        ) from exc
                    delta = _extract_delta(obj)
                    if delta:
                        parts.append(delta)
                        if on_chunk is not None:
                            on_chunk(delta)
        except httpx.HTTPError as exc:
            raise _network_error(exc) from exc

        text = "".join(parts)
        elapsed = round(time.perf_counter() - started, 2)
        stats = {
            "provider": self.provider_id,
            "model": model,
            "chars": len(text),
            "seconds": elapsed,
        }
        return text, stats


# --------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------- #
def _encode_message(message: Dict[str, Any]) -> Dict[str, Any]:
    """Convierte un mensaje interno al formato OpenAI-compatible."""
    images = message.get("images")
    content = message.get("content", "")
    if not images:
        return {
            "role": message.get("role", "user"),
            "content": content,
        }
    parts: List[Dict[str, Any]] = [{"type": "text", "text": content}]
    for b64 in images:
        parts.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}"},
            }
        )
    return {"role": message.get("role", "user"), "content": parts}


def _extract_delta(obj: Dict[str, Any]) -> str:
    """Extrae ``choices[0].delta.content`` (o ``message.content`` final)."""
    choices = obj.get("choices") or []
    if not choices:
        return ""
    choice = choices[0] or {}
    delta = choice.get("delta") or {}
    if isinstance(delta.get("content"), str):
        return delta["content"]
    message = choice.get("message") or {}
    if isinstance(message.get("content"), str):
        return message["content"]
    return ""


def _read_error(response: httpx.Response) -> str:
    try:
        body = response.json()
        if isinstance(body, dict):
            error = body.get("error")
            if isinstance(error, dict):
                return str(error.get("message") or error)
            if isinstance(error, str):
                return error
            return str(body)[:300]
    except (json.JSONDecodeError, ValueError):
        pass
    return "(sin detalle)"


def _network_error(exc: httpx.HTTPError) -> ProviderError:
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        return ProviderError(
            "No se pudo conectar con el proveedor (revisa tu conexión o el "
            "estado del servicio).",
            "network",
        )
    if isinstance(exc, (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout)):
        return ProviderError(
            "El proveedor tardó demasiado en responder (timeout).",
            "network",
        )
    return ProviderError(f"Error de red con el proveedor: {exc}", "network")

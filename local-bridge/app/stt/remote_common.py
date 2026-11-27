"""Utilidades compartidas por los adaptadores STT remotos."""

from __future__ import annotations

import httpx

from .base import STTError

_HTTP_ERRORS: dict[int, tuple[str, str]] = {
    400: ("El servicio rechazó el audio o los parámetros enviados.", "invalid_audio"),
    401: ("Clave API inválida o sin permisos para la transcripción.", "auth"),
    403: ("El servicio denegó el acceso (clave sin permiso o región).", "auth"),
    402: ("Saldo o créditos insuficientes para transcribir.", "payment"),
    404: ("El modelo de voz no existe o tu cuenta no tiene acceso.", "auth"),
    413: ("El fragmento de audio supera el tamaño permitido.", "invalid_audio"),
    429: ("Límite de uso del servicio de voz alcanzado (429).", "quota"),
}

LANGUAGE_NAMES = {
    "spanish": "es",
    "english": "en",
    "portuguese": "pt",
    "french": "fr",
    "german": "de",
    "italian": "it",
    "catalan": "ca",
    "japanese": "ja",
    "chinese": "zh",
}


def normalize_language(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip().lower()
    return LANGUAGE_NAMES.get(raw, raw)


def retry_after_seconds(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


def error_from_response(response: httpx.Response, provider: str) -> STTError:
    message, kind = _HTTP_ERRORS.get(
        response.status_code,
        (f"{provider} devolvió HTTP {response.status_code}.", "general"),
    )
    retry = retry_after_seconds(response) if response.status_code == 429 else None
    if retry is not None:
        message += f" Reintenta en {retry:.0f} s."
    return STTError(f"{provider}: {message}", kind, retry_after=retry)


def network_error(exc: httpx.HTTPError, provider: str) -> STTError:
    if isinstance(exc, httpx.TimeoutException):
        return STTError(f"{provider} tardó demasiado en transcribir.", "network")
    return STTError(
        f"No se pudo conectar con {provider} (revisa la conexión).", "network"
    )

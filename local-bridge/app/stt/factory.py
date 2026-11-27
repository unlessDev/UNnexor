"""Construcción de motores de voz y estado de disponibilidad.

La búsqueda de claves se inyecta (por defecto, el keyring del gestor de
proveedores) para que el núcleo pueda probarse sin credenciales reales.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .base import SpeechRecognizer, STTError
from .catalog import ALL_MODELS, STTModelSpec, get_spec

KeyLookup = Callable[[str], "str | None"]

_BASE_URLS = {
    "groq": "https://api.groq.com/openai/v1",
    "openai": "https://api.openai.com/v1",
}


def _default_key_lookup(provider_id: str) -> str | None:
    from ..providers.manager import get_key

    try:
        return get_key(provider_id)
    except Exception:  # keyring no disponible en este sistema
        return None


@dataclass(frozen=True)
class EngineStatus:
    spec: STTModelSpec
    ready: bool
    reason: str = ""  # por qué no está listo (para la interfaz)
    installed_mb: float | None = None


def engine_status(
    spec: STTModelSpec, key_lookup: KeyLookup | None = None
) -> EngineStatus:
    if spec.is_local:
        from . import faster_whisper_engine as fw

        ok, reason = fw.engine_available()
        if not ok:
            return EngineStatus(spec, False, reason)
        if not fw.is_installed(spec.model):
            size = f" (~{spec.download_mb} MB)" if spec.download_mb else ""
            return EngineStatus(spec, False, f"Modelo no descargado{size}.")
        return EngineStatus(spec, True, installed_mb=fw.installed_size_mb(spec.model))
    lookup = key_lookup or _default_key_lookup
    if not lookup(spec.provider_id):
        return EngineStatus(
            spec, False, f"Conecta tu cuenta de {spec.provider_id} en 'Proveedores'."
        )
    return EngineStatus(spec, True)


def all_statuses(key_lookup: KeyLookup | None = None) -> list[EngineStatus]:
    return [engine_status(spec, key_lookup) for spec in ALL_MODELS]


def create_recognizer(
    key: str,
    *,
    key_lookup: KeyLookup | None = None,
    transport: Any = None,
    **local_options: Any,
) -> SpeechRecognizer:
    """Crea el motor ``key`` (p. ej. ``faster-whisper:small``)."""
    spec = get_spec(key)
    if spec.is_local:
        from .faster_whisper_engine import FasterWhisperRecognizer

        return FasterWhisperRecognizer(spec.model, **local_options)
    api_key = (key_lookup or _default_key_lookup)(spec.provider_id)
    if not api_key:
        raise STTError(
            f"No hay clave guardada para {spec.provider_id}. Conéctalo en "
            "'Proveedores'.",
            "auth",
        )
    if spec.engine == "deepgram":
        from .deepgram_engine import DeepgramTranscriber

        return DeepgramTranscriber(api_key, spec.model, transport=transport)
    if spec.engine in _BASE_URLS:
        from .openai_compat_engine import OpenAICompatTranscriber

        return OpenAICompatTranscriber(
            spec.provider_id,
            _BASE_URLS[spec.engine],
            api_key,
            spec.model,
            label=spec.label.split(" · ")[0],
            transport=transport,
        )
    raise STTError(f"Motor de voz sin adaptador: {spec.engine}", "unavailable")

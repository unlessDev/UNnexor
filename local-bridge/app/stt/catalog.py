"""Catálogo de motores y modelos de reconocimiento de voz.

Fuentes verificadas el 2026-11-24 (ver docs/SPEECH.md):

* faster-whisper (MIT): nombres de modelos y repos de ``faster_whisper.utils``
  v1.2.1; tamaños de descarga medidos (base 148 MB, small 486 MB) o
  aproximados para los demás. Los modelos ``distil-*`` son solo inglés.
* Groq: ``/openai/v1/audio/transcriptions``; ``whisper-large-v3-turbo``
  (0,04 $/h, sin traducción) y ``whisper-large-v3`` (0,111 $/h); mínimo
  facturado 10 s por petición; 25 MB por archivo en el plan gratuito;
  límites publicados 20 RPM, 2K RPD, 7,2K s de audio/hora (ASH).
* OpenAI: ``gpt-transcribe`` (recomendado); ``whisper-1``,
  ``gpt-4o-transcribe`` y ``gpt-4o-mini-transcribe`` están deprecados y se
  apagan el 2027-02-26. 25 MB por archivo.
* Deepgram: ``POST /v1/listen`` (pregrabado), ``nova-3`` con ``es`` y
  ``es-419``; idioma por defecto ``en``; 200 $ de crédito inicial y luego
  0,0043 $/min (monolingüe) o 0,0052 $/min (multilingüe).

Requisitos de memoria locales: estimaciones para CPU int8 con fragmentos
cortos; se sustituyen por mediciones reales cuando existe benchmark.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..providers.access import AccessTier
from .base import STTMode

VERIFIED_AT = "2026-11-24"


@dataclass(frozen=True)
class STTModelSpec:
    key: str  # identificador estable "engine:model"
    engine: str  # faster-whisper | groq | openai | deepgram
    model: str
    label: str
    mode: STTMode
    access: AccessTier
    spanish_quality: int  # 1 (pobre) – 5 (excelente), orientativo
    speed: int  # 1 (lento) – 5 (muy rápido) en su entorno típico
    multilingual: bool = True
    download_mb: int | None = None
    ram_mb: int | None = None  # memoria estimada en CPU int8
    vram_mb: int | None = None  # memoria estimada en GPU (CUDA)
    price_note: str = ""
    limits_note: str = ""
    notes: str = ""
    docs_url: str = ""
    deprecated: str = ""  # fecha/aviso de retirada si existe
    provider_id: str = ""  # proveedor con clave en el keyring
    supports_translation_to_english: bool = False
    tags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_local(self) -> bool:
        return self.access is AccessTier.LOCAL


_FW_DOCS = "https://github.com/SYSTRAN/faster-whisper"

LOCAL_MODELS: tuple[STTModelSpec, ...] = (
    STTModelSpec(
        "faster-whisper:tiny",
        "faster-whisper",
        "tiny",
        "Whisper tiny (local)",
        STTMode.CHUNKED,
        AccessTier.LOCAL,
        spanish_quality=2,
        speed=5,
        download_mb=75,
        ram_mb=350,
        vram_mb=400,
        notes="Muy rápido; comete errores frecuentes en español.",
        docs_url=_FW_DOCS,
        supports_translation_to_english=True,
    ),
    STTModelSpec(
        "faster-whisper:base",
        "faster-whisper",
        "base",
        "Whisper base (local)",
        STTMode.CHUNKED,
        AccessTier.LOCAL,
        spanish_quality=3,
        speed=4,
        download_mb=148,
        ram_mb=500,
        vram_mb=600,
        notes="Equilibrio para equipos modestos; aceptable en español.",
        docs_url=_FW_DOCS,
        supports_translation_to_english=True,
        tags=("recommended-low-end",),
    ),
    STTModelSpec(
        "faster-whisper:small",
        "faster-whisper",
        "small",
        "Whisper small (local)",
        STTMode.CHUNKED,
        AccessTier.LOCAL,
        spanish_quality=4,
        speed=3,
        download_mb=486,
        ram_mb=1100,
        vram_mb=1300,
        notes="Buena calidad en español con CPU de 6+ núcleos.",
        docs_url=_FW_DOCS,
        supports_translation_to_english=True,
        tags=("recommended-mid",),
    ),
    STTModelSpec(
        "faster-whisper:medium",
        "faster-whisper",
        "medium",
        "Whisper medium (local)",
        STTMode.CHUNKED,
        AccessTier.LOCAL,
        spanish_quality=4,
        speed=2,
        download_mb=1530,
        ram_mb=2600,
        vram_mb=2800,
        notes="Más preciso que small; en CPU puede no seguir el ritmo real.",
        docs_url=_FW_DOCS,
        supports_translation_to_english=True,
    ),
    STTModelSpec(
        "faster-whisper:large-v3-turbo",
        "faster-whisper",
        "large-v3-turbo",
        "Whisper large-v3-turbo (local)",
        STTMode.CHUNKED,
        AccessTier.LOCAL,
        spanish_quality=5,
        speed=2,
        download_mb=1620,
        ram_mb=2800,
        vram_mb=2500,
        notes="Calidad casi de large-v3 con mucha menos latencia; ideal con "
        "GPU NVIDIA o CPU potente. No traduce (solo transcribe).",
        docs_url=_FW_DOCS,
        tags=("recommended-high-end",),
    ),
    STTModelSpec(
        "faster-whisper:large-v3",
        "faster-whisper",
        "large-v3",
        "Whisper large-v3 (local)",
        STTMode.CHUNKED,
        AccessTier.LOCAL,
        spanish_quality=5,
        speed=1,
        download_mb=3090,
        ram_mb=4800,
        vram_mb=4500,
        notes="Máxima calidad; necesita GPU NVIDIA para tiempo real.",
        docs_url=_FW_DOCS,
        supports_translation_to_english=True,
    ),
)

REMOTE_MODELS: tuple[STTModelSpec, ...] = (
    STTModelSpec(
        "groq:whisper-large-v3-turbo",
        "groq",
        "whisper-large-v3-turbo",
        "Groq · Whisper large-v3-turbo",
        STTMode.CHUNKED,
        AccessTier.FREE_LIMITED,
        spanish_quality=5,
        speed=5,
        price_note="0,04 $/hora en el plan de pago; mínimo facturado 10 s "
        "por petición.",
        limits_note="Plan gratuito: 20 peticiones/min, 2.000/día, 7.200 s de "
        "audio/hora, 25 MB por archivo (verificar en tu consola).",
        notes="Muy rápido y preciso; requiere Internet y envía el audio a Groq.",
        docs_url="https://console.groq.com/docs/speech-to-text",
        provider_id="groq",
        tags=("recommended-remote",),
    ),
    STTModelSpec(
        "groq:whisper-large-v3",
        "groq",
        "whisper-large-v3",
        "Groq · Whisper large-v3",
        STTMode.CHUNKED,
        AccessTier.FREE_LIMITED,
        spanish_quality=5,
        speed=4,
        price_note="0,111 $/hora en el plan de pago; mínimo facturado 10 s.",
        limits_note="Mismos límites publicados que turbo en el plan gratuito.",
        docs_url="https://console.groq.com/docs/speech-to-text",
        provider_id="groq",
        supports_translation_to_english=True,
    ),
    STTModelSpec(
        "openai:gpt-transcribe",
        "openai",
        "gpt-transcribe",
        "OpenAI · gpt-transcribe",
        STTMode.CHUNKED,
        AccessTier.PAY_AS_YOU_GO,
        spanish_quality=5,
        speed=4,
        price_note="Pago por consumo (consultar precios vigentes).",
        limits_note="25 MB por archivo.",
        notes="Modelo recomendado por OpenAI para transcripción.",
        docs_url="https://developers.openai.com/api/docs/guides/speech-to-text",
        provider_id="openai",
    ),
    STTModelSpec(
        "openai:whisper-1",
        "openai",
        "whisper-1",
        "OpenAI · whisper-1",
        STTMode.CHUNKED,
        AccessTier.PAY_AS_YOU_GO,
        spanish_quality=4,
        speed=3,
        price_note="Pago por consumo.",
        deprecated="Deprecado; OpenAI lo apaga el 2027-02-26.",
        docs_url="https://developers.openai.com/api/docs/guides/speech-to-text",
        provider_id="openai",
    ),
    STTModelSpec(
        "deepgram:nova-3",
        "deepgram",
        "nova-3",
        "Deepgram · Nova-3",
        STTMode.CHUNKED,
        AccessTier.CREDITS,
        spanish_quality=4,
        speed=5,
        price_note="200 $ de crédito inicial sin caducidad; después 0,0043 "
        "$/min (monolingüe) o 0,0052 $/min (multilingüe), por segundo.",
        limits_note="Español: es, es-419. Su API de streaming nativa no está "
        "integrada todavía: se usa la API de audio pregrabado por fragmentos.",
        docs_url="https://developers.deepgram.com/docs/pre-recorded-audio",
        provider_id="deepgram",
    ),
)

ALL_MODELS: tuple[STTModelSpec, ...] = LOCAL_MODELS + REMOTE_MODELS


def get_spec(key: str) -> STTModelSpec:
    for spec in ALL_MODELS:
        if spec.key == key:
            return spec
    raise KeyError(f"Motor de voz desconocido: {key}")


def local_spec(model: str) -> STTModelSpec:
    return get_spec(f"faster-whisper:{model}")

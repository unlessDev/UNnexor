"""Presets de proveedores OpenAI-compatible (base_urls verificados).

Fuente: documentación oficial de cada proveedor, consulta del
2026-11-16 (ver docs/PROVIDERS.md con la tabla completa). ``kind``
describe el acceso según lo documentado; NUNCA precios ni cuotas
exactas no verificables: donde el límite no está publicado se indica
"consultar documentación".

Adaptadores nativos PENDIENTES (protocolo propio, sin implementar):
  * Anthropic  — x-api-key + anthropic-version, POST /v1/messages.
  * Cohere     — POST /v2/chat (v2), Bearer.
  * Cloudflare — REST por-account, Bearer token.
Gemini se incluye vía su capa de compatibilidad OpenAI OFICIAL
(generativelanguage.googleapis.com/v1beta/openai/).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

PRESETS: List[Dict[str, Any]] = [
    {
        "id": "openai",
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "kind": "de pago",
        "key_format": "sk-…",
        "docs": "https://platform.openai.com/docs/api-reference",
        "description": "API oficial de OpenAI (GPT). Requiere facturación activa.",
        "models_endpoint": True,
    },
    {
        "id": "groq",
        "label": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "kind": "free tier documentado (límites por modelo)",
        "key_format": "gsk_…",
        "docs": "https://console.groq.com/docs",
        "description": "Inferencia ultra-rápida de modelos abiertos; tiene "
        "nivel gratuito con límites por modelo.",
        "models_endpoint": True,
    },
    {
        "id": "openrouter",
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "kind": "mixto: modelos ':free' (20 rpm/50 req-día sin créditos) y de pago",
        "key_format": "sk-or-…",
        "docs": "https://openrouter.ai/docs",
        "description": "Gateway con cientos de modelos detrás de una sola clave.",
        "models_endpoint": True,
    },
    {
        "id": "mistral",
        "label": "Mistral AI",
        "base_url": "https://api.mistral.ai/v1",
        "kind": "free mode documentado (límites: consultar docs)",
        "key_format": "…",
        "docs": "https://docs.mistral.ai/api",
        "description": "Modelos Mistral; la API publica un modo gratuito con "
        "restricciones de capacidad.",
        "models_endpoint": True,
    },
    {
        "id": "cerebras",
        "label": "Cerebras",
        "base_url": "https://api.cerebras.ai/v1",
        "kind": "free tier existe (límites: consultar docs)",
        "key_format": "csk-…",
        "docs": "https://inference-docs.cerebras.ai",
        "description": "Inferencia muy rápida con hardware propio de Cerebras.",
        "models_endpoint": True,
    },
    {
        "id": "together",
        "label": "Together AI",
        "base_url": "https://api.together.ai/v1",
        "kind": "de pago",
        "key_format": "…",
        "docs": "https://docs.together.ai",
        "description": "Catálogo grande de modelos abiertos servidos en la nube.",
        "models_endpoint": True,
    },
    {
        "id": "xai",
        "label": "xAI (Grok)",
        "base_url": "https://api.x.ai/v1",
        "kind": "de pago",
        "key_format": "xai-…",
        "docs": "https://docs.x.ai",
        "description": "Modelos Grok; compatibles con el formato OpenAI.",
        "models_endpoint": True,
    },
    {
        "id": "moonshot",
        "label": "Moonshot AI (Kimi)",
        "base_url": "https://api.moonshot.ai/v1",
        "kind": "de pago",
        "key_format": "sk-…",
        "docs": "https://platform.moonshot.ai/docs",
        "description": "Modelos Kimi con contexto muy largo.",
        "models_endpoint": True,
    },
    {
        "id": "hf_router",
        "label": "Hugging Face Router",
        "base_url": "https://router.huggingface.co/v1",
        "kind": "créditos según plan (PRO/Team/Enterprise)",
        "key_format": "hf_…",
        "docs": "https://huggingface.co/docs/inference-providers",
        "description": "Acceso a muchos proveedores detrás de la cuenta de HF.",
        "models_endpoint": True,
    },
    {
        "id": "zai",
        "label": "Z.ai (GLM)",
        "base_url": "https://api.z.ai/api/paas/v4/",
        "kind": "de pago",
        "key_format": "…",
        "docs": "https://docs.z.ai",
        "description": "Modelos GLM de Zhipu; endpoint OpenAI-compatible.",
        "models_endpoint": True,
    },
    {
        "id": "nvidia_nim",
        "label": "NVIDIA NIM",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "kind": "créditos de prueba (límites: consultar docs)",
        "key_format": "nvapi-…",
        "docs": "https://docs.api.nvidia.com",
        "description": "Catálogo de NVIDIA para modelos optimizados en sus GPUs.",
        "models_endpoint": True,
    },
    {
        "id": "gemini",
        "label": "Google AI Studio (Gemini)",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "kind": "free tier documentado (acceso limitado a ciertos modelos)",
        "key_format": "AIza…",
        "docs": "https://ai.google.dev/gemini-api/docs/openai",
        "description": "Gemini vía la capa de compatibilidad OpenAI oficial de "
        "Google (NO es Vertex AI).",
        "models_endpoint": True,
    },
]

PRESET_IDS = [p["id"] for p in PRESETS]

# Proveedores que solo ofrecen voz (no chat OpenAI-compatible). Sus claves
# viven en el mismo keyring y se incluyen en la purga de secretos.
SPEECH_ONLY_PROVIDER_IDS = ["deepgram"]


def preset(provider_id: str) -> Optional[Dict[str, Any]]:
    for item in PRESETS:
        if item["id"] == provider_id:
            return dict(item)
    return None

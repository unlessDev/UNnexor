"""Catálogo de modelos recomendados (referencia estática, documentada).

Los tamaños son aproximados y la cuantización concreta depende del tag;
la fuente oficial es ``https://ollama.com/library`` (consulta del
2026-11-16).  El catálogo es una REFERENCIA para "modelos recomendados que
puedes descargar": los datos vivos (instalados, tamaños reales, detalles)
vienen siempre de la API de Ollama.  Si un modelo del catálogo no coincide
con el registry actual, gana la API: nunca se inventan especificaciones.

Diseño (según investigación documentada en docs/MODELS_CATALOG.md):
  * Modelos de 3B–9B en cuantización Q4: el punto óptimo para equipos con
    8 GB de VRAM o 16 GB de RAM.
  * Se incluyen modelos con visión para el flujo de capturas de pantalla.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

CATALOG_SOURCE = (
    "https://ollama.com/library (tamaños aproximados; verificado 2026-11-16)"
)

# Campos: name, size_gb (aprox), family, vision, thinking, description,
#         params_b (para estimar KV cuando no esté instalado).
_CATALOG_RAW: List[Dict[str, Any]] = [
    {
        "name": "qwen3:4b",
        "size_gb": 2.6,
        "params_b": 4.0,
        "family": "qwen3",
        "vision": False,
        "thinking": True,
        "description": "Modelo compacto con modo de razonamiento; muy buen español.",
    },
    {
        "name": "qwen3:8b",
        "size_gb": 5.2,
        "params_b": 8.0,
        "family": "qwen3",
        "vision": False,
        "thinking": True,
        "description": "El mejor equilibrio calidad/velocidad de su clase para "
        "respuestas académicas estructuradas.",
    },
    {
        "name": "qwen2.5:7b",
        "size_gb": 4.7,
        "params_b": 7.6,
        "family": "qwen2.5",
        "vision": False,
        "thinking": False,
        "description": "Clásico estable; sigue instrucciones largas con rigor.",
    },
    {
        "name": "qwen2.5vl:7b",
        "size_gb": 6.0,
        "params_b": 7.6,
        "family": "qwen2.5-vl",
        "vision": True,
        "thinking": False,
        "description": "Visión potente: lee capturas, fórmulas y diagramas directamente.",
    },
    {
        "name": "llama3.1:8b",
        "size_gb": 4.9,
        "params_b": 8.0,
        "family": "llama",
        "vision": False,
        "thinking": False,
        "description": "Generalista de Meta; buen punto de comparación.",
    },
    {
        "name": "llama3.2:3b",
        "size_gb": 2.0,
        "params_b": 3.2,
        "family": "llama",
        "vision": False,
        "thinking": False,
        "description": "Muy ligero y rápido para resúmenes y consultas simples.",
    },
    {
        "name": "gemma3:4b",
        "size_gb": 3.3,
        "params_b": 4.3,
        "family": "gemma3",
        "vision": True,
        "thinking": False,
        "description": "Multimodal ligero de Google; visión decente en poco espacio.",
    },
    {
        "name": "gemma3:12b",
        "size_gb": 8.1,
        "params_b": 12.8,
        "family": "gemma3",
        "vision": True,
        "thinking": False,
        "description": "Multimodal más capaz; alto consumo, solo con VRAM holgada.",
    },
    {
        "name": "deepseek-r1:8b",
        "size_gb": 5.2,
        "params_b": 8.2,
        "family": "qwen3",
        "vision": False,
        "thinking": True,
        "description": "Especializado en razonamiento paso a paso (matemática/lógica).",
    },
    {
        "name": "mistral-nemo:12b",
        "size_gb": 7.1,
        "params_b": 12.2,
        "family": "mistral3",
        "vision": False,
        "thinking": False,
        "description": "Más conocimiento que los 7-8B; necesita hybrid CPU/GPU.",
    },
    {
        "name": "llava:7b",
        "size_gb": 4.7,
        "params_b": 7.0,
        "family": "llama",
        "vision": True,
        "thinking": False,
        "description": "Visión clásica (Llama+CLIP); alternativa a qwen2.5vl.",
    },
    {
        "name": "phi4-mini:3.8b",
        "size_gb": 2.9,
        "params_b": 3.8,
        "family": "phi3",
        "vision": False,
        "thinking": False,
        "description": "Pequeño de Microsoft, sorprendentemente capaz en texto.",
    },
]

# Consultado 2026-11-16; se vuelve a verificar contra /api/show en vivo.
PULL_SIZE_UNCERTAINTY = "El tamaño real de descarga puede variar según el tag y las actualizaciones del registry."


def recommended_catalog() -> List[Dict[str, Any]]:
    """Copia del catálogo con la fuente anotada."""
    entries: List[Dict[str, Any]] = []
    for item in _CATALOG_RAW:
        entry = dict(item)
        entry["size_bytes"] = int(entry["size_gb"] * (1024**3))
        entry["source"] = CATALOG_SOURCE
        entries.append(entry)
    return entries


def catalog_entry(name: str) -> Optional[Dict[str, Any]]:
    """Busca un modelo del catálogo: primero coincidencia exacta del nombre
    completo (con tag), luego por familia sin tag."""
    lowered = name.lower()
    for item in _CATALOG_RAW:  # 1) coincidencia exacta
        if item["name"].lower() == lowered:
            return dict(item)
    base = lowered.split(":")[0]
    for item in _CATALOG_RAW:  # 2) coincidencia por familia
        if item["name"].split(":")[0].lower() == base:
            return dict(item)
    return None

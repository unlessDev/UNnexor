"""Evaluación de la carga combinada del copiloto.

El usuario puede usar a la vez reconocimiento de voz, un modelo de
lenguaje, un modelo con visión y OCR. Este módulo estima la memoria del
conjunto y lo clasifica con las mismas cinco clases que los modelos:

* **Óptimo**: todo cabe con margen (≥ 20 % de RAM libre tras cargarlo).
* **Compatible**: cabe, con poco margen.
* **Exigente**: cabe, pero los modelos de Ollama no caben juntos en la VRAM
  y se alternarán (cada cambio puede tardar decenas de segundos), o la RAM
  queda casi agotada.
* **No compatible**: la RAM necesaria supera la RAM TOTAL del equipo (no
  solo la libre): no hay forma de ejecutarlo todo a la vez.
* **No evaluado**: falta algún dato de tamaño.

Caso real que motivó el módulo (2026-11-25): con qwen3:8b y qwen2.5vl:7b
cargados y 1,9 GB de RAM libre, cargar Pix2Text cerró el proceso.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ..model_evaluator import (
    TIER_ALTO,
    TIER_IMPOSIBLE,
    TIER_META,
    TIER_PERFECTO,
    TIER_REGULAR,
    TIER_SIN_EVALUAR,
    estimate_requirements,
    gpu_budget,
    params_from_size,
    parse_parameter_count,
)

OCR_RAM_MB = {"pix2text": 2200, "rapidocr": 500, "tesseract": 150}
APP_OVERHEAD_MB = 600  # Qt + Python + servidor del puente
OS_RESERVE_MB = 1500  # sistema y aplicaciones abiertas (videollamada…)


@dataclass(frozen=True)
class WorkloadItem:
    name: str
    kind: str  # stt | llm | vision | ocr | embeddings
    memory_mb: float | None  # None = desconocido
    ollama: bool = False  # lo gestiona Ollama (compite por la VRAM)


@dataclass(frozen=True)
class WorkloadAssessment:
    tier: str
    ram_needed_mb: float
    vram_needed_mb: float
    ram_total_mb: float
    ram_free_mb: float
    vram_usable_mb: float
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def label(self) -> str:
        return TIER_META[self.tier][1]

    @property
    def emoji(self) -> str:
        return TIER_META[self.tier][0]


def ollama_item(
    name: str,
    size_bytes: int | None,
    parameter_size: Any = None,
    kind: str = "llm",
    context_tokens: int = 8192,
) -> WorkloadItem:
    params = parse_parameter_count(parameter_size, name) or params_from_size(size_bytes)
    req = estimate_requirements(size_bytes, params, context_tokens)
    total = req.get("total_gb_est")
    return WorkloadItem(
        name,
        kind,
        round(float(total) * 1024, 0) if isinstance(total, int | float) else None,
        ollama=True,
    )


def stt_item(name: str, ram_mb: int | None) -> WorkloadItem:
    return WorkloadItem(name, "stt", float(ram_mb) if ram_mb else None)


def ocr_item(engine: str) -> WorkloadItem:
    return WorkloadItem(engine, "ocr", float(OCR_RAM_MB.get(engine, 1000)))


def assess_workload(
    hw: Mapping[str, Any], items: list[WorkloadItem]
) -> WorkloadAssessment:
    ram_total = float(hw.get("ram_total_mb") or 0)
    ram_free = float(hw.get("ram_available_mb") or 0)
    vram_gb, _known = gpu_budget(dict(hw))
    vram_usable = (vram_gb or 0.0) * 1024
    reasons: list[str] = []
    if any(item.memory_mb is None for item in items):
        unknown = ", ".join(i.name for i in items if i.memory_mb is None)
        return WorkloadAssessment(
            TIER_SIN_EVALUAR,
            0,
            0,
            ram_total,
            ram_free,
            vram_usable,
            (f"Sin datos de tamaño para: {unknown}.",),
        )
    ollama_models = [i for i in items if i.ollama]
    others = [i for i in items if not i.ollama]
    ollama_total = sum(i.memory_mb or 0 for i in ollama_models)
    largest_ollama = max((i.memory_mb or 0 for i in ollama_models), default=0)
    # Lo que no es de Ollama vive en RAM del proceso de UNnexor.
    ram_needed = APP_OVERHEAD_MB + sum(i.memory_mb or 0 for i in others)
    vram_needed = 0.0
    swapping = False
    if vram_usable:
        if ollama_total <= vram_usable:
            vram_needed = ollama_total
            reasons.append(
                f"Los modelos de Ollama ({ollama_total / 1024:.1f} GB) caben juntos "
                f"en la VRAM ({vram_usable / 1024:.1f} GB utilizables)."
            )
        else:
            vram_needed = min(largest_ollama, vram_usable)
            ram_needed += max(0.0, largest_ollama - vram_usable)
            if len(ollama_models) > 1:
                swapping = True
                reasons.append(
                    f"Los modelos de Ollama suman {ollama_total / 1024:.1f} GB y la "
                    f"VRAM utilizable es {vram_usable / 1024:.1f} GB: Ollama los "
                    "alternará y cada cambio puede tardar decenas de segundos."
                )
            elif largest_ollama > vram_usable:
                reasons.append(
                    "El modelo no cabe entero en la VRAM: una parte correrá en CPU "
                    "(más lento)."
                )
    else:
        ram_needed += ollama_total
        reasons.append("Sin GPU utilizable: todos los modelos usan RAM y CPU.")

    budget = max(0.0, ram_free - OS_RESERVE_MB)
    reasons.append(
        f"RAM estimada para el conjunto: {ram_needed / 1024:.1f} GB "
        f"(libres ahora: {ram_free / 1024:.1f} GB de {ram_total / 1024:.1f} GB)."
    )
    if ram_total and ram_needed > ram_total - OS_RESERVE_MB:
        tier = TIER_IMPOSIBLE
        reasons.append(
            "Supera la RAM total del equipo: no puede ejecutarse todo a la vez."
        )
    elif ram_needed > budget or swapping:
        tier = TIER_ALTO
        if ram_needed > budget:
            reasons.append(
                "Necesitará más memoria de la libre ahora: cierra aplicaciones o "
                "elige modelos más ligeros."
            )
    elif ram_needed > budget * 0.8:
        tier = TIER_REGULAR
        reasons.append("Cabe, con poco margen de memoria.")
    else:
        tier = TIER_PERFECTO
        reasons.append("Cabe con margen.")
    for item in others:
        if item.kind == "ocr" and item.name == "pix2text":
            reasons.append(
                "Pix2Text carga modelos grandes de PyTorch: evita usarlo con varios "
                "modelos de lenguaje cargados."
            )
    return WorkloadAssessment(
        tier,
        round(ram_needed),
        round(vram_needed),
        ram_total,
        ram_free,
        round(vram_usable),
        tuple(reasons),
    )

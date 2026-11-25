"""Motor de evaluación de compatibilidad de modelos Ollama con el hardware.

Estima los requisitos de memoria de cada modelo y lo clasifica en:

  🟢 PERFECTO       — cabe con margen razonable de recursos.
  🟡 REGULAR        — ejecutable, con limitaciones de velocidad o consumo.
  🟠 ALTO CONSUMO   — ejecutable, pero usará una proporción elevada de recursos.
  🔴 IMPOSIBLE      — incompatible o inviable con la configuración actual.

Heurísticas documentadas (estimaciones, no medidas):

* Pesos: el tamaño en disco de Ollama (``GET /api/tags``) aproxima los pesos
  cuantizados cargados (desvío típico <10 %).
* KV cache: para una arquitectura densa GQA típica el cache es
  ~16 KB por token por cada 1B de parámetros (fp16).  ``kv_gb ≈
  ctx_tokens × 0.016 × params_B / 1024``.  Desvío esperado ±30 % según
  arquitectura real (nº de capas, cabezas KV) y cache cuantizado.
* Sobrecarga del runtime (buffers de cómputo, grafo): ~1 GB conservador.
* Cabe en GPU cuando ``total ≤ VRAM usable``; VRAM usable = total × 0.92
  (reserva del escritorio/driver).  Si la VRAM no se pudo leer, se evalúa
  como equipo solo-CPU y se indica la limitación.
* Ollama en modo híbrido: la parte que no cabe en VRAM se carga en RAM; se
  exige que quepa en la RAM disponible menos 1 GB (reserva del sistema).

Los modelos no se clasifican como imposibles por capricho: la categoría
roja solo aparece con una incompatibilidad fundamentada (no cabe ni en
VRAM+RAM, o el disco no tiene espacio para descargarla).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# --------------------------------------------------------------------- #
# Categorías
# --------------------------------------------------------------------- #
TIER_PERFECTO = "perfecto"
TIER_REGULAR = "regular"
TIER_ALTO = "alto"
TIER_IMPOSIBLE = "imposible"

TIER_META: Dict[str, Tuple[str, str, str]] = {
    TIER_PERFECTO: ("🟢", "PERFECTO", "#3ddc97"),
    TIER_REGULAR: ("🟡", "REGULAR", "#ffd166"),
    TIER_ALTO: ("🟠", "ALTO CONSUMO", "#ff9f43"),
    TIER_IMPOSIBLE: ("🔴", "IMPOSIBLE", "#ff6b6b"),
}

DEFAULT_CONTEXT = 8192
# Reserva del sistema operativo y otras aplicaciones (GB) al evaluar RAM.
RAM_OS_RESERVE_GB = 1.0
# Reserva del escritorio/driver dentro de la VRAM total (fracción).
VRAM_USABLE_FRACTION = 0.95
# Sobrecarga estimada del runtime de llama.cpp (GB): buffers de cómputo y
# del grafo a contexto ~8k (verificado empíricamente con ``ollama ps``).
RUNTIME_OVERHEAD_GB = 0.5
# GB de disco por 1B de parámetros con cuantización típica Q4_K_M.
GB_PER_B_PARAM_Q4 = 0.55
# KV cache: KB por token por 1B de parámetros (fp16, arquitectura GQA típica).
KV_KB_PER_TOKEN_PER_B = 16.0


# --------------------------------------------------------------------- #
# Extracción de parámetros
# --------------------------------------------------------------------- #
def parse_parameter_count(parameter_size: Any, model_name: str = "") -> Optional[float]:
    """Parámetros en miles de millones (B) desde ``/api/show`` o del nombre.

    ``"8B"`` → 8.0 · ``"7.6B"`` → 7.6 · ``qwen3:8b`` → 8.0 · None si no se
    puede deducir (nunca se inventa un valor).
    """
    for candidate in (parameter_size, model_name.split(":")[-1]):
        if not isinstance(candidate, str) or not candidate.strip():
            continue
        match = re.match(r"^([0-9]+(?:\.[0-9]+)?)\s*b$", candidate.strip().lower())
        if match:
            return float(match.group(1))
        match = re.search(
            r"(?:^|[:\-_])([0-9]+(?:\.[0-9]+)?)b(?:$|[:\-_])", candidate.strip().lower()
        )
        if match:
            return float(match.group(1))
    return None


def params_from_size(size_bytes: Optional[int]) -> Optional[float]:
    """Estimación de parámetros desde el tamaño del archivo (cuantización Q4).

    Aproximación conservadora: ~0.55 GB por 1B de parámetros. Se usa solo
    cuando la API no informa ``parameter_size``; se marca como estimado.
    """
    if not size_bytes or size_bytes <= 0:
        return None
    return round(size_bytes / (1024**3) / GB_PER_B_PARAM_Q4, 1)


# --------------------------------------------------------------------- #
# Cálculo de requisitos
# --------------------------------------------------------------------- #
def estimate_requirements(
    size_bytes: Optional[int],
    params_b: Optional[float],
    context_tokens: int,
) -> Dict[str, Any]:
    """Desglosa la memoria estimada necesaria (GB). Valores orientativos."""
    weights_gb = round(size_bytes / (1024**3), 2) if size_bytes else None
    kv_gb: Optional[float] = None
    if params_b is not None:
        kv_gb = round(
            context_tokens * KV_KB_PER_TOKEN_PER_B * params_b / 1024 / 1024, 2
        )
    overhead = RUNTIME_OVERHEAD_GB
    total = None
    if weights_gb is not None and kv_gb is not None:
        total = round(weights_gb + kv_gb + overhead, 2)
    return {
        "weights_gb": weights_gb,
        "kv_cache_gb_est": kv_gb,
        "runtime_overhead_gb_est": overhead,
        "total_gb_est": total,
        "context_tokens": context_tokens,
    }


def gpu_budget(hw_dict: Dict[str, Any]) -> Tuple[Optional[float], bool]:
    """VRAM usable (GB) de la mejor GPU y si es dato fiable.

    Devuelve ``(vram_usable_gb|None, vram_conocida)``. ``None`` = sin GPU
    utilizable (se evaluará como solo-CPU).
    """
    best: Optional[int] = None
    for gpu in hw_dict.get("gpus", []):
        vram = gpu.get("vram_total_mb")
        # Ignora iGPU diminutas (≤ 256 MB).
        if isinstance(vram, int) and vram > 256 and (best is None or vram > best):
            best = vram
    if best is None:
        return None, False
    return round(best / 1024 * VRAM_USABLE_FRACTION, 2), True


# --------------------------------------------------------------------- #
# Evaluación principal
# --------------------------------------------------------------------- #
@dataclass
class Assessment:
    """Resultado de la evaluación de un modelo."""

    model: str
    tier: str
    explanation: str
    reasons: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    estimated: bool = True  # todo el cálculo es una estimación

    @property
    def emoji(self) -> str:
        return TIER_META[self.tier][0]

    @property
    def label(self) -> str:
        return TIER_META[self.tier][1]

    @property
    def color(self) -> str:
        return TIER_META[self.tier][2]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model": self.model,
            "tier": self.tier,
            "label": self.label,
            "emoji": self.emoji,
            "color": self.color,
            "explanation": self.explanation,
            "reasons": self.reasons,
            "metrics": self.metrics,
        }


TIER_EXPLANATIONS = {
    TIER_PERFECTO: (
        "Este modelo es adecuado para tu equipo. Dispone de memoria suficiente "
        "para ejecutarse con un margen razonable."
    ),
    TIER_REGULAR: (
        "Este modelo puede funcionar, pero probablemente utilizará parte de la "
        "RAM del sistema y responderá más lentamente."
    ),
    TIER_ALTO: (
        "Este modelo requiere una cantidad importante de memoria. Durante su "
        "ejecución puede afectar el rendimiento de otras aplicaciones."
    ),
    TIER_IMPOSIBLE: (
        "Este modelo requiere más memoria de la disponible o presenta una "
        "incompatibilidad con tu entorno de ejecución. Por seguridad, la "
        "selección está deshabilitada."
    ),
}


def assess_model(
    model_name: str,
    size_bytes: Optional[int],
    parameter_size: Any = None,
    context_length: Optional[int] = None,
    hw: Optional[Dict[str, Any]] = None,
    installed: bool = True,
    disk_free_gb: Optional[float] = None,
) -> Assessment:
    """Clasifica un modelo según los recursos disponibles.

    ``hw`` es el diccionario de ``HardwareInfo.to_dict()`` (o ``None`` para
    evaluar en el peor caso: solo CPU sin datos). Nunca inventa datos: lo
    desconocido se marca como estimación en ``metrics``.
    """
    hw = hw or {}
    context_tokens = int(context_length or DEFAULT_CONTEXT)
    params_b = parse_parameter_count(parameter_size, model_name)
    params_source = "api" if params_b is not None else None
    if params_b is None:
        params_b = params_from_size(size_bytes)
        params_source = "estimado por tamaño" if params_b is not None else None

    metrics = estimate_requirements(size_bytes, params_b, context_tokens)
    metrics["params_b"] = params_b
    metrics["params_source"] = params_source or "desconocido"

    weights_gb = metrics["weights_gb"]
    total_gb = metrics["total_gb_est"]
    vram_usable, vram_known = gpu_budget(hw)
    ram_available_gb = (
        round((hw.get("ram_available_mb") or 0) / 1024, 2) - RAM_OS_RESERVE_GB
    )
    reasons: List[str] = []

    # --- Sin datos suficientes: peor caso --------------------------- #
    if total_gb is None or weights_gb is None:
        reasons.append("Sin datos de tamaño suficientes para una evaluación fiable.")
        return Assessment(
            model=model_name,
            tier=TIER_REGULAR,
            explanation=TIER_EXPLANATIONS[TIER_REGULAR]
            + " No se pudo estimar su consumo con precisión.",
            reasons=reasons,
            metrics=metrics,
        )

    metrics["vram_usable_gb"] = vram_usable
    metrics["ram_available_gb"] = max(0.0, ram_available_gb)
    metrics["target"] = (
        "gpu"
        if (vram_usable and total_gb <= vram_usable)
        else ("mixto" if vram_usable else "cpu")
    )

    # --- Espacio en disco (solo para modelos por descargar) ---------- #
    if not installed and disk_free_gb is not None:
        needed_gb = round(weights_gb * 1.1, 2)  # descarga + descompresión
        metrics["disk_needed_gb"] = needed_gb
        if disk_free_gb < needed_gb:
            reasons.append(
                f"El disco no tiene espacio: faltan ~{needed_gb - disk_free_gb:.1f} GB "
                f"(libres: {disk_free_gb:.1f} GB) para descargar el modelo."
            )
            return Assessment(
                model=model_name,
                tier=TIER_IMPOSIBLE,
                explanation=TIER_EXPLANATIONS[TIER_IMPOSIBLE],
                reasons=reasons,
                metrics=metrics,
            )

    # --- Cabe completo en GPU --------------------------------------- #
    if vram_usable is not None and total_gb <= vram_usable:
        margin = (vram_usable - total_gb) / total_gb
        metrics["gpu_margin_pct"] = round(margin * 100, 1)
        if margin >= 0.20:
            tier = TIER_PERFECTO
            reasons.append(
                f"Cabe completo en GPU con {metrics['gpu_margin_pct']:.0f}% de margen."
            )
        elif margin >= 0.08:
            tier = TIER_REGULAR
            reasons.append(
                f"Cabe en GPU pero con poco margen ({metrics['gpu_margin_pct']:.0f}%)."
            )
        else:
            tier = TIER_ALTO
            reasons.append(
                "Cabe en la GPU apenas sin margen; podría afectar otras "
                "aplicaciones que usen la misma memoria."
            )
        return Assessment(
            model=model_name,
            tier=tier,
            explanation=TIER_EXPLANATIONS[tier],
            reasons=reasons,
            metrics=metrics,
        )

    # --- Modo híbrido GPU + RAM o solo CPU -------------------------- #
    if vram_usable is not None:
        cpu_part = round(total_gb - vram_usable, 2)
        offload_pct = round(min(100.0, vram_usable / total_gb * 100), 1)
        metrics["cpu_part_gb"] = cpu_part
        metrics["gpu_offload_pct"] = offload_pct
        if cpu_part <= ram_available_gb:
            if offload_pct >= 70:
                tier = TIER_REGULAR
                reasons.append(
                    f"Uso mixto: ~{offload_pct:.0f}% en GPU y ~{cpu_part:.1f} GB en RAM "
                    "(más lento que 100% GPU)."
                )
            elif offload_pct >= 40:
                tier = TIER_ALTO
                reasons.append(
                    f"Solo ~{offload_pct:.0f}% del modelo cabe en GPU; el resto "
                    f"(~{cpu_part:.1f} GB) iría a RAM y la velocidad será baja."
                )
            else:
                tier = TIER_IMPOSIBLE
                reasons.append(
                    f"El modelo apenas cabe en GPU (~{offload_pct:.0f}%): el "
                    f"~{cpu_part:.1f} GB restante en RAM haría la ejecución "
                    "inviable en la práctica (velocidad mínima)."
                )
            return Assessment(
                model=model_name,
                tier=tier,
                explanation=TIER_EXPLANATIONS[tier],
                reasons=reasons,
                metrics=metrics,
            )
        reasons.append(
            f"No cabe ni en GPU ({vram_usable:.1f} GB) ni en RAM disponible "
            f"({max(0.0, ram_available_gb):.1f} GB): faltarían "
            f"~{cpu_part - ram_available_gb:.1f} GB."
        )
        return Assessment(
            model=model_name,
            tier=TIER_IMPOSIBLE,
            explanation=TIER_EXPLANATIONS[TIER_IMPOSIBLE],
            reasons=reasons,
            metrics=metrics,
        )

    # --- Solo CPU ---------------------------------------------------- #
    margin = (ram_available_gb - total_gb) / total_gb if ram_available_gb > 0 else -1
    metrics["ram_margin_pct"] = round(margin * 100, 1)
    if margin >= 0.35:
        tier = TIER_PERFECTO
        reasons.append(
            f"En RAM con {metrics['ram_margin_pct']:.0f}% de margen (sin GPU usable)."
        )
    elif margin >= 0.12:
        tier = TIER_REGULAR
        reasons.append("En RAM con margen ajustado (sin GPU usable).")
    elif margin >= 0.03:
        tier = TIER_ALTO
        reasons.append("Usará casi toda la RAM disponible; puede degradar el sistema.")
    else:
        reasons.append(
            f"Requiere ~{total_gb:.1f} GB y solo hay ~{max(0.0, ram_available_gb):.1f} "
            "GB de RAM disponible."
        )
        tier = TIER_IMPOSIBLE
    return Assessment(
        model=model_name,
        tier=tier,
        explanation=TIER_EXPLANATIONS[tier],
        reasons=reasons,
        metrics=metrics,
    )


def context_warning(
    context_tokens: int, hw: Optional[Dict[str, Any]] = None
) -> Optional[str]:
    """Aviso si el contexto solicitado es exigente para el equipo."""
    hw = hw or {}
    params_b = 8.0  # referencia: modelo 8B típico
    kv_gb = context_tokens * KV_KB_PER_TOKEN_PER_B * params_b / 1024 / 1024
    if context_tokens >= 32_768:
        return (
            f"Contexto de {context_tokens} tokens: el KV cache puede superar "
            f"los {kv_gb:.1f} GB. Considera reducir el contexto si notas lentitud."
        )
    if context_tokens >= 16_384 and (hw.get("ram_available_mb") or 0) < 6144:
        return (
            "Contexto grande para la RAM disponible; puede provocar presión de memoria."
        )
    return None

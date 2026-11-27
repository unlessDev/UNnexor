"""Recomendación de motor de voz según equipo, idioma y preferencias.

No es una caja negra: cada recomendación lleva sus razones y advertencias.
Los datos de hardware son los de ``hardware.HardwareInfo.to_dict()``.

Modelo de coste de Whisper local: el codificador procesa siempre una
ventana de 30 s, así que la latencia es casi constante por fragmento
(``chunk_length`` no la reduce en CTranslate2). Se estima en segundos por
llamada y se escala por núcleos físicos. Si existen mediciones del propio
equipo (``measured_call_s``), sustituyen a la estimación.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .catalog import ALL_MODELS, STTModelSpec

PREFERENCES = ("balanced", "quality", "speed", "privacy")


@dataclass(frozen=True)
class STTRecommendation:
    spec: STTModelSpec
    score: float
    ready: bool
    realtime_ok: bool | None  # ¿se espera que siga el ritmo de la voz?
    reasons: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)


TYPICAL_SEGMENT_S = 4.0  # duración media de una intervención segmentada
PARTIAL_INTERVAL_S = 1.5  # cadencia de los fragmentos provisionales

# Segundos por llamada en CPU int8. MEDIDOS en Ryzen 5 2600X (6 núcleos,
# 5 hilos) con scripts/stt_benchmark.py el 2026-11-24: base 1,03 y small
# 3,48. El resto son ESTIMACIONES por tamaño del codificador.
_REFERENCE_CORES = 6
_CPU_CALL_S = {
    "tiny": (0.5, False),
    "base": (1.03, True),
    "small": (3.48, True),
    "medium": (9.0, False),
    "large-v3-turbo": (12.0, False),
    "large-v3": (20.0, False),
}
# GPU NVIDIA con float16: estimaciones (sin medición propia todavía).
_CUDA_CALL_S = {
    "tiny": 0.08,
    "base": 0.1,
    "small": 0.2,
    "medium": 0.4,
    "large-v3-turbo": 0.45,
    "large-v3": 0.9,
}


def _cpu_power(hw: Mapping[str, Any]) -> float:
    """Núcleos físicos (máx. 16); 4 si no se conocen."""
    cores = hw.get("cpu_cores") or 4
    return float(min(16, max(1, int(cores))))


def estimated_call_seconds(
    spec: STTModelSpec, hw: Mapping[str, Any], cuda: bool
) -> tuple[float, bool]:
    """(segundos por fragmento, ¿basado en una medición?)."""
    if cuda:
        return _CUDA_CALL_S.get(spec.model, 1.0), False
    base, measured = _CPU_CALL_S.get(spec.model, (20.0, False))
    scale = _REFERENCE_CORES / _cpu_power(hw)
    exact = measured and abs(scale - 1.0) < 1e-9
    return round(base * scale, 2), exact


def recommend(
    hw: Mapping[str, Any],
    *,
    preference: str = "balanced",
    allow_remote: bool = False,
    connected: set[str] | None = None,
    installed: set[str] | None = None,
    measured_call_s: Mapping[str, float] | None = None,
    cuda_available: bool = False,
    language: str = "es",
) -> list[STTRecommendation]:
    if preference not in PREFERENCES:
        raise ValueError(f"Preferencia desconocida: {preference}")
    connected = connected or set()
    installed = installed or set()
    measured_call_s = measured_call_s or {}
    ram_free = float(hw.get("ram_available_mb") or 0)
    vram = max((g.get("vram_total_mb") or 0 for g in hw.get("gpus", [])), default=0)
    weights = {
        "balanced": (1.0, 1.0),
        "quality": (1.6, 0.6),
        "speed": (0.6, 1.6),
        "privacy": (1.0, 1.0),
    }[preference]

    results: list[STTRecommendation] = []
    for spec in ALL_MODELS:
        reasons: list[str] = []
        warnings: list[str] = []
        if spec.deprecated:
            continue
        if language != "en" and not spec.multilingual:
            continue
        score = weights[0] * spec.spanish_quality + weights[1] * spec.speed
        realtime_ok: bool | None = None
        if spec.is_local:
            ready = spec.model in installed
            call_s = measured_call_s.get(spec.model)
            measured = call_s is not None
            if call_s is None:
                call_s, measured = estimated_call_seconds(spec, hw, cuda_available)
            source = "medido en este equipo" if measured else "estimado"
            realtime_ok = call_s <= TYPICAL_SEGMENT_S * 0.9
            if not realtime_ok:
                score -= 4
                warnings.append(
                    f"Tardaría ~{call_s:.1f} s por intervención ({source}): la "
                    "transcripción se irá retrasando en este equipo."
                )
            elif call_s <= PARTIAL_INTERVAL_S:
                reasons.append(
                    f"~{call_s:.1f} s por intervención ({source}): admite texto "
                    "provisional mientras se habla."
                )
            else:
                score -= 1
                reasons.append(
                    f"~{call_s:.1f} s por intervención ({source}): sigue el ritmo, "
                    "pero sin texto provisional."
                )
            need = (spec.vram_mb if cuda_available and vram else spec.ram_mb) or 0
            if not cuda_available and ram_free and need > ram_free * 0.5:
                score -= 3
                warnings.append(
                    f"Usaría ~{need} MB de los {int(ram_free)} MB libres; el modelo "
                    "de lenguaje también necesita memoria."
                )
            reasons.append("Privado: el audio no sale del equipo.")
            if preference == "privacy":
                score += 4
            if ready:
                score += 1.5
                reasons.append("Ya está descargado.")
            else:
                warnings.append(f"Requiere descargar ~{spec.download_mb} MB.")
        else:
            ready = spec.provider_id in connected
            if not allow_remote or preference == "privacy":
                continue
            realtime_ok = True
            reasons.append("Rápido y preciso; no consume recursos del equipo.")
            warnings.append("Envía el audio a un servicio externo.")
            if spec.access.may_cost_money:
                warnings.append(f"{spec.access.label}: puede generar costos.")
                score -= 1.5
            if spec.limits_note:
                warnings.append(spec.limits_note)
            if ready:
                score += 1.5
                reasons.append("Tu cuenta ya está conectada.")
            else:
                score -= 2
                warnings.append("Requiere conectar una cuenta.")
        results.append(
            STTRecommendation(
                spec,
                round(score, 2),
                ready,
                realtime_ok,
                tuple(reasons),
                tuple(warnings),
            )
        )
    results.sort(key=lambda r: (r.score, r.ready), reverse=True)
    return results

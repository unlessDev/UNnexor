"""Recomendación de configuración por funcionalidad y perfil de consumo.

Entrada: el hardware REAL detectado (``hardware.detect``), los modelos
instalados en Ollama (con sus capacidades) y el catálogo (curado + biblioteca
en línea). Salida: una propuesta independiente para cada función de UNnexor,
con requisitos, compatibilidad, alternativa ligera, parámetros de generación
y los motivos técnicos.

Principios:

* Nunca se recomienda un modelo clasificado como EXIGENTE o NO COMPATIBLE
  para el equipo, aunque el perfil sea "alto consumo".
* Lo que se sabe se distingue de lo que se estima: cada propuesta indica si
  su compatibilidad está **medida** (benchmark propio), **estimada**
  (cálculo de memoria) o es **desconocida**.
* No todas las funciones usan Ollama: la voz usa Whisper (faster-whisper),
  el OCR motores locales y la búsqueda en documentos BM25 + embeddings.
* A igualdad, se prefiere un modelo ya instalado (no requiere descarga).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ..model_evaluator import (
    TIER_ALTO,
    TIER_IMPOSIBLE,
    TIER_META,
    TIER_PERFECTO,
    TIER_REGULAR,
    TIER_SIN_EVALUAR,
    assess_model,
    gpu_backend_available,
)

PROFILES = {
    "low": "Menor consumo",
    "medium": "Consumo medio",
    "high": "Alto consumo",
}

# Bandas de tamaño preferidas (miles de millones de parámetros) por perfil.
SIZE_BANDS = {
    "low": (2.5, 4.5),  # por debajo de ~2,5B la calidad cae mucho
    "medium": (3.0, 9.5),
    "high": (6.0, 72.0),
}

# Contexto (tokens) por perfil: más contexto = más memoria (caché KV).
CONTEXT = {"low": 4096, "medium": 8192, "high": 16384}

# Parámetros de generación por funcionalidad (todos soportados por Ollama).
# temperature/top_p/top_k/repeat_penalty/num_ctx/num_predict.
GENERATION: dict[str, dict[str, Any]] = {
    "chat": {
        "temperature": 0.4,
        "top_p": 0.9,
        "top_k": 40,
        "repeat_penalty": 1.1,
        "why": "Explicaciones de estudio: precisas pero con redacción natural.",
    },
    "conversation": {
        "temperature": 0.6,
        "top_p": 0.9,
        "top_k": 40,
        "repeat_penalty": 1.1,
        "num_predict": 320,
        "why": "Respuestas habladas: algo más de naturalidad, breves.",
    },
    "translation": {
        "temperature": 0.2,
        "top_p": 0.9,
        "top_k": 20,
        "repeat_penalty": 1.05,
        "why": "Traducción fiel: poca variación para no alterar el sentido.",
    },
    "vision": {
        "temperature": 0.1,
        "top_p": 0.8,
        "top_k": 20,
        "repeat_penalty": 1.05,
        "why": "Ejercicios y capturas: máxima consistencia en cálculos y lectura.",
    },
}

FEATURES = {
    "chat": "Procesamiento de texto y consultas desde el navegador",
    "conversation": "Asistencia en conversaciones (tiempo real)",
    "translation": "Traducción en tiempo real",
    "vision": "Capturas de pantalla y desafíos (modelo con visión)",
    "stt": "Transcripción de audio",
    "ocr": "OCR (texto de capturas)",
    "documents": "Documentos del perfil (búsqueda de contexto)",
}

ACCEPTABLE = (TIER_PERFECTO, TIER_REGULAR)


@dataclass
class Candidate:
    name: str
    params_b: float | None
    size_bytes: int | None
    installed: bool
    vision: bool = False
    thinking: bool = False
    embedding: bool = False
    code: bool = False
    estimated_size: bool = False
    tier: str = TIER_SIN_EVALUAR
    metrics: dict[str, Any] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)


@dataclass
class Recommendation:
    feature: str
    title: str
    engine: str  # "Ollama", "faster-whisper", "OCR local", "BM25 + embeddings"
    choice: str  # modelo / motor / variante
    tier: str = TIER_SIN_EVALUAR
    confidence: str = "estimada"  # medida | estimada | desconocida
    requirements: str = ""
    speed: str = ""
    quality: str = ""
    alternative: str = ""
    download: str = ""  # "" = ya instalado / sin descarga
    params: dict[str, Any] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    available: bool = True  # False = no hay ninguna opción viable

    @property
    def label(self) -> str:
        emoji, text = TIER_META.get(self.tier, TIER_META[TIER_SIN_EVALUAR])[:2]
        return f"{emoji} {text}"

    def to_dict(self) -> dict[str, Any]:
        data = dict(self.__dict__)
        data["label"] = self.label
        return data


# ---------------------------------------------------------------- utilidades
def _params_from_text(text: str) -> float | None:
    import re

    match = re.search(r"(\d+(?:\.\d+)?)\s*([bBmM])", text or "")
    if not match:
        return None
    value = float(match.group(1))
    return value / 1000 if match.group(2).lower() == "m" else value


def build_candidates(
    installed: Iterable[Mapping[str, Any]],
    catalog: Iterable[Mapping[str, Any]],
    hw: Mapping[str, Any],
    context: int,
) -> list[Candidate]:
    """Instalados + catálogo evaluados contra el equipo (sin duplicados)."""
    out: dict[str, Candidate] = {}
    for entry in installed:
        name = str(entry.get("name", ""))
        if not name:
            continue
        caps = [str(c) for c in entry.get("capabilities") or []]
        params = _params_from_text(str(entry.get("parameter_size", ""))) or (
            _params_from_text(name.split(":")[-1])
        )
        out[name.lower()] = Candidate(
            name=name,
            params_b=params,
            size_bytes=entry.get("size_bytes") or entry.get("size"),
            installed=True,
            vision="vision" in caps,
            thinking="thinking" in caps,
            embedding="embedding" in caps or "embed" in name.lower(),
            code="coder" in name.lower() or "code" in name.lower(),
        )
    for entry in catalog:
        name = str(entry.get("name", ""))
        if not name or name.lower() in out:
            continue
        out[name.lower()] = Candidate(
            name=name,
            params_b=entry.get("params_b"),
            size_bytes=entry.get("size_bytes"),
            installed=False,
            vision=bool(entry.get("vision")),
            thinking=bool(entry.get("thinking")),
            embedding="embed" in name.lower(),
            code="coder" in name.lower(),
            estimated_size=bool(entry.get("estimated")),
        )
    disk_free = hw.get("disk_free_gb")
    for cand in out.values():
        assessment = assess_model(
            model_name=cand.name,
            size_bytes=cand.size_bytes,
            parameter_size=f"{cand.params_b}B" if cand.params_b else None,
            context_length=context,
            hw=dict(hw),
            installed=cand.installed,
            disk_free_gb=None if cand.installed else disk_free,
        )
        cand.tier = assessment.tier
        cand.metrics = assessment.metrics
        cand.reasons = assessment.reasons
    return list(out.values())


def _fits(cand: Candidate) -> bool:
    return cand.tier in ACCEPTABLE


def _pick(
    pool: list[Candidate], profile: str, prefer_fast: bool = False
) -> tuple[Candidate | None, Candidate | None]:
    """(elegido, alternativa ligera) dentro de la banda del perfil."""
    viable = [c for c in pool if _fits(c) and c.params_b]
    if not viable:
        return None, None
    low, high = SIZE_BANDS[profile]
    in_band = [c for c in viable if low <= (c.params_b or 0) <= high] or viable

    def key(c: Candidate) -> tuple:
        params = c.params_b or 0
        tier_rank = 0 if c.tier == TIER_PERFECTO else 1
        if profile == "low":
            # El más ligero que funcione bien; a igualdad, instalado.
            return (tier_rank, params, not c.installed)
        if profile == "high":
            # El mayor que siga siendo ÓPTIMO/COMPATIBLE; a igual tamaño, sin
            # razonamiento en tareas en tiempo real.
            return (
                -round(params),
                tier_rank,
                (c.thinking and prefer_fast),
                not c.installed,
            )
        # Medio: equilibrio, el mayor ÓPTIMO de la banda; razonamiento penaliza
        # en tareas en tiempo real.
        return (
            tier_rank,
            (c.thinking and prefer_fast),
            -params,
            not c.installed,
        )

    ordered = sorted(in_band, key=key)
    chosen = ordered[0]
    lighter = sorted(
        (c for c in viable if (c.params_b or 0) < (chosen.params_b or 0)),
        key=lambda c: (-(c.params_b or 0), not c.installed),
    )
    return chosen, (lighter[0] if lighter else None)


def _speed_hint(cand: Candidate, measured: Mapping[str, Any]) -> tuple[str, str]:
    bench = measured.get(f"llm:{cand.name}")
    if isinstance(bench, Mapping) and bench.get("tokens_per_s"):
        return f"{bench['tokens_per_s']:.0f} tok/s medidos en este equipo", "medida"
    target = cand.metrics.get("target")
    if target == "gpu":
        return "rápida (cabe entero en la VRAM de la GPU)", "estimada"
    if target == "mixto":
        return "moderada (una parte se ejecuta en RAM/CPU)", "estimada"
    if target == "cpu":
        return "lenta (solo CPU)", "estimada"
    return "sin datos: mídelo con «Medir rendimiento»", "desconocida"


def _quality_hint(params: float | None) -> str:
    if params is None:
        return "desconocida"
    if params < 3:
        return "básica (respuestas cortas y tareas simples)"
    if params < 7:
        return "buena para tareas generales"
    if params < 15:
        return "alta (mejor razonamiento y redacción)"
    return "muy alta (requiere mucha memoria)"


def _requirements(cand: Candidate) -> str:
    m = cand.metrics
    need = m.get("total_gb_est")
    parts = []
    if cand.params_b:
        parts.append(f"{cand.params_b:g}B parámetros")
    if need:
        parts.append(f"~{float(need):.1f} GB de memoria")
    disk = m.get("disk_needed_gb")
    if disk and not cand.installed:
        parts.append(
            f"~{float(disk):.1f} GB de descarga"
            + (" (estimado)" if cand.estimated_size else "")
        )
    return " · ".join(parts)


def _llm_recommendation(
    feature: str,
    pools: list[tuple[int, list[Candidate]]],
    profile: str,
    measured: Mapping[str, Any],
    prefer_fast: bool,
) -> Recommendation:
    """``pools``: [(contexto, candidatos evaluados con ese contexto)], del
    preferido al de respaldo. En alto consumo se prefiere conservar un modelo
    grande y reducir el contexto antes que bajar de tamaño de modelo."""
    chosen = lighter = None
    context = pools[0][0]
    reduced = False
    for index, (ctx, pool) in enumerate(pools):
        chosen, lighter = _pick(pool, profile, prefer_fast=prefer_fast)
        context = ctx
        in_band = (
            chosen is not None and (chosen.params_b or 0) >= SIZE_BANDS[profile][0]
        )
        if in_band or index == len(pools) - 1:
            reduced = index > 0
            break
    pool = pools[0][1]
    params = dict(GENERATION.get(feature, {}))
    why = params.pop("why", "")
    params["num_ctx"] = context
    if feature == "vision":
        params["num_ctx"] = max(params["num_ctx"], 8192)  # imágenes = muchos tokens
    if feature in ("conversation", "translation"):
        params["think"] = False
    rec = Recommendation(
        feature=feature,
        title=FEATURES[feature],
        engine="Ollama",
        choice=chosen.name if chosen else "—",
        params=params,
    )
    if chosen is None:
        rec.available = False
        rec.tier = TIER_IMPOSIBLE if pool else TIER_SIN_EVALUAR
        rec.reasons.append(
            "Ningún modelo adecuado cabe con margen en este equipo."
            if pool
            else "No hay modelos instalados ni catálogo disponible."
        )
        return rec
    rec.tier = chosen.tier
    rec.requirements = _requirements(chosen)
    rec.speed, rec.confidence = _speed_hint(chosen, measured)
    rec.quality = _quality_hint(chosen.params_b)
    rec.download = "" if chosen.installed else f"ollama pull {chosen.name}"
    if lighter is not None:
        rec.alternative = f"{lighter.name} ({_requirements(lighter)})"
    rec.reasons.append(
        f"Perfil «{PROFILES[profile]}»: {chosen.params_b:g}B dentro de la banda "
        f"{SIZE_BANDS[profile][0]:g}–{SIZE_BANDS[profile][1]:g}B."
    )
    rec.reasons.append(f"Clasificación para tu equipo: {rec.label}.")
    rec.reasons += chosen.reasons[:2]
    if reduced:
        rec.reasons.append(
            f"Contexto limitado a {context} tokens: con {CONTEXT[profile]} el "
            "modelo no cabría con margen en la memoria."
        )
    if chosen.installed:
        rec.reasons.append("Ya está instalado: no requiere descarga.")
    if chosen.thinking and prefer_fast:
        rec.reasons.append(
            "Es un modelo de razonamiento: se usará sin «thinking» para "
            "responder a tiempo."
        )
    if why:
        rec.reasons.append(f"Parámetros: {why}")
    return rec


# ---------------------------------------------------------------- aceleración
def acceleration_report(hw: Mapping[str, Any]) -> list[dict[str, str]]:
    """Detectado / compatible / controladores / usado en inferencia, por GPU."""
    backends = hw.get("backends") or {}
    used = hw.get("ollama_gpu_active")
    rows: list[dict[str, str]] = []
    for gpu in hw.get("gpus") or []:
        vendor = str(gpu.get("vendor", "desconocido"))
        tech = {
            "NVIDIA": "CUDA",
            "AMD": "ROCm (HIP) o Vulkan",
            "Intel": "Vulkan (experimental en Ollama)",
        }.get(vendor, "sin aceleración conocida")
        drivers = gpu_backend_available(vendor, dict(backends))
        vram = gpu.get("vram_total_mb")
        rows.append(
            {
                "gpu": str(gpu.get("name", "")),
                "vendor": vendor,
                "vram": f"{vram / 1024:.1f} GB" if vram else "no detectada",
                "detected": "sí",
                "compatible": tech,
                "drivers": "sí" if drivers else "no encontrados",
                "used": (
                    "sí (Ollama la usa ahora)"
                    if used is True
                    else (
                        "no (Ollama corre en CPU)"
                        if used is False
                        else "sin datos (carga un modelo para comprobarlo)"
                    )
                ),
            }
        )
    return rows


# ---------------------------------------------------------------- otras funciones
def _stt_recommendation(stt_recs: list[Any], profile: str) -> Recommendation:
    rec = Recommendation(
        feature="stt",
        title=FEATURES["stt"],
        engine="faster-whisper (Whisper local)",
        choice="—",
        params={"temperature": 0.0, "language": "fija si la conoces"},
    )
    local = [r for r in stt_recs if r.spec.is_local]
    if not local:
        rec.available = False
        rec.reasons.append("faster-whisper no está disponible en esta instalación.")
        return rec
    ok = [r for r in local if r.realtime_ok is not False] or local
    chosen = ok[0]
    if profile == "low":
        chosen = min(ok, key=lambda r: r.spec.ram_mb or 0)
    lighter = [r for r in ok if (r.spec.ram_mb or 0) < (chosen.spec.ram_mb or 0)]
    rec.choice = chosen.spec.label
    rec.tier = TIER_PERFECTO if chosen.realtime_ok else TIER_REGULAR
    rec.confidence = (
        "medida" if any("medid" in r for r in chosen.reasons) else "estimada"
    )
    rec.requirements = (
        f"~{(chosen.spec.ram_mb or 0) / 1024:.1f} GB de RAM (CPU int8)"
        if chosen.spec.ram_mb
        else ""
    )
    rec.speed = (
        "sigue el ritmo de la voz"
        if chosen.realtime_ok
        else (
            "puede ir por detrás de la voz"
            if chosen.realtime_ok is False
            else "sin medir"
        )
    )
    rec.quality = f"español {chosen.spec.spanish_quality}/5 (orientativo)"
    rec.download = "" if chosen.ready else "Descárgalo en Copiloto → Motores y modelos"
    if lighter:
        rec.alternative = min(lighter, key=lambda r: r.spec.ram_mb or 0).spec.label
    rec.reasons = list(chosen.reasons) + list(chosen.warnings)
    rec.reasons.append("Temperatura 0: transcripción fiel y repetible.")
    return rec


def _ocr_recommendation(hw: Mapping[str, Any], available: list[str]) -> Recommendation:
    from .. import ocr_manager

    rec = Recommendation(
        feature="ocr", title=FEATURES["ocr"], engine="OCR local", choice="—"
    )
    best = ocr_manager.recommend(dict(hw), prefer_math=True) if available else None
    if best is None:
        rec.available = False
        rec.tier = TIER_SIN_EVALUAR
        rec.download = "pip install rapidocr-onnxruntime (texto) o pix2text (fórmulas)"
        rec.reasons.append(
            "No hay motores OCR instalados; las capturas pueden leerse con un "
            "modelo con visión."
        )
        return rec
    info = ocr_manager.OCR_ENGINES[best]
    rec.choice = info["label"]
    rec.tier = TIER_PERFECTO
    rec.requirements = f"{info['tier']} · {info['weights']}"
    rec.quality = info["purpose"]
    rec.alternative = (
        "RapidOCR (más ligero, sin fórmulas)" if best == "pix2text" else ""
    )
    rec.reasons.append(f"Instalado y {info['tier']}; {info['output']}.")
    return rec


def _documents_recommendation(
    candidates: list[Candidate], profile: str
) -> Recommendation:
    embed = [c for c in candidates if c.embedding]
    installed = [c for c in embed if c.installed]
    rec = Recommendation(
        feature="documents",
        title=FEATURES["documents"],
        engine="BM25 + embeddings (Ollama)",
        choice="BM25 (siempre activo)",
        tier=TIER_PERFECTO,
        confidence="estimada",
        requirements="BM25: sin memoria adicional",
        quality="BM25 encuentra palabras exactas; los embeddings, ideas parecidas",
    )
    if profile == "low":
        rec.reasons.append("Menor consumo: solo BM25, sin modelo de embeddings.")
        return rec
    if installed:
        rec.choice = f"BM25 + {installed[0].name}"
        rec.reasons.append("Modelo de embeddings ya instalado.")
    else:
        rec.choice = "BM25 + nomic-embed-text"
        rec.download = "ollama pull nomic-embed-text (~0,3 GB)"
        rec.reasons.append(
            "Los embeddings mejoran la búsqueda por significado con muy poca memoria."
        )
    return rec


# ---------------------------------------------------------------- API
def recommend_all(
    hw: Mapping[str, Any],
    *,
    profile: str,
    installed: Iterable[Mapping[str, Any]],
    catalog: Iterable[Mapping[str, Any]],
    stt_recs: list[Any] | None = None,
    ocr_available: list[str] | None = None,
    measured: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if profile not in PROFILES:
        raise ValueError(f"Perfil desconocido: {profile}")
    measured = measured or {}
    installed = list(installed)
    catalog = list(catalog)
    contexts = [CONTEXT[profile]]
    if profile == "high":
        contexts.append(CONTEXT["medium"])  # respaldo: menos contexto, mismo modelo
    by_context = {
        ctx: build_candidates(installed, catalog, hw, ctx) for ctx in contexts
    }
    candidates = by_context[contexts[0]]

    def pools(kind: str) -> list[tuple[int, list[Candidate]]]:
        out = []
        for ctx in contexts:
            cands = by_context[ctx]
            if kind == "vision":
                pool = [c for c in cands if c.vision and not c.embedding]
            else:
                text = [c for c in cands if not c.embedding and not c.vision]
                # Para estudio/conversación se prefieren modelos generalistas.
                pool = [c for c in text if not c.code] or text
            out.append((ctx, pool))
        return out

    recs = [
        _llm_recommendation("chat", pools("text"), profile, measured, False),
        _llm_recommendation("conversation", pools("text"), profile, measured, True),
        _llm_recommendation("translation", pools("text"), profile, measured, True),
        _llm_recommendation("vision", pools("vision"), profile, measured, False),
        _stt_recommendation(stt_recs or [], profile),
        _ocr_recommendation(hw, ocr_available or []),
        _documents_recommendation(candidates, profile),
    ]
    return {
        "profile": profile,
        "profile_label": PROFILES[profile],
        "recommendations": recs,
        "acceleration": acceleration_report(hw),
        "never_recommended": [TIER_META[TIER_ALTO][1], TIER_META[TIER_IMPOSIBLE][1]],
    }

"""Model Router: qué proveedor y modelo atiende cada tarea.

Reglas no negociables (ADR-0005):

1. Nunca se elige un proveedor remoto sin consentimiento ``remote_data``.
2. Nunca se elige uno que puede generar costos sin consentimiento ``costs``.
3. Si la petición lleva documentos personales (``sensitive``), un remoto
   solo es válido con el consentimiento ``documents`` para ese proveedor.
4. Las alternativas (``fallbacks``) salen del mismo conjunto permitido; la
   decisión marca qué alternativas implican pasar de local a remoto para
   que el ejecutor lo avise. No hay cambios silenciosos.
5. Cada candidato descartado lleva su motivo (para la interfaz).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..providers.access import AccessTier


class Task(str, Enum):
    ANSWER = "answer"
    QUESTION_DETECTION = "question_detection"
    TRANSLATION = "translation"
    SUMMARY = "summary"
    VISION = "vision"
    CHALLENGE = "challenge"
    EMBEDDING = "embedding"

    @property
    def label(self) -> str:
        return {
            Task.ANSWER: "Generación de respuestas",
            Task.QUESTION_DETECTION: "Detección de preguntas",
            Task.TRANSLATION: "Traducción",
            Task.SUMMARY: "Resumen",
            Task.VISION: "Análisis de imágenes",
            Task.CHALLENGE: "Resolución de desafíos",
            Task.EMBEDDING: "Recuperación contextual (embeddings)",
        }[self]

    @property
    def required_capabilities(self) -> frozenset[str]:
        if self in (Task.VISION,):
            return frozenset({"chat", "vision"})
        if self is Task.EMBEDDING:
            return frozenset({"embeddings"})
        return frozenset({"chat"})


class Strategy(str, Enum):
    BALANCED = "balanced"
    SPEED = "speed"
    QUALITY = "quality"
    LOCAL_FIRST = "local_first"
    LOW_COST = "low_cost"
    SELECTED_ONLY = "selected_only"

    @property
    def label(self) -> str:
        return {
            Strategy.BALANCED: "Equilibrado",
            Strategy.SPEED: "Priorizar velocidad",
            Strategy.QUALITY: "Priorizar calidad",
            Strategy.LOCAL_FIRST: "Priorizar ejecución local",
            Strategy.LOW_COST: "Priorizar bajo costo",
            Strategy.SELECTED_ONLY: "Solo los modelos que elegí",
        }[self]


@dataclass(frozen=True)
class Candidate:
    provider_id: str
    model: str
    local: bool
    access: AccessTier
    capabilities: frozenset[str]
    quality: int = 3  # 1–5 orientativo
    speed: int = 3  # 1–5 orientativo en este equipo
    ready: bool = True  # instalado / clave guardada
    fits_hardware: bool | None = None  # None = no evaluado (solo locales)
    hardware_note: str = ""

    @property
    def key(self) -> str:
        return f"{self.provider_id}:{self.model}"

    @property
    def label(self) -> str:
        where = "local" if self.local else self.provider_id
        return f"{self.model} ({where})"


@dataclass(frozen=True)
class RoutingPolicy:
    strategy: Strategy = Strategy.LOCAL_FIRST
    remote_allowed: frozenset[str] = frozenset()  # consentimiento remote_data
    cost_allowed: frozenset[str] = frozenset()  # consentimiento costs
    documents_allowed: frozenset[str] = frozenset()  # consentimiento documents
    pinned: dict[Task, str] = field(default_factory=dict)  # Task → candidate.key
    selected: frozenset[str] = frozenset()  # claves para SELECTED_ONLY


@dataclass(frozen=True)
class RouteRequest:
    task: Task
    sensitive: bool = False  # incluye documentos personales del perfil
    extra_capabilities: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Rejection:
    candidate: Candidate
    reason: str


@dataclass(frozen=True)
class RouteDecision:
    task: Task
    primary: Candidate | None
    fallbacks: tuple[Candidate, ...] = ()
    rejected: tuple[Rejection, ...] = ()
    reasons: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.primary is not None

    def crosses_to_remote(self, candidate: Candidate) -> bool:
        """¿Usar esta alternativa implica pasar de local a remoto?"""
        return self.primary is not None and self.primary.local and not candidate.local

    def explain(self) -> str:
        if self.primary is None:
            motives = "; ".join(
                f"{r.candidate.label}: {r.reason}" for r in self.rejected
            )
            return f"No hay ningún modelo permitido para {self.task.label.lower()}." + (
                f" Descartados: {motives}." if motives else ""
            )
        return f"{self.task.label}: {self.primary.label}. " + " ".join(self.reasons)


def _rejection_reason(
    cand: Candidate, request: RouteRequest, policy: RoutingPolicy
) -> str | None:
    needed = request.task.required_capabilities | request.extra_capabilities
    missing = needed - cand.capabilities
    if missing:
        return f"no admite {', '.join(sorted(missing))}"
    if not cand.ready:
        return "no está instalado o falta la clave"
    if cand.local and cand.fits_hardware is False:
        return cand.hardware_note or "no es compatible con el hardware de este equipo"
    if not cand.local:
        if cand.provider_id not in policy.remote_allowed:
            return "servicio remoto sin autorización para enviar datos"
        if cand.access.may_cost_money and cand.provider_id not in policy.cost_allowed:
            return f"{cand.access.label.lower()}: falta autorizar posibles costos"
        if request.sensitive and cand.provider_id not in policy.documents_allowed:
            return "la consulta incluye documentos personales y este servicio no está autorizado para recibirlos"
    if policy.strategy is Strategy.SELECTED_ONLY and cand.key not in policy.selected:
        return "no está entre los modelos elegidos"
    return None


_TEXT_TASKS = frozenset(
    {Task.ANSWER, Task.QUESTION_DETECTION, Task.TRANSLATION, Task.SUMMARY}
)


def _score(cand: Candidate, strategy: Strategy, task: Task | None = None) -> float:
    quality, speed = float(cand.quality), float(cand.speed)
    base = {
        Strategy.BALANCED: quality + speed,
        Strategy.SPEED: quality + 2 * speed,
        Strategy.QUALITY: 2 * quality + speed,
        Strategy.LOCAL_FIRST: quality + speed + (10 if cand.local else 0),
        Strategy.LOW_COST: quality + speed + _cost_bonus(cand.access),
        Strategy.SELECTED_ONLY: quality + speed,
    }[strategy]
    if cand.local and cand.fits_hardware is None:
        base -= 0.5  # sin evaluar: ligera penalización
    # Desempates por especialización: a igual tamaño, un modelo de visión o
    # de código suele rendir menos en conversación/traducción que uno general.
    if task in _TEXT_TASKS:
        if "vision" in cand.capabilities:
            base -= 0.25
        if "code" in cand.capabilities:
            base -= 0.5
    elif task is Task.CHALLENGE and "code" in cand.capabilities:
        base += 0.5
    return base


def _cost_bonus(access: AccessTier) -> float:
    return {
        AccessTier.LOCAL: 10,
        AccessTier.FREE: 9,
        AccessTier.FREE_LIMITED: 6,
        AccessTier.MIXED: 4,
        AccessTier.CREDITS: 2,
    }.get(access, 0)


def route(
    request: RouteRequest,
    candidates: list[Candidate],
    policy: RoutingPolicy,
) -> RouteDecision:
    allowed: list[Candidate] = []
    rejected: list[Rejection] = []
    for cand in candidates:
        reason = _rejection_reason(cand, request, policy)
        if reason is None:
            allowed.append(cand)
        else:
            rejected.append(Rejection(cand, reason))
    if not allowed:
        return RouteDecision(request.task, None, rejected=tuple(rejected))

    ordered = sorted(
        allowed, key=lambda c: _score(c, policy.strategy, request.task), reverse=True
    )
    reasons: list[str] = [f"Estrategia: {policy.strategy.label.lower()}."]
    pinned_key = policy.pinned.get(request.task)
    if pinned_key:
        pinned = next((c for c in ordered if c.key == pinned_key), None)
        if pinned is not None:
            ordered.remove(pinned)
            ordered.insert(0, pinned)
            reasons.append("Modelo fijado por ti para esta tarea.")
        else:
            reasons.append(
                "El modelo que fijaste no está disponible o no está permitido; "
                "se eligió otro."
            )
    primary = ordered[0]
    if primary.local:
        reasons.append("Se ejecuta en tu equipo.")
    else:
        reasons.append(f"Servicio remoto autorizado ({primary.access.label.lower()}).")
    return RouteDecision(
        request.task,
        primary,
        fallbacks=tuple(ordered[1:]),
        rejected=tuple(rejected),
        reasons=tuple(reasons),
    )

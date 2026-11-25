"""Detección de preguntas e intervenciones que piden respuesta.

Heurística explicable (sin modelos) para español e inglés. Whisper no
siempre escribe "¿?" (p. ej. "Podrías contarme cuál fue tu experiencia…"),
así que pesan más los patrones de petición que los signos.

``TurnAccumulator`` une los segmentos finales consecutivos de una misma
fuente en un "turno" y solo lo entrega cuando la persona deja de hablar
``settle_s`` segundos: así no se genera una respuesta por fragmento.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from ..audio.types import SourceKind


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


_FILLERS = re.compile(
    r"^(?:y|e|bueno|bien|entonces|ok|okay|vale|perfecto|ahora|muy bien|"
    r"so|well|okay so|alright|now|and)[,.]?\s+",
)
_INTERROGATIVE = re.compile(
    r"^(?:que|como|cual(?:es)?|cuando|donde|por que|para que|quien(?:es)?|"
    r"cuant[oa]s?|what|how|why|when|where|who|which|whose)\b"
)
_REQUEST = re.compile(
    r"\b(?:cuentame|cuentanos|hablame|hablanos|explica(?:me|nos)?|describe(?:me|nos)?|"
    r"dime|dinos|comparte(?:nos)?|podrias|puedes|pudieras|serias capaz|"
    r"me (?:podrias|puedes|dirias)|que opinas|que harias|como harias|"
    r"como resolverias|como abordarias|has (?:trabajado|usado|hecho|tenido|liderado)|"
    r"tienes experiencia|conoces|sabes|te consideras|danos un ejemplo|"
    r"tell me|walk me through|could you|can you|would you|describe|explain|"
    r"have you|do you|did you|are you|what would you|how would you)\b"
)
_CLOSING = re.compile(
    r"^(?:gracias|muchas gracias|perfecto|excelente|genial|entendido|de acuerdo|"
    r"thank you|thanks|great|perfect|got it)\W*$"
)


@dataclass(frozen=True)
class QuestionScore:
    score: float  # 0–1
    reasons: tuple[str, ...]

    def is_question(self, threshold: float) -> bool:
        return self.score >= threshold


def score_question(text: str) -> QuestionScore:
    raw = text.strip()
    if not raw:
        return QuestionScore(0.0, ("vacío",))
    folded = _FILLERS.sub("", _fold(raw)).strip()
    words = folded.split()
    reasons: list[str] = []
    score = 0.0
    if "?" in raw or "¿" in raw:
        score += 0.6
        reasons.append("signos de interrogación")
    if _INTERROGATIVE.match(folded):
        score += 0.4
        reasons.append("empieza con interrogativo")
    if _REQUEST.search(folded):
        score += 0.5
        reasons.append("pide una respuesta o experiencia")
    if _CLOSING.match(folded):
        score -= 0.6
        reasons.append("cierre o agradecimiento")
    if len(words) < 3:
        score -= 0.4
        reasons.append("demasiado corto")
    elif len(words) > 80:
        score -= 0.1
        reasons.append("intervención muy larga")
    return QuestionScore(max(0.0, min(1.0, score)), tuple(reasons))


SENSITIVITY_THRESHOLDS = {"low": 0.75, "medium": 0.5, "high": 0.35}


def similar(a: str, b: str, threshold: float = 0.8) -> bool:
    """¿Es la misma pregunta? (para no responder dos veces).

    Jaccard de palabras significativas, o contención casi total cuando una
    es la otra con un preámbulo ("Podrías contarme cuál fue…" ≈ "¿Cuál fue…?").
    """
    ta = {w for w in re.findall(r"\w{3,}", _fold(a))}
    tb = {w for w in re.findall(r"\w{3,}", _fold(b))}
    if not ta or not tb:
        return False
    common = len(ta & tb)
    if common / len(ta | tb) >= threshold:
        return True
    smaller = min(len(ta), len(tb))
    return smaller >= 4 and common / smaller >= 0.9


@dataclass
class Turn:
    source: SourceKind
    texts: list[str] = field(default_factory=list)
    t_start: float = 0.0
    t_end: float = 0.0
    last_update: float = 0.0  # reloj monotónico de la última llegada

    @property
    def text(self) -> str:
        return " ".join(t.strip() for t in self.texts if t.strip())


class TurnAccumulator:
    """Agrupa segmentos finales consecutivos por fuente."""

    def __init__(self, settle_s: float = 1.2, max_turn_s: float = 45.0) -> None:
        self.settle_s = settle_s
        self.max_turn_s = max_turn_s
        self._open: dict[SourceKind, Turn] = {}

    def add(
        self, source: SourceKind, text: str, t_start: float, t_end: float, now: float
    ) -> list[Turn]:
        """Añade un final; devuelve turnos cerrados (p. ej. de la otra fuente)."""
        closed: list[Turn] = []
        # Si habla la otra parte, el turno anterior termina.
        for other in list(self._open):
            if other is not source:
                closed.append(self._open.pop(other))
        current = self._open.get(source)
        if current is None:
            current = Turn(source, t_start=t_start)
            self._open[source] = current
        turn = current
        turn.texts.append(text)
        turn.t_end = t_end
        turn.last_update = now
        if turn.t_end - turn.t_start >= self.max_turn_s:
            closed.append(self._open.pop(source))
        return closed

    def due(self, now: float) -> list[Turn]:
        """Turnos cuya fuente lleva ``settle_s`` sin hablar."""
        ready = [
            s for s, t in self._open.items() if now - t.last_update >= self.settle_s
        ]
        return [self._open.pop(s) for s in ready]

    def flush(self) -> list[Turn]:
        turns = list(self._open.values())
        self._open.clear()
        return turns

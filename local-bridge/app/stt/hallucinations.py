"""Filtro de "alucinaciones" típicas de Whisper.

Con silencio, ruido o música, Whisper tiende a producir frases de
subtítulos de su corpus de entrenamiento ("Subtítulos realizados por la
comunidad de Amara.org", "Gracias por ver el vídeo"...). Se descartan solo
cuando el texto completo coincide con una de esas fórmulas: nunca se
recortan frases reales que las contengan dentro de otro contenido.
"""

from __future__ import annotations

import re
import unicodedata

_PATTERNS = [
    r"subt[ií]tulos? (realizados|hechos) por la comunidad de amara\.?org",
    r"subt[ií]tulos? por la comunidad de amara\.?org",
    r"subtitulado por .{0,40}",
    r"gracias por ver( el v[ií]deo)?",
    r"¡?gracias por ver!?",
    r"suscr[ií]bete( al canal)?",
    r"no olvides suscribirte.{0,40}",
    r"thanks? for watching!?",
    r"thank you\.?",
    r"please subscribe.{0,40}",
    r"subtitles by the amara\.?org community",
    r"\[?m[uú]sica\]?",
    r"\[?music\]?",
    r"\(?m[uú]sica\)?",
    r"\.+",
]
_REGEX = re.compile(r"^\s*(?:" + "|".join(_PATTERNS) + r")\s*[.!¡?¿]*\s*$", re.I)


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text).strip().lower()
    return re.sub(r"\s+", " ", text)


def is_hallucination(text: str) -> bool:
    cleaned = _normalize(text)
    if not cleaned:
        return True
    return bool(_REGEX.match(cleaned))


def is_repetition_loop(text: str, min_repeats: int = 4) -> bool:
    """Detecta bucles del decodificador ("sí sí sí sí sí...")."""
    words = _normalize(text).split()
    if len(words) < min_repeats * 2:
        return False
    for size in (1, 2, 3):
        chunks = [" ".join(words[i : i + size]) for i in range(0, len(words), size)]
        streak = best = 1
        for prev, cur in zip(chunks, chunks[1:], strict=False):
            streak = streak + 1 if cur == prev else 1
            best = max(best, streak)
        if best >= min_repeats and best * size >= len(words) * 0.6:
            return True
    return False

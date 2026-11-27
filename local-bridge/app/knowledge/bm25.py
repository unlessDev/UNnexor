"""Recuperación léxica BM25 (sin modelos ni descargas).

Tokenización: minúsculas, sin tildes (para que "gestión" y "gestion"
coincidan), palabras de 2+ caracteres, sin palabras vacías en español e
inglés.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Sequence

_STOPWORDS = frozenset(
    [
        "a",
        "al",
        "algo",
        "algunas",
        "algunos",
        "ante",
        "antes",
        "como",
        "con",
        "contra",
        "cual",
        "cuando",
        "de",
        "del",
        "desde",
        "donde",
        "durante",
        "e",
        "el",
        "ella",
        "ellas",
        "ellos",
        "en",
        "entre",
        "era",
        "es",
        "esa",
        "esas",
        "ese",
        "eso",
        "esos",
        "esta",
        "estas",
        "este",
        "esto",
        "estos",
        "fue",
        "ha",
        "hay",
        "la",
        "las",
        "le",
        "les",
        "lo",
        "los",
        "mas",
        "me",
        "mi",
        "mis",
        "mucho",
        "muy",
        "nada",
        "ni",
        "no",
        "nos",
        "o",
        "os",
        "otra",
        "otro",
        "para",
        "pero",
        "poco",
        "por",
        "que",
        "quien",
        "se",
        "sea",
        "ser",
        "si",
        "sin",
        "sobre",
        "su",
        "sus",
        "tambien",
        "te",
        "tiene",
        "tu",
        "tus",
        "un",
        "una",
        "uno",
        "unos",
        "y",
        "ya",
        "yo",
        "the",
        "of",
        "and",
        "to",
        "in",
        "is",
        "it",
        "that",
        "for",
        "on",
        "with",
        "as",
        "was",
        "are",
        "be",
        "this",
        "by",
        "an",
        "at",
        "or",
        "from",
        "you",
        "your",
        "have",
        "has",
        "had",
        "not",
        "but",
        "what",
        "which",
        "who",
        "how",
        "why",
        "can",
        "could",
        "would",
        "will",
        "do",
        "does",
        "did",
        "its",
        "their",
        "they",
        "them",
        "we",
        "our",
        "i",
        "me",
        "my",
    ]
)


def tokenize(text: str) -> list[str]:
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return [t for t in re.findall(r"[a-z0-9]{2,}", text) if t not in _STOPWORDS]


class BM25Index:
    def __init__(
        self, documents: Sequence[str], k1: float = 1.4, b: float = 0.75
    ) -> None:
        self._k1 = k1
        self._b = b
        self._docs = [Counter(tokenize(doc)) for doc in documents]
        self._lengths = [sum(c.values()) for c in self._docs]
        self._avg = (sum(self._lengths) / len(self._lengths)) if self._lengths else 0.0
        df: Counter[str] = Counter()
        for counts in self._docs:
            df.update(counts.keys())
        n = len(self._docs)
        self._idf = {
            term: math.log(1 + (n - freq + 0.5) / (freq + 0.5))
            for term, freq in df.items()
        }

    def __len__(self) -> int:
        return len(self._docs)

    def scores(self, query: str) -> list[float]:
        terms = tokenize(query)
        out: list[float] = []
        for counts, length in zip(self._docs, self._lengths, strict=True):
            score = 0.0
            norm = self._k1 * (1 - self._b + self._b * length / (self._avg or 1))
            for term in terms:
                tf = counts.get(term, 0)
                if tf:
                    score += self._idf[term] * tf * (self._k1 + 1) / (tf + norm)
            out.append(score)
        return out

    def top(self, query: str, k: int) -> list[tuple[int, float]]:
        ranked = sorted(enumerate(self.scores(query)), key=lambda x: x[1], reverse=True)
        return [(i, s) for i, s in ranked[:k] if s > 0]

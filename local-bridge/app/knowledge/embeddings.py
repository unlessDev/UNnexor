"""Embeddings opcionales mediante Ollama (``POST /api/embed``).

Solo se usan si el usuario instaló un modelo de embeddings (p. ej.
``nomic-embed-text``); si no, la base de conocimiento funciona con BM25.
El cálculo es local: los documentos no salen del equipo.
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx
import numpy as np

from ..audio.types import FloatArray

RECOMMENDED_MODEL = "nomic-embed-text"


class EmbeddingError(RuntimeError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class Embedder(Protocol):
    model: str

    def embed(self, texts: list[str]) -> list[FloatArray]: ...


class OllamaEmbedder:
    def __init__(
        self,
        model: str = RECOMMENDED_MODEL,
        base_url: str = "http://127.0.0.1:11434",
        timeout: float = 120.0,
        transport: httpx.BaseTransport | None = None,
        batch_size: int = 32,
    ) -> None:
        self.model = model
        self._batch = max(1, batch_size)
        self._client = httpx.Client(
            base_url=base_url, timeout=timeout, transport=transport
        )

    def close(self) -> None:
        self._client.close()

    def embed(self, texts: list[str]) -> list[FloatArray]:
        vectors: list[FloatArray] = []
        for start in range(0, len(texts), self._batch):
            batch = texts[start : start + self._batch]
            try:
                response = self._client.post(
                    "/api/embed", json={"model": self.model, "input": batch}
                )
            except httpx.HTTPError as exc:
                raise EmbeddingError(
                    "No se pudo conectar con Ollama para calcular embeddings."
                ) from exc
            if response.status_code == 404:
                raise EmbeddingError(
                    f"El modelo de embeddings '{self.model}' no está instalado en Ollama."
                )
            if response.status_code != 200:
                raise EmbeddingError(f"Ollama devolvió HTTP {response.status_code}.")
            data: Any = response.json()
            rows = data.get("embeddings") if isinstance(data, dict) else None
            if not isinstance(rows, list) or len(rows) != len(batch):
                raise EmbeddingError("Respuesta de embeddings no válida.")
            for row in rows:
                vector = np.asarray(row, dtype=np.float32)
                norm = float(np.linalg.norm(vector))
                vectors.append(vector / norm if norm else vector)
        return vectors

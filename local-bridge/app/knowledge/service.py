"""Base de conocimiento: documentos por perfil, búsqueda y contexto.

Almacenamiento en SQLite (``paths.copilot_dir()/knowledge.sqlite3``):
documentos, fragmentos y vectores (``float32``). Búsqueda híbrida:

* BM25 siempre (sin modelos).
* Vectores si hay un ``Embedder`` y los fragmentos ya tienen embeddings
  de ese modelo; ambos rankings se fusionan con Reciprocal Rank Fusion.

Escala prevista: cientos de documentos personales por perfil; el índice
léxico y la matriz de vectores se cachean por perfil y se invalidan al
cambiar los documentos.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .. import paths
from .bm25 import BM25Index
from .chunker import chunk_document
from .embeddings import Embedder, EmbeddingError
from .extract import (
    MAX_FILE_BYTES,
    ExtractedDocument,
    ExtractedPage,
    ExtractionError,
    detect_kind,
    extract,
)

log = logging.getLogger("extdator.knowledge")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, name TEXT NOT NULL,
    kind TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL,
    chars INTEGER NOT NULL, added_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id TEXT NOT NULL,
    profile_id TEXT NOT NULL, idx INTEGER NOT NULL, text TEXT NOT NULL,
    page INTEGER, section TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS vectors (
    chunk_id INTEGER NOT NULL, model TEXT NOT NULL, dim INTEGER NOT NULL,
    data BLOB NOT NULL, PRIMARY KEY (chunk_id, model)
);
CREATE INDEX IF NOT EXISTS idx_chunks_profile ON chunks(profile_id);
CREATE INDEX IF NOT EXISTS idx_docs_profile ON documents(profile_id);
"""

RRF_K = 60


@dataclass(frozen=True)
class DocumentInfo:
    id: str
    name: str
    kind: str
    size: int
    chars: int
    chunks: int
    added_at: str
    embedded_chunks: int = 0


@dataclass(frozen=True)
class SearchHit:
    chunk_id: int
    doc_id: str
    doc_name: str
    text: str
    page: int | None
    section: str
    score: float
    method: str  # bm25 | vector | hybrid

    @property
    def source(self) -> str:
        where = [self.doc_name]
        if self.page:
            where.append(f"pág. {self.page}")
        if self.section:
            where.append(self.section)
        return " · ".join(where)


@dataclass
class _Cache:
    signature: tuple[int, int]
    rows: list[tuple[int, str, str, str, int | None, str]]
    bm25: BM25Index
    vectors: dict[str, tuple[list[int], np.ndarray]]


class KnowledgeBase:
    def __init__(
        self, db_path: Path | None = None, embedder: Embedder | None = None
    ) -> None:
        self._db = db_path or (paths.copilot_dir() / "knowledge.sqlite3")
        self.embedder = embedder
        self._lock = threading.RLock()
        self._cache: dict[str, _Cache] = {}
        self._db.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._db, timeout=10)
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------ ingesta
    def add_file(self, profile_id: str, path: Path) -> DocumentInfo:
        detect_kind(path)  # formato admitido antes de leer nada
        if not path.is_file():
            raise ExtractionError("El archivo no existe o no es accesible.")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ExtractionError(
                f"El archivo supera el máximo de {MAX_FILE_BYTES // (1024 * 1024)} MB."
            )
        data = path.read_bytes()
        document = extract(path, data)
        return self._store(profile_id, document, data)

    def add_note(self, profile_id: str, title: str, text: str) -> DocumentInfo:
        title = (title or "Nota").strip()[:120]
        body = text.strip()
        if not body:
            raise ValueError("La nota está vacía.")
        document = ExtractedDocument(title, "note", (ExtractedPage(body),))
        return self._store(profile_id, document, body.encode("utf-8"))

    def _store(
        self, profile_id: str, document: ExtractedDocument, raw: bytes
    ) -> DocumentInfo:
        digest = hashlib.sha256(raw).hexdigest()
        chunks = chunk_document(document)
        now = datetime.now(timezone.utc).isoformat()
        doc_id = uuid.uuid4().hex[:16]
        with self._lock, self._connect() as conn:
            # Mismo nombre en el perfil = nueva versión: se reemplaza.
            for (old_id,) in conn.execute(
                "SELECT id FROM documents WHERE profile_id=? AND name=?",
                (profile_id, document.name),
            ).fetchall():
                self._delete_doc(conn, old_id)
            conn.execute(
                "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?)",
                (
                    doc_id,
                    profile_id,
                    document.name,
                    document.kind,
                    len(raw),
                    digest,
                    document.char_count,
                    now,
                ),
            )
            conn.executemany(
                "INSERT INTO chunks (doc_id, profile_id, idx, text, page, section) "
                "VALUES (?,?,?,?,?,?)",
                [
                    (doc_id, profile_id, c.index, c.text, c.page, c.section)
                    for c in chunks
                ],
            )
            self._cache.pop(profile_id, None)
        log.info("Documento añadido (%s, %d fragmentos).", document.kind, len(chunks))
        return DocumentInfo(
            doc_id,
            document.name,
            document.kind,
            len(raw),
            document.char_count,
            len(chunks),
            now,
        )

    @staticmethod
    def _delete_doc(conn: sqlite3.Connection, doc_id: str) -> None:
        conn.execute(
            "DELETE FROM vectors WHERE chunk_id IN (SELECT id FROM chunks WHERE doc_id=?)",
            (doc_id,),
        )
        conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
        conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))

    def remove(self, doc_id: str) -> None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT profile_id FROM documents WHERE id=?", (doc_id,)
            ).fetchone()
            self._delete_doc(conn, doc_id)
            if row:
                self._cache.pop(row[0], None)

    def delete_profile(self, profile_id: str) -> None:
        with self._lock, self._connect() as conn:
            for (doc_id,) in conn.execute(
                "SELECT id FROM documents WHERE profile_id=?", (profile_id,)
            ).fetchall():
                self._delete_doc(conn, doc_id)
            self._cache.pop(profile_id, None)

    def list_documents(self, profile_id: str) -> list[DocumentInfo]:
        model = self.embedder.model if self.embedder else ""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT d.id, d.name, d.kind, d.size, d.chars, d.added_at,
                          COUNT(c.id),
                          SUM(CASE WHEN v.chunk_id IS NOT NULL THEN 1 ELSE 0 END)
                   FROM documents d
                   LEFT JOIN chunks c ON c.doc_id = d.id
                   LEFT JOIN vectors v ON v.chunk_id = c.id AND v.model = ?
                   WHERE d.profile_id = ? GROUP BY d.id ORDER BY d.added_at""",
                (model, profile_id),
            ).fetchall()
        return [
            DocumentInfo(r[0], r[1], r[2], r[3], r[4], r[6], r[5], int(r[7] or 0))
            for r in rows
        ]

    # ---------------------------------------------------------- embeddings
    def embed_pending(
        self, profile_id: str, on_progress: Callable[[int, int], None] | None = None
    ) -> int:
        """Calcula los vectores que faltan para el modelo actual."""
        if self.embedder is None:
            return 0
        model = self.embedder.model
        with self._connect() as conn:
            pending = conn.execute(
                """SELECT c.id, c.text FROM chunks c
                   LEFT JOIN vectors v ON v.chunk_id = c.id AND v.model = ?
                   WHERE c.profile_id = ? AND v.chunk_id IS NULL ORDER BY c.id""",
                (model, profile_id),
            ).fetchall()
        done = 0
        for start in range(0, len(pending), 32):
            batch = pending[start : start + 32]
            vectors = self.embedder.embed([text for _, text in batch])
            with self._lock, self._connect() as conn:
                conn.executemany(
                    "INSERT OR REPLACE INTO vectors VALUES (?,?,?,?)",
                    [
                        (
                            cid,
                            model,
                            int(vec.shape[0]),
                            vec.astype(np.float32).tobytes(),
                        )
                        for (cid, _), vec in zip(batch, vectors, strict=True)
                    ],
                )
                self._cache.pop(profile_id, None)
            done += len(batch)
            if on_progress is not None:
                on_progress(done, len(pending))
        return done

    def rebuild_vectors(self, profile_id: str) -> int:
        """Reconstruye los vectores del perfil (p. ej. al cambiar de modelo)."""
        if self.embedder is None:
            return 0
        with self._lock, self._connect() as conn:
            conn.execute(
                "DELETE FROM vectors WHERE model=? AND chunk_id IN "
                "(SELECT id FROM chunks WHERE profile_id=?)",
                (self.embedder.model, profile_id),
            )
            self._cache.pop(profile_id, None)
        return self.embed_pending(profile_id)

    # ------------------------------------------------------------ búsqueda
    def _load(self, profile_id: str) -> _Cache:
        with self._connect() as conn:
            signature = conn.execute(
                "SELECT COUNT(*), COALESCE(MAX(id), 0) FROM chunks WHERE profile_id=?",
                (profile_id,),
            ).fetchone()
            cached = self._cache.get(profile_id)
            if cached is not None and cached.signature == tuple(signature):
                return cached
            rows = conn.execute(
                """SELECT c.id, c.doc_id, d.name, c.text, c.page, c.section
                   FROM chunks c JOIN documents d ON d.id = c.doc_id
                   WHERE c.profile_id = ? ORDER BY c.id""",
                (profile_id,),
            ).fetchall()
        cache = _Cache(
            tuple(signature),
            rows,
            BM25Index([f"{r[2]} {r[5]} {r[3]}" for r in rows]),
            {},
        )
        self._cache[profile_id] = cache
        return cache

    def _vectors(
        self, profile_id: str, cache: _Cache, model: str
    ) -> tuple[list[int], np.ndarray]:
        if model in cache.vectors:
            return cache.vectors[model]
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT v.chunk_id, v.data FROM vectors v
                   JOIN chunks c ON c.id = v.chunk_id
                   WHERE c.profile_id = ? AND v.model = ?""",
                (profile_id, model),
            ).fetchall()
        ids = [r[0] for r in rows]
        matrix = (
            np.vstack([np.frombuffer(r[1], dtype=np.float32) for r in rows])
            if rows
            else np.zeros((0, 0), dtype=np.float32)
        )
        cache.vectors[model] = (ids, matrix)
        return ids, matrix

    def search(self, profile_id: str, query: str, k: int = 5) -> list[SearchHit]:
        if not query.strip():
            return []
        with self._lock:
            cache = self._load(profile_id)
        if not cache.rows:
            return []
        by_id = {row[0]: row for row in cache.rows}
        ranks: dict[int, float] = {}
        methods: dict[int, set[str]] = {}
        for rank, (pos, _score) in enumerate(cache.bm25.top(query, 20)):
            cid = cache.rows[pos][0]
            ranks[cid] = ranks.get(cid, 0.0) + 1.0 / (RRF_K + rank)
            methods.setdefault(cid, set()).add("bm25")
        if self.embedder is not None:
            try:
                ids, matrix = self._vectors(profile_id, cache, self.embedder.model)
                if ids:
                    query_vec = self.embedder.embed([query])[0]
                    sims = matrix @ query_vec
                    for rank, idx in enumerate(np.argsort(-sims)[:20]):
                        if sims[idx] <= 0.2:
                            break
                        cid = ids[int(idx)]
                        ranks[cid] = ranks.get(cid, 0.0) + 1.0 / (RRF_K + rank)
                        methods.setdefault(cid, set()).add("vector")
            except EmbeddingError as exc:
                log.info(
                    "Búsqueda vectorial no disponible (%s); solo BM25.", exc.message
                )
        ordered = sorted(ranks.items(), key=lambda x: x[1], reverse=True)[:k]
        hits = []
        for cid, score in ordered:
            row = by_id[cid]
            found = methods[cid]
            method = "hybrid" if len(found) == 2 else next(iter(found))
            hits.append(
                SearchHit(
                    cid, row[1], row[2], row[3], row[4], row[5], round(score, 5), method
                )
            )
        return hits


def build_context(hits: list[SearchHit], max_chars: int = 3500) -> str:
    """Bloque de contexto con fuentes citables para el prompt."""
    parts: list[str] = []
    used = 0
    for number, hit in enumerate(hits, start=1):
        snippet = hit.text.strip()
        remaining = max_chars - used
        if remaining < 200:
            break
        if len(snippet) > remaining:
            snippet = snippet[: remaining - 1].rsplit(" ", 1)[0] + "…"
        block = f"[{number}] Fuente: {hit.source}\n{snippet}"
        parts.append(block)
        used += len(block)
    return "\n\n".join(parts)

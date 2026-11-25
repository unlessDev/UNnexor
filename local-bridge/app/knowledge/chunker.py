"""Troceado de documentos con solapamiento y metadatos de origen.

Se respetan límites naturales (párrafos, luego frases) y se conserva el
último encabezado Markdown como "sección" para citar la fuente.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .extract import ExtractedDocument

_HEADING = re.compile(r"^#{1,6}\s+(.+)$", re.M)
_SENTENCE = re.compile(r"(?<=[.!?¿¡:;])\s+")


@dataclass(frozen=True)
class Chunk:
    index: int
    text: str
    page: int | None = None
    section: str = ""


def _split_long(paragraph: str, size: int) -> list[str]:
    if len(paragraph) <= size:
        return [paragraph]
    pieces: list[str] = []
    current = ""
    for sentence in _SENTENCE.split(paragraph):
        if len(sentence) > size:  # frase enorme: corte duro por palabras
            words = sentence.split()
            buf = ""
            for word in words:
                if len(buf) + len(word) + 1 > size and buf:
                    pieces.append(buf)
                    buf = ""
                buf = f"{buf} {word}".strip()
            sentence = buf
        if current and len(current) + len(sentence) + 1 > size:
            pieces.append(current)
            current = ""
        current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    return pieces


def chunk_document(
    document: ExtractedDocument, size: int = 900, overlap: int = 150
) -> list[Chunk]:
    if size < 200 or not 0 <= overlap < size // 2:
        raise ValueError("Parámetros de troceado inválidos")
    chunks: list[Chunk] = []
    for page in document.pages:
        section = ""
        buffer = ""
        buffer_section = ""
        for paragraph in re.split(r"\n\s*\n", page.text):
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            heading = _HEADING.match(paragraph)
            if heading:
                section = heading.group(1).strip()
            for piece in _split_long(paragraph, size):
                if buffer and len(buffer) + len(piece) + 2 > size:
                    chunks.append(Chunk(len(chunks), buffer, page.page, buffer_section))
                    tail = buffer[-overlap:] if overlap else ""
                    # El solapamiento empieza en un límite de palabra.
                    tail = tail[tail.find(" ") + 1 :] if " " in tail else tail
                    buffer = tail
                if not buffer:
                    buffer_section = section
                buffer = f"{buffer}\n\n{piece}".strip()
        if buffer:
            chunks.append(Chunk(len(chunks), buffer, page.page, buffer_section))
    return chunks

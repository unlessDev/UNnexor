"""Extracción de texto de documentos (TXT, Markdown, PDF, DOCX).

Seguridad y límites:

* Tamaño máximo de archivo (``MAX_FILE_BYTES``) y de texto extraído.
* El tipo se valida por contenido, no solo por extensión: PDF debe empezar
  por ``%PDF-``; DOCX debe ser un ZIP con ``word/document.xml``.
* DOCX: se limita el tamaño descomprimido del XML (bombas ZIP) y se parsea
  sin resolver entidades externas (``xml.etree`` no las resuelve).
* PDF con ``pypdf`` (BSD-3, Python puro): sin ejecutar JavaScript ni
  formularios; máximo de páginas. Los PDF cifrados se rechazan.
* Nunca se ejecuta contenido del documento.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_TEXT_CHARS = 2_000_000
MAX_PDF_PAGES = 400
MAX_DOCX_XML_BYTES = 40 * 1024 * 1024

# Formatos recomendados para el contexto del perfil: el texto llega al
# modelo tal cual, sin extracción intermedia.
RECOMMENDED_SUFFIXES = frozenset({".md", ".markdown", ".txt"})
# Extracción disponible pero no recomendada (maquetación, columnas, tablas
# y PDF escaneados pueden dar texto incompleto o desordenado).
EXPERIMENTAL_SUFFIXES = frozenset({".pdf", ".docx"})
MAX_TEXT_FILE_BYTES = 5 * 1024 * 1024  # .md/.txt: un CV o apuntes caben de sobra

SUPPORTED = {
    ".txt": "text",
    ".md": "markdown",
    ".markdown": "markdown",
    ".pdf": "pdf",
    ".docx": "docx",
}

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class ExtractionError(ValueError):
    """El documento no se puede leer (mensaje para el usuario)."""


@dataclass(frozen=True)
class ExtractedPage:
    text: str
    page: int | None = None  # PDF: número de página (1-based)


@dataclass(frozen=True)
class ExtractedDocument:
    name: str
    kind: str
    pages: tuple[ExtractedPage, ...]

    @property
    def text(self) -> str:
        return "\n\n".join(p.text for p in self.pages if p.text)

    @property
    def char_count(self) -> int:
        return sum(len(p.text) for p in self.pages)


def is_recommended(path: Path) -> bool:
    return path.suffix.lower() in RECOMMENDED_SUFFIXES


def detect_kind(path: Path) -> str:
    kind = SUPPORTED.get(path.suffix.lower())
    if kind is None:
        allowed = ", ".join(sorted(SUPPORTED))
        raise ExtractionError(f"Formato no admitido ({path.suffix}). Usa: {allowed}.")
    return kind


def extract(path: Path, data: bytes | None = None) -> ExtractedDocument:
    kind = detect_kind(path)
    if data is None:
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise ExtractionError(f"No se pudo leer el archivo: {exc}") from exc
        if size > MAX_FILE_BYTES:
            raise ExtractionError(
                f"El archivo supera el máximo de {MAX_FILE_BYTES // (1024 * 1024)} MB."
            )
        data = path.read_bytes()
    elif len(data) > MAX_FILE_BYTES:
        raise ExtractionError("El archivo supera el tamaño máximo permitido.")
    pages: tuple[ExtractedPage, ...]
    if kind in ("text", "markdown"):
        if len(data) > MAX_TEXT_FILE_BYTES:
            raise ExtractionError(
                "El archivo de texto supera "
                f"{MAX_TEXT_FILE_BYTES // (1024 * 1024)} MB; divídelo en partes."
            )
        if not data.strip():
            raise ExtractionError("El archivo está vacío.")
        pages = (ExtractedPage(_decode_text(data)),)
    elif kind == "pdf":
        pages = _extract_pdf(data)
    else:
        pages = (ExtractedPage(_extract_docx(data)),)
    document = ExtractedDocument(path.name, kind, pages)
    if document.char_count == 0:
        raise ExtractionError(
            "No se encontró texto en el documento (¿es un PDF escaneado? usa OCR)."
        )
    if document.char_count > MAX_TEXT_CHARS:
        raise ExtractionError("El documento tiene demasiado texto para indexarlo.")
    return document


def _decode_text(data: bytes) -> str:
    if b"\x00" in data[:4096]:
        raise ExtractionError("El archivo parece binario, no texto.")
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return _normalize(data.decode(encoding))
        except UnicodeDecodeError:
            continue
    raise ExtractionError("No se pudo decodificar el texto.")  # pragma: no cover


def _extract_pdf(data: bytes) -> tuple[ExtractedPage, ...]:
    if not data.startswith(b"%PDF-"):
        raise ExtractionError("El archivo no es un PDF válido.")
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError as exc:  # pragma: no cover - dependencia declarada
        raise ExtractionError("La lectura de PDF no está disponible.") from exc
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise ExtractionError("El PDF está protegido con contraseña.")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise ExtractionError(f"El PDF supera las {MAX_PDF_PAGES} páginas.")
        pages = []
        for number, page in enumerate(reader.pages, start=1):
            text = _normalize(page.extract_text() or "")
            if text:
                pages.append(ExtractedPage(text, number))
    except ExtractionError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError) as exc:
        raise ExtractionError(f"No se pudo leer el PDF: {exc}") from exc
    return tuple(pages)


def _extract_docx(data: bytes) -> str:
    if not data.startswith(b"PK"):
        raise ExtractionError("El archivo no es un DOCX válido.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            try:
                info = archive.getinfo("word/document.xml")
            except KeyError as exc:
                raise ExtractionError(
                    "El DOCX no contiene un documento de Word."
                ) from exc
            if info.file_size > MAX_DOCX_XML_BYTES:
                raise ExtractionError("El contenido del DOCX es demasiado grande.")
            xml = archive.read(info)
    except zipfile.BadZipFile as exc:
        raise ExtractionError("El DOCX está dañado.") from exc
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise ExtractionError("El DOCX tiene un XML inválido.") from exc
    paragraphs: list[str] = []
    for para in root.iter(f"{_W}p"):
        parts: list[str] = []
        for node in para.iter():
            if node.tag == f"{_W}t" and node.text:
                parts.append(node.text)
            elif node.tag == f"{_W}tab":
                parts.append("\t")
            elif node.tag in (f"{_W}br", f"{_W}cr"):
                parts.append("\n")
        style = para.find(f"{_W}pPr/{_W}pStyle")
        text = "".join(parts).strip()
        if not text:
            continue
        style_name = style.get(f"{_W}val", "") if style is not None else ""
        if style_name.lower().startswith(("heading", "título", "titulo")):
            level = re.sub(r"\D", "", style_name) or "2"
            text = "#" * min(6, int(level)) + " " + text
        paragraphs.append(text)
    return _normalize("\n\n".join(paragraphs))


def _normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t ]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

"""Perfiles, extracción, troceado, BM25, búsqueda híbrida y contexto."""

from __future__ import annotations

import io
import zipfile

import numpy as np
import pytest

from app.knowledge.bm25 import BM25Index, tokenize
from app.knowledge.chunker import chunk_document
from app.knowledge.embeddings import EmbeddingError
from app.knowledge.extract import (
    ExtractedDocument,
    ExtractedPage,
    ExtractionError,
    extract,
)
from app.knowledge.profiles import ContextProfile, Detail, ProfileStore, Tone
from app.knowledge.service import KnowledgeBase, build_context

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def make_docx(paragraphs: list[tuple[str, str]], pad_bytes: int = 0) -> bytes:
    body = "".join(
        (
            f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr><w:r><w:t>{text}</w:t></w:r></w:p>'
            if style
            else f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"
        )
        for style, text in paragraphs
    )
    xml = f'<?xml version="1.0"?><w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", xml + " " * pad_bytes)
    return buffer.getvalue()


def make_pdf(qapp, tmp_path, text: str):
    """PDF mínimo válido con texto real (Helvetica estándar, WinAnsi)."""
    del qapp
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("cp1252")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length "
        + str(len(stream)).encode()
        + b" >>\nstream\n"
        + stream
        + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
        b"/Encoding /WinAnsiEncoding >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n"
    ).encode()
    target = tmp_path / "doc.pdf"
    target.write_bytes(bytes(out))
    return target


# ------------------------------------------------------------------ perfiles
def test_profile_store_roundtrip_and_validation(tmp_path):
    store = ProfileStore(tmp_path)
    profile = ContextProfile(
        name="Entrevista backend",
        role="Desarrollador Python",
        about="5 años con FastAPI y PostgreSQL.",
        tone=Tone.TECHNICAL,
        detail=Detail.DETAILED,
        glossary="FastAPI, PostgreSQL, Kubernetes",
    )
    store.save(profile)
    loaded = store.get(profile.id)
    assert loaded is not None and loaded.tone is Tone.TECHNICAL
    assert loaded.allow_remote_documents is False  # privado por defecto
    assert [p.name for p in store.list()] == ["Entrevista backend"]
    assert "técnico" in loaded.tone_instruction()
    custom = ContextProfile(
        name="x", tone=Tone.CUSTOM, custom_tone="Habla como pirata."
    )
    assert custom.tone_instruction() == "Habla como pirata."
    with pytest.raises(ValueError):
        store.save(ContextProfile(name="  "))
    assert store.get("../../etc") is None  # ID inválido: nunca toca rutas
    store.delete(profile.id)
    assert store.list() == []


# --------------------------------------------------------------- extracción
def test_text_and_markdown_decoding(tmp_path):
    latin = tmp_path / "cv.txt"
    latin.write_bytes("Gestión de proyectos\r\n\r\n\r\nSí".encode("cp1252"))
    assert extract(latin).text == "Gestión de proyectos\n\nSí"
    binary = tmp_path / "x.txt"
    binary.write_bytes(b"\x00\x01\x02")
    with pytest.raises(ExtractionError):
        extract(binary)
    with pytest.raises(ExtractionError):
        extract(tmp_path / "virus.exe")


def test_docx_extraction_with_headings_and_bomb_guard(tmp_path):
    data = make_docx(
        [("Heading1", "Experiencia"), ("", "Lideré la migración a la nube.")]
    )
    doc = extract(tmp_path / "cv.docx", data)
    assert doc.text == "# Experiencia\n\nLideré la migración a la nube."
    with pytest.raises(ExtractionError):
        extract(
            tmp_path / "bomb.docx", make_docx([("", "x")], pad_bytes=41 * 1024 * 1024)
        )
    with pytest.raises(ExtractionError):
        extract(tmp_path / "fake.docx", b"PK\x03\x04garbage")
    with pytest.raises(ExtractionError):
        extract(tmp_path / "notzip.docx", b"hola")


def test_pdf_extraction_real_file(qapp, tmp_path):
    path = make_pdf(
        qapp, tmp_path, "Certificación AWS Solutions Architect obtenida en 2024."
    )
    doc = extract(path)
    assert doc.kind == "pdf" and doc.pages[0].page == 1
    assert "AWS Solutions Architect" in doc.text
    with pytest.raises(ExtractionError):
        extract(tmp_path / "fake.pdf", b"no soy un pdf")


def test_encrypted_pdf_is_rejected(qapp, tmp_path):
    from pypdf import PdfReader, PdfWriter

    source = make_pdf(qapp, tmp_path, "secreto")
    writer = PdfWriter()
    for page in PdfReader(str(source)).pages:
        writer.add_page(page)
    writer.encrypt("clave")
    buffer = io.BytesIO()
    writer.write(buffer)
    with pytest.raises(ExtractionError, match="contraseña"):
        extract(tmp_path / "locked.pdf", buffer.getvalue())


# ------------------------------------------------------------------ troceado
def test_chunker_respects_size_overlap_and_sections():
    paragraphs = ["# Proyectos"] + [
        f"Proyecto {i}: " + "detalle " * 40 for i in range(6)
    ]
    doc = ExtractedDocument(
        "cv.md", "markdown", (ExtractedPage("\n\n".join(paragraphs)),)
    )
    chunks = chunk_document(doc, size=600, overlap=100)
    assert len(chunks) > 2
    assert all(len(c.text) <= 600 + 100 for c in chunks)
    assert all(c.section == "Proyectos" for c in chunks)
    assert [c.index for c in chunks] == list(range(len(chunks)))
    with pytest.raises(ValueError):
        chunk_document(doc, size=100)


def test_bm25_is_accent_insensitive_and_ignores_stopwords():
    assert tokenize("La Gestión de los equipos") == ["gestion", "equipos"]
    index = BM25Index(
        ["gestion de equipos remotos", "recetas de cocina", "equipos de futbol"]
    )
    top = index.top("¿Cómo fue tu gestión de equipos?", 3)
    assert top[0][0] == 0
    assert all(score > 0 for _, score in top)


# ------------------------------------------------------------- servicio RAG
class FakeEmbedder:
    model = "fake-embed"

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        if self.fail:
            raise EmbeddingError("ollama caído")
        out = []
        for text in texts:
            low = text.lower()
            vec = np.array(
                [low.count("nube") + low.count("cloud"), low.count("cocina"), 0.1],
                dtype=np.float32,
            )
            out.append(vec / np.linalg.norm(vec))
        return out


def test_knowledge_base_add_search_replace_remove(tmp_path):
    kb = KnowledgeBase(tmp_path / "kb.sqlite3")
    cv = tmp_path / "cv.md"
    cv.write_text(
        "# Experiencia\n\nLideré la migración de la plataforma a Kubernetes.\n\n"
        "# Formación\n\nIngeniería en Sistemas, UTN.",
        encoding="utf-8",
    )
    info = kb.add_file("p1", cv)
    kb.add_note("p1", "Empresa", "La empresa usa Terraform y AWS.")
    kb.add_note("p2", "Otro perfil", "Kubernetes también aquí.")
    hits = kb.search("p1", "¿Has trabajado con Kubernetes?")
    assert hits and hits[0].doc_name == "cv.md" and hits[0].method == "bm25"
    assert hits[0].section == "Experiencia"
    assert all(h.doc_name != "Otro perfil" for h in hits)  # aislamiento por perfil
    # Mismo nombre = nueva versión.
    cv.write_text("Ahora hablo de Rust.", encoding="utf-8")
    kb.add_file("p1", cv)
    docs = kb.list_documents("p1")
    assert len(docs) == 2 and info.id not in {d.id for d in docs}
    assert kb.search("p1", "Kubernetes") == []
    kb.remove(next(d.id for d in docs if d.name == "Empresa"))
    assert [d.name for d in kb.list_documents("p1")] == ["cv.md"]
    kb.delete_profile("p1")
    assert kb.list_documents("p1") == [] and kb.list_documents("p2")


def test_hybrid_search_with_embeddings_and_fallback(tmp_path):
    embedder = FakeEmbedder()
    kb = KnowledgeBase(tmp_path / "kb.sqlite3", embedder=embedder)
    kb.add_note("p", "A", "Experiencia en plataformas cloud y nube híbrida.")
    kb.add_note("p", "B", "Me gusta la cocina italiana.")
    assert kb.list_documents("p")[0].embedded_chunks == 0
    assert kb.embed_pending("p") == 2
    assert all(d.embedded_chunks == 1 for d in kb.list_documents("p"))
    hits = kb.search("p", "nube")  # léxico + vector
    assert hits[0].doc_name == "A" and hits[0].method == "hybrid"
    semantic = kb.search("p", "cloud")  # solo el vector lo relaciona con "nube"
    assert semantic[0].doc_name == "A"
    assert kb.embed_pending("p") == 0  # nada pendiente
    kb.embedder = FakeEmbedder(fail=True)
    assert kb.search("p", "cocina")[0].method == "bm25"


def test_build_context_cites_sources_and_respects_budget(tmp_path):
    kb = KnowledgeBase(tmp_path / "kb.sqlite3")
    kb.add_note("p", "Proyecto X", "Diseñé la API de pagos. " * 80)
    hits = kb.search("p", "API de pagos")
    context = build_context(hits, max_chars=500)
    assert context.startswith("[1] Fuente: Proyecto X")
    assert len(context) <= 560 and context.endswith("…")
    assert build_context([]) == ""

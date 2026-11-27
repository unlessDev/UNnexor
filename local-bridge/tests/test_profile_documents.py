"""Documentos del perfil: .md/.txt recomendados, PDF/DOCX con aviso."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.knowledge.extract import (
    MAX_TEXT_FILE_BYTES,
    ExtractionError,
    extract,
    is_recommended,
)
from tests.test_copilot_ui import window  # noqa: F401 - fixture reutilizada


def test_recommended_formats():
    assert is_recommended(Path("cv.md")) and is_recommended(Path("notas.TXT"))
    assert not is_recommended(Path("cv.pdf")) and not is_recommended(Path("x.docx"))


def test_text_validation(tmp_path):
    empty = tmp_path / "vacio.md"
    empty.write_text("   \n", encoding="utf-8")
    with pytest.raises(ExtractionError, match="vacío"):
        extract(empty)
    big = tmp_path / "grande.txt"
    big.write_bytes(b"a" * (MAX_TEXT_FILE_BYTES + 1))
    with pytest.raises(ExtractionError, match="divídelo"):
        extract(big)
    binary = tmp_path / "falso.txt"
    binary.write_bytes(b"\x00\x01\x02binario")
    with pytest.raises(ExtractionError, match="binario"):
        extract(binary)
    cv = tmp_path / "cv.md"
    cv.write_text("# Ana\nIngeniera de datos con Python.", encoding="utf-8")
    assert "Python" in extract(cv).text


def test_unsupported_format_is_rejected(tmp_path):
    with pytest.raises(ExtractionError, match="Formato no admitido"):
        extract(tmp_path / "foto.png")


def test_profile_page_screens_files(window, monkeypatch):  # noqa: F811
    from PySide6.QtWidgets import QLabel, QMessageBox

    page = window.profiles
    help_texts = [w.text() for w in page.findChildren(QLabel)]
    assert any("Markdown (.md) o texto plano (.txt)" in t for t in help_texts)
    assert any("no aprende" in t for t in help_texts)
    warnings: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(a[2]))
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No),
    )
    kept = page._screen_files(
        ["C:/a/cv.md", "C:/a/notas.txt", "C:/a/x.pdf", "C:/a/y.exe"]
    )
    assert kept == ["C:/a/cv.md", "C:/a/notas.txt"]
    assert warnings and "y.exe" in warnings[0]
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )
    assert page._screen_files(["C:/a/x.pdf"]) == ["C:/a/x.pdf"]

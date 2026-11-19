"""Pruebas del gestor de OCR.

Los motores reales son OPCIONALES (el proyecto no los instala por
defecto): las pruebas verifican el registro, la disponibilidad, la
recomendación y los errores. Los motores concretos (RapidOCR/Pix2Text)
solo se prueban si están instalados en este entorno (``importorskip``).
"""

from __future__ import annotations

import pytest

from app import ocr_manager
from app.ocr_manager import (
    OCREngineError,
    OCRNotAvailable,
    _coerce_text,
    available_engines,
    catalog,
    engine_info,
    recommend,
)


# --------------------------------------------------------------------- #
# Registro
# --------------------------------------------------------------------- #
def test_catalog_documents_all_engines():
    entries = catalog()
    names = {e["name"] for e in entries}
    assert {"rapidocr", "pix2text", "pytesseract"} <= names
    for entry in entries:
        assert entry["license"]  # licencia siempre documentada
        assert entry["install"].startswith("pip install")
        assert entry["source"].startswith("http")
        assert entry["tier"] in ("bajo consumo", "consumo medio")


def test_engine_info_includes_real_installability():
    info = engine_info("pix2text")
    assert info["math"] is True
    assert isinstance(info["installed"], bool)  # consultado de verdad


def test_available_engines_is_subset_and_never_crashes():
    available = available_engines()
    assert set(available) <= {"pix2text", "rapidocr", "pytesseract"}


# --------------------------------------------------------------------- #
# Recomendación según hardware
# --------------------------------------------------------------------- #
def test_recommend_without_engines_is_none(monkeypatch):
    monkeypatch.setattr(ocr_manager, "available_engines", lambda: [])
    assert recommend() is None


def test_recommend_prefers_math_engine_with_enough_ram(monkeypatch):
    monkeypatch.setattr(
        ocr_manager, "available_engines", lambda: ["pix2text", "rapidocr"]
    )
    hw = {"ram_available_mb": 8000}
    assert recommend(hw, prefer_math=True) == "pix2text"


def test_recommend_low_ram_falls_back_to_light_engine(monkeypatch):
    monkeypatch.setattr(
        ocr_manager, "available_engines", lambda: ["pix2text", "rapidocr"]
    )
    hw = {"ram_available_mb": 2000}
    assert recommend(hw, prefer_math=True) == "rapidocr"


def test_recommend_text_only_machine(monkeypatch):
    monkeypatch.setattr(ocr_manager, "available_engines", lambda: ["pytesseract"])
    assert recommend() == "pytesseract"


# --------------------------------------------------------------------- #
# Errores controlados
# --------------------------------------------------------------------- #
def test_transcribe_without_engines_raises_with_hint(monkeypatch):
    monkeypatch.setattr(ocr_manager, "available_engines", lambda: [])
    monkeypatch.setattr(ocr_manager, "recommend", lambda *a, **k: None)
    with pytest.raises(OCRNotAvailable, match="ningún motor"):
        ocr_manager.transcribe("no-sirve.png")


def test_transcribe_unknown_engine_rejected(monkeypatch):
    with pytest.raises(OCRNotAvailable, match="desconocido"):
        ocr_manager.transcribe("no-sirve.png", engine="motor-inexistente")


def test_transcribe_not_installed_engine_raises_with_install_hint(monkeypatch):
    monkeypatch.setattr(
        ocr_manager,
        "_importable",
        lambda module: False,
    )
    monkeypatch.setattr(ocr_manager, "available_engines", lambda: ["pytesseract"])
    with pytest.raises(OCRNotAvailable, match="Instálalo"):
        ocr_manager.transcribe("no-sirve.png", engine="pytesseract")


# --------------------------------------------------------------------- #
# Normalización de salidas
# --------------------------------------------------------------------- #
def test_coerce_text_accepts_string_and_dict():
    assert _coerce_text("texto", "rapidocr") == "texto"
    assert _coerce_text({"text": "md"}, "pix2text") == "md"
    assert _coerce_text({"md": "doc"}, "pix2text") == "doc"
    with pytest.raises(OCREngineError):
        _coerce_text(12345, "rapidocr")


# --------------------------------------------------------------------- #
# Motores reales (opcionales: solo si están instalados)
# --------------------------------------------------------------------- #
def test_rapidocr_real_if_installed(tmp_path):
    pytest.importorskip("rapidocr_onnxruntime")
    # Imagen blanca: RapidOCR no reconocerá texto pero no debe fallar.
    from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPixmap

    pixmap = QPixmap(240, 120)
    pixmap.fill(QColor("#ffffff"))
    path = tmp_path / "blank.png"
    assert pixmap.save(str(path), "PNG")
    try:
        result = ocr_manager.transcribe(str(path), engine="rapidocr")
        assert result.engine == "rapidocr"
        assert isinstance(result.text, str)
        assert result.markdown is False
    finally:
        ocr_manager.release_engine()


def test_pix2text_real_if_installed(tmp_path):
    pytest.importorskip("pix2text")
    from PySide6.QtGui import QColor, QPixmap

    pixmap = QPixmap(240, 120)
    pixmap.fill(QColor("#ffffff"))
    path = tmp_path / "blank.png"
    assert pixmap.save(str(path), "PNG")
    try:
        result = ocr_manager.transcribe(str(path), engine="pix2text")
        assert result.engine == "pix2text"
        assert result.markdown is True
    finally:
        ocr_manager.release_engine()

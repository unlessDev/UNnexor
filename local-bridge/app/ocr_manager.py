"""Gestor de OCR local: registro de motores, disponibilidad, recomendación y uso.

Los motores son OPCIONALES: Extdator no los instala por defecto (peso y
dependencias solo se incorporan si el usuario los pide). Cada entrada del
registro incluye licencia, propósito, peso de los modelos y orden de
instalación — datos verificados contra los repositorios oficiales
(consulta 2026-11-16; ver docs/OCR.md para la comparativa completa).

Diseño:
  * ``available_engines()`` comprueba importabilidad REAL (lazy import);
    nunca lista un motor que no puede ejecutarse.
  * ``recommend()`` sugiere un motor según el hardware detectado.
  * ``transcribe()`` instancia el motor bajo demanda y cachea la instancia
    (el OCR es lento de arrancar); ``release_engine()`` libera memoria.
  * No descarga pesos automáticamente: si el motor necesita descargarlos,
    es la propia librería la que lo hace al primer uso (se documenta).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

log = logging.getLogger("extdator.ocr")


# --------------------------------------------------------------------- #
# Registro de motores (fuente: repositorios oficiales, verificado 2026-11-16)
# --------------------------------------------------------------------- #
OCR_ENGINES: Dict[str, Dict[str, Any]] = {
    "rapidocr": {
        "label": "RapidOCR",
        "license": "Apache-2.0",
        "purpose": "texto (alta calidad, PP-OCRv6)",
        "math": False,
        "output": "texto plano",
        "install": "pip install rapidocr-onnxruntime",
        "weights": "modelos PP-OCR incluidos (~decenas de MB)",
        "tier": "bajo consumo",
        "offline": True,
        "source": "https://github.com/RapidAI/RapidOCR — v3.10.0 (2026-10)",
    },
    "pix2text": {
        "label": "Pix2Text",
        "license": "MIT (código y pesos MFR)",
        "purpose": "texto + fórmulas matemáticas",
        "math": True,
        "output": "Markdown con LaTeX ($$...$$)",
        "install": "pip install pix2text",
        "weights": "MFR ~120 MB ONNX (descarga al primer uso)",
        "tier": "consumo medio",
        "offline": True,
        "source": "https://github.com/breezedeus/Pix2Text — v1.1.7 (2026-08)",
    },
    "pytesseract": {
        "label": "Tesseract (pytesseract)",
        "license": "Apache-2.0",
        "purpose": "texto (requiere binario externo del sistema)",
        "math": False,
        "output": "texto plano",
        "install": "pip install pytesseract + instalar Tesseract en el sistema",
        "weights": "datos de idioma (~decenas de MB por idioma)",
        "tier": "bajo consumo",
        "offline": True,
        "source": "https://github.com/tesseract-ocr/tesseract — estable",
    },
}

# Motores evaluados y descartados como integración directa (documentados en
# docs/OCR.md): Texify (deprecado oficialmente → Surya), Surya/Marker
# (pesos OpenRAIL-M: restricciones de redistribución comercial), Nougat
# (pesos CC-BY-NC y proyecto parado desde 2023), pix2tex/LaTeX-OCR
# (solo fórmulas, poco mantenido), Docling (MIT pero pesado en CPU),
# PaddleOCR (framework Paddle propio, grande para un motor opcional).

_ENGINE_IMPORT = {
    "rapidocr": "rapidocr_onnxruntime",
    "pix2text": "pix2text",
    "pytesseract": "pytesseract",
}


@dataclass
class OCRResult:
    """Salida de una transcripción."""

    text: str
    engine: str
    engine_label: str = ""
    duration_s: float = 0.0
    markdown: bool = False  # True si el motor produce Markdown+LaTeX
    extra: Dict[str, Any] = field(default_factory=dict)


class OCREngineError(RuntimeError):
    """El motor existe pero falló durante la transcripción."""


class OCRNotAvailable(RuntimeError):
    """El motor no está instalado (con indicación de cómo instalarlo)."""


# --------------------------------------------------------------------- #
# Disponibilidad y recomendación
# --------------------------------------------------------------------- #
def _importable(module: str) -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError, ModuleNotFoundError):
        return False


def available_engines() -> List[str]:
    """Motores realmente importables ahora mismo (orden de preferencia)."""
    return [
        name
        for name in ("pix2text", "rapidocr", "pytesseract")
        if _importable(_ENGINE_IMPORT[name])
    ]


def engine_info(name: str) -> Dict[str, Any]:
    info = dict(OCR_ENGINES[name])
    info["installed"] = _importable(_ENGINE_IMPORT[name])
    return info


def catalog() -> List[Dict[str, Any]]:
    """Todo el registro, para el diálogo de ajustes/docs."""
    return [{"name": name, **engine_info(name)} for name in OCR_ENGINES]


def recommend(
    hw: Optional[Dict[str, Any]] = None, prefer_math: bool = True
) -> Optional[str]:
    """Sugiere un motor según el hardware y si se necesita matemática.

    Regla conservadora (sin datos inventados): Pix2Text es el único motor
    registrado que produce LaTeX; si hay ≥6 GB de RAM disponible se
    recomienda para matemáticas; en equipos más modestos, RapidOCR
    (texto) y se indica que las fórmulas quedarán como texto plano.
    """
    available = available_engines()
    if not available:
        return None
    if prefer_math and "pix2text" in available:
        ram_available_gb = ((hw or {}).get("ram_available_mb") or 0) / 1024
        if ram_available_gb <= 0 or ram_available_gb >= 4.0:
            return "pix2text"
    if "rapidocr" in available:
        return "rapidocr"
    return available[0]


# --------------------------------------------------------------------- #
# Instancias cacheadas (lazy) y liberación
# --------------------------------------------------------------------- #
_instances: Dict[str, Any] = {}
_lock = threading.Lock()


def release_engine(name: Optional[str] = None) -> None:
    """Libera la instancia cacheada (memoria) de uno o todos los motores."""
    with _lock:
        if name is None:
            _instances.clear()
        else:
            _instances.pop(name, None)


def _get_engine(name: str) -> Any:
    with _lock:
        if name in _instances:
            return _instances[name]
    if not _importable(_ENGINE_IMPORT[name]):
        raise OCRNotAvailable(
            f"El motor '{OCR_ENGINES[name]['label']}' no está instalado. "
            f"Instálalo con: {OCR_ENGINES[name]['install']}"
        )
    instance = _create_engine(name)
    with _lock:
        _instances[name] = instance
    return instance


def _create_engine(name: str) -> Any:
    """Crea la instancia del motor (import lazy real)."""
    if name == "rapidocr":
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as exc:  # paquete renombrado en el futuro
            try:
                from rapidocr import RapidOCR
            except ImportError:
                raise OCREngineError(
                    "No se pudo inicializar RapidOCR (reinstala "
                    "rapidocr-onnxruntime)."
                ) from exc
        return RapidOCR()
    if name == "pix2text":
        try:
            from pix2text import Pix2Text
        except ImportError as exc:
            raise OCREngineError(
                "No se pudo inicializar Pix2Text (reinstala pix2text)."
            ) from exc
        return Pix2Text()
    if name == "pytesseract":
        import pytesseract

        # Comprueba que el binario externo existe (no lo instala).
        pytesseract.get_tesseract_version()
        return pytesseract
    raise OCRNotAvailable(f"Motor desconocido: '{name}'.")


# --------------------------------------------------------------------- #
# Transcripción
# --------------------------------------------------------------------- #
def _coerce_text(result: Any, name: str) -> str:
    """Normaliza la salida de cada motor a texto/Markdown."""
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        # Pix2Text 1.x devuelve dict con claves 'text' / 'html' según config.
        for key in ("text", "md", "markdown", "html"):
            if isinstance(result.get(key), str):
                return result[key]
    if name == "pytesseract":
        return str(result)
    raise OCREngineError(
        f"Salida no reconocida del motor '{name}': {type(result).__name__}."
    )


def transcribe(
    image_path: str,
    engine: Optional[str] = None,
    lang: str = "es",
    hw: Optional[Dict[str, Any]] = None,
) -> OCRResult:
    """Transcribe una imagen a texto/Markdown con el motor elegido.

    ``engine=None`` usa el motor recomendado disponible. El llamador es
    responsable de borrar la imagen temporal cuando termine (privacidad).
    """
    import time

    chosen = engine or recommend(hw) or ""
    if not chosen:
        raise OCRNotAvailable(
            "No hay ningún motor OCR instalado. Consulta 'Ajustes → Captura "
            "y OCR' para instalar uno (p. ej. pip install rapidocr-onnxruntime)."
        )
    if chosen not in OCR_ENGINES:
        raise OCRNotAvailable(f"Motor desconocido: '{chosen}'.")

    started = time.perf_counter()
    instance = _get_engine(chosen)
    try:
        if chosen == "rapidocr":
            raw = instance(image_path)
            # RapidOCR: ([box, text, score], ...) o dict según versión.
            lines: List[str] = []
            if isinstance(raw, dict):
                raw = raw.get("results") or raw.get("rec_texts") or []
            for entry in raw or []:
                if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                    lines.append(str(entry[1]))
                elif isinstance(entry, str):
                    lines.append(entry)
            text = "\n".join(lines)
        elif chosen == "pix2text":
            raw = instance.recognize(image_path)
            text = _coerce_text(raw, chosen)
        elif chosen == "pytesseract":
            from PIL import Image

            text = instance.image_to_string(
                Image.open(image_path), lang=lang or "eng"
            ).strip()
        else:
            raise OCRNotAvailable(f"Motor desconocido: '{chosen}'.")
    except OCRNotAvailable:
        raise
    except Exception as exc:
        log.exception("El motor '%s' falló durante la transcripción", chosen)
        raise OCREngineError(
            f"El OCR falló con {OCR_ENGINES[chosen]['label']}: {exc}"
        ) from exc

    duration = round(time.perf_counter() - started, 2)
    return OCRResult(
        text=text,
        engine=chosen,
        engine_label=OCR_ENGINES[chosen]["label"],
        duration_s=duration,
        markdown=bool(OCR_ENGINES[chosen]["math"]),
    )

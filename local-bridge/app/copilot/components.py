"""Comprobación de los componentes nativos del copiloto.

Se usa en el auto-chequeo (``--check``) del ejecutable para demostrar que
el empaquetado incluye todo lo necesario (NumPy, SoundCard y sus cabeceras
cffi, faster-whisper con el modelo Silero, CTranslate2, ONNX Runtime,
PyAV, pypdf). No carga modelos ni abre dispositivos de audio.
"""

from __future__ import annotations

import importlib
from typing import Any

COMPONENTS = (
    "numpy",
    "soundcard",
    "faster_whisper",
    "ctranslate2",
    "onnxruntime",
    "av",
    "pypdf",
)


def check_components() -> dict[str, Any]:
    report: dict[str, Any] = {}
    for name in COMPONENTS:
        try:
            module = importlib.import_module(name)
            report[name] = {
                "ok": True,
                "version": str(getattr(module, "__version__", "")),
            }
        except Exception as exc:  # ImportError o fallo nativo (DLL, cabeceras)
            report[name] = {
                "ok": False,
                "error": f"{exc.__class__.__name__}: {exc}"[:300],
            }
    try:
        import numpy as np

        from ..audio.vad import SileroVAD, silero_model_path

        path = silero_model_path()
        # Una trama real por ONNX Runtime: valida sus DLL, no solo el import.
        probability = SileroVAD(path).speech_probability(
            np.zeros(512, dtype=np.float32)
        )
        report["silero_vad"] = {
            "ok": path is not None and 0.0 <= probability <= 1.0,
            "path": str(path or ""),
        }
    except Exception as exc:
        report["silero_vad"] = {"ok": False, "error": str(exc)[:300]}
    try:
        import ctranslate2

        types = sorted(ctranslate2.get_supported_compute_types("cpu"))
        report["ctranslate2_cpu"] = {"ok": "int8" in types, "compute_types": types}
    except Exception as exc:
        report["ctranslate2_cpu"] = {"ok": False, "error": str(exc)[:300]}
    report["whisper_load"] = _whisper_smoke()
    report["ok"] = all(
        v.get("ok")
        for v in report.values()
        if isinstance(v, dict) and not v.get("skipped")
    )
    return report


def _whisper_smoke() -> dict[str, Any]:
    """Carga el modelo local más pequeño instalado y transcribe 1 s de
    silencio. Se omite si no hay modelos (no se descarga nada)."""
    try:
        import numpy as np

        from ..stt import faster_whisper_engine as fw

        order = ("tiny", "base", "small", "medium", "large-v3-turbo", "large-v3")
        installed = [m for m in order if fw.is_installed(m)]
        if not installed:
            return {"ok": True, "skipped": True, "reason": "sin modelos descargados"}
        recognizer = fw.FasterWhisperRecognizer(installed[0], beam_size=1)
        try:
            result = recognizer.transcribe(
                np.zeros(16_000, dtype=np.float32), language="es"
            )
        finally:
            recognizer.close()
        return {
            "ok": True,
            "model": installed[0],
            "device": recognizer.device,
            "load_s": recognizer.load_seconds,
            "transcribe_s": result.elapsed_seconds,
        }
    except Exception as exc:
        return {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"[:300]}

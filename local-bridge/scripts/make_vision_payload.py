"""Genera el payload JSON para el E2E de visión (imagen matemática)."""

import base64
import importlib.util
import json
import os
import tempfile
from pathlib import Path

# pix2text instala su propio paquete ``scripts`` en site-packages, que
# tapa al directorio local: se carga ``ocr_e2e.py`` por ruta de archivo.
_spec = importlib.util.spec_from_file_location(
    "ocr_e2e", Path(__file__).resolve().parent / "ocr_e2e.py"
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
render_math = _mod.render_math

path = os.path.join(tempfile.gettempdir(), "extdator_vision_math.png")
render_math(["x^2 + y^2 = 25", "x + y = 7"], path)
with open(path, "rb") as handle:
    b64 = base64.b64encode(handle.read()).decode()
payload = {
    "image_b64": b64,
    "instruction": (
        "La imagen contiene dos ecuaciones. Resuelve el sistema paso a paso "
        "en español."
    ),
    "source_url": "screenshot://e2e-vision",
}
out = os.path.join(tempfile.gettempdir(), "extdator_vision_payload.json")
with open(out, "w", encoding="utf-8") as fh:
    json.dump(payload, fh)
print("imagen bytes:", os.path.getsize(path))
print("payload:", out)
print("b64 chars:", len(b64))

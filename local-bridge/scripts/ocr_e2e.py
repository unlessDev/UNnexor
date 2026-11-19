"""E2E real del OCR: imagen con texto matemático → Markdown/LaTeX.

Genera una imagen limpia (texto renderizado con la fuente TTF real del
sistema vía PIL — el plugin offscreen de Qt no resuelve fuentes), la
transcribe con el motor elegido y libera la instancia (prioridad RAM).
Uso:

    .venv/Scripts/python.exe scripts/ocr_e2e.py [motor]

Sin argumento usa el motor recomendado (pix2text si está instalado).
La primera ejecución descarga los pesos del motor (se documenta en
docs/OCR.md); después corre offline.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def render_math(lines: list[str], out_path: str) -> None:
    """Imagen blanca con texto matemático renderizado (fácil de leer).

    Usa PIL + la fuente TTF real de Windows: el plugin offscreen de Qt
    no carga la base de fuentes del sistema y dibuja cajas (□).
    """
    from PIL import Image, ImageDraw, ImageFont

    font_path = r"C:\Windows\Fonts\arial.ttf"
    font = ImageFont.truetype(font_path, 56)
    img = Image.new("RGB", (980, 120 * len(lines) + 60), "white")
    draw = ImageDraw.Draw(img)
    y = 40
    for line in lines:
        draw.text((50, y), line, font=font, fill="black")
        y += 120
    img.save(out_path, "PNG")


from app.ocr_manager import (  # noqa: E402
    available_engines,
    recommend,
    release_engine,
    transcribe,
)


def main() -> int:
    engine = sys.argv[1] if len(sys.argv) > 1 else None
    print("Motores disponibles:", available_engines())
    chosen = engine or recommend({"ram_available_mb": 8600})
    print("Motor elegido:", chosen)

    path = os.path.join(tempfile.gettempdir(), "extdator_ocr_e2e.png")
    render_math(
        ["x^2 + y^2 = 25", "x + y = 7", "E = mc^2"],
        path,
    )
    print(f"Imagen de prueba: {path} ({os.path.getsize(path)} bytes)")

    started = time.perf_counter()
    result = transcribe(path, engine=chosen, hw={"ram_available_mb": 8600})
    print(
        f"Transcrito en {time.perf_counter() - started:.1f}s "
        f"(motor={result.engine}, markdown={result.markdown})"
    )
    print("--- salida ---")
    print(result.text)
    print("--- fin salida ---")

    release_engine(chosen)
    os.remove(path)
    print("Instancia liberada y temporal borrada:", not os.path.exists(path))
    ok = bool(result.text.strip())
    print("RESULTADO:", "OK" if ok else "VACÍO")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

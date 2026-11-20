"""Genera el icono .ico del ejecutable a partir de los PNG de la extensión.

Uso:
    .venv\\Scripts\\python.exe deployment\\windows\\make_icon.py

Requiere Pillow (ver local-bridge/requirements-build.txt). El .ico resultante
se versiona para que el build sea reproducible sin depender de este script.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE = PROJECT_ROOT / "browser-extension" / "assets" / "icon128.png"
TARGET = PROJECT_ROOT / "local-bridge" / "assets" / "extdator.ico"
SIZES = [(16, 16), (32, 32), (48, 48), (128, 128)]


def main() -> int:
    if not SOURCE.is_file():
        print(f"No existe el icono de origen: {SOURCE}")
        return 1
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(SOURCE) as image:
        image = image.convert("RGBA")
        image.save(TARGET, format="ICO", sizes=SIZES)
    print(f"Icono generado: {TARGET} ({TARGET.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

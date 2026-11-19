"""Genera los iconos PNG de la extensión sin dependencias externas.

Dibuja un mosaico redondeado con degradado índigo y una estrella de 4 puntas
(sparkle de IA), con antialiasing por supermuestreo. Escribe PNG RGBA 8-bit
a mano (IHDR/IDAT/IEND + zlib).

Uso:  python scripts/make_icons.py
"""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "browser-extension" / "assets"
SIZES = [16, 32, 48, 128]

BG_TOP = (110, 96, 252)
BG_BOTTOM = (146, 128, 255)
STAR = (255, 255, 255)
P = 0.55          # exponente de la superelipse (menor = puntas más finas)
STAR_R = 0.80     # radio de la estrella
CORNER = 0.20     # radio de esquina del mosaico (fracción del semi-lado)
SS = 4            # supermuestreo por eje


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data)) + tag + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def write_png(path: Path, size: int, rows: list[bytearray]) -> None:
    raw = b"".join(b"\x00" + bytes(row) for row in rows)
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(raw, 9))
        + _chunk(b"IEND", b"")
    )


def inside_tile(nx: float, ny: float) -> bool:
    """Mosaico redondeado en coords normalizadas [-1, 1]."""
    ax, ay = abs(nx), abs(ny)
    r = CORNER
    if ax > 1 - r and ay > 1 - r:
        dx, dy = ax - (1 - r), ay - (1 - r)
        if dx * dx + dy * dy > r * r:
            return False
    return True


def inside_star(nx: float, ny: float) -> tuple[bool, float]:
    """Estrella de 4 puntas; devuelve (dentro, cobertura en el borde)."""
    value = abs(nx) ** P + abs(ny) ** P
    if value <= STAR_R - 0.08:
        return True, 1.0
    if value <= STAR_R:
        return True, (STAR_R - value) / 0.08
    return False, 0.0


def render(size: int) -> list[bytearray]:
    rows = [bytearray() for _ in range(size)]
    for py in range(size):
        row = bytearray()
        for px in range(size):
            star_cov = 0.0
            tile_cov = 0.0
            bg_r = bg_g = bg_b = 0.0
            for sy in range(SS):
                for sx in range(SS):
                    x = (px + (sx + 0.5) / SS) / size * 2 - 1
                    y = (py + (sy + 0.5) / SS) / size * 2 - 1
                    if not inside_tile(x, y):
                        continue
                    tile_cov += 1.0
                    t = (y + 1) / 2
                    bg_r += BG_TOP[0] + (BG_BOTTOM[0] - BG_TOP[0]) * t
                    bg_g += BG_TOP[1] + (BG_BOTTOM[1] - BG_TOP[1]) * t
                    bg_b += BG_TOP[2] + (BG_BOTTOM[2] - BG_TOP[2]) * t
                    star, coverage = inside_star(x, y)
                    if star:
                        star_cov += coverage
            if tile_cov == 0:
                row += bytes((0, 0, 0, 0))
                continue
            tile_cov /= SS * SS
            bg_r /= tile_cov * SS * SS
            bg_g /= tile_cov * SS * SS
            bg_b /= tile_cov * SS * SS
            star_cov /= SS * SS
            r = bg_r + (STAR[0] - bg_r) * star_cov
            g = bg_g + (STAR[1] - bg_g) * star_cov
            b = bg_b + (STAR[2] - bg_b) * star_cov
            row += bytes((
                int(min(255, max(0, r))),
                int(min(255, max(0, g))),
                int(min(255, max(0, b))),
                int(255 * tile_cov),
            ))
        rows[py] = row
    return rows


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for size in SIZES:
        path = OUT_DIR / f"icon{size}.png"
        write_png(path, size, render(size))
        print(f"[icons] {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

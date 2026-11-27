"""Presentación de UNnexor AI Bridge en las terminales de arranque.

Una única implementación para todos los puntos de entrada: ``run.py`` (la
consola del puente), ``scripts/start.ps1`` (que ejecuta
``python -m app.banner``) y la consola de registros integrada en la
aplicación (que muestra la versión sin colores).

Colores ANSI de 24 bits con degradado cian → azul → violeta → magenta (los de
la X). En Windows se activa el procesamiento de secuencias VT de la consola;
si no se puede (consolas antiguas, salida redirigida) o si ``NO_COLOR`` está
definida, se imprime la misma composición sin colores.
"""

from __future__ import annotations

import os
import sys
from typing import TextIO

from . import __version__

# "UNnexor": UN en bloque macizo (UnlessDev) y "nexor" en trazo de contorno,
# como en el logotipo. Cada letra mide 6 filas.
_LETTERS = {
    "U": [
        "██╗   ██╗",
        "██║   ██║",
        "██║   ██║",
        "██║   ██║",
        "╚██████╔╝",
        " ╚═════╝ ",
    ],
    "N": [
        "███╗   ██╗",
        "████╗  ██║",
        "██╔██╗ ██║",
        "██║╚██╗██║",
        "██║ ╚████║",
        "╚═╝  ╚═══╝",
    ],
    "n": [
        "╔╗  ╔╗",
        "║╚╗ ║║",
        "║╔╗╚╝║",
        "║║╚╗ ║",
        "║║ ╚╗║",
        "╚╝  ╚╝",
    ],
    "e": [
        "╔════╗",
        "║╔═══╝",
        "║╚══╗ ",
        "║╔══╝ ",
        "║╚═══╗",
        "╚════╝",
    ],
    "x": [
        "██╗  ██╗",
        "╚██╗██╔╝",
        " ╚███╔╝ ",
        " ██╔██╗ ",
        "██╔╝ ██╗",
        "╚═╝  ╚═╝",
    ],
    "o": [
        "╔════╗",
        "║╔══╗║",
        "║║  ║║",
        "║║  ║║",
        "║╚══╝║",
        "╚════╝",
    ],
    "r": [
        "╔════╗",
        "║╔══╗║",
        "║╚══╝║",
        "║╔╗╔═╝",
        "║║╚╗╚╗",
        "╚╝ ╚═╝",
    ],
}
_WORD = "UNnexor"
_SUBTITLE = "A I   B R I D G E"
_BYLINE = "by UnlessDev"

# Degradado de la X: cian → azul → violeta → magenta.
_STOPS = [
    (0.00, (0, 229, 255)),
    (0.35, (30, 80, 255)),
    (0.65, (122, 20, 255)),
    (1.00, (242, 5, 252)),
]
_WHITE = (232, 234, 242)
_DIM = (125, 131, 154)
_RESET = "\x1b[0m"


def _lerp(t: float) -> tuple[int, int, int]:
    t = min(1.0, max(0.0, t))
    for (t0, c0), (t1, c1) in zip(_STOPS, _STOPS[1:], strict=False):
        if t <= t1:
            k = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
            return (
                round(c0[0] + (c1[0] - c0[0]) * k),
                round(c0[1] + (c1[1] - c0[1]) * k),
                round(c0[2] + (c1[2] - c0[2]) * k),
            )
    return _STOPS[-1][1]


def _fg(rgb: tuple[int, int, int]) -> str:
    return f"\x1b[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m"


def word_rows() -> list[str]:
    rows = []
    for i in range(6):
        rows.append(" ".join(_LETTERS[ch][i] for ch in _WORD))
    return rows


def render(color: bool, width: int | None = None) -> str:
    """Composición completa; ``color`` decide si se emiten secuencias ANSI."""
    rows = word_rows()
    art_width = max(len(r) for r in rows)
    # "UN" ocupa las dos primeras letras: se pinta en blanco, como en el logo.
    un_width = len(_LETTERS["U"][0]) + 1 + len(_LETTERS["N"][0])
    total = max(art_width, width or 0)
    pad = " " * max(0, (total - art_width) // 2)

    lines: list[str] = [""]
    for row in rows:
        if not color:
            lines.append(pad + row)
            continue
        out = [pad, _fg(_WHITE), row[:un_width], _RESET]
        tail = row[un_width:]
        span = max(1, len(tail) - 1)
        for i, ch in enumerate(tail):
            out.append(_fg(_lerp(i / span)) + ch if ch != " " else ch)
        out.append(_RESET)
        lines.append("".join(out))

    rule = "━" * 12
    sub = f"{rule}   {_SUBTITLE}   {rule}"
    sub_pad = " " * max(0, (art_width - len(sub)) // 2)
    by = f"{_BYLINE}  ·  v{__version__}"
    by_pad = " " * max(0, (art_width - len(by)) // 2)
    if color:
        rule_l = _fg(_STOPS[0][1]) + rule + _RESET
        rule_r = _fg(_STOPS[-1][1]) + rule + _RESET
        letters = []
        span = max(1, len(_SUBTITLE) - 1)
        for i, ch in enumerate(_SUBTITLE):
            letters.append(_fg(_lerp(i / span)) + ch if ch != " " else ch)
        sub_line = f"{rule_l}   {''.join(letters)}{_RESET}   {rule_r}"
        lines.append("")
        lines.append(pad + sub_pad + sub_line)
        lines.append(pad + by_pad + _fg(_DIM) + by + _RESET)
    else:
        lines.append("")
        lines.append(pad + sub_pad + sub)
        lines.append(pad + by_pad + by)
    lines.append("")
    return "\n".join(lines)


def enable_ansi(stream: TextIO) -> bool:
    """¿Se pueden usar colores en ``stream``? Activa VT en Windows si hace falta."""
    if os.environ.get("NO_COLOR"):
        return False
    try:
        if not stream.isatty():
            return False
    except (AttributeError, ValueError):
        return False
    if sys.platform != "win32":
        return os.environ.get("TERM", "") != "dumb"
    try:
        import ctypes
        import ctypes.wintypes as wt

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = wt.DWORD()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        enable_vt = 0x0004  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        if mode.value & enable_vt:
            return True
        return bool(kernel32.SetConsoleMode(handle, mode.value | enable_vt))
    except Exception:
        return False


def print_banner(stream: TextIO | None = None, title: bool = True) -> None:
    """Imprime la presentación una sola vez por proceso."""
    global _printed
    if _printed:
        return
    out = stream or sys.stdout
    if out is None:  # ejecutable sin consola
        return
    _printed = True
    color = enable_ansi(out)
    try:
        if title and sys.platform == "win32":
            import ctypes

            ctypes.windll.kernel32.SetConsoleTitleW("UNnexor AI Bridge")
    except Exception:
        pass
    try:
        out.write(render(color) + "\n")
        out.flush()
    except UnicodeEncodeError:
        # Consola con página de códigos sin los caracteres de caja.
        out.write(f"\n  UNnexor · AI BRIDGE · by UnlessDev · v{__version__}\n\n")
        out.flush()


_printed = False


def status(kind: str, message: str, stream: TextIO | None = None) -> None:
    """Línea de estado breve y coherente: ``[ ok ] mensaje``."""
    out = stream or sys.stdout
    if out is None:
        return
    tags = {
        "ok": ("  ok  ", (61, 220, 151)),
        "info": (" info ", (0, 229, 255)),
        "warn": (" aviso", (255, 196, 0)),
        "error": ("error ", (255, 107, 107)),
    }
    tag, rgb = tags.get(kind, tags["info"])
    if enable_ansi(out):
        out.write(f"{_fg(rgb)}[{tag}]{_RESET} {message}\n")
    else:
        out.write(f"[{tag}] {message}\n")
    out.flush()


def _main(argv: list[str]) -> int:
    if "--status" in argv:
        i = argv.index("--status")
        kind = argv[i + 1] if len(argv) > i + 1 else "info"
        message = " ".join(argv[i + 2 :])
        status(kind, message)
        return 0
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except Exception:
            pass
    print_banner()
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))

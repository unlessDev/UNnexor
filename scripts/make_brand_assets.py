"""Genera la identidad gráfica de UNnexor a partir de una única geometría.

La X de UNnexor se construye aquí desde cero, con trazados vectoriales, a
partir de la imagen de referencia ``branding/source/unnexor-logo-reference.png``
(antes ``newDesign/``). Las rectas se midieron sobre esa imagen (barrido por
filas de la silueta y muestreo de color); todas las coordenadas de abajo
están en píxeles de la referencia (1672×941) para que cualquiera pueda
volver a comprobarlas.

Estructura de la X (fiel a la referencia):

* **Trazo "/"**: marco hueco con remate superior, barra inferior, banda
  derecha (cian → azul → violeta) y una ranura diagonal estrecha con
  suelo en sombra sobre la barra inferior. Su banda izquierda tiene dos partes: la
  superior (azul brillante, se apaga hacia abajo como una cara en sombra) y,
  en la mitad inferior, una banda magenta exterior.
* **Trazo "\\"**: dos brazos huecos en forma de "U" (superior izquierdo e
  inferior derecho) que pasan *por debajo* del "/": quedan cortados por
  líneas paralelas al "/" con una separación negra de ~12 px. El hueco del
  brazo superior tiene el chaflán característico de la referencia.
* El brazo superior izquierdo y la banda magenta forman el cruce en "<"
  del lado izquierdo.

Única diferencia buscada respecto de la referencia: **sin destellos** en
las esquinas (ni brillos ni filtros). Solo polígonos y degradados lineales,
compatibles con navegadores, Qt (QtSvg) y cualquier renderizador SVG 1.1.

Salidas (todas derivan de la misma geometría):

* ``branding/unnexor-x.svg``        SVG maestro (fondo transparente).
* ``branding/unnexor-icon.svg``     X centrada en lienzo cuadrado.
* ``branding/unnexor-badge.svg``    X dentro de un contenedor redondeado
                                    (botón flotante, iconos pequeños).
* ``branding/png/*.png``            rasterizados en varias resoluciones.
* ``local-bridge/assets/unnexor.ico`` / ``unnexor-x.svg`` / ``unnexor.png``
* ``browser-extension/assets/icon{16,32,48,128}.png``
* Bloque ``UNNEXOR_X_SVG`` dentro de ``browser-extension/content.js``.

Uso (requiere PySide6 y Pillow del entorno de build)::

    local-bridge\\.venv\\Scripts\\python.exe scripts\\make_brand_assets.py
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRANDING = ROOT / "branding"
PNG_DIR = BRANDING / "png"
BRIDGE_ASSETS = ROOT / "local-bridge" / "assets"
EXT_ASSETS = ROOT / "browser-extension" / "assets"
CONTENT_JS = ROOT / "browser-extension" / "content.js"

# ---------------------------------------------------------------- geometría
K = 1.06  # pendiente de las rectas "/" medida en la referencia (dx/dy)


@dataclass(frozen=True)
class Line:
    """Recta x = c + s·y (las diagonales del logo se expresan mejor así)."""

    c: float
    s: float

    def x(self, y: float) -> float:
        return self.c + self.s * y

    def shifted(self, dx: float) -> Line:
        return Line(self.c + dx, self.s)


def slash(x0: float, y0: float) -> Line:
    """Recta paralela al trazo "/" que pasa por (x0, y0)."""
    return Line(x0 + K * y0, -K)


def back(x0: float, y0: float, slope: float = 1.0) -> Line:
    """Recta paralela al trazo "\\" que pasa por (x0, y0)."""
    return Line(x0 - slope * y0, slope)


def meet(a: Line, b: Line) -> tuple[float, float]:
    y = (b.c - a.c) / (a.s - b.s)
    return a.x(y), y


def at(line: Line, y: float) -> tuple[float, float]:
    return line.x(y), y


# Bordes medidos (píxeles de la referencia).
Y_TOP = 133.0  # borde superior de ambos trazos
Y_BOTTOM = 489.0  # borde inferior de ambos trazos
Y_UL_HOLE = 179.0  # techo del hueco del brazo superior izquierdo
Y_SLOT = 181.0  # techo de la ranura del "/"
Y_FLOOR = 428.0  # inicio del suelo en sombra sobre la barra inferior
Y_BAR = 450.0  # borde superior iluminado de la barra inferior del "/"
Y_LR_HOLE = 450.0  # suelo del hueco del brazo inferior derecho

S1 = slash(663, 406)  # borde exterior de la banda magenta
S2 = slash(926, 198)  # borde izquierdo del "/" (y borde interior magenta)
S3 = slash(914, 270)  # borde izquierdo de la ranura
S4 = slash(946, 270)  # borde derecho de la ranura
S5 = slash(1062, 214)  # borde derecho del "/"
CUT_UL = S2.shifted(-13)  # corte del brazo superior (separación negra)
CUT_LR = S5.shifted(11)  # corte del brazo inferior

D1 = back(630, 182)  # brazo sup.: borde exterior izquierdo
D2 = back(731, 214)  # brazo sup.: borde izquierdo del hueco
D4 = back(785, 214)  # brazo sup.: borde derecho del hueco
D3 = back(846, 214)  # brazo sup.: borde exterior derecho
CUT_MAGENTA = D1.shifted(-12)  # corte superior de la banda magenta
D5 = back(925, 438)  # brazo inf.: borde exterior izquierdo
D6 = back(930, 382)  # brazo inf.: borde izquierdo del hueco
D8 = back(984, 382)  # brazo inf.: borde derecho del hueco
D7 = back(1067, 400)  # brazo inf.: borde exterior derecho

# Chaflán del hueco superior izquierdo: de (651, 179) hacia D2.
UL_CHAMFER = Line(651 - 3.3 * Y_UL_HOLE, 3.3)

Point = tuple[float, float]


def shapes() -> dict[str, list[Point]]:
    upper_left = [
        at(D1, Y_TOP),
        at(D3, Y_TOP),
        meet(D3, CUT_UL),
        meet(D4, CUT_UL),
        at(D4, Y_UL_HOLE),
        at(UL_CHAMFER, Y_UL_HOLE),
        meet(UL_CHAMFER, D2),
        meet(D2, CUT_UL),
        meet(D1, CUT_UL),
    ]
    lower_right = [
        at(D5, Y_BOTTOM),
        at(D7, Y_BOTTOM),
        meet(D7, CUT_LR),
        meet(D8, CUT_LR),
        at(D8, Y_LR_HOLE),
        at(D6, Y_LR_HOLE),
        meet(D6, CUT_LR),
        meet(D5, CUT_LR),
    ]
    slash_frame = [
        at(S3, Y_TOP),
        at(S5, Y_TOP),
        at(S5, Y_BOTTOM),
        at(S1, Y_BOTTOM),
        meet(S1, CUT_MAGENTA),
        meet(S2, CUT_MAGENTA),
        at(S2, Y_BAR),
        at(S4, Y_BAR),
        at(S4, Y_SLOT),
        at(S3, Y_SLOT),
    ]
    # La banda interior y el suelo se dibujan ANTES del marco y se solapan
    # 1,5 px bajo él: así el antialiasing de los bordes compartidos no deja
    # ver el fondo (costura fina) en ningún tamaño.
    under = S3.shifted(1.5)
    slash_inner = [
        at(S2, Y_TOP),
        at(under, Y_TOP),
        at(under, Y_FLOOR),
        at(S2, Y_FLOOR),
    ]
    slash_floor = [
        at(S2, Y_FLOOR),
        at(S4.shifted(1.5), Y_FLOOR),
        at(S4.shifted(1.5), Y_BAR + 1.5),
        at(S2, Y_BAR + 1.5),
    ]
    return {
        "lower-right": lower_right,
        "upper-left": upper_left,
        "slash-floor": slash_floor,
        "slash-inner": slash_inner,
        "slash": slash_frame,
    }


def bounds(polys: dict[str, list[Point]]) -> tuple[float, float, float, float]:
    xs = [x for poly in polys.values() for x, _ in poly]
    ys = [y for poly in polys.values() for _, y in poly]
    return min(xs), min(ys), max(xs), max(ys)


# ---------------------------------------------------------------- color
# Colores muestreados en la referencia (cian → azul → violeta → magenta).
GRADIENTS = {
    # id: (x1, y1, x2, y2, [(offset, color, opacity)])
    "ux-slash": (
        1150,
        135,
        575,
        490,
        [
            (0.00, "#00F4FF", 1),
            (0.18, "#00D6FF", 1),
            (0.40, "#1C4CF6", 1),
            (0.58, "#5A12F2", 1),
            (0.72, "#A000FA", 1),
            (0.88, "#F205FC", 1),
            (1.00, "#FF2CE8", 1),
        ],
    ),
    "ux-inner": (
        1000,
        140,
        760,
        420,
        [
            (0.00, "#00E0FF", 1),
            (0.30, "#00B8FA", 1),
            (0.58, "#0A3FC8", 1),
            (0.82, "#0A0A60", 0.6),
            (1.00, "#05001A", 0.15),
        ],
    ),
    "ux-floor": (
        640,
        430,
        800,
        450,
        [(0.00, "#7A00E6", 1), (0.55, "#4A00B8", 1), (1.00, "#2A0078", 1)],
    ),
    "ux-upper": (
        600,
        150,
        830,
        262,
        [
            (0.00, "#00CFFF", 1),
            (0.40, "#1F45FF", 1),
            (0.75, "#5A0BFF", 1),
            (1.00, "#8C00FF", 1),
        ],
    ),
    "ux-lower": (
        900,
        380,
        1150,
        470,
        [
            (0.00, "#1028D0", 1),
            (0.35, "#0A6BF5", 1),
            (0.70, "#00C8FD", 1),
            (1.00, "#00F6FF", 1),
        ],
    ),
}
FILL = {
    "lower-right": "ux-lower",
    "upper-left": "ux-upper",
    "slash-floor": "ux-floor",
    "slash-inner": "ux-inner",
    "slash": "ux-slash",
}

PAD = 10.0  # margen alrededor de la X en el SVG maestro (px de referencia)


def _fmt(value: float) -> str:
    text = f"{value:.1f}"
    return text[:-2] if text.endswith(".0") else text


def _path(points: list[Point], ox: float, oy: float) -> str:
    coords = " ".join(f"{_fmt(x - ox)},{_fmt(y - oy)}" for x, y in points)
    return f"M{coords}Z"


def x_markup(ox: float, oy: float, prefix: str = "ux") -> tuple[str, str]:
    """Devuelve (defs, paths) de la X desplazada por (ox, oy)."""
    defs = []
    for gid, (x1, y1, x2, y2, stops) in GRADIENTS.items():
        gid_p = gid.replace("ux", prefix, 1)
        stop_xml = "".join(
            f'<stop offset="{off:g}" stop-color="{color}"'
            + (f' stop-opacity="{op:g}"' if op != 1 else "")
            + "/>"
            for off, color, op in stops
        )
        defs.append(
            f'<linearGradient id="{gid_p}" gradientUnits="userSpaceOnUse" '
            f'x1="{_fmt(x1 - ox)}" y1="{_fmt(y1 - oy)}" '
            f'x2="{_fmt(x2 - ox)}" y2="{_fmt(y2 - oy)}">{stop_xml}</linearGradient>'
        )
    paths = []
    for name, points in shapes().items():
        gid = FILL[name].replace("ux", prefix, 1)
        paths.append(
            f'<path data-part="{name}" fill="url(#{gid})" d="{_path(points, ox, oy)}"/>'
        )
    return "".join(defs), "".join(paths)


def master_svg() -> str:
    x0, y0, x1, y1 = bounds(shapes())
    ox, oy = x0 - PAD, y0 - PAD
    width, height = x1 - x0 + 2 * PAD, y1 - y0 + 2 * PAD
    defs, paths = x_markup(ox, oy)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {_fmt(width)} {_fmt(height)}" '
        f'width="{_fmt(width)}" height="{_fmt(height)}" role="img" '
        'aria-label="UNnexor">\n'
        "  <title>UNnexor</title>\n"
        f"  <defs>{defs}</defs>\n"
        f"  {paths}\n"
        "</svg>\n"
    )


def _square_geometry(size: float, inset: float) -> tuple[float, float, float]:
    """Escala y desplazamiento para centrar la X en un cuadrado."""
    x0, y0, x1, y1 = bounds(shapes())
    avail = size - 2 * inset
    scale = avail / max(x1 - x0, y1 - y0)
    tx = (size - (x1 - x0) * scale) / 2
    ty = (size - (y1 - y0) * scale) / 2
    return scale, tx, ty


def icon_svg(badge: bool) -> str:
    """Variante cuadrada (256×256). ``badge`` añade el contenedor redondeado.

    El redondeado se aplica SOLO al contenedor; la X conserva sus aristas.
    """
    size = 256.0
    inset = 28.0 if badge else 8.0
    scale, tx, ty = _square_geometry(size, inset)
    x0, y0 = bounds(shapes())[:2]
    defs, paths = x_markup(x0, y0, prefix="ub" if badge else "ui")
    group = (
        f'<g transform="translate({_fmt(tx)} {_fmt(ty)}) scale({scale:.5f})">'
        f"{paths}</g>"
    )
    container = ""
    if badge:
        defs += (
            '<linearGradient id="ub-bg" x1="0" y1="0" x2="1" y2="1">'
            '<stop offset="0" stop-color="#161A2E"/>'
            '<stop offset="1" stop-color="#07080F"/></linearGradient>'
            '<linearGradient id="ub-rim" x1="1" y1="0" x2="0" y2="1">'
            '<stop offset="0" stop-color="#00E5FF" stop-opacity=".75"/>'
            '<stop offset=".5" stop-color="#3A2BFF" stop-opacity=".55"/>'
            '<stop offset="1" stop-color="#F205FC" stop-opacity=".75"/>'
            "</linearGradient>"
        )
        container = (
            '<rect x="6" y="6" width="244" height="244" rx="56" '
            'fill="url(#ub-bg)" stroke="url(#ub-rim)" stroke-width="6"/>'
        )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" '
        'width="256" height="256" role="img" aria-label="UNnexor">\n'
        "  <title>UNnexor</title>\n"
        f"  <defs>{defs}</defs>\n"
        f"  {container}{group}\n"
        "</svg>\n"
    )


def content_js_svg() -> str:
    """X para el botón flotante (SVG en línea dentro del Shadow DOM)."""
    x0, y0, x1, y1 = bounds(shapes())
    defs, paths = x_markup(x0, y0, prefix="unx")
    return (
        f'<svg viewBox="0 0 {_fmt(x1 - x0)} {_fmt(y1 - y0)}" '
        'aria-hidden="true" focusable="false">'
        f"<defs>{defs}</defs>{paths}</svg>"
    )


# ---------------------------------------------------------------- raster
def render_png(svg: str, width: int, height: int | None = None) -> bytes:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    assert app is not None
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    if not renderer.isValid():
        raise RuntimeError("SVG no válido para QtSvg")
    view = renderer.viewBoxF()
    if height is None:
        height = round(width * view.height() / view.width())
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    renderer.render(painter, QRectF(0, 0, width, height))
    painter.end()
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")  # type: ignore[call-overload]
    return bytes(buffer.data().data())


def write_ico(path: Path, svg: str, sizes: list[int]) -> None:
    """ICO multirresolución: cada tamaño se rasteriza desde el SVG."""
    from PIL import Image

    frames = [
        Image.open(io.BytesIO(render_png(svg, s, s))).convert("RGBA") for s in sizes
    ]
    largest = frames[-1]
    largest.save(
        path,
        format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=frames[:-1],
    )


def update_content_js(svg: str) -> None:
    text = CONTENT_JS.read_text(encoding="utf-8")
    pattern = re.compile(
        r"(/\* BEGIN UNNEXOR_X_SVG \(generado\) \*/\n).*?(\n\s*/\* END UNNEXOR_X_SVG \*/)",
        re.S,
    )
    if not pattern.search(text):
        raise RuntimeError("content.js no tiene el bloque UNNEXOR_X_SVG")
    literal = "  const UNNEXOR_X_SVG =\n    " + json.dumps(svg) + ";"
    new = pattern.sub(lambda m: m.group(1) + literal + m.group(2), text)
    CONTENT_JS.write_text(new, encoding="utf-8", newline="\n")


def main() -> int:
    BRANDING.mkdir(exist_ok=True)
    PNG_DIR.mkdir(exist_ok=True)
    master = master_svg()
    icon = icon_svg(badge=False)
    badge = icon_svg(badge=True)
    (BRANDING / "unnexor-x.svg").write_text(master, encoding="utf-8", newline="\n")
    (BRANDING / "unnexor-icon.svg").write_text(icon, encoding="utf-8", newline="\n")
    (BRANDING / "unnexor-badge.svg").write_text(badge, encoding="utf-8", newline="\n")
    (BRIDGE_ASSETS / "unnexor-x.svg").write_text(master, encoding="utf-8", newline="\n")
    (EXT_ASSETS / "unnexor-x.svg").write_text(master, encoding="utf-8", newline="\n")

    for width in (256, 512, 1024):
        (PNG_DIR / f"unnexor-x-{width}.png").write_bytes(render_png(master, width))
    for size in (16, 32, 48, 64, 128, 256, 512):
        (PNG_DIR / f"unnexor-badge-{size}.png").write_bytes(
            render_png(badge, size, size)
        )
        (PNG_DIR / f"unnexor-icon-{size}.png").write_bytes(render_png(icon, size, size))
    for size in (16, 32, 48, 128):
        (EXT_ASSETS / f"icon{size}.png").write_bytes(render_png(badge, size, size))
    (BRIDGE_ASSETS / "unnexor.png").write_bytes(render_png(badge, 256, 256))
    write_ico(
        BRIDGE_ASSETS / "unnexor.ico", badge, [16, 20, 24, 32, 40, 48, 64, 128, 256]
    )
    update_content_js(content_js_svg())
    print("Recursos de marca generados en", BRANDING)
    return 0


if __name__ == "__main__":
    sys.exit(main())

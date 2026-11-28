"""Renderizado de respuestas: Markdown + fórmulas + código resaltado.

Arquitectura (ADR-0006):

1. ``split_segments`` (puro, sin Qt) separa la respuesta del modelo en
   texto Markdown, bloques de código cercados y fórmulas (``$…$``,
   ``$$…$$``, ``\\(…\\)``, ``\\[…\\]`` y entornos ``\\begin{…}``). Los
   bloques y el código en línea se extraen **antes** de buscar fórmulas,
   así un ``$`` dentro de código nunca se interpreta como matemática.
2. El Markdown se convierte con el motor de Qt (``MarkdownNoHTML``: el HTML
   que escriba el modelo se muestra como texto, nunca se interpreta).
3. Cada fórmula se compone con **ziamath** (LaTeX → MathML → SVG, puro
   Python, fuente STIX Two Math) y se rasteriza con QtSvg. No se ejecuta
   JavaScript ni contenido del modelo: solo se dibujan trazados.
4. El código se colorea con **Pygments** (estilo oscuro tipo VS Code), con
   cabecera de lenguaje y enlace «Copiar» que copia el código original.
5. Si una fórmula no se puede componer, se muestra su versión Unicode
   legible (``math_unicode``) en cursiva: una expresión mal formada nunca
   rompe el resto de la respuesta.

``RichTextBrowser`` solo sirve recursos ``unxmath:`` propios; cualquier
otra imagen (``![](…)``) se ignora, de modo que una respuesta no puede
hacer que la aplicación lea archivos locales o remotos.
"""

from __future__ import annotations

import html
import logging
import re
from collections import OrderedDict
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("unnexor.desktop.rich_text")

# ------------------------------------------------------------------ segmentos
TEXT = "text"
CODE = "code"
MATH_INLINE = "math_inline"
MATH_DISPLAY = "math_display"

MAX_HIGHLIGHT_CHARS = 60_000
MAX_GUESS_CHARS = 4_000

_FENCE_RE = re.compile(
    r"^(?P<indent>[ \t]{0,3})(?P<fence>`{3,}|~{3,})[ \t]*(?P<lang>[^\s`]*)[^\n]*\n",
    re.MULTILINE,
)
_INLINE_CODE_RE = re.compile(r"(`+)(?:.+?)\1", re.DOTALL)
_ENV_NAMES = (
    "equation|equation\\*|align|align\\*|aligned|gather|gather\\*|multline|"
    "cases|matrix|pmatrix|bmatrix|vmatrix|Vmatrix|array|split"
)
_MATH_RE = re.compile(
    r"(?P<dd>\$\$(?P<dd_body>.+?)\$\$)"
    r"|(?P<br>\\\[(?P<br_body>.+?)\\\])"
    r"|(?P<pa>\\\((?P<pa_body>.+?)\\\))"
    rf"|(?P<env>\\begin\{{(?P<env_name>{_ENV_NAMES})\}}.+?\\end\{{(?P=env_name)\}})"
    r"|(?P<d>(?<![\\$\w])\$(?![\s$])(?P<d_body>[^$\n]+?)(?<![\s\\])\$(?![\d$]))",
    re.DOTALL,
)


@dataclass(frozen=True)
class Segment:
    kind: str
    text: str
    lang: str = ""
    raw: str = ""  # texto original (fórmulas: con delimitadores)
    closed: bool = True  # False = bloque de código sin cerrar (streaming)


def _split_math(text: str) -> list[Segment]:
    """Fórmulas dentro de un tramo de texto sin bloques de código."""
    # Enmascara el código en línea para no buscar fórmulas dentro.
    masked = list(text)
    for match in _INLINE_CODE_RE.finditer(text):
        for i in range(match.start(), match.end()):
            masked[i] = "\x00"
    probe = "".join(masked)
    out: list[Segment] = []
    pos = 0
    for match in _MATH_RE.finditer(probe):
        start, end = match.span()
        if "\x00" in probe[start:end]:
            continue
        if start > pos:
            out.append(Segment(TEXT, text[pos:start]))
        raw = text[start:end]
        if match.group("dd") is not None:
            body, kind = match.group("dd_body"), MATH_DISPLAY
        elif match.group("br") is not None:
            body, kind = match.group("br_body"), MATH_DISPLAY
        elif match.group("env") is not None:
            body, kind = raw, MATH_DISPLAY
        elif match.group("pa") is not None:
            body, kind = match.group("pa_body"), MATH_INLINE
        else:
            body, kind = match.group("d_body"), MATH_INLINE
        body = text[start:end] if kind == MATH_DISPLAY and match.group("env") else body
        if not body.strip():
            out.append(Segment(TEXT, raw))
        else:
            out.append(Segment(kind, body.strip(), raw=raw))
        pos = end
    if pos < len(text):
        out.append(Segment(TEXT, text[pos:]))
    return out


def split_segments(text: str) -> list[Segment]:
    """Divide una respuesta en texto, código y fórmulas (función pura)."""
    segments: list[Segment] = []
    pos = 0
    while pos < len(text):
        match = _FENCE_RE.search(text, pos)
        if match is None:
            segments.extend(_split_math(text[pos:]))
            break
        if match.start() > pos:
            segments.extend(_split_math(text[pos : match.start()]))
        fence = match.group("fence")
        closer = re.compile(
            rf"^[ \t]{{0,3}}{re.escape(fence[0])}{{{len(fence)},}}[ \t]*$", re.MULTILINE
        )
        end_match = closer.search(text, match.end())
        lang = match.group("lang").strip().lower()
        if end_match is None:
            body = text[match.end() :]
            segments.append(
                Segment(CODE, body, lang=lang, raw=text[match.start() :], closed=False)
            )
            break
        body = text[match.end() : end_match.start()]
        if body.endswith("\n"):
            body = body[:-1]
        segments.append(
            Segment(CODE, body, lang=lang, raw=text[match.start() : end_match.end()])
        )
        pos = end_match.end()
    return [s for s in segments if s.kind != TEXT or s.text]


# --------------------------------------------------------------- resaltado
LANG_ALIASES = {
    "py": "python",
    "js": "javascript",
    "ts": "typescript",
    "sh": "bash",
    "shell": "bash",
    "ps": "powershell",
    "ps1": "powershell",
    "c++": "cpp",
    "cs": "csharp",
    "c#": "csharp",
    "yml": "yaml",
    "md": "markdown",
    "html5": "html",
}
CODE_STYLE = "github-dark"
CODE_BG = "#0d1117"
CODE_BORDER = "#30363d"


def _lexer_for(code: str, lang: str):
    from pygments.lexers import TextLexer, get_lexer_by_name, guess_lexer
    from pygments.util import ClassNotFound

    name = LANG_ALIASES.get(lang, lang)
    if name:
        try:
            return get_lexer_by_name(name)
        except ClassNotFound:
            pass
    if len(code) <= MAX_GUESS_CHARS and code.strip():
        try:
            lexer = guess_lexer(code)
            # Las adivinanzas con poca confianza suelen ser texto plano.
            if lexer.analyse_text(code) >= 0.3:
                return lexer
        except ClassNotFound:
            pass
    return TextLexer()


def highlight_code(code: str, lang: str = "") -> tuple[str, str]:
    """(HTML con estilos en línea, nombre legible del lenguaje)."""
    if len(code) > MAX_HIGHLIGHT_CHARS:
        return f"<pre>{html.escape(code)}</pre>", lang or "texto"
    try:
        from pygments import highlight
        from pygments.formatters import HtmlFormatter

        lexer = _lexer_for(code, lang)
        try:
            formatter = HtmlFormatter(noclasses=True, nowrap=True, style=CODE_STYLE)
        except Exception:
            formatter = HtmlFormatter(noclasses=True, nowrap=True, style="monokai")
        body = highlight(code, lexer, formatter)
        label = lang or (lexer.name if lexer.name != "Text only" else "texto")
        # Pygments añade un salto final; el bloque no lo necesita.
        return f"<pre>{body.rstrip()}</pre>", label
    except Exception:  # pragma: no cover - Pygments ausente
        return f"<pre>{html.escape(code)}</pre>", lang or "texto"


# ----------------------------------------------------------------- fórmulas
_math_cache: OrderedDict[tuple, object] = OrderedDict()
MATH_CACHE_SIZE = 256
_ziamath_ready: Optional[bool] = None


def _ziamath():
    global _ziamath_ready
    try:
        import ziamath

        if not _ziamath_ready:
            # QtSvg implementa SVG Tiny 1.2: sin <symbol>/<use> de SVG 2.
            ziamath.config.svg2 = False
            _ziamath_ready = True
        return ziamath
    except Exception as exc:  # pragma: no cover - dependencia ausente
        if _ziamath_ready is None:
            log.warning("ziamath no disponible: fórmulas en Unicode (%s)", exc)
        _ziamath_ready = False
        return None


def render_math(
    latex: str, *, display: bool, color: str, size_px: float, scale: float = 2.0
):
    """Compone ``latex`` y devuelve ``QImage`` (con DPR=scale) o ``None``."""
    key = (latex, display, color, round(size_px, 1), scale)
    cached = _math_cache.get(key)
    if cached is not None:
        _math_cache.move_to_end(key)
        return cached
    zm = _ziamath()
    if zm is None:
        return None
    try:
        from PySide6.QtCore import QByteArray, QRectF, Qt
        from PySide6.QtGui import QImage, QPainter
        from PySide6.QtSvg import QSvgRenderer

        expr = zm.Latex(latex, size=size_px, color=color, inline=not display)
        svg = expr.svg()
        renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
        if not renderer.isValid():
            return None
        size = renderer.defaultSize()
        if size.width() <= 0 or size.height() <= 0:
            return None
        image = QImage(
            max(1, int(size.width() * scale)),
            max(1, int(size.height() * scale)),
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter, QRectF(0, 0, image.width(), image.height()))
        painter.end()
        image.setDevicePixelRatio(scale)
    except Exception as exc:
        log.debug("Fórmula no compuesta (%s): %r", exc, latex[:80])
        return None
    _math_cache[key] = image
    while len(_math_cache) > MATH_CACHE_SIZE:
        _math_cache.popitem(last=False)
    return image


# ------------------------------------------------------------ documento HTML
_PLACEHOLDER = "UNXPH{0}X"
_PLACEHOLDER_RE = re.compile(r"UNXPH(\d+)X")
_BLOCK_PARA_RE = re.compile(
    r"<p[^>]*>(?:\s|<span[^>]*>|</span>)*UNXPH(\d+)X(?:\s|<span[^>]*>|</span>)*</p>"
)


@dataclass
class RenderResult:
    html: str
    images: dict  # nombre de recurso -> QImage
    codes: list  # código original de cada bloque (para «Copiar»)


class RichRenderer:
    """Convierte la respuesta del modelo en HTML seguro para QTextBrowser."""

    def __init__(self, *, text_color: str = "#e8eaf2", font_px: float = 14.0) -> None:
        self.text_color = text_color
        self.font_px = font_px

    def render(self, text: str) -> RenderResult:
        from PySide6.QtGui import QTextDocument

        from ..math_unicode import to_human_math

        segments = split_segments(text)
        blocks: dict[int, str] = {}
        inline: dict[int, str] = {}
        images: dict = {}
        codes: list[str] = []
        parts: list[str] = []
        for seg in segments:
            index = len(blocks) + len(inline)
            token = _PLACEHOLDER.format(index)
            if seg.kind == TEXT:
                parts.append(_humanize_loose_math(seg.text, to_human_math))
            elif seg.kind == CODE:
                blocks[index] = self._code_html(seg, len(codes))
                codes.append(seg.text)
                parts.append(f"\n\n{token}\n\n")
            else:
                display = seg.kind == MATH_DISPLAY
                image = render_math(
                    seg.text,
                    display=display,
                    color=self.text_color,
                    size_px=self.font_px * (1.2 if display else 1.1),
                )
                if image is None:
                    fallback = html.escape(to_human_math(seg.raw or seg.text))
                    rendered = f'<i title="{html.escape(seg.text)}">{fallback}</i>'
                else:
                    name = f"unxmath:{len(images)}"
                    images[name] = image
                    ratio = image.devicePixelRatio() or 1.0
                    width = int(image.width() / ratio)
                    height = int(image.height() / ratio)
                    rendered = (
                        f'<img src="{name}" width="{width}" height="{height}"'
                        f' style="vertical-align: middle" alt="{html.escape(seg.text)}">'
                    )
                if display:
                    blocks[index] = (
                        f'<p align="center" style="margin: 8px 0">{rendered}</p>'
                    )
                    parts.append(f"\n\n{token}\n\n")
                else:
                    inline[index] = rendered
                    parts.append(token)

        doc = QTextDocument()
        features = QTextDocument.MarkdownFeature(
            QTextDocument.MarkdownFeature.MarkdownDialectGitHub.value
            | QTextDocument.MarkdownFeature.MarkdownNoHTML.value
        )
        doc.setMarkdown("".join(parts), features)
        body = _extract_body(doc.toHtml())

        def block_sub(match: re.Match) -> str:
            index = int(match.group(1))
            return blocks.get(index, inline.get(index, match.group(0)))

        body = _BLOCK_PARA_RE.sub(block_sub, body)

        def inline_sub(match: re.Match) -> str:
            index = int(match.group(1))
            return inline.get(index, blocks.get(index, ""))

        body = _PLACEHOLDER_RE.sub(inline_sub, body)
        return RenderResult(html=body, images=images, codes=codes)

    @staticmethod
    def _code_html(seg: Segment, index: int) -> str:
        code_html, label = highlight_code(seg.text, seg.lang)
        status = "" if seg.closed else " · escribiendo…"
        return (
            f'<table width="100%" cellspacing="0" cellpadding="8"'
            f' style="background-color: {CODE_BG}; border: 1px solid {CODE_BORDER};'
            ' margin-top: 6px; margin-bottom: 6px">'
            f'<tr><td style="background-color: #161b22; color: #8b949e;'
            f' font-size: 11px; padding: 4px 8px">'
            f"<b>{html.escape(label)}</b>{status}"
            f'&nbsp;&nbsp;&nbsp;<a href="unxcopy:{index}" style="color: #58a6ff;'
            ' text-decoration: none">⧉ Copiar código</a></td></tr>'
            '<tr><td style="font-family: Cascadia Mono, Consolas, monospace;'
            f' color: #e6edf3; padding: 8px">{code_html}</td></tr></table>'
        )


def _humanize_loose_math(text: str, convert) -> str:
    """Notación suelta (``x^2``, ``\\alpha``) → Unicode, salvo en código."""
    out: list[str] = []
    pos = 0
    for match in _INLINE_CODE_RE.finditer(text):
        out.append(convert(text[pos : match.start()]))
        out.append(match.group(0))
        pos = match.end()
    out.append(convert(text[pos:]))
    return "".join(out)


def _extract_body(document_html: str) -> str:
    start = document_html.find("<body")
    if start < 0:
        return document_html
    start = document_html.find(">", start) + 1
    end = document_html.rfind("</body>")
    return document_html[start:end] if end > start else document_html[start:]


# ------------------------------------------------------------------- widget
from PySide6.QtCore import QUrl, Signal  # noqa: E402
from PySide6.QtGui import QDesktopServices, QGuiApplication, QTextDocument  # noqa: E402
from PySide6.QtWidgets import QTextBrowser  # noqa: E402


class RichTextBrowser(QTextBrowser):
    """QTextBrowser que solo sirve recursos propios y gestiona «Copiar»."""

    codeCopied = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._images: dict = {}
        self._codes: list[str] = []
        self._renderer = RichRenderer()
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.anchorClicked.connect(self._on_anchor)

    def set_text_style(self, color: str, font_px: float) -> None:
        self._renderer = RichRenderer(text_color=color, font_px=font_px)

    def set_rich_text(self, text: str) -> None:
        result = self._renderer.render(text)
        self._images = result.images
        self._codes = result.codes
        self.setHtml(result.html)

    def code_blocks(self) -> list[str]:
        return list(self._codes)

    def loadResource(self, kind: int, url: QUrl | str):
        if kind == QTextDocument.ResourceType.ImageResource.value:
            # Solo fórmulas propias: nunca leer archivos ni URL arbitrarias.
            key = url.toString() if isinstance(url, QUrl) else str(url)
            return self._images.get(key)
        return None

    def _on_anchor(self, url: QUrl) -> None:
        scheme = url.scheme()
        if scheme == "unxcopy":
            try:
                index = int(url.toString().split(":", 1)[1])
            except (ValueError, IndexError):
                return
            if 0 <= index < len(self._codes):
                clipboard = QGuiApplication.clipboard()
                if clipboard is not None:
                    clipboard.setText(self._codes[index])
                self.codeCopied.emit(index)
        elif scheme in ("http", "https", "mailto"):
            QDesktopServices.openUrl(url)


def self_check() -> dict:
    """Comprueba en el EXE que fórmulas y resaltado funcionan (sin red).

    Lo usa ``UNnexor.exe --check``: detecta fuentes de ziamath o lexers de
    Pygments que no se empaquetaron.
    """
    from PySide6.QtGui import QGuiApplication

    result: dict = {"ok": False}
    owner = None
    if QGuiApplication.instance() is None:
        owner = QGuiApplication(["unnexor-check", "-platform", "offscreen"])
    try:
        image = render_math(
            r"x = \frac{-b \pm \sqrt{b^2-4ac}}{2a}",
            display=True,
            color="#ffffff",
            size_px=16,
        )
        result["math"] = image is not None and image.width() > 20
        code_html, label = highlight_code("def f(x):\n    return x * 2\n", "python")
        result["code"] = "<span" in code_html and label == "python"
        result["sql"] = "<span" in highlight_code("SELECT 1 FROM t;", "sql")[0]
        result["ok"] = bool(result["math"] and result["code"] and result["sql"])
    except Exception as exc:  # el chequeo nunca debe tumbar el proceso
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        del owner
    return result

"""LaTeX → matemática Unicode legible para personas.

El panel usa ``QTextBrowser`` (sin WebView ni KaTeX), así que el LaTeX
crudo que produce el modelo (``\\frac{-b \\pm \\sqrt{b^2-4ac}}{2a}``) se
convierte a matemática Unicode que una persona lee y copia a mano tal
cual:

    (-b ± √(b²-4ac))/(2a)

Reglas de diseño:

* Solo se tocan los tramos matemáticos del mensaje: los bloques de
  código (```` ``` ```` y `` `...` ``) pasan intactos, y un texto sin
  marcas de matemática (``\\comando``, ``$…$``, ``^``, ``_``) no cambia.
* Un comando LaTeX desconocido se deja tal cual: nunca se inventa.
* Función pura y sin dependencias (se prueba sin Qt).
* Nota: ``_`` sin llaves solo se convierte si va seguido de dígitos o
  de una sola letra (``a_1`` → ``a₁``), para no romper ``mi_variable``
  en prosa; ``_{...}`` explícito siempre se convierte.
"""

from __future__ import annotations

import re

__all__ = ["to_human_math"]

# Marcador para proteger los caracteres escapados (\{, \$, \%…) durante
# la conversión; se restaura al final. Es un carácter de uso privado que
# no aparece en texto normal.
_PLACEHOLDER = "\ue000"

_SUPERSCRIPT = {
    "0": "⁰",
    "1": "¹",
    "2": "²",
    "3": "³",
    "4": "⁴",
    "5": "⁵",
    "6": "⁶",
    "7": "⁷",
    "8": "⁸",
    "9": "⁹",
    "+": "⁺",
    "-": "⁻",
    "=": "⁼",
    "(": "⁽",
    ")": "⁾",
    "a": "ᵃ",
    "b": "ᵇ",
    "c": "ᶜ",
    "d": "ᵈ",
    "e": "ᵉ",
    "f": "ᶠ",
    "g": "ᵌ",
    "h": "ʰ",
    "i": "ⁱ",
    "j": "ʲ",
    "k": "ᵏ",
    "l": "ˡ",
    "m": "ᵐ",
    "n": "ⁿ",
    "o": "ᵒ",
    "p": "ᵖ",
    "r": "ʳ",
    "s": "ˢ",
    "t": "ᵗ",
    "u": "ᵘ",
    "v": "ᵛ",
    "w": "ʷ",
    "x": "ˣ",
    "y": "ʸ",
    "z": "ᶻ",
}
_SUBSCRIPT = {
    "0": "₀",
    "1": "₁",
    "2": "₂",
    "3": "₃",
    "4": "₄",
    "5": "₅",
    "6": "₆",
    "7": "₇",
    "8": "₈",
    "9": "₉",
    "+": "₊",
    "-": "₋",
    "=": "₌",
    "(": "₍",
    ")": "₎",
    "a": "ₐ",
    "e": "ₑ",
    "h": "ₕ",
    "i": "ᵢ",
    "j": "ⱼ",
    "k": "ₖ",
    "l": "ₗ",
    "m": "ₘ",
    "n": "ₙ",
    "o": "ₒ",
    "p": "ₚ",
    "r": "ᵣ",
    "s": "ₛ",
    "t": "ₜ",
    "u": "ᵤ",
    "v": "ᵥ",
    "x": "ₓ",
}
# \mathbb{X} para las letras con variante "doble trazo" estándar.
_BLACKBOARD = {
    "A": "𝔸",
    "B": "𝔹",
    "C": "ℂ",
    "D": "𝔻",
    "E": "𝔼",
    "F": "𝔽",
    "G": "𝔾",
    "H": "ℍ",
    "I": "𝕀",
    "J": "𝕁",
    "K": "𝕂",
    "L": "𝕃",
    "M": "𝕄",
    "N": "ℕ",
    "O": "𝕆",
    "P": "ℙ",
    "Q": "ℚ",
    "R": "ℝ",
    "S": "𝕊",
    "T": "𝕋",
    "U": "𝕌",
    "V": "𝕍",
    "W": "𝕎",
    "X": "𝕏",
    "Y": "𝕐",
    "Z": "ℤ",
}
# \hat{x} → x̂, etc. (carácter combinador sobre el último símbolo).
_ACCENTS = {
    "hat": "\u0302",
    "widehat": "\u0302",
    "bar": "\u0304",
    "overline": "\u0304",
    "tilde": "\u0303",
    "widetilde": "\u0303",
    "vec": "\u20d7",
    "dot": "\u0307",
    "ddot": "\u0308",
    "acute": "\u0301",
    "grave": "\u0300",
    "breve": "\u0306",
    "check": "\u030c",
}
# Comandos → símbolo/texto. La búsqueda usa el token completo
# (\infty no choca con \in porque el token es la palabra entera).
_COMMANDS = {
    # griegas minúsculas
    "alpha": "α",
    "beta": "β",
    "gamma": "γ",
    "delta": "δ",
    "epsilon": "ε",
    "varepsilon": "ε",
    "zeta": "ζ",
    "eta": "η",
    "theta": "θ",
    "vartheta": "ϑ",
    "iota": "ι",
    "kappa": "κ",
    "lambda": "λ",
    "mu": "μ",
    "nu": "ν",
    "xi": "ξ",
    "omicron": "ο",
    "pi": "π",
    "rho": "ρ",
    "sigma": "σ",
    "varsigma": "ς",
    "tau": "τ",
    "upsilon": "υ",
    "phi": "φ",
    "varphi": "φ",
    "chi": "χ",
    "psi": "ψ",
    "omega": "ω",
    # griegas mayúsculas
    "Gamma": "Γ",
    "Delta": "Δ",
    "Theta": "Θ",
    "Lambda": "Λ",
    "Xi": "Ξ",
    "Pi": "Π",
    "Sigma": "Σ",
    "Upsilon": "Υ",
    "Phi": "Φ",
    "Psi": "Ψ",
    "Omega": "Ω",
    # operadores y relaciones
    "times": "×",
    "div": "÷",
    "cdot": "·",
    "pm": "±",
    "mp": "∓",
    "ast": "∗",
    "star": "⋆",
    "circ": "∘",
    "bullet": "•",
    "oplus": "⊕",
    "ominus": "⊖",
    "otimes": "⊗",
    "oslash": "⊘",
    "leq": "≤",
    "le": "≤",
    "geq": "≥",
    "ge": "≥",
    "neq": "≠",
    "ne": "≠",
    "equiv": "≡",
    "approx": "≈",
    "sim": "∼",
    "simeq": "≃",
    "cong": "≅",
    "propto": "∝",
    "subset": "⊂",
    "supset": "⊃",
    "subseteq": "⊆",
    "supseteq": "⊇",
    "in": "∈",
    "notin": "∉",
    "ni": "∋",
    "cup": "∪",
    "cap": "∩",
    "emptyset": "∅",
    "varnothing": "∅",
    "forall": "∀",
    "exists": "∃",
    "nexists": "∄",
    "neg": "¬",
    "land": "∧",
    "lor": "∨",
    "wedge": "∧",
    "vee": "∨",
    "Rightarrow": "⇒",
    "rightarrow": "→",
    "to": "→",
    "leftarrow": "←",
    "gets": "←",
    "Leftrightarrow": "⇔",
    "leftrightarrow": "↔",
    "mapsto": "↦",
    "uparrow": "↑",
    "downarrow": "↓",
    "Uparrow": "⇑",
    "Downarrow": "⇓",
    "perp": "⊥",
    "parallel": "∥",
    "angle": "∠",
    "triangle": "△",
    "square": "□",
    "nabla": "∇",
    "partial": "∂",
    "infty": "∞",
    "degree": "°",
    "textdegree": "°",
    "prime": "′",
    "aleph": "ℵ",
    "hbar": "ℏ",
    "ell": "ℓ",
    "Re": "ℜ",
    "Im": "ℑ",
    "ldots": "…",
    "cdots": "⋯",
    "vdots": "⋮",
    "ddots": "⋱",
    "dots": "…",
    "because": "∵",
    "therefore": "∴",
    "mid": "∣",
    # operadores grandes
    "sum": "∑",
    "prod": "∏",
    "coprod": "∐",
    "int": "∫",
    "iint": "∬",
    "iiint": "∭",
    "oint": "∮",
    # funciones con nombre (se dejan como texto legible)
    "sin": "sin",
    "cos": "cos",
    "tan": "tan",
    "cot": "cot",
    "sec": "sec",
    "csc": "csc",
    "sinh": "sinh",
    "cosh": "cosh",
    "tanh": "tanh",
    "arcsin": "arcsin",
    "arccos": "arccos",
    "arctan": "arctan",
    "log": "log",
    "ln": "ln",
    "exp": "exp",
    "lim": "lim",
    "limsup": "lim sup",
    "liminf": "lim inf",
    "max": "max",
    "min": "min",
    "sup": "sup",
    "inf": "inf",
    "det": "det",
    "dim": "dim",
    "deg": "deg",
    "gcd": "gcd",
    "arg": "arg",
    "bmod": "mod",
    # varios
    "item": "• ",
}
# Comandos cuya única función es envolver: se elimina el envoltorio y
# queda el contenido (el interior se sigue convirtiendo en el pipeline).
_WRAP_RE = re.compile(
    r"\\(mathbb|mathrm|mathit|mathbf|mathsf|mathtt|mathcal|mathfrak"
    r"|boldsymbol|bm|operatorname|text|textbf|textit|textsf|texttt"
    r"|textrm|textsc|textnormal|boxed|fbox|emph|mbox|hbox)\s*\{"
)
_ENV_RE = re.compile(r"\\begin\{([a-zA-Z]+\*?)\}(.*?)\\end\{\1\}", re.DOTALL)
_ROW_ENVS = {
    "aligned",
    "align",
    "align*",
    "alignedat",
    "alignat",
    "cases",
    "array",
    "matrix",
    "pmatrix",
    "bmatrix",
    "vmatrix",
    "Vmatrix",
    "Bmatrix",
    "smallmatrix",
    "split",
    "gather",
    "gathered",
    "equation",
    "equation*",
}
_FRAC_RE = re.compile(r"\\(?:dfrac|tfrac|cfrac|frac)(?![a-zA-Z])")
_BINOM_RE = re.compile(r"\\(?:dbinom|tbinom|binom)(?![a-zA-Z])")
_CMD_TOKEN_RE = re.compile(r"\\([a-zA-Z]+)")
_ACCENT_RE = re.compile(
    r"\\(hat|widehat|bar|overline|tilde|widetilde|vec|dot|ddot|acute"
    r"|grave|breve|check)\s*\{([^{}]*)\}"
)
_CODE_SPLIT_RE = re.compile(r"```.*?(?:```|\Z)|`[^`\n]*`", re.DOTALL)

# Entornos de fila: se elimina el separador de columnas &.
_ROW_MARK_RE = re.compile(r"\\(?:begin|end)\{[a-zA-Z]+\*?\}")


def to_human_math(text: str) -> str:
    """Convierte el LaTeX/ASCII matemático de ``text`` a Unicode legible.

    El texto que no tiene marcas de matemática se devuelve sin cambios,
    y los bloques de código nunca se tocan.
    """
    if not text or not any(c in text for c in "\\$^_<>!"):
        return text
    parts: list[str] = []
    last = 0
    for match in _CODE_SPLIT_RE.finditer(text):
        parts.append(_convert_segment(text[last : match.start()]))
        parts.append(match.group(0))  # código intacto
        last = match.end()
    parts.append(_convert_segment(text[last:]))
    return "".join(parts)


# ---------------------------------------------------------------------- #
# Núcleo por segmento
# ---------------------------------------------------------------------- #
def _convert_segment(text: str) -> str:
    """Pipeline completo sobre un tramo fuera de bloques de código."""
    if not _looks_like_math(text):
        return text
    text = _convert_environments(text)
    text = _protect_escapes(text)
    text = _strip_delimiters(text)
    # 25^\circ → 25° (antes de que \circ se vuelva "∘").
    text = re.sub(r"\^\{?\\circ\}?", "°", text)
    text = re.sub(r"\\left(?![a-zA-Z])", "", text)
    text = re.sub(r"\\right(?![a-zA-Z])", "", text)
    text = _unwrap_wrappers(text)
    text = _map_commands(text)
    text = _ACCENT_RE.sub(_accent_repl, text)
    text = _convert_sqrts(text)
    text = _convert_fracs(text)
    text = _convert_binoms(text)
    text = _convert_braced_scripts(text)
    text = _convert_ascii_scripts(text)
    text = _cleanup(text)
    return text.replace(_PLACEHOLDER, "")


def _looks_like_math(text: str) -> bool:
    """¿Hay marcas de matemática que justifiquen convertir?"""
    if "$$" in text or re.search(r"\\[a-zA-Z]", text):
        return True
    if re.search(r"[\^_][-+]?[a-zA-Z0-9{(]", text):
        return True
    # Operadores comparativos/flecha escritos en ASCII (x <= 5, a -> b).
    if re.search(r"[<>]=|=>|!=|->", text):
        return True
    inline = re.search(r"\$([^$\n]+)\$", text)
    if inline and re.search(r"[\\^_{}]", inline.group(1)):
        return True
    return False


def _convert_environments(text: str) -> str:
    """Quita begin/end, el & de columnas y convierte \\ en salto de línea."""

    def env_repl(match: re.Match) -> str:
        name, body = match.group(1), match.group(2)
        if name.rstrip("*") in _ROW_ENVS:
            body = body.replace("&", "")  # separador de columnas
        return body

    text = _ENV_RE.sub(env_repl, text)
    # Salto de fila LaTeX (\\, \\[2pt] y un espacio posterior) → línea.
    text = re.sub(r"\\\\(?:\[[^\]\n]*\])? ?", "\n", text)
    return _ROW_MARK_RE.sub("", text)


def _protect_escapes(text: str) -> str:
    """\\{ \\} \\$ \\% \\& \\# \\_ \\~ → marcador + carácter."""
    escapable = "{}$%&#_~"
    out: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text) and text[i + 1] in escapable:
            out.append(_PLACEHOLDER)
            out.append(text[i + 1])
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _strip_delimiters(text: str) -> str:
    """Quita $$…$$, $…$ (con contenido matemático), \\[…\\] y \\(…\\)."""
    text = re.sub(r"\$\$(.+?)\$\$", r"\1", text, flags=re.DOTALL)

    def inline(match: re.Match) -> str:
        inner = match.group(1)
        stripped = inner.strip()
        # Solo se abre si el contenido parece matemática (no dinero:
        # "cuesta $5 y $10" se queda igual).
        if re.search(r"[=±\\^_{}+]", stripped):
            return inner
        if re.fullmatch(r"[A-Za-z0-9]{1,12}", stripped):
            return stripped
        return match.group(0)

    return (
        re.sub(r"\$([^$\n]+)\$", inline, text)
        .replace("\\[", "")
        .replace("\\]", "")
        .replace("\\(", "")
        .replace("\\)", "")
    )


def _match_brace(text: str, start: int) -> int:
    """Índice del } que cierra el { de ``start``; -1 si no hay cierre."""
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _unwrap_wrappers(text: str) -> str:
    """\\mathrm{…}, \\text{…}, \\boxed{…}… → su contenido."""
    for _ in range(16):
        match = _WRAP_RE.search(text)
        if not match:
            return text
        brace = match.end() - 1
        end = _match_brace(text, brace)
        if end == -1:
            return text  # llave sin cerrar: no se toca el resto
        inner = text[brace + 1 : end]
        name = match.group(1)
        if name == "mathbb" and len(inner) == 1:
            inner = _BLACKBOARD.get(inner, inner)
        text = text[: match.start()] + inner + text[end + 1 :]


def _map_commands(text: str) -> str:
    """Tabla de símbolos: \\alpha → α, \\leq → ≤… Token completo."""

    def repl(match: re.Match) -> str:
        return _COMMANDS.get(match.group(1), match.group(0))

    return _CMD_TOKEN_RE.sub(repl, text)


def _accent_repl(match: re.Match) -> str:
    name, content = match.group(1), match.group(2)
    if not content:
        return ""
    return content + _ACCENTS[name]


def _convert_sqrts(text: str) -> str:
    """\\sqrt{x} → √(x) y \\sqrt[n]{x} → ⁿ√(x)."""
    out: list[str] = []
    i = 0
    while i < len(text):
        idx = text.find("\\sqrt", i)
        if idx == -1:
            out.append(text[i:])
            break
        out.append(text[i:idx])
        j = idx + len("\\sqrt")
        if j < len(text) and text[j].isalpha():
            out.append("\\sqrt")  # era otro comando (\sqrtx)
            i = j
            continue
        k = j
        index = ""
        while k < len(text) and text[k] == " ":
            k += 1
        if k < len(text) and text[k] == "[":
            close = text.find("]", k + 1)
            if close != -1:
                index = text[k + 1 : close]
                k = close + 1
                while k < len(text) and text[k] == " ":
                    k += 1
        if k < len(text) and text[k] == "{":
            end = _match_brace(text, k)
            if end != -1:
                out.append(_sqrt_repr(index, text[k + 1 : end]))
                i = end + 1
                continue
        token = re.match(r"[a-zA-Z0-9]+", text[k:])
        if token:
            out.append(_sqrt_repr(index, token.group(0)))
            i = k + token.end()
            continue
        out.append("\\sqrt")
        i = j
    return "".join(out)


def _sqrt_repr(index: str, argument: str) -> str:
    argument = _convert_segment(argument)
    prefix = ""
    if index:
        index = index.strip()
        if index and all(c in _SUPERSCRIPT for c in index):
            prefix = "".join(_SUPERSCRIPT[c] for c in index)
        else:
            prefix = f"^({index})"
    return f"{prefix}√({argument})"


def _convert_fracs(text: str) -> str:
    """\\frac{a}{b} → a/b (con paréntesis cuando el orden lo exige)."""
    out: list[str] = []
    i = 0
    while True:
        match = _FRAC_RE.search(text, i)
        if not match:
            out.append(text[i:])
            break
        out.append(text[i : match.start()])
        j = match.end()
        while j < len(text) and text[j] == " ":
            j += 1
        if j < len(text) and text[j] == "{":
            end_num = _match_brace(text, j)
            if end_num != -1:
                k = end_num + 1
                while k < len(text) and text[k] == " ":
                    k += 1
                if k < len(text) and text[k] == "{":
                    end_den = _match_brace(text, k)
                    if end_den != -1:
                        out.append(
                            _frac_repr(text[j + 1 : end_num], text[k + 1 : end_den])
                        )
                        i = end_den + 1
                        continue
        out.append(match.group(0))
        i = match.end()
    return "".join(out)


def _frac_repr(num: str, den: str) -> str:
    num = _convert_segment(num)
    den = _convert_segment(den)

    def wrap_num(part: str) -> str:
        if part and not any(c in part for c in " +-=*/\n"):
            return part
        return f"({part})"

    def wrap_den(part: str) -> str:
        # El denominador es el divisor: "x/2a" se leería ambiguo, así
        # que todo denominador de más de un símbolo lleva paréntesis
        # (excepto números: 1/10 se lee sin ambigüedad).
        if not part:
            return "()"
        if any(c in part for c in " +-=*/\n"):
            return f"({part})"
        if len(part) > 1 and not part.isdigit():
            return f"({part})"
        return part

    return f"{wrap_num(num)}/{wrap_den(den)}"


def _convert_binoms(text: str) -> str:
    """\\binom{n}{k} → (n sobre k)."""
    out: list[str] = []
    i = 0
    while True:
        match = _BINOM_RE.search(text, i)
        if not match:
            out.append(text[i:])
            break
        out.append(text[i : match.start()])
        j = match.end()
        while j < len(text) and text[j] == " ":
            j += 1
        groups: list[str] = []
        ok = True
        for _ in range(2):
            if j < len(text) and text[j] == "{":
                end = _match_brace(text, j)
                if end == -1:
                    ok = False
                    break
                groups.append(_convert_segment(text[j + 1 : end]))
                j = end + 1
                while j < len(text) and text[j] == " ":
                    j += 1
            else:
                ok = False
                break
        if ok and len(groups) == 2:
            out.append(f"({groups[0]} sobre {groups[1]})")
            i = j
        else:
            out.append(match.group(0))
            i = match.end()
    return "".join(out)


def _convert_braced_scripts(text: str) -> str:
    """^{…} y _{…} con llaves → super/subíndices Unicode (o (…))."""
    out: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        out.append(ch)
        if (
            ch in "^_"
            and (i == 0 or text[i - 1] != _PLACEHOLDER)
            and i + 1 < len(text)
            and text[i + 1] == "{"
        ):
            end = _match_brace(text, i + 1)
            if end != -1:
                out.pop()  # el marcador lo reemplaza _apply_script
                out.append(_apply_script(ch, text[i + 2 : end]))
                i = end + 1
                continue
        i += 1
    return "".join(out)


def _apply_script(marker: str, inner: str) -> str:
    if not inner:
        return ""  # ^{} / _{} vacíos no aportan nada
    if "^" in inner or "_" in inner:
        inner = _convert_braced_scripts(inner)
    table = _SUPERSCRIPT if marker == "^" else _SUBSCRIPT
    if all(c in table for c in inner):
        return "".join(table[c] for c in inner)
    return f"{marker}({inner})"


def _convert_ascii_scripts(text: str) -> str:
    """Poderes escritos sin LaTeX: x^2 → x², x^(n+1) → xⁿ⁺¹, a_1 → a₁."""

    def paren(match: re.Match) -> str:
        marker, inner = match.group(1), match.group(2)
        table = _SUPERSCRIPT if marker == "^" else _SUBSCRIPT
        if marker == "_" and not (
            inner.isdigit() or (len(inner) == 1 and inner in table)
        ):
            return match.group(0)
        if inner and all(c in table for c in inner):
            return "".join(table[c] for c in inner)
        return match.group(0)

    def run(match: re.Match) -> str:
        marker, sign, run = match.group(1), match.group(2) or "", match.group(3)
        table = _SUPERSCRIPT if marker == "^" else _SUBSCRIPT
        full = sign + run
        # En prosa, "_palabra" suele ser snake_case: no se toca.
        if marker == "_" and not (full.isdigit() or (len(full) == 1 and full in table)):
            return match.group(0)
        if all(c in table for c in full):
            return "".join(table[c] for c in full)
        return f"{marker}({full})"

    text = re.sub(r"(?<!\ue000)([\^_])\(([^()]{1,32})\)", paren, text)
    return re.sub(r"(?<!\ue000)([\^_])([-+]?)([a-zA-Z0-9]+)", run, text)


def _cleanup(text: str) -> str:
    """Espaciado LaTeX, delimitadores de tamaño y símbolos ASCII."""
    text = re.sub(
        r"\\(?:displaystyle|textstyle|scriptstyle|n?limits|middle"
        r"|allowbreak|noindent|qquad|quad)\b",
        " ",
        text,
    )
    text = re.sub(r"\\[bB]igg?[lrm]?\b", "", text)
    text = re.sub(r"\\[,;:]", " ", text)
    text = re.sub(r"\\!|\\ ", " ", text)
    for old, new in (
        ("<=>", "⇔"),
        ("<->", "↔"),
        ("<=", "≤"),
        (">=", "≥"),
        ("!=", "≠"),
        ("=>", "⇒"),
        ("->", "→"),
        ("<-", "←"),
    ):
        text = text.replace(old, new)
    return text.replace("{}", "")

"""Pruebas de LaTeX → matemática Unicode (``app.math_unicode``).

Incluye casos reales capturados en las pruebas E2E: la salida de
qwen2.5vl leyendo una imagen y los tres bloques ``$$…$$`` que produjo
Pix2Text sobre esa misma imagen (con su ``\\Lambda`` por el ``^``).
"""

from __future__ import annotations

from app.math_unicode import to_human_math


# --------------------------------------------------------------------- #
# Guardas: lo que NO debe cambiarse
# --------------------------------------------------------------------- #
def test_plain_text_unchanged():
    assert to_human_math("") == ""
    assert to_human_math("Hola, ¿cómo resuelvo esto?") == ("Hola, ¿cómo resuelvo esto?")


def test_code_blocks_untouched():
    code = "```python\nx^2 + y_1\n```"
    assert to_human_math(f"mira: {code}") == f"mira: {code}"


def test_inline_code_untouched():
    assert to_human_math("usa `a^2 + b_1` aquí") == "usa `a^2 + b_1` aquí"


def test_money_not_eaten():
    # Sin marcas de matemática: los $ quedan.
    assert to_human_math("cuesta $5 y $10") == "cuesta $5 y $10"


def test_snake_case_in_prose_survives():
    assert to_human_math("la variable mi_precio sube") == ("la variable mi_precio sube")


# --------------------------------------------------------------------- #
# Salidas reales del sistema (capturadas en E2E)
# --------------------------------------------------------------------- #
def test_real_vision_output():
    # qwen2.5vl:7b leyó exactamente esto de la imagen de prueba.
    assert to_human_math("x^2 + y^2 = 25\n\nx + y = 7") == ("x² + y² = 25\n\nx + y = 7")


def test_real_ocr_output_lambda():
    # Pix2Text leyó el ^ escrito como \Lambda y envolvió en $$…$$.
    raw = "$$\\mathrm{x}^{\\Lambda} 2+\\mathrm{y}^{\\Lambda} 2=2 5$$"
    assert to_human_math(raw) == "x^(Λ) 2+y^(Λ) 2=2 5"


def test_real_ocr_output_textsc():
    raw = "$$\\textsc{x}+\\textsc{y}=7$$"
    assert to_human_math(raw) == "x+y=7"


def test_real_ocr_output_emc2():
    raw = "$$\\mathrm{E}=\\mathrm{m c}^{\\Lambda} 2$$"
    # El OCR dejó un espacio dentro de \mathrm{m c}: se conserva.
    assert to_human_math(raw) == "E=m c^(Λ) 2"


# --------------------------------------------------------------------- #
# Potencias (ASCII y LaTeX)
# --------------------------------------------------------------------- #
def test_ascii_powers():
    assert to_human_math("E = mc^2") == "E = mc²"
    assert to_human_math("x^{10}") == "x¹⁰"
    assert to_human_math("2^33") == "2³³"
    assert to_human_math("x^-1") == "x⁻¹"


def test_braced_power_fallback():
    # "q" no tiene superíndice Unicode: se conserva el exponente entre
    # paréntesis en vez de inventar un símbolo.
    assert to_human_math("x^q") == "x^(q)"


def test_ascii_power_parentheses():
    assert to_human_math("x^(2)") == "x²"
    assert to_human_math("2^(n+1)") == "2ⁿ⁺¹"
    assert to_human_math("(x+1)^2") == "(x+1)²"


def test_ascii_subscripts():
    assert to_human_math("a_1 + a_2 = 3") == "a₁ + a₂ = 3"
    assert to_human_math("x_n") == "xₙ"
    # sin variante Unicode: queda tal cual
    assert to_human_math("x_q") == "x_q"
    assert to_human_math("x_{bcd}") == "x_(bcd)"


# --------------------------------------------------------------------- #
# Fracciones, raíces y binomios
# --------------------------------------------------------------------- #
def test_frac_simple():
    assert to_human_math("$\\frac{1}{2}$") == "1/2"
    assert to_human_math("\\frac{a+b}{2}") == "(a+b)/2"


def test_frac_quadratic_formula():
    raw = "\\frac{-b \\pm \\sqrt{b^2-4ac}}{2a}"
    assert to_human_math(raw) == "(-b ± √(b²-4ac))/(2a)"


def test_sqrt():
    assert to_human_math("\\sqrt{x}") == "√(x)"
    assert to_human_math("\\sqrt[3]{8}") == "³√(8)"
    assert to_human_math("\\sqrt[2]{x+1}") == "²√(x+1)"
    assert to_human_math("\\sqrt 2") == "√(2)"


def test_binom():
    assert to_human_math("\\binom{5}{2}") == "(5 sobre 2)"


def test_nested_frac():
    assert to_human_math("\\frac{\\frac{a}{b}}{c}") == "(a/b)/c"


# --------------------------------------------------------------------- #
# Símbolos y comandos
# --------------------------------------------------------------------- #
def test_greek():
    assert to_human_math("\\alpha + \\beta = \\gamma") == "α + β = γ"
    assert to_human_math("\\Lambda \\Delta \\Omega") == "Λ Δ Ω"


def test_operators():
    assert to_human_math("x \\leq 5") == "x ≤ 5"
    assert to_human_math("x \\ge 3") == "x ≥ 3"
    assert to_human_math("a \\neq b") == "a ≠ b"
    assert to_human_math("n \\approx \\infty") == "n ≈ ∞"
    assert to_human_math("2 \\times 3 \\cdot 4") == "2 × 3 · 4"


def test_ascii_operators():
    assert to_human_math("x <= 5") == "x ≤ 5"
    assert to_human_math("x >= 3") == "x ≥ 3"
    assert to_human_math("a != b") == "a ≠ b"


def test_sets_and_logic():
    assert to_human_math("x \\in \\mathbb{R}") == "x ∈ ℝ"
    assert to_human_math("A \\cup B \\cap C") == "A ∪ B ∩ C"
    assert to_human_math("\\forall x \\exists n") == "∀ x ∃ n"


def test_big_operators_with_limits():
    assert to_human_math("\\sum_{i=1}^{n} i^2") == "∑ᵢ₌₁ⁿ i²"
    assert to_human_math("\\int_0^1 x^2 dx") == "∫₀¹ x² dx"


def test_functions_read_as_text():
    assert to_human_math("\\sin^2(x) + \\cos^2(x) = 1") == ("sin²(x) + cos²(x) = 1")
    assert to_human_math("\\log_2 8 = 3") == "log₂ 8 = 3"
    assert to_human_math("\\lim_{x \\to 0} f(x)") == "lim_(x → 0) f(x)"


def test_degree_and_wrappers():
    assert to_human_math("25^\\circ") == "25°"
    assert to_human_math("\\boxed{x = 5}") == "x = 5"
    assert to_human_math("\\mathrm{E} = \\mathrm{mc}^2") == "E = mc²"


def test_accents():
    assert to_human_math("\\vec{v}") == "v⃗"
    assert to_human_math("\\bar{x}") == "x̄"


def test_environments():
    raw = "\\begin{aligned} x &= 2 \\\\ y &= 3 \\end{aligned}"
    assert to_human_math(raw) == " x = 2 \ny = 3 "
    raw_cases = "\\begin{cases} 2x &= 4 \\\\ 3y &= 9 \\end{cases}"
    assert "2x = 4 \n3y = 9" in to_human_math(raw_cases)


def test_row_break_outside_env():
    assert to_human_math("x^2 \\\\ y^2") == "x² \ny²"


def test_unknown_command_left_raw():
    # Comando desconocido: se deja tal cual, nunca se inventa.
    assert to_human_math("\\fakexyz{3}") == "\\fakexyz{3}"


def test_display_delimiters_removed():
    raw = "\\[ x^2 + y^2 \\]"
    assert to_human_math(raw) == " x² + y² "


def test_mixed_prose_and_math():
    raw = "Resolviendo: $x^2 = 9$, entonces $x = \\pm 3$."
    assert to_human_math(raw) == "Resolviendo: x² = 9, entonces x = ± 3."


def test_markdown_structure_survives():
    raw = "**Paso 1:** plantear $ax^2 + bx + c = 0$\n\n- factor común"
    result = to_human_math(raw)
    assert result.startswith("**Paso 1:** plantear ax² + bx + c = 0")
    assert "\n\n- factor común" in result

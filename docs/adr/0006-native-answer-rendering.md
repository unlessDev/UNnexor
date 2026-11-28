# ADR-0006 — Fórmulas y código en el visor nativo (sin motor web)

- **Estado:** aceptado
- **Fecha:** 2026-11-28 (versión 2.1.0)

## Contexto

Las respuestas de los modelos (sobre todo tras OCR o visión de ejercicios)
traen LaTeX (`\frac`, `\sqrt`, `\begin{pmatrix}`…) y bloques de código.
Hasta 2.0.0 el LaTeX se convertía a Unicode aproximado (`math_unicode.py`)
y el código se veía como texto monoespaciado sin color: las fracciones,
matrices y sistemas eran ilegibles.

Opciones evaluadas:

1. **QtWebEngine + KaTeX + highlight.js/Prism**: calidad de navegador,
   pero añade Chromium (≈300 MB sin comprimir: `Qt6WebEngineCore.dll`
   ≈195 MB + recursos ≈100 MB), procesos extra y memoria por ventana. El
   spec de PyInstaller excluye QtWebEngine a propósito y el panel
   flotante usa `WS_EX_NOACTIVATE` + Hover Scroll, que habría que
   reimplementar sobre Chromium. Requiere ejecutar JavaScript.
2. **matplotlib mathtext → imágenes**: sin matrices, `cases` ni `aligned`;
   matplotlib pesa decenas de MB y estaba excluido.
3. **ziamath (LaTeX → MathML con latex2mathml → SVG) + QtSvg**, dentro del
   `QTextBrowser` existente, y **Pygments** para el código.

## Decisión

Opción 3 (`local-bridge/app/desktop/rich_text.py`):

- `split_segments` (puro) separa código cercado, código en línea y
  fórmulas (`$…$`, `$$…$$`, `\(…\)`, `\[…\]`, entornos `\begin{…}`). El
  código se extrae **antes** de buscar fórmulas; `$5 y $10` no se toma
  como fórmula.
- El Markdown lo convierte Qt con `MarkdownNoHTML`: el HTML del modelo se
  muestra como texto. No se ejecuta JavaScript ni contenido del modelo.
- Cada fórmula se compone con ziamath (`svg2=False` para QtSvg Tiny 1.2),
  se rasteriza a 2× y se cachea (LRU). Si falla, se muestra su versión
  Unicode legible: una fórmula mal formada no rompe la respuesta.
- El código se colorea con Pygments (`github-dark`, estilos en línea),
  con cabecera de lenguaje, scroll horizontal y enlace «Copiar código»
  que copia el texto original.
- El visor solo sirve recursos `unxmath:` propios (`loadResource`): una
  imagen Markdown no puede leer archivos locales ni remotos.

## Consecuencias

- Sin Chromium: el instalador no crece cientos de MB y no hay procesos
  web; las dependencias nuevas (ziamath, ziafont, latex2mathml, Pygments)
  suman unos pocos MB y son MIT/BSD (fuente STIX Two Math: OFL).
- La cobertura de LaTeX es la de ziamath/latex2mathml (amplia para
  álgebra, cálculo y matrices); lo no soportado cae a Unicode.
- El auto-chequeo del EXE (`--check`) compone una fórmula y resalta
  Python/SQL para detectar recursos no empaquetados.
- Durante el streaming el formato se recalcula cada 140 ms; las respuestas
  de más de 24 000 caracteres se formatean solo al terminar.

# OCR local en Extdator (captura → texto/Markdown)

La captura por región (botón 📷 del panel) puede procesar la imagen de dos
formas: **visión multimodal** (un modelo como `qwen2.5vl` en Ollama) u
**OCR local** dedicado. Este documento recoge la investigación de motores
OCR hecha para elegir los integrables (consulta 2026-11-16 contra los
repositorios oficiales).

## Filosofía

* Los motores OCR son **opcionales**: Extdator no instala ninguno por
  defecto. Se añaden solo si el usuario los pide (peso y dependencias).
* `available_engines()` comprueba importabilidad **real** (lazy import):
  nunca lista un motor que no puede ejecutarse.
* No se descargan pesos sin saberlo: si un motor descarga modelos en el
  primer uso, así está documentado en la tabla.
* La instancia del motor se cachea en caliente pero se libera con
  `release_engine()` tras cada transcripción (prioridad a la RAM).

## Motores integrables (registro oficial)

| Motor | Licencia | Qué hace | Salida | Instalación | Pesos |
|---|---|---|---|---|---|
| **Pix2Text** *(elegido para matemáticas)* | MIT (código y pesos MFR) | texto + fórmulas | Markdown con LaTeX `$$…$$` | `pip install pix2text` + `pip install "rapidocr==3.9.2"` (compatibilidad, ver abajo) | MFR ~120 MB ONNX (descarga al primer uso) |
| **RapidOCR** | Apache-2.0 | texto (alta calidad, PP-OCRv6) | texto plano | `pip install rapidocr-onnxruntime` (o el paquete nuevo `rapidocr`) | modelos PP-OCR incluidos (~decenas de MB) |
| **Tesseract (pytesseract)** | Apache-2.0 | texto | texto plano | `pip install pytesseract` **+** binario del sistema | datos de idioma (~decenas de MB/idioma) |

* Pix2Text v1.1.7 (2026-08) — <https://github.com/breezedeus/Pix2Text>.
  Compatibilidad verificada: cnocr 2.3.3 / cnstd 1.2.8 funcionan con
  `rapidocr` 3.9.x; la 3.10.0 rompe los imports de `cnstd` (documentado
  en TROUBLESHOOTING). Los modelos inferen con ONNX Runtime; el paquete
  instala PyTorch como dependencia aunque la inferencia no lo use.
* RapidOCR v3.9.2 (pin) / rapidocr-onnxruntime 1.x —
  <https://github.com/RapidAI/RapidOCR>.
* Tesseract estable — <https://github.com/tesseract-ocr/tesseract>.

**Recomendación automática** (`recommend()`): si hay ≥4 GB de RAM
disponible y se busca matemática, Pix2Text; si no, RapidOCR. Las
fórmulas con RapidOCR/Tesseract quedarán como texto plano (sin LaTeX).

## Motores evaluados y descartados (y por qué)

| Motor | Licencia | Motivo del descarte |
|---|---|---|
| **Texify** | MIT | **Oficialmente deprecado**: el autor redirige a Surya. |
| **Surya / Marker** | OpenRAIL-M (pesos) | Las licencias de los pesos tienen restricciones de redistribución/comercial poco compatibles con una app redistribuible. Buen rendimiento, documentado por si cambian de licencia. |
| **Nougat (Meta)** | CC-BY-NC (pesos) | Uso no comercial; proyecto sin actividad desde 2023. |
| **pix2tex / LaTeX-OCR** | MIT | Solo fórmulas (sin texto general) y poco mantenido; Pix2Text lo cubre. |
| **Docling (IBM)** | MIT | Excelente para documentos completos, pero pesado en CPU-only para una captura interactiva. |
| **PaddleOCR** | Apache-2.0 | Muy bueno, pero arrastra el framework Paddle (instalación grande para un motor opcional). RapidOCR usa los mismos pesos PP-OCR con ONNX ligero. |

## Cómo funciona en la app

1. Captura la región (overlay por pantalla, con `devicePixelRatio`
   correcto en pantallas escaladas).
2. `ScreenshotPreviewDialog` ofrece elegir: **visión** (si el modelo
   activo la soporta) u **OCR** (si hay algún motor instalado).
3. OCR: `ocr_manager.transcribe(path)` → `OCRResult` (texto, motor,
   duración, si es Markdown).
4. El texto entra al pipeline normal (como una selección del navegador)
   con un prefijo de contexto; la imagen temporal
   (`%TEMP%\extdator_capture_*.png`) se **borra** tras procesar.
5. La respuesta llega al panel como siempre (streaming, Markdown). El
   LaTeX que devuelve el OCR (`$$…$$`) y el de la consulta se muestran
   como **matemática Unicode** legible en las burbujas (no como LaTeX
   crudo); al copiar se obtiene esa misma versión legible. Ver
   "Las fórmulas se ven como matemática Unicode" en
   [TROUBLESHOOTING](TROUBLESHOOTING.md).

## Estado actual (verificado en el equipo de desarrollo, 2026-11-16)

* **Pix2Text instalado y probado E2E real** sobre el registro de
  Extdator: `scripts/ocr_e2e.py` generó una imagen con tres fórmulas y
  `transcribe()` devolvió Markdown con tres bloques `$$…$$`
  (`\mathrm{x}^{\Lambda} 2+\mathrm{y}^{\Lambda} 2=2 5`, etc.),
  `markdown=True`, instancia liberada y temporal borrada. Primera
  ejecución: 54 s (incluye la descarga de pesos; siguientes usos son
  rápidos y offline).
* **Advertencia honesta sobre la imagen de prueba**: contiene los
  acentos circunflejos *escritos* (`^`), que el OCR lee como `Λ`; en
  capturas reales de fórmulas **tipografiadas** (superíndices elevados,
  fracciones reales) el modelo MFR rinde como está diseñado. El modelo
  de visión (qwen2.5vl) leyó esa misma imagen perfecta.
* Compatibilidad: pix2text 1.1.7 + cnocr 2.3.3 + cnstd 1.2.8 exigen
  `rapidocr>=3.9.1` pero **no** la 3.10.0 (renombró
  `resolve_model_key`). Con el instalador por defecto se resuelve 3.10:
  fijar `rapidocr==3.9.2` (ver TROUBLESHOOTING).
* Pix2Text arrastra un paquete top-level llamado `scripts` en
  site-packages: cualquier proyecto con su propio directorio `scripts/`
  debe importar por ruta de archivo, no como paquete (colisión de
  nombres; no afecta al código de Extdator, que usa `app.*`).
* Suite completa: **222 passed, 1 skipped** (2026-11-19; el test real de
  Pix2Text corre con el motor instalado; queda 1 skip:
  `rapidocr_onnxruntime` clásico no instalado — el paquete nuevo `rapidocr`
  sí lo está).
* Las consultas OCR con LaTeX y las respuestas matemáticas del modelo se
  verificaron E2E: `qwen3:8b` devolvió la fórmula cuadrática en LaTeX y
  el panel la mostró como `x = (-b ± √(b² - 4ac))/(2a)`.
* Para fórmulas matemáticas de verdad hace falta **Pix2Text**; un modelo
  con visión no es necesario en ese camino.

## Referencia técnica

* Registro y API: `local-bridge/app/ocr_manager.py`
  (`catalog()`, `available_engines()`, `recommend()`, `transcribe()`,
  `release_engine()`).
* Captura y preview: `local-bridge/app/desktop/screenshot.py`.
* Pruebas: `local-bridge/tests/test_ocr_manager.py`.

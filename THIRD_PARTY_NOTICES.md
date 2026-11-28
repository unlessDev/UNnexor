# Avisos de terceros

> Inventario inicial. El SBOM completo y los textos de licencia se generan
> en la fase de seguridad/licencias. Este documento no sustituye los textos
> legales originales.

UNnexor no tiene todavía una licencia principal aprobada. Las licencias de
los componentes de terceros se conservan y se listan aquí.

## Dependencias directas de ejecución

| Componente | Versión de referencia | Licencia | Uso |
|---|---|---|---|
| PySide6 / Qt 6 | 6.12.x | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | Interfaz de escritorio y plugins Qt |
| FastAPI | 0.162.x | MIT | API local |
| uvicorn | 0.54.x | BSD-3-Clause | Servidor ASGI |
| httpx | 0.28.x | BSD-3-Clause | Cliente HTTP/streaming |
| Pydantic | 2.14.x | MIT | Validación de contratos |
| python-dotenv | 1.2.x | BSD-3-Clause | Lectura de `.env` |
| psutil | 7.x | BSD-3-Clause | Detección de hardware |
| keyring | 25.x | MIT | Windows Credential Manager |
| NumPy | 2.x | BSD-3-Clause y componentes permisivos | Audio del copiloto (DSP, VAD) |
| SoundCard | 0.4.x | BSD-3-Clause | Captura WASAPI (loopback del sistema y micrófono) |
| cffi / pycparser | 2.x / 3.x | MIT-0 / BSD-3-Clause | Dependencias nativas de SoundCard |
| faster-whisper | 1.2.x | MIT | Reconocimiento de voz local; incluye el modelo Silero VAD v6 (MIT) |
| CTranslate2 | 4.9.x | MIT | Motor de inferencia de Whisper |
| PyAV | 19.x | BSD-3-Clause; sus ruedas incluyen DLL de FFmpeg (LGPL-2.1+), libiconv (LGPL) y libgcc (GPL con excepción de runtime) | Dependencia de faster-whisper (decodificación de archivos) |
| ONNX Runtime | 1.32.x | MIT | Silero VAD |
| pypdf | 6.x | BSD-3-Clause | Texto de PDF para la base de conocimiento (PyMuPDF se descarta por AGPL) |
| tokenizers / huggingface_hub | 0.22.x / 0.36.x | Apache-2.0 | Tokenizador de Whisper y descarga consentida de modelos |
| ziamath / ziafont | 0.13 / 0.11 | MIT | Composición de fórmulas (LaTeX → SVG) en las respuestas |
| latex2mathml | 3.81.x | MIT | Conversión LaTeX → MathML usada por ziamath |
| STIX Two Math (fuente incluida en ziamath) | 2.x | SIL Open Font License 1.1 | Glifos matemáticos de las fórmulas |
| Pygments | 2.21.x | BSD-2-Clause | Resaltado de sintaxis de los bloques de código |

Modelos descargados por el usuario (no se distribuyen con la aplicación):
pesos de Whisper convertidos por SYSTRAN (`Systran/faster-whisper-*`, MIT) y
`mobiuslabsgmbh/faster-whisper-large-v3-turbo` (MIT). Pendiente para la
auditoría de licencias: confirmar la configuración LGPL de las DLL de
FFmpeg que incluye PyAV antes de redistribuir el ejecutable.

## Dependencias opcionales (no se instalan ni empaquetan por defecto)

| Componente | Versión probada | Licencia | Uso |
|---|---|---|---|
| Pix2Text | 1.1.7 | MIT | OCR de fórmulas |
| RapidOCR | 3.9.2 (pin) | Apache-2.0 | OCR de texto |
| ONNX Runtime | 1.32.x | MIT | Inferencia OCR |
| PyTorch | 2.15.x | BSD-style + componentes con licencias permisivas | Dependencia de Pix2Text |
| NumPy | 2.5.x | BSD-3-Clause y componentes permisivos | Procesamiento numérico |
| Pillow | 13.x | MIT-CMU | Imágenes |
| Tesseract / pytesseract | sistema / opcional | Apache-2.0 | OCR de texto |

## Herramientas de desarrollo y build (no se distribuyen como parte de la app)

| Componente | Licencia | Uso |
|---|---|---|
| pytest | MIT | Pruebas |
| Black | MIT | Formato |
| PyInstaller | GPL-2.0-or-later con excepción de empaquetado | Build del ejecutable |
| Inno Setup | Inno Setup License (freeware, fuente disponible) | Instalador Windows |

## Obligaciones relevantes

- **Qt/PySide6 (LGPL-3.0)**: se distribuye enlazado dinámicamente en modo
  one-folder. Se deben incluir los avisos, el texto LGPL y permitir la
  sustitución de las bibliotecas Qt. No se modifican los binarios de Qt.
- **PyInstaller**: el ejecutable generado no queda cubierto por la GPL de
  PyInstaller gracias a su excepción, pero se debe conservar el aviso.
- **Pix2Text/OCR**: si el usuario instala motores opcionales, sus pesos y
  licencias son responsabilidad de esa instalación; no se empaquetan.
- **Tesseract**: requiere el binario del sistema y datos de idioma; no se
  incluye sin consentimiento.

## Enlaces de licencia

- Qt/PySide6: <https://www.qt.io/licensing/open-source-lgpl-obligations>
- FastAPI: <https://github.com/fastapi/fastapi/blob/master/LICENSE>
- uvicorn: <https://github.com/encode/uvicorn/blob/master/LICENSE.md>
- httpx: <https://github.com/encode/httpx/blob/master/LICENSE.md>
- Pydantic: <https://github.com/pydantic/pydantic/blob/main/LICENSE>
- psutil: <https://github.com/giampaolo/psutil/blob/master/LICENSE>
- keyring: <https://github.com/jaraco/keyring/blob/main/LICENSE>
- Pix2Text: <https://github.com/breezedeus/Pix2Text/blob/master/LICENSE>
- RapidOCR: <https://github.com/RapidAI/RapidOCR/blob/main/LICENSE>
- PyInstaller: <https://pyinstaller.org/en/stable/license.html>
- Inno Setup: <https://jrsoftware.org/isinfo.php>

La lista completa de dependencias transitivas se generará en el SBOM y se
revisará antes de cualquier release público.

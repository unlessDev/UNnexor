# Changelog

Todos los cambios relevantes se registran aquí. El formato sigue
[Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/) y el proyecto
usa [Semantic Versioning](https://semver.org/lang/es/).

## [Unreleased]

### Architecture
- Documento canónico `ARCHITECTURE.md` en la raíz, con módulos, capas,
  contratos, flujos, seguridad y límites reales del proyecto.
- ADR-0001: monolito modular con límites Clean/Hexagonal pragmáticos.
- ADR-0002: se mantiene HTTP local autenticado; Native Messaging queda
  como alternativa evaluada, no como migración inmediata.
- ADR-0003: PyInstaller + Inno Setup como estrategia de distribución.
- ADR-0004: licencia principal pendiente de aprobación del propietario.
- `app/paths.py` separa rutas de desarrollo e instalación
  (`%LOCALAPPDATA%\Extdator`), con overrides para tests/portable.
- El detector de hardware depende de un puerto (`RunningModelsSource`) y
  no del cliente HTTP concreto.
- Pruebas de arquitectura para límites de imports y ausencia de código
  remoto/eval en la extensión.
- ADR-0005: arquitectura del copiloto en tiempo real (paquetes `audio`,
  `stt`, `copilot`, `knowledge`, `routing`, concurrencia, enrutamiento y
  privacidad; sin exclusión de captura ni mecanismos de ocultación).
  Avance verificable en `docs/COPILOT_STATUS.md`.

### Copilot
- Paquete `app/audio`: captura WASAPI del audio del sistema (loopback) y
  del micrófono con SoundCard, en hilos propios con COM por hilo, pausa
  sin cerrar el dispositivo, reconexión exponencial y liberación
  garantizada; remuestreo en streaming a 16 kHz mono; VAD Silero v6 (ONNX
  incluido en faster-whisper, sin descargas) con respaldo por energía;
  segmentador con intervenciones provisionales/finales, pre-roll, corte de
  monólogos y descarte de ruidos; `AudioPipeline` para varias fuentes a la
  vez. Validado con una locución TTS reproducida y capturada en loopback.
- `paths.models_dir()` (`%LOCALAPPDATA%\Extdator\models`) para modelos
  descargados con consentimiento y `paths.copilot_dir()` para sus datos.
- Paquete `app/stt`: reconocimiento de voz local con faster-whisper (CPU
  int8 o CUDA, modelos tiny → large-v3, descarga solo con consentimiento) y
  remoto por fragmentos con Groq/OpenAI (`/audio/transcriptions`) y
  Deepgram (`/v1/listen`). Catálogo con modalidad real, acceso y costes
  verificados; filtro de alucinaciones de Whisper; recomendación según el
  equipo con latencia medida. `docs/SPEECH.md` documenta motores, costes y
  mediciones.
- `TranscriptionService`: texto provisional y confirmado en tiempo real con
  prioridad a los finales, reintentos ante límites de uso y errores
  explícitos sin cambios silenciosos de motor.
- Medido en el equipo de referencia (Ryzen 5 2600X, sin CUDA): Whisper base
  1,03 s y small 3,48 s por intervención; Groq turbo 0,47 s; los tres
  transcriben exacto el español de prueba. Prueba en vivo: captura loopback
  + Whisper base con 1,3–2,0 s de retraso por intervención.

### Fixed
- `--server-only` no podía funcionar: pasaba un `uvicorn.Config` a
  `uvicorn.run()` (que espera una app ASGI), así que arrancaba en
  `127.0.0.1:8000` con una "aplicación" no invocable e ignoraba
  `BRIDGE_PORT`. Ahora usa `uvicorn.Server(config).run()`.
- `to_human_math` lanzaba `TypeError` cuando una fórmula tenía más de 16
  envoltorios (`\text{…}`, `\mathrm{…}`…): el desenvoltorio devolvía
  `None` al agotar las iteraciones.
- Una prueba de texto Unicode en `/api/selection` estaba sombreada por otra
  función con el mismo nombre y nunca se ejecutaba; se renombra y ejecuta.
- `app/__init__.py` declaraba `1.0.0` mientras el manifest declaraba
  `1.2.0`; la versión queda unificada en `1.2.0`.
- El auto-chequeo y el smoke GUI fallaban en equipos sin Ollama porque
  `/health` puede tardar hasta 2 s en sondearlo y los clientes cortaban a
  1,5 s. Ahora esperan 5 s por sondeo (30 s en total en el auto-chequeo)
  para tolerar arranques fríos. Los smoke tests del escritorio adjuntan el
  final de `bridge.log` a los errores para facilitar el diagnóstico.

### Added
- Auditoría inicial con línea base de pruebas y riesgos conocidos.
- Higiene de repositorio: `.editorconfig`, `.gitattributes` y `.gitignore`
  ampliado para datos de usuario, artefactos y credenciales.
- `privacy.html` empaquetado con la política de privacidad de la extensión.
- Script `deployment/extension/build.py` con validación de Manifest V3,
  permisos, CSP, iconos, código remoto y sintaxis JavaScript; genera ZIP
  reproducibles para Chrome y Edge con `SHA256SUMS.txt` y `build-report.json`.
- Guías de tienda, justificación de permisos y matriz de cumplimiento en
  `docs/stores/`.
- Pruebas del paquete de extensión (validación, generación y reproducibilidad).
- Ejecutable one-folder de Windows con PyInstaller: `deployment/windows/build.py`,
  spec, icono `.ico`, recurso de versión generado y supresión controlada de
  módulos Qt/OCR no usados.
- Auto-chequeo `--check` con informe `self-check.json` en el directorio de
  datos, útil sin consola y para validar el build.
- Prueba de humo del EXE y arranque GUI con `QT_QPA_PLATFORM=offscreen`.
- `requirements-build.txt` para PyInstaller y Pillow (herramientas, no runtime).
- Instalador Windows por usuario con Inno Setup 6 en `deployment/installer/`:
  instalación sin administrador, accesos directos, desinstalador,
  preservación de datos y limpieza completa opcional.
- `Extdator.exe --purge-secrets`: elimina las claves de proveedores del
  keyring, vacía `BRIDGE_TOKEN` y retira `*_API_KEY` del `.env` sin tocar
  ajustes no sensibles.
- `deployment/installer/build.py` compila el instalador y prueba el ciclo
  completo (instalación silenciosa, self-check, preservación, purga y
  desinstalación) antes de generar SHA-256 e informe.
- CI de Windows en `.github/workflows/ci.yml`: pruebas y empaquetado
  completo (extensión, escritorio, instalador y SBOM) en push/PR a `main`
  y `develop`, con permisos de solo lectura y sin publicación.
- `deployment/sbom/build.py`: SBOM CycloneDX 1.5 del entorno Python y los
  productos, con `SHA256SUMS.txt` e identidad determinista.
- `deployment/verify_artifacts.py`: verificación de checksums, informes de
  build y SBOM, con protección contra rutas inseguras y symlinks; genera
  `release/verification-report.json`.
- Pruebas del SBOM y del verificador, incluidas manipulación de artefactos,
  hashes incorrectos, rutas inseguras y licencia pendiente.
- Documentación de la cadena en `docs/CI.md`.
- Asistente de primer inicio del escritorio en tres pasos (bienvenida y
  privacidad, requisitos de Ollama/modelo y emparejamiento por código).
  Comprueba Ollama en segundo plano sin bloquear la UI, permite reintentar,
  abrir el selector de modelos o Ajustes, detecta la vinculación y se puede
  reabrir desde el menú ⚙. No se muestra en pruebas `offscreen`.
- `PairingManager.is_confirmed()` para que la interfaz detecte que la
  extensión completó el emparejamiento sin volver a exponer el token.
- Exportador de diagnóstico desde ⚙: ZIP con resumen legible y JSON,
  comprobaciones locales, estado de Ollama/modelo, emparejamiento (solo
  sí/no) y logs redactados. No incluye token, claves API, `.env`, historial
  ni capturas; los logs se limitan y se sanean antes de escribirse.

### Changed
- Calidad: `local-bridge/pyproject.toml` configura Black, Ruff y Mypy;
  `requirements-dev.txt` instala Ruff y Mypy; la CI ejecuta formato, lint
  y tipos antes de las pruebas. Mypy queda sin errores en `app/` (tipos de
  Qt, enums cualificados y anotaciones corregidas sin cambiar
  comportamiento) y es estricto para los paquetes nuevos del copiloto.
- `docs/ARCHITECTURE.md` se traslada a `ARCHITECTURE.md` (raíz) para que
  sea el documento canónico; se actualizan los enlaces.
- La extensión deja de solicitar `scripting` y permisos de host para webs
  arbitrarias; solo conserva `storage` y acceso a `127.0.0.1`/`localhost`.
- El primer arranque de la extensión sin vinculación abre el asistente de
  opciones.
- Los mensajes de extensión y opciones evitan rutas de desarrollo y
  terminología técnica innecesaria.
- La conexión manual solo acepta direcciones locales (`127.0.0.1` o
  `localhost`).

### Distribution
- Paquetes `chrome.zip` y `edge.zip` generados y validados localmente;
  pendientes de cuenta de tienda, URL pública de privacidad, capturas y
  autorización.
- Ejecutable `Extdator.exe` one-folder (≈79 MB) y ZIP sin firma (≈36 MB)
  generados y verificados con auto-chequeo y GUI offscreen. El OCR local
  pesado no se incluye en este build; la visión del modelo sigue disponible.
- Instalador `Extdator-1.2.0-win64-setup.exe` (≈29 MB, sin firma) generado
  y probado localmente: instalación por usuario, datos preservados al
  desinstalar, purga de secretos y limpieza completa. No se ha publicado.

### Tests
- Línea base: `202 passed, 1 skipped` (2026-11-19).
- Tras rutas de runtime y pruebas de arquitectura: `212 passed, 1 skipped`.
- Tras extensión y empaquetado: `224 passed, 1 skipped` (2026-11-19).
- Tras purga de secretos e instalador: `231 passed, 1 skipped` (2026-11-20).
- Tras CI, SBOM y verificación de artefactos: `241 passed, 1 skipped`
  (2026-11-21).
- Tras tolerancia de arranque en frío y diagnósticos: `246 passed,
  1 skipped` (2026-11-23).
- Tras el asistente de primer inicio del escritorio: `262 passed,
  1 skipped` (2026-11-23).
- Tras el diagnóstico exportable: `272 passed, 1 skipped` (2026-11-24).
- Tras Ruff/Mypy y las regresiones de `--server-only`, matemática y
  redimensionado del panel: `278 passed, 1 skipped` (2026-11-24).

## [1.2.0] - 2026-11-19

### Added
- Selector de modelos con evaluación según hardware.
- Proveedores remotos OpenAI-compatible con claves en keyring.
- Configuración desde la UI, emparejamiento por código, captura por región.
- OCR opcional (Pix2Text/RapidOCR/Tesseract) y visión multimodal.
- Conversión de LaTeX a matemática Unicode legible y corrección de altura.

### Security
- Token de puente, validación de origen, límites y logs sin contenido.
- Claves de proveedores fuera de archivos y de la extensión.

[Unreleased]: https://github.com/unlessDev/Extdator/compare/v1.2.0...HEAD
[1.2.0]: https://github.com/unlessDev/Extdator/releases/tag/v1.2.0

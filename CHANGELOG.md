# Changelog

Todos los cambios relevantes se registran aquí. El formato sigue
[Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/) y el proyecto
usa [Semantic Versioning](https://semver.org/lang/es/).

## [Unreleased]

### UNnexor — nueva identidad
- **Extdator pasa a llamarse UNnexor** (UN = UnlessDev; *nexor* = indexación).
  Nombre visible actualizado en la aplicación, la extensión, ventanas,
  menús, mensajes, scripts, instalador (`UNnexor.exe`,
  `UNnexor-<versión>-win64-setup.exe`), SBOM y documentación.
  Se conservan a propósito los identificadores técnicos que garantizan la
  compatibilidad (ver `docs/BRANDING.md`): carpeta de datos
  `%LOCALAPPDATA%\Extdator`, servicio del keyring, claves de `QSettings`,
  variables `EXTDATOR_*`, el identificador `extdator-bridge` de `/health`
  y los mensajes internos `extdator:*` de la extensión.
- **Logotipo vectorial creado desde cero**: `branding/unnexor-x.svg`
  reproduce la X de la imagen de referencia (rectas medidas sobre ella,
  mismo cruce, proporciones y chaflanes; sin los destellos de las esquinas,
  sin filtros ni imágenes incrustadas). `scripts/make_brand_assets.py`
  genera desde esa única geometría el icono cuadrado, la insignia con
  contenedor redondeado, PNG de 16 a 1024 px, el ICO multirresolución del
  ejecutable y los iconos de la extensión. La imagen original de
  `newDesign/` se conserva en `branding/source/`.
- La X sustituye al icono de Python en ventanas y barra de tareas
  (`AppUserModelID` propio) y aparece en la barra de título del panel.

### Added
- **Extensión: dos opciones al pulsar el botón** de la selección:
  *Enviar directamente* (comportamiento de siempre) o *Consultar…*, un
  panel pequeño para escribir una pregunta sobre el texto seleccionado
  (Ctrl+Enter envía, Esc cancela; conserva la selección, informa del
  progreso y de los errores sin cerrar el panel). El puente acepta el campo
  opcional `question` en `POST /api/selection` y responde
  `question_received` para detectar puentes antiguos.
- **Presentación ASCII "UNnexor · AI BRIDGE · by UnlessDev"** con degradado
  de color en `start.ps1` y en la terminal del puente (`app/banner.py`, una
  sola implementación; sin colores si la consola no admite ANSI o con
  `NO_COLOR`).
- **Consola del puente opcional** (⚙ → *Consola del puente*): registros en
  vivo —peticiones HTTP, estado de Ollama, errores— con filtro, copiar y
  "Mostrar al iniciar". `start.ps1` arranca ahora el puente sin ventana de
  terminal (sin icono en la barra de tareas); `-Console` recupera la
  terminal visible.
- **Arranque de Ollama con propiedad explícita** (`app/ollama_service.py`):
  si Ollama no está en ejecución y `OLLAMA_AUTOSTART=1` (por defecto),
  UNnexor lanza `ollama serve` sin ventana y al cerrarse detiene **solo ese
  proceso**; un Ollama que ya estaba abierto nunca se reinicia ni se cierra.

- **Control del tiempo de escucha del copiloto**: además de la escucha
  continua, modo *temporizado* (10 s–10 min, con valores rápidos) y *manual*
  (el atajo inicia y termina). Lo escuchado en la ventana se envía junto al
  modelo para que identifique la pregunta aunque el interlocutor hable mucho
  tiempo. Fuera de la ventana la captura queda en pausa (sin acumular
  audio), no se activa el micrófono por su cuenta, hay cancelación e
  indicador visual con cuenta atrás. Atajos configurables con detección de
  conflictos (repetidos o reservados por Windows). Validado con audio real:
  `scripts/listen_window_e2e.py`.
- **«Siempre visible» por ventana**: el 📌 del panel principal, el del panel
  flotante y el interruptor de la ventana del copiloto aplican el estado
  real del sistema (`SetWindowPos` sobre el mismo HWND) y se guardan por
  separado.

- **Documentos del perfil**: explicación visible de que los formatos
  recomendados son Markdown (.md) y texto (.txt) y de cómo se usan (se
  recuperan fragmentos como contexto en cada respuesta; el modelo no los
  "aprende"). Los formatos no admitidos se rechazan con un mensaje; PDF y
  Word siguen disponibles solo tras confirmar un aviso (extracción
  experimental). Validación de vacíos, binarios y archivos de más de 5 MB.
- **Catálogo en línea de Ollama** (`app/ollama_library.py`): al iniciar (y
  con «🔄 Actualizar catálogo») se consulta `ollama.com/library` y la última
  versión de Ollama en GitHub, solo lectura. *Modelos de IA* muestra una
  sección «Biblioteca de Ollama» con modelos nuevos (🆕), capacidades y
  tamaños **estimados** evaluados contra el equipo; sin conexión se usa la
  última copia válida. Avisa si hay una versión más reciente de Ollama (no la
  actualiza). Nunca descarga nada sin una acción explícita. Desactivable con
  `ONLINE_CATALOG=0` o en Ajustes → Ollama.
- **Recomendar configuración** (⚙ del panel y página Inicio del copiloto):
  detecta CPU (modelo, arquitectura, núcleos/hilos, AVX), RAM
  (total/disponible/usada/reservable), GPU (fabricante, VRAM, tecnología
  compatible, controladores presentes, uso real en la inferencia), disco y
  sistema, indicando lo que no se pudo detectar. Con tres perfiles (menor,
  medio y alto consumo) propone por separado modelo/motor para texto y
  navegador, conversación, traducción, visión, voz, OCR y documentos, con
  requisitos, velocidad, calidad, compatibilidad (medida/estimada/
  desconocida), alternativa más ligera, descarga necesaria y parámetros de
  generación por función (temperature, top_p, top_k, repeat_penalty,
  num_ctx, think). Nunca recomienda modelos EXIGENTES ni NO COMPATIBLES; en
  alto consumo prefiere reducir el contexto antes que el modelo. Aplicar
  muestra un resumen, solo aplica modelos instalados, guarda una copia y
  permite restaurar la configuración anterior. El copiloto respeta la
  temperatura por tarea.

### Fixed
- El botón 📌 no funcionaba de forma fiable: arrancaba siempre marcado sin
  mirar el estado real, su estilo de "activado" nunca se aplicaba y al
  pulsarlo Qt recreaba la ventana (perdiendo los estilos de barra de
  tareas y el destino del Hover Scroll).
- El diálogo de Ajustes mostraba todas las casillas desmarcadas: al
  guardar se desactivaban opciones activas como "Siempre visible" o la
  privacidad de los registros.

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
- Catálogo unificado de proveedores: nivel de acceso estructurado (local,
  gratuito con límites, créditos, mixto, pago por consumo, desconocido),
  capacidades, protocolo y fecha de verificación; estado honesto de la
  conexión ("clave válida · acceso sin confirmar"). El diálogo de
  proveedores agrupa por nivel de acceso automáticamente.
- Adaptador nativo de Anthropic (Messages API) y Deepgram como proveedor
  solo de voz (validación con `GET /v1/projects`).
- Consentimientos explícitos por proveedor (`remote_data`, `costs`,
  `documents`) e historial local de uso con límites informados por el
  proveedor, sin contenido.
- Model Router (`app/routing`): tareas, estrategias, política solo-local por
  defecto, descarte explicado, alternativas sin cambios silenciosos a
  remoto y ejecutor en streaming. Validado con una petición real a Groq.
- Perfiles de contexto y base de conocimiento (`app/knowledge`): TXT,
  Markdown, PDF (pypdf) y DOCX con validación y límites; notas manuales;
  fragmentos con fuente; BM25 siempre disponible y embeddings locales
  opcionales con Ollama fusionados por RRF; contexto con fuentes citables.
  Probado con `nomic-embed-text` real: las preguntas sin palabras en común
  ("¿Qué estudiaste?") encuentran el documento correcto.
- Asistencia en conversaciones (núcleo, sin interfaz todavía): detección de
  preguntas y peticiones en español/inglés aunque Whisper omita "¿?",
  turnos cerrados por pausa (no se responde por fragmento), sin duplicados,
  prompts con perfil, documentos citados y conversación reciente, regla de
  no inventar experiencias (`[completar: …]`), otra respuesta, ampliar,
  preguntas manuales, cancelación. Los datos personales solo van a modelos
  locales salvo permiso explícito del perfil.
- `OllamaClient.preload()` y `RoutedEngine.warm_up()`: precarga del modelo
  local al iniciar la sesión.
- Prueba real (`scripts/copilot_e2e.py`): pregunta en voz TTS → loopback →
  Whisper base → qwen3:8b local (RX 590 por Vulkan): pregunta detectada
  2,3 s después de terminar el audio; primer token 0,26 s con el modelo
  cargado y 2,8 s tras una precarga en frío.
- Traducción en tiempo real (núcleo): segmentos provisionales nunca
  traducidos ni presentados como definitivos, confirmados traducidos en
  orden con contexto, confianza baja marcada, exportación TXT/Markdown y
  resumen. Real: dos frases es→en correctas en 2,0 s con un modelo local.
- Desafíos en pantalla (núcleo): visión del modelo o, si no hay, OCR +
  modelo de texto; respuesta estructurada (problema, consigna, enfoque,
  plan, pasos, código, explicación, resultado, advertencias, alternativas);
  detector de cambios por hash perceptual para el análisis periódico. Real:
  qwen2.5vl:7b resolvió un sistema de ecuaciones desde una imagen.
- Interfaz del copiloto (`app/desktop/copilot`): ventana con Inicio
  (configuración guiada), Conversación, Traducción, Desafíos, Perfiles y
  documentos, Motores y modelos (motor de voz y descarga consentida,
  estrategia, modelos por tarea, permisos por servicio, uso) y Privacidad;
  panel flotante siempre visible sin robar el foco; atajos globales
  configurables; botón 🧭 y entrada de menú en el panel principal.
  Validada en Windows real: sesión iniciada desde la página, pregunta TTS
  por altavoces → sugerencia de qwen3:8b en el panel flotante.
- Compatibilidad (etapa 8): clasificación pública en cinco clases
  (Óptimo, Compatible, Exigente, No compatible, No evaluado — antes, sin
  datos se mostraba "Regular"); detección de backends (CUDA, Vulkan,
  HIP/ROCm, AVX2/AVX-512) que decide si la VRAM de una GPU cuenta;
  evaluación de la carga combinada voz + respuesta + visión + OCR; y
  mediciones opcionales con consentimiento desde "Motores y modelos". En
  el equipo de referencia: Whisper + qwen3:8b → Óptimo; añadir
  qwen2.5vl:7b → Exigente (Ollama alterna modelos en la VRAM).
  Mediciones reales con voz TTS: Whisper base 1,42 s y 217 MB, small
  4,94 s y 582 MB por intervención de ~6,5 s.
- Router: desempate por especialización (modelos de visión o de código
  penalizados en tareas de texto; los de código preferidos en desafíos).

### Fixed
- **Falta de memoria en Ollama (Vulkan/CUDA)**: el copiloto precargaba su
  modelo durante 15 minutos y, junto al modelo del panel y la VRAM usada
  por otras aplicaciones, la GPU de 8 GB se quedaba sin memoria (HTTP 500
  "out-of-memory" o "connection forcibly closed"). Ahora se respeta el
  `keep_alive` configurado y, ante un error de memoria antes de emitir
  texto, se descargan los otros modelos y se reintenta una vez; si persiste
  se muestra un mensaje con acciones concretas. Las capturas para modelos
  con visión se limitan a 1280 px / 1,2 MP (los búferes de imagen agotaban
  la VRAM).
- **Bucles en el reconocimiento de voz** ("el informe del informe del
  informe…"): causa medida — el texto ya confirmado se enviaba como prompt a
  Whisper y, con fragmentos casi sin voz, el modelo lo repetía (además
  tardaba ~7 s re-decodificando). Ahora solo se envía el glosario, se usan
  parámetros antibucle (`no_repeat_ngram_size`, `repetition_penalty`,
  umbrales de compresión/probabilidad), los fragmentos de menos de −50 dBFS
  no se transcriben y las repeticiones residuales se colapsan.
- **Las respuestas desaparecían**: el panel flotante y la sección Desafíos
  borraban el contenido anterior en cada respuesta. Ahora conservan el
  historial (con botón para vaciarlo) y no interrumpen la lectura; la
  sección Conversación ya no salta a la pregunta nueva si estás leyendo
  otra.
- **No se podían agrandar ni reducir** el panel principal ni el flotante: el
  contenido cubría los bordes. `desktop/frameless.py` permite redimensionar
  desde cualquier borde con el redimensionado nativo del sistema.
- **Minimizar hacía desaparecer la ventana**: `WS_EX_NOACTIVATE` sin
  `WS_EX_APPWINDOW` la sacaba de la barra de tareas. Ambos paneles tienen
  ahora botón en la barra de tareas y el flotante, botón de minimizar.
- Si una edición no incluyera los componentes del copiloto, el botón 🧭
  muestra un aviso en lugar de cerrar la aplicación.
- Las burbujas de respuesta programaban su repintado con temporizadores no
  ligados al widget: si se borraban mientras llegaba texto, Qt lanzaba
  "objeto C++ ya eliminado". Los temporizadores usan ahora el widget como
  contexto.
- La prueba opcional de Pix2Text creaba un `QPixmap` sin `QApplication`:
  ejecutada sola abortaba el intérprete (código 127). Ahora usa `qapp`.
- Proveedores OpenAI-compatible: con un error HTTP no tabulado (p. ej. 400)
  durante el streaming, el cuerpo no se leía y se mostraba un error interno
  de httpx (`ResponseNotRead`) en lugar del mensaje del proveedor.
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
- El ejecutable y el instalador incluyen ya el copiloto en tiempo real:
  carpeta 302 MB (antes ≈79 MB), ZIP 121 MB, instalador 84,5 MB (antes
  ≈29 MB). El smoke test del build exige que el auto-chequeo del EXE
  confirme los componentes del copiloto: Silero VAD ejecutado en ONNX
  Runtime, CTranslate2 con int8 y, si hay un modelo Whisper descargado,
  una transcripción real (0,94 s con base en el equipo de referencia). Se
  excluyen SciPy, pandas y otros paquetes que arrastraba el entorno.
  Instalador probado en su ciclo completo. Sin firma Authenticode.
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

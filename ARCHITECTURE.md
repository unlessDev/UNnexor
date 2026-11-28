# Arquitectura de UNnexor

> Documento canónico. Se actualiza en cada fase; `docs/adr/` recoge las
> decisiones que no son evidentes a partir del código.

## 1. Objetivo y estado

UNnexor es un asistente local para el navegador. El usuario selecciona
texto, pulsa un botón contextual y la respuesta del modelo aparece en un
panel flotante siempre visible, sin perder el foco de la página.

El proyecto es un **monolito modular** con cuatro superficies:

| Superficie | Estado | Tecnología |
|---|---|---|
| Extensión de navegador | Implementada | Manifest V3, JavaScript sin build |
| Puente HTTP local | Implementado | Python 3.10+, FastAPI, uvicorn |
| Aplicación de escritorio | Implementada | PySide6/Qt 6, hilos Qt |
| Distribución | Empaquetada y probada localmente | PyInstaller + Inno Setup 6 |

La arquitectura sigue un enfoque pragmático de Clean/Hexagonal: las reglas
de negocio no conocen Qt, HTTP ni proveedores concretos; las integraciones
se encapsulan en adaptadores. No se ha hecho una extracción física completa
de `domain/application/infrastructure` porque el tamaño actual no lo exige
y el coste de regresión sería mayor que el beneficio. Los límites se
refuerzan con contratos, pruebas y revisiones.

## 2. Vista de contenedores

```text
┌──────────────────────────────────────────────────────────────┐
│ Navegador (Chrome / Brave / Edge)                            │
│  content.js ── mensaje interno ──▶ background.js             │
└──────────────────────────────┬───────────────────────────────┘
                               │ HTTP POST 127.0.0.1:5000
                               │ Authorization: Bearer
┌──────────────────────────────▼───────────────────────────────┐
│ Puente local (Python)                                        │
│  FastAPI ── autenticación/origen/límites ──▶ Bus FIFO        │
│  GenerationWorker (QThread) ◀── cola                          │
│       ├── OllamaClient (HTTP NDJSON)                          │
│       └── OpenAICompatClient (HTTPS SSE)                      │
│  BridgeWindow (Qt) ◀── señales ── worker                      │
└──────────────────────────────┬───────────────────────────────┘
                               │
              ┌────────────────┴────────────────┐
              ▼                                 ▼
      Ollama local (11434)             Proveedor remoto (opcional)
```

El servidor y la GUI viven en el **mismo proceso** (uvicorn en un hilo
daemon, Qt en el hilo principal, generación en un `QThread`). No hay
microservicios ni contenedores: para una aplicación de escritorio local
añadirían complejidad de instalación sin beneficio.

## 3. Capas y reglas de dependencia

| Capa conceptual | Responsabilidad | Ubicación actual |
|---|---|---|
| Presentación | Extensión, panel Qt, diálogos | `browser-extension/`, `local-bridge/app/desktop/` |
| Aplicación | Casos de uso, cola, worker, ciclo de generación | `queue_manager.py`, `desktop/workers.py`, `main.py` |
| Dominio | Conversación, selección, configuración validada, catálogo/evaluación | `conversation.py`, `config.py`, `model_catalog.py`, `model_evaluator.py`, `math_unicode.py` |
| Infraestructura | HTTP, Ollama, proveedores, OCR, hardware, keyring, persistencia, Win32 | `server.py`, `ollama_client.py`, `providers/`, `ocr_manager.py`, `hardware.py`, `settings_store.py`, `security.py`, `desktop/window_manager.py` |

Reglas:

1. La presentación no habla directamente con Ollama ni con proveedores:
   publica eventos en la cola y recibe señales.
2. El servidor no ejecuta generación: valida y encola.
3. Los adaptadores traducen errores de infraestructura a mensajes de
   usuario; las vistas no interpretan códigos HTTP.
4. La configuración se valida antes de construir el estado de ejecución.
5. Los secretos nunca atraviesan la capa de presentación de la extensión:
   las claves de proveedores se capturan y guardan en el keyring desde Qt.
6. Un cambio de contrato entre extension y puente exige actualizar pruebas.

## 4. Módulos y responsabilidades

### Extensión (`browser-extension/`)

| Archivo | Responsabilidad |
|---|---|
| `manifest.json` | MV3, permisos mínimos (`storage` + loopback), CSP explícita, content scripts, service worker, iconos. |
| `content.js` | Detección de selección, botón en Shadow DOM cerrado, toast; única UI inyectada. |
| `background.js` | Único punto de red de la extensión; configuración en `chrome.storage.local`; mapeo de errores de usuario. |
| `options.html/js` | Asistente de conexión, emparejamiento por código, diagnóstico y preferencias. |
| `privacy.html` | Política de privacidad empaquetada; su publicación en URL pública está pendiente. |

La extensión no contiene claves de proveedores ni ejecuta código remoto. No
solicita `scripting`, `tabs` ni acceso de host a webs arbitrarias: el acceso
a páginas se limita al content script y la red al puente local. El paquete
de tienda se genera con `deployment/extension/build.py` (validación +
ZIP reproducibles + SHA-256). Las guías de tienda están en `docs/stores/`.

### Puente y dominio (`local-bridge/app/`)

| Módulo | Responsabilidad |
|---|---|
| `config.py` | Configuración tipada desde `.env` + entorno; valida host loopback, límites y opciones. |
| `env_setup.py` | Crea/repara `.env` y genera `BRIDGE_TOKEN`. |
| `paths.py` | Rutas de desarrollo vs. instalado (`%LOCALAPPDATA%\Extdator`) y recursos empaquetados. |
| `security.py` | Token en tiempo constante, validación de `Origin`, rate limit deslizante. |
| `server.py` | Endpoints FastAPI, autenticación, CORS, validación Pydantic, cola. |
| `pairing.py` | Códigos efímeros de 6 dígitos, un uso, 10 minutos, límite de intentos. |
| `queue_manager.py` | `Bus` FIFO thread-safe, `RuntimeState` con lock y banderas de control. |
| `conversation.py` | Historial en memoria con recorte por número de mensajes. |
| `ollama_client.py` | Adaptador HTTP nativo de Ollama; streaming NDJSON; errores tipificados; `think` condicional. |
| `providers/` | Puertos/adaptadores de proveedores OpenAI-compatible, presets, keyring y estado sin secretos. |
| `model_catalog.py` | Catálogo de modelos recomendados y requisitos orientativos. |
| `model_evaluator.py` | Evaluación de encaje modelo/hardware con semáforo. |
| `hardware.py` | CPU, RAM, GPU y discos vía psutil/WMI sin privilegios. |
| `ocr_manager.py` | Registro de motores OCR opcionales, detección real e instancia cacheada. |
| `math_unicode.py` | Conversión segura de LaTeX/ASCII a matemática Unicode legible. |
| `settings_store.py` | Persistencia de ajustes de UI en JSON; secretos fuera. |
| `main.py` | Composición: modos `bridge`, `--server-only`, `--check`; logging. |

### Audio del copiloto (`local-bridge/app/audio/`)

Primer bloque del copiloto en tiempo real (diseño completo en
[ADR-0005](docs/adr/0005-realtime-copilot-architecture.md); avance en
`docs/COPILOT_STATUS.md`). No importa Qt: se comunica por callbacks.

| Módulo | Responsabilidad |
|---|---|
| `types.py` | DTO: `SourceKind` (sistema/micrófono), `AudioDevice`, `AudioChunk`, `SpeechSegment` (provisional/final). Formato interno mono `float32` 16 kHz. |
| `dsp.py` | Mezcla a mono, remuestreo en streaming (FIR + fase continua), niveles dBFS, normalización limitada. |
| `vad.py` | Puerto `VoiceActivityDetector`; `SileroVAD` (ONNX v6 incluido en faster-whisper) y `EnergyVAD` de respaldo. |
| `segmenter.py` | Máquina de estados: pre-roll, provisionales periódicos, cierre por silencio, corte de monólogos, descarte de ruidos. |
| `capture.py` | Puerto `CaptureBackend`; `SoundCardBackend` (WASAPI loopback + micrófono, COM por hilo); `CaptureWorker` con pausa, reconexión exponencial y liberación garantizada. |
| `pipeline.py` | `AudioPipeline`: varias fuentes simultáneas, un segmentador por fuente, IDs globales, niveles, cola acotada sin bloquear la captura. |
| `wav.py` | WAV PCM16 en memoria (STT remoto y exportes). |

### Reconocimiento de voz (`local-bridge/app/stt/`) y transcripción

Detalle de motores, costes y mediciones en `docs/SPEECH.md`.

| Módulo | Responsabilidad |
|---|---|
| `base.py` | Puerto `SpeechRecognizer`, `Transcript` (confianza, idioma, RTF), `STTError` tipado, `STTMode` (streaming / fragmentos / lotes). |
| `catalog.py` | Modelos locales y remotos con modalidad real, acceso (`AccessTier`), precios/límites verificados y fecha. |
| `faster_whisper_engine.py` | Whisper local (CTranslate2): CUDA float16 o CPU int8, carga perezosa solo desde `models_dir()`, descarga explícita, filtro de alucinaciones. |
| `openai_compat_engine.py` | `POST /audio/transcriptions` multipart (Groq `verbose_json`, OpenAI `gpt-transcribe` con `languages[]`). |
| `deepgram_engine.py` | API pregrabada de Deepgram (`nova-3`, idioma o detección). |
| `recommend.py` | Recomendación por equipo/preferencia con latencia por llamada medida o estimada, razones y advertencias. |
| `factory.py` | Construye el motor con la clave del keyring (búsqueda inyectable) y su estado de disponibilidad. |
| `../providers/access.py` | Vocabulario común de acceso/coste (local, gratuito con límites, créditos, pago por consumo…). |
| `../copilot/transcription.py` | `TranscriptionService`: un hilo STT, finales con prioridad y nunca descartados, solo el provisional más reciente, reintento ante cuota/red, fallo explícito sin cambiar de motor, prompt con contexto y glosario. |

### Catálogo de proveedores y Model Router (`app/providers/`, `app/routing/`)

| Módulo | Responsabilidad |
|---|---|
| `providers/catalog.py` | Catálogo unificado (Ollama local + presets) con acceso, capacidades, protocolo y estado honesto ("clave válida · acceso sin confirmar"). |
| `providers/anthropic.py` | Adaptador nativo Messages API con la misma interfaz que `OpenAICompatClient`. |
| `providers/consent.py` | Consentimientos `remote_data` / `costs` / `documents` por proveedor. |
| `providers/usage.py` | Historial de uso por día/proveedor/tarea y últimos encabezados de límites; sin contenido. |
| `routing/router.py` | Tareas (respuesta, traducción, resumen, visión, desafíos, embeddings…), candidatos, estrategias (equilibrado, velocidad, calidad, local, bajo costo, solo elegidos), política y decisión explicada con alternativas. |
| `routing/executor.py` | Ejecuta la decisión en streaming; alternativas solo antes de emitir texto, aviso visible (y explícito si pasa a remoto), cancelación sin alternativas. |
| `routing/candidates.py` | Candidatos reales: Ollama (`/api/tags` + capacidades de `/api/show` + encaje con el hardware) y modelos remotos elegidos; backend de Ollama con errores tipados. |

### Asistencia en conversaciones (`app/copilot/`)

| Módulo | Responsabilidad |
|---|---|
| `questions.py` | Puntuación explicable de preguntas/peticiones (es/en, sin depender de "¿?"), `TurnAccumulator` (turno = finales seguidos de una fuente hasta una pausa), detección de duplicados. |
| `prompts.py` | Prompts de respuesta (perfil, documentos con fuente, conversación reciente, otra respuesta, ampliar), traducción, resumen y desafíos. Regla: no inventar experiencias ni datos; marcar `[completar: …]`. |
| `settings.py` | `CopilotSettings` (voz, fuentes, sensibilidad, estrategia, modelos fijados/remotos, ventana, atajos) y su repositorio JSON. |
| `engine.py` | `RoutedEngine`: candidatos cacheados + router + ejecutor + historial de uso con límites; `local_only` para datos personales; `warm_up()` precarga el modelo local al iniciar. |
| `translation.py` | `LiveTranslator`: provisionales sin traducir y marcados, confirmados traducidos en orden con contexto, confianza baja marcada, exportación TXT/Markdown y resumen. |
| `challenges.py` | dHash + `ChangeDetector` (análisis periódico sin repetir pantallas iguales) y `ChallengeSolver` (visión si hay modelo permitido; si no, OCR + texto; si no, explicación). |
| `assistant.py` | `ConversationAssistant`: turnos → preguntas del interlocutor → una generación a la vez, preguntas manuales con prioridad, otra respuesta, ampliar, útil/no útil, cancelación, historial. |

### Perfiles y base de conocimiento (`app/knowledge/`)

| Módulo | Responsabilidad |
|---|---|
| `profiles.py` | `ContextProfile` (rol, experiencia, objetivo, empresa, idioma, tono, detalle, glosario, `allow_remote_documents=False` por defecto) y `ProfileStore` JSON. |
| `extract.py` | TXT/MD (UTF-8/CP1252), PDF con pypdf, DOCX leyendo su XML; validación por contenido, límites de tamaño/páginas, guarda contra bombas ZIP, PDF cifrados rechazados. |
| `chunker.py` | Fragmentos ~900 caracteres con solapamiento, respetando párrafos/frases y conservando sección y página. |
| `bm25.py` | BM25 sin tildes ni palabras vacías (es/en): funciona sin modelos. |
| `embeddings.py` | Embeddings locales vía Ollama `/api/embed` (p. ej. `nomic-embed-text`), normalizados. |
| `service.py` | `KnowledgeBase` en SQLite: añadir archivo/nota (misma nombre = nueva versión), eliminar, borrar perfil, vectores pendientes/reconstrucción, búsqueda híbrida (RRF) y `build_context` con fuentes citables. |

Regla de diseño: la política por defecto es **solo local**. Un remoto
entra únicamente con `remote_data`; uno que puede costar dinero, además
con `costs`; con documentos personales, además con `documents`.

### Compatibilidad del equipo (`local-bridge/app/compat/`)

| Módulo | Responsabilidad |
|---|---|
| `../hardware.py` (`detect_backends`) | Driver CUDA (`nvcuda.dll`), cargador Vulkan (`vulkan-1.dll`), runtime HIP/ROCm, AVX/AVX2/AVX-512 y GPUs CUDA visibles para CTranslate2. Presencia ≠ compatibilidad: se informa así. |
| `../model_evaluator.py` | Cinco clases públicas: Óptimo, Compatible, Exigente, No compatible, No evaluado (sin datos). La VRAM de una GPU solo cuenta si hay un backend utilizable para su fabricante. |
| `workload.py` | Evalúa el conjunto (voz + modelo de respuesta + visión + OCR): VRAM compartida por los modelos de Ollama (alternancia si no caben juntos), RAM del proceso y del sistema; "No compatible" solo si supera la RAM total. |
| `benchmark.py` | Mediciones consentidas: carga, primer token, tokens/s, VRAM y RAM (Ollama); latencia por intervención y RSS del proceso (Whisper) con voz TTS real, nunca con ruido sintético. Los resultados alimentan la recomendación de voz. |

### Interfaz del copiloto (`local-bridge/app/desktop/copilot/`)

MVVM pragmático: `CopilotController` es el ViewModel (posee sesión y
servicios, traduce callbacks de hilos a señales Qt); las páginas solo se
enlazan a señales y llaman a sus métodos.

| Módulo | Responsabilidad |
|---|---|
| `controller.py` | Sesión de conversación/traducción (pipeline de audio + STT + asistente/traductor), pausa/fin, precarga del modelo, desafíos, ajustes, consentimientos, uso. |
| `window.py` | Ventana con navegación: Inicio, Conversación, Traducción, Desafíos, Perfiles y documentos, Motores y modelos, Privacidad. |
| `pages_*.py` | Vistas de cada sección (sin lógica de negocio). |
| `overlay.py` | Panel flotante siempre visible, sin foco, arrastrable, compacto, con estado y controles. |
| `hotkeys.py` | Atajos globales con `RegisterHotKey`; informa conflictos. |
| `tasks.py` | Trabajo pesado en `QThreadPool` con entrega segura a la GUI. |

### Escritorio (`local-bridge/app/desktop/`)

| Módulo | Responsabilidad |
|---|---|
| `window.py` | Panel flotante frameless (superficie de respuestas): always-on-top, no-activación, menú por secciones, opacidad, copia, historial visual; posee el `DownloadManager` y abre el centro de control. |
| `control_center.py` | **Centro de control** (2.1): estado real (Ollama, modelo, descargas), guía de uso, acciones frecuentes y el catálogo de modelos integrado. Superficie de gestión única. |
| `widgets.py` | Burbujas, autoscroll, recálculo de altura; `AssistantMessage` delega el formato en `rich_text`. |
| `rich_text.py` | Respuestas con Markdown seguro (`MarkdownNoHTML`), fórmulas compuestas con **ziamath** (LaTeX → MathML → SVG → QtSvg) y código resaltado con **Pygments**; visor que solo sirve recursos propios (ADR-0006). |
| `downloads.py` | `DownloadManager`: cola única y en serie de descargas (Ollama y Whisper), sin duplicados, progreso real (`ollama_pull.PullTracker`), verificación final y cancelación. Sobrevive al cierre de las ventanas. |
| `ui_kit.py` | Lenguaje visual común: QSS de diálogos, `fit_to_screen`, `ChoiceCard`, `TierChip` (misma escala que el catálogo), `DownloadProgress`. |
| `workers.py` | Consumo de cola, streaming, cancelación, visión/OCR, sondeo de estado y generación del diagnóstico. |
| `dialogs.py` | `ModelsPanel`/`ModelSelectorDialog` (catálogo con columna de acciones fija), ajustes y emparejamiento. |
| `recommend_dialog.py` | «Recomendar configuración»: tarjetas de consumo, recomendación por función y aplicación con descarga automática consentida. |
| `onboarding.py` | Asistente de primer inicio: privacidad, requisitos y código de vinculación. |
| `diagnostics.py` | Paquete ZIP para soporte: resumen, comprobaciones y logs redactados; no lee claves ni historial. |
| `providers_dialog.py` | Alta/baja de proveedores; claves al keyring. |
| `screenshot.py` | Overlay de captura por monitor, DPI y preview visión/OCR. |
| `window_manager.py` | Win32: hook de rueda, `WS_EX_NOACTIVATE`, `WM_MOUSEACTIVATE`, DPI. |
| `styles.py` | QSS de la identidad visual. |

## 5. Puertos y adaptadores

### Inferencia

```text
GenerationWorker
   ├── OllamaClient          (HTTP local, NDJSON, think condicional)
   └── OpenAICompatClient    (HTTPS, SSE, 12 presets + custom)
```

Ambos exponen `chat_stream(messages, model, temperature, on_chunk,
should_abort) -> (texto, stats)`. Las capacidades opcionales (visión,
thinking, herramientas) se comprueban antes de usarse; no se finge soporte.

### OCR

`ocr_manager` mantiene un registro de motores (Pix2Text, RapidOCR,
Tesseract) y aplica una estrategia: motor local si está instalado; si no,
visión multimodal del modelo activo. La instancia se libera tras cada
transcripción y los pesos no se descargan sin consentimiento.

### Persistencia

| Dato | Ubicación | Sensibilidad |
|---|---|---|
| Configuración base | `local-bridge/.env` (desarrollo) / `%LOCALAPPDATA%\UNnexor\.env` (instalado) | Token de puente |
| Ajustes de UI | `app/data/user_settings.json` (desarrollo) / `%LOCALAPPDATA%\UNnexor\` (instalado) | Baja |
| Preferencias de ventanas | Registro `HKCU\Software\UNnexor` (QSettings) | Baja |
| Proveedores | `providers.json` sin claves | Media (metadatos) |
| Claves API | Windows Credential Manager (keyring); posibles copias en `.env` hasta purgar | Alta |
| Historial | Memoria del worker; no se persiste | Alta |
| Modelos locales del copiloto (voz) | `%LOCALAPPDATA%\UNnexor\models` (también en desarrollo; `UNNEXOR_MODELS_DIR`) | Baja (pesos públicos) |
| Audio capturado | Solo en memoria durante la sesión; nunca se escribe a disco | Alta |
| Logs | `local-bridge/logs/` (desarrollo) / `%LOCALAPPDATA%\UNnexor\logs\` (instalado) | Contenido prohibido por `LOG_PRIVACY=1` |

`app/paths.py` centraliza las rutas. En desarrollo conserva las rutas
históricas; en un ejecutable PyInstaller usa `%LOCALAPPDATA%\UNnexor`.
`UNNEXOR_DATA_DIR`, `UNNEXOR_ENV_FILE` y `UNNEXOR_MODELS_DIR` permiten
tests y modo portable (los alias `EXTDATOR_*` siguen aceptándose).

**Migración desde versiones anteriores** (`app/migration.py`): al arrancar,
antes de leer configuración, se copia `%LOCALAPPDATA%\Extdator` a
`%LOCALAPPDATA%\UNnexor` archivo a archivo (temporal + verificación de
tamaño + renombrado atómico), sin sobrescribir archivos existentes, sin
migrar logs y **sin borrar el origen** (se deja una nota). El resultado se
registra en `migration.json`; si hubo errores se reintenta en el siguiente
arranque. Las preferencias QSettings (`HKCU\Software\Extdator`) se copian a
`UNnexor` si el destino está vacío y las credenciales del keyring se copian
del servicio `Extdator` al servicio `UNnexor` la primera vez que se leen.

## 6. Flujos de información

### 6.1 Selección de texto

1. `content.js` detecta `mouseup`/`selectionchange` y muestra la X.
2. El menú ofrece **Enviar directamente** (solo el texto) o **Consultar…**
   (panel para escribir una pregunta u orden sobre la selección).
3. El SW hace `POST /api/selection` con token y
   `{text, question, mode: "direct" | "ask", source_url}`. Hasta 2.0.0 el
   SW no reenviaba `question` y «Consultar» se comportaba como un envío
   directo; desde 2.1.0 el SW exige `question_received` en modo `ask`.
4. FastAPI valida origen, token, tamaño, tasa y la coherencia del modo
   (`ask` sin pregunta o `direct` con pregunta → 422); publica en `Bus`.
5. `GenerationWorker` añade el turno: en modo directo, el texto tal cual;
   en modo consulta, la consulta como tarea y la selección como contexto
   (`SelectionRequest.prompt`). El historial conserva el contexto para
   consultas de seguimiento.
6. `BridgeWindow` muestra el streaming formateado (`rich_text`) y el
   autoscroll sigue el final salvo que el usuario haya subido a leer.

### 6.2 Captura y OCR

1. 📷 abre un overlay por monitor; se captura la región con DPI correcto.
2. El preview ofrece visión (si el modelo la soporta) u OCR instalado.
3. OCR devuelve texto/Markdown; la imagen temporal se borra.
4. El texto entra al pipeline normal y la respuesta se muestra igual que
   una selección.

### 6.3 Proveedores remotos

1. El diálogo valida la clave con `GET /models` y la guarda en keyring.
2. El estado (sin clave) se escribe en `providers.json`.
3. Al seleccionar el proveedor, `RuntimeState` cambia el modo.
4. El worker construye el adaptador y envía el historial completo (los
   proveedores remotos no conservan estado entre peticiones).

### 6.4 Descargas de modelos

1. Las ventanas (Modelos de IA, Recomendar configuración) piden
   consentimiento con tamaño y recursos **antes** de encolar nada.
2. `DownloadManager` ejecuta una descarga a la vez en un `QThread`;
   `OllamaClient.pull_stream(on_line=…)` entrega cada línea NDJSON.
3. `PullTracker` agrega las capas (`digest`): porcentaje = Σ completado /
   Σ total; sin total → barra indeterminada; nunca 100 % antes de
   verificar; fases Preparando → Descargando → Verificando → Finalizando →
   Completado/Cancelado/Error; velocidad en ventana deslizante.
4. Al terminar, se comprueba que el modelo aparece en `/api/tags`; solo
   entonces se emite «Completado» y la recomendación aplica su parte.
5. Un flujo que se corta sin `success` es un error (reintento: Ollama
   reanuda las capas ya descargadas).

### 6.5 Actualizaciones (objetivo)

La extensión se actualiza por las tiendas oficiales. La aplicación
comprobará releases firmadas/checksum desde GitHub, nunca ejecutará un
binario sin verificar SHA-256 y conservará configuración, claves e
historial. Se implementará y documentará antes de declararlo disponible.

## 7. Seguridad

- El puente escucha solo en `127.0.0.1`/`localhost`; `Config.validate()`
  rechaza cualquier otra interfaz.
- Token generado con `secrets.token_urlsafe(32)`, comparado con
  `secrets.compare_digest`.
- `Origin` restringido a extensiones; orígenes web rechazados en `/api/*`.
- CORS nunca usa `*`; el token no se usa como única barrera.
- Límite de texto y rate limit por cliente.
- Emparejamiento por código efímero, un solo uso, caducidad y límite de
  intentos; solo acepta orígenes de extensión.
- Claves de proveedores en el keyring del sistema. La migración desde
  `.env` es no destructiva; `--purge-secrets` retira también esas copias.
- `LOG_PRIVACY=1` evita registrar contenido; los errores no incluyen claves.
- Capturas en `%TEMP%` se eliminan tras procesar.
- `UNnexor.exe --purge-secrets` elimina las claves del keyring, vacía el
  token del puente y retira `*_API_KEY` del `.env`; el instalador lo usa en
  la limpieza completa.
- No hay código remoto ejecutable en la extensión ni en el escritorio.

Riesgos residuales: cualquier proceso local del mismo usuario puede leer el
`.env`; el token protege frente a páginas web y otros orígenes, no frente a
malware con los mismos permisos. Se documenta como limitación del modelo de
seguridad de una aplicación local.

## 8. Distribución

| Artefacto | Herramienta | Estado |
|---|---|---|
| ZIP Chrome | `deployment/extension/build.py` + validación estática | **Generado y verificado localmente** |
| ZIP Edge | mismo paquete, validación de manifiesto | **Generado y verificado localmente** |
| EXE/one-folder | `deployment/windows/build.py` + PyInstaller 6 | Generado y validado en este equipo (smoke `--check` con componentes del copiloto y renderizado, GUI offscreen). Tamaños en `release/windows/build-report.json` |
| Instalador | `deployment/installer/build.py` + Inno Setup 6 por usuario | Generado y probado en directorios temporales (instalación, actualización con migración desde Extdator, preservación, purga y desinstalación). Informe en `release/installer/build-report.json` |
| SBOM | `deployment/sbom/build.py` (CycloneDX 1.5) | **Generado y verificado localmente** |
| Verificación | `deployment/verify_artifacts.py` | **Checksums, informes y SBOM** |
| CI | `.github/workflows/ci.yml` (GitHub Actions, Windows) | Pruebas + empaquetado + verificación |
| Actualizaciones | GitHub Releases + SHA-256 | Diseño pendiente |
| Firma | Authenticode si el propietario obtiene certificado | Pendiente de decisión |

Los paquetes se escriben en `release/browser-extension/` (ignorado por Git)
junto con `SHA256SUMS.txt` y `build-report.json`. No se declaran publicados:
las tiendas requieren cuenta, URL pública de privacidad, capturas reales y
aprobación del propietario. La matriz de cumplimiento está en
`docs/stores/COMPLIANCE_MATRIX.md`.

El instalador no incluye modelos de Ollama ni de voz, pesos OCR, claves ni
historiales. La aplicación instalada escribe datos en
`%LOCALAPPDATA%\UNnexor` (con migración desde `%LOCALAPPDATA%\Extdator`).
El EXE incluye ziamath (con la fuente STIX Two Math), latex2mathml y
Pygments para el formato de respuestas; **no** incluye QtWebEngine.

El instalador de Inno Setup 6 se instala **por usuario** en
`%LOCALAPPDATA%\Programs\UNnexor`, no solicita administrador y crea
accesos directos opcionales. Al desinstalar conserva
`%LOCALAPPDATA%\UNnexor` por defecto; si el usuario elige la limpieza
completa, ejecuta `UNnexor.exe --purge-secrets` y borra el directorio de
datos y la copia antigua `%LOCALAPPDATA%\Extdator` si existe. La purga de secretos también puede ejecutarse manualmente y no toca
ajustes no sensibles.

El ejecutable se construye en modo one-folder (`UNnexor/UNnexor.exe` +
`_internal/`). El self-check (`UNnexor.exe --check`) escribe
`self-check.json` en el directorio de datos y permite validar rutas y
servidor sin consola. El build poda módulos Qt no usados (Qml/Quick/PDF/
OpenGL) y ejecuta una prueba GUI con `QT_QPA_PLATFORM=offscreen`. El OCR
local pesado no se empaqueta en esta fase; la visión del modelo sigue
disponible.

La cadena de CI es deliberadamente de solo lectura: `permissions:
contents: read`, sin secretos de firma ni de tiendas. El trabajo de
pruebas instala dependencias y ejecuta pytest; el de artefactos compila la
extensión, el EXE, el instalador (Inno Setup instalado con Chocolatey),
genera el SBOM y ejecuta `deployment/verify_artifacts.py`. Los artefactos
se conservan 14 días como adjuntos del flujo y no se publican. El SBOM
CycloneDX 1.5 cubre el entorno Python de build/runtime y declara la
licencia del proyecto (Apache-2.0); no incluye modelos, claves ni datos de
usuario. Límites y comandos: `docs/CI.md`.

## 9. Decisiones registradas

- [ADR-0001](docs/adr/0001-modular-monolith-and-clean-boundaries.md):
  monolito modular con límites pragmáticos, sin microservicios.
- [ADR-0002](docs/adr/0002-local-http-transport.md): mantener HTTP local
  autenticado en lugar de migrar a Native Messaging en esta etapa.
- [ADR-0003](docs/adr/0003-packaging-and-installer.md): PyInstaller +
  Inno Setup, instalación por usuario.
- [ADR-0004](docs/adr/0004-project-license-pending.md): licencia del
  proyecto — Apache-2.0, elegida por el propietario el 2026-11-27.
- [ADR-0005](docs/adr/0005-realtime-copilot-architecture.md): paquetes,
  concurrencia, enrutamiento y reglas de privacidad del copiloto en tiempo
  real (sin mecanismos para ocultarlo a terceros).
- [ADR-0006](docs/adr/0006-native-answer-rendering.md): fórmulas con
  ziamath y código con Pygments dentro del visor nativo, sin QtWebEngine.

## 10. Deuda técnica y límites conocidos

Revisado el 2026-11-28 contra el código (versión 2.1.0):

1. `server.py` (≈570 líneas) y `window.py` (≈1250) siguen concentrando
   varias responsabilidades. En 2.1 se extrajeron el centro de control, el
   gestor de descargas, el renderizado y el kit visual; dividir más se
   hará con pruebas cuando haga falta.
2. La UI no tiene un ViewModel formal: la separación es por widgets,
   señales y workers. El estado compartido de descargas vive en un único
   `DownloadManager` para no duplicarlo entre ventanas.
3. El historial del panel principal es en memoria (`Conversation`); no se
   persisten conversaciones entre arranques.
4. El EXE y el instalador se validaron en este equipo de desarrollo, en
   directorios y `LOCALAPPDATA` temporales (incluida la actualización con
   migración desde Extdator). **Falta** una prueba en un equipo limpio y no
   existe mecanismo de actualización automática de la aplicación.
5. No hay firma de código ni certificado: los builds no están firmados y
   SmartScreen puede advertir.
6. La política de privacidad está empaquetada, pero falta una URL pública
   para las tiendas; tampoco existen capturas oficiales ni IDs de tienda.
7. El ejecutable incluye el copiloto (NumPy, SoundCard, faster-whisper,
   CTranslate2, ONNX Runtime, PyAV, pypdf) y el formato de respuestas
   (ziamath, latex2mathml, Pygments), pero no los modelos: los de Whisper y
   Ollama se descargan con consentimiento. El auto-chequeo del EXE valida
   componentes del copiloto y el renderizado de fórmulas/código.
8. El ejecutable no incluye motores OCR locales pesados
   (Pix2Text/RapidOCR/Tesseract): «Recomendar configuración» los marca como
   instalación manual. La visión multimodal del modelo activo sí funciona.
9. ziamath cubre LaTeX habitual (fracciones, raíces, sumatorias,
   integrales, límites, matrices, `cases`, `aligned`); comandos fuera de su
   soporte se muestran en Unicode legible en lugar de componerse.
10. La comprobación de que una respuesta del copiloto «suena en primera
    persona» depende del modelo: el prompt lo exige y `clean_spoken_answer`
    retira introducciones del tipo «Podrías responder…», pero un modelo muy
    pequeño puede seguir desviándose.

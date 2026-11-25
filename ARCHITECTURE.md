# Arquitectura de Extdator

> Documento canónico. Se actualiza en cada fase; `docs/adr/` recoge las
> decisiones que no son evidentes a partir del código.

## 1. Objetivo y estado

Extdator es un asistente local para el navegador. El usuario selecciona
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

### Escritorio (`local-bridge/app/desktop/`)

| Módulo | Responsabilidad |
|---|---|
| `window.py` | Ventana frameless, always-on-top, no-activación, menú, opacidad, copia, historial visual. |
| `widgets.py` | Burbujas, Markdown final, matemática Unicode, autoscroll y recálculo de altura. |
| `workers.py` | Consumo de cola, streaming, cancelación, visión/OCR, sondeo de estado y generación del diagnóstico. |
| `dialogs.py` | Ajustes, selector de modelos y emparejamiento. |
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
| Configuración base | `local-bridge/.env` (desarrollo) / `%LOCALAPPDATA%\Extdator\.env` (instalado) | Token de puente |
| Ajustes de UI | `app/data/user_settings.json` (desarrollo) / `%LOCALAPPDATA%\Extdator\` (instalado) | Baja |
| Proveedores | `providers.json` sin claves | Media (metadatos) |
| Claves API | Windows Credential Manager (keyring); posibles copias en `.env` hasta purgar | Alta |
| Historial | Memoria del worker; no se persiste | Alta |
| Modelos locales del copiloto (voz, embeddings) | `%LOCALAPPDATA%\Extdator\models` (también en desarrollo; `EXTDATOR_MODELS_DIR`) | Baja (pesos públicos) |
| Audio capturado | Solo en memoria durante la sesión; nunca se escribe a disco | Alta |
| Logs | `local-bridge/logs/` (desarrollo) / `%LOCALAPPDATA%\Extdator\logs\` (instalado) | Contenido prohibido por `LOG_PRIVACY=1` |

`app/paths.py` centraliza las rutas. En desarrollo conserva las rutas
históricas; en un ejecutable PyInstaller usa `%LOCALAPPDATA%\Extdator`.
`EXTDATOR_DATA_DIR` y `EXTDATOR_ENV_FILE` permiten tests y modo portable.

## 6. Flujos de información

### 6.1 Selección de texto

1. `content.js` detecta `mouseup`/`selectionchange` y muestra ✦.
2. El clic conserva la selección y envía `{type,text,sourceUrl}` al SW.
3. El SW hace `POST /api/selection` con token.
4. FastAPI valida origen, token, tamaño y tasa; publica en `Bus` (202).
5. `GenerationWorker` añade el turno, llama al adaptador y emite señales.
6. `BridgeWindow` muestra el streaming y el autoscroll sigue el final salvo
   que el usuario haya subido a leer.

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

### 6.4 Actualizaciones (objetivo)

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
- `Extdator.exe --purge-secrets` elimina las claves del keyring, vacía el
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
| EXE/one-folder | `deployment/windows/build.py` + PyInstaller 6 | **Generado y validado (smoke + GUI offscreen)** |
| Instalador | `deployment/installer/build.py` + Inno Setup 6 por usuario | **Generado y probado (instalación, preservación, purga y desinstalación)** |
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

El instalador no incluye modelos de Ollama, pesos OCR, claves ni historiales.
La aplicación instalada escribe datos en `%LOCALAPPDATA%\Extdator`.

El instalador de Inno Setup 6 se instala **por usuario** en
`%LOCALAPPDATA%\Programs\Extdator`, no solicita administrador y crea
accesos directos opcionales. Al desinstalar conserva
`%LOCALAPPDATA%\Extdator` por defecto; si el usuario elige la limpieza
completa, ejecuta `Extdator.exe --purge-secrets` y borra el directorio de
datos. La purga de secretos también puede ejecutarse manualmente y no toca
ajustes no sensibles.

El ejecutable se construye en modo one-folder (`Extdator/Extdator.exe` +
`_internal/`). El self-check (`Extdator.exe --check`) escribe
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
CycloneDX 1.5 cubre el entorno Python de build/runtime y declara
explícitamente la licencia como pendiente; no incluye modelos, claves ni
datos de usuario. Límites y comandos: `docs/CI.md`.

## 9. Decisiones registradas

- [ADR-0001](docs/adr/0001-modular-monolith-and-clean-boundaries.md):
  monolito modular con límites pragmáticos, sin microservicios.
- [ADR-0002](docs/adr/0002-local-http-transport.md): mantener HTTP local
  autenticado en lugar de migrar a Native Messaging en esta etapa.
- [ADR-0003](docs/adr/0003-packaging-and-installer.md): PyInstaller +
  Inno Setup, instalación por usuario.
- [ADR-0004](docs/adr/0004-project-license-pending.md): licencia del
  proyecto pendiente de autorización del propietario.
- [ADR-0005](docs/adr/0005-realtime-copilot-architecture.md): paquetes,
  concurrencia, enrutamiento y reglas de privacidad del copiloto en tiempo
  real (sin mecanismos para ocultarlo a terceros).

## 10. Deuda técnica y límites conocidos

1. `server.py` y `window.py` concentran varias responsabilidades; se
   dividirán solo cuando aparezca una necesidad real y con pruebas.
2. La UI no tiene un ViewModel formal: la separación es por widgets,
   señales y worker. Introducir MVVM completo ahora duplicaría estado.
3. El historial es en memoria; no hay persistencia de conversaciones.
4. El modo instalado se validó desde el EXE PyInstaller y el instalador
   Inno en directorios temporales; falta una prueba manual en un equipo
   limpio, la firma Authenticode y un mecanismo de actualización.
5. No hay firma de código ni certificado; los builds no firmados deben
   identificarse como tales.
6. La política de privacidad está empaquetada, pero falta una URL pública
   para las tiendas; tampoco existen capturas reales ni IDs de tienda.
7. El ejecutable base no incluye motores OCR locales pesados
   (Pix2Text/RapidOCR/Tesseract): el OCR local queda para la versión de
   desarrollo o una futura edición opcional. La visión multimodal del
   modelo activo sí está disponible.
8. La licencia principal no está decidida; no se redistribuye públicamente
   hasta que el propietario la confirme.

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
| Distribución | En preparación | PyInstaller + Inno Setup (fase 6/7) |

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
| `manifest.json` | MV3, permisos, content scripts, service worker, iconos. |
| `content.js` | Detección de selección, botón en Shadow DOM cerrado, toast; única UI inyectada. |
| `background.js` | Único punto de red de la extensión; configuración en `chrome.storage.local`; mapeo de errores. |
| `options.html/js` | Asistente de conexión, emparejamiento por código, diagnóstico y preferencias. |

La extensión no contiene claves de proveedores ni ejecuta código remoto.

### Puente y dominio (`local-bridge/app/`)

| Módulo | Responsabilidad |
|---|---|
| `config.py` | Configuración tipada desde `.env` + entorno; valida host loopback, límites y opciones. |
| `env_setup.py` | Crea/repara `.env` y genera `BRIDGE_TOKEN`. |
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

### Escritorio (`local-bridge/app/desktop/`)

| Módulo | Responsabilidad |
|---|---|
| `window.py` | Ventana frameless, always-on-top, no-activación, menú, opacidad, copia, historial visual. |
| `widgets.py` | Burbujas, Markdown final, matemática Unicode, autoscroll y recálculo de altura. |
| `workers.py` | Consumo de cola, streaming, cancelación, visión/OCR y sondeo de estado. |
| `dialogs.py` | Ajustes, selector de modelos, emparejamiento, primer uso y diagnóstico. |
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
| Configuración base | `local-bridge/.env` (desarrollo) / `%LOCALAPPDATA%\Extdator\.env` (instalado, previsto) | Token de puente |
| Ajustes de UI | `app/data/user_settings.json` (desarrollo) / `%LOCALAPPDATA%\Extdator\` (instalado, previsto) | Baja |
| Proveedores | `providers.json` sin claves | Media (metadatos) |
| Claves API | Windows Credential Manager (keyring) | Alta |
| Historial | Memoria del worker; no se persiste | Alta |
| Logs | `local-bridge/logs/` (desarrollo) / `%LOCALAPPDATA%\Extdator\logs\` (instalado, previsto) | Contenido prohibido por `LOG_PRIVACY=1` |

La ruta instalada se implementará en la fase de empaquetado mediante un
módulo de rutas de runtime; el modo desarrollo conserva las rutas actuales.

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
- Claves de proveedores exclusivamente en el keyring del sistema.
- `LOG_PRIVACY=1` evita registrar contenido; los errores no incluyen claves.
- Capturas en `%TEMP%` se eliminan tras procesar.
- No hay código remoto ejecutable en la extensión ni en el escritorio.

Riesgos residuales: cualquier proceso local del mismo usuario puede leer el
`.env`; el token protege frente a páginas web y otros orígenes, no frente a
malware con los mismos permisos. Se documenta como limitación del modelo de
seguridad de una aplicación local.

## 8. Distribución

| Artefacto | Herramienta prevista | Estado |
|---|---|---|
| ZIP Chrome | script reproducible + validación de manifest | En preparación |
| ZIP Edge | mismo paquete, verificación de compatibilidad | En preparación |
| EXE/one-folder | PyInstaller (PySide6, FastAPI, keyring, psutil) | En preparación |
| Instalador | Inno Setup por usuario, sin admin | En preparación |
| Actualizaciones | GitHub Releases + SHA-256 | Diseño pendiente |
| Firma | Authenticode si el propietario obtiene certificado | Pendiente de decisión |

El instalador no incluye modelos de Ollama, pesos OCR, claves ni historiales.
La aplicación instalada escribe datos en `%LOCALAPPDATA%\Extdator`.

## 9. Decisiones registradas

- [ADR-0001](docs/adr/0001-modular-monolith-and-clean-boundaries.md):
  monolito modular con límites pragmáticos, sin microservicios.
- [ADR-0002](docs/adr/0002-local-http-transport.md): mantener HTTP local
  autenticado en lugar de migrar a Native Messaging en esta etapa.
- [ADR-0003](docs/adr/0003-packaging-and-installer.md): PyInstaller +
  Inno Setup, instalación por usuario.
- [ADR-0004](docs/adr/0004-project-license-pending.md): licencia del
  proyecto pendiente de autorización del propietario.

## 10. Deuda técnica y límites conocidos

1. `server.py` y `window.py` concentran varias responsabilidades; se
   dividirán solo cuando aparezca una necesidad real y con pruebas.
2. La UI no tiene un ViewModel formal: la separación es por widgets,
   señales y worker. Introducir MVVM completo ahora duplicaría estado.
3. El historial es en memoria; no hay persistencia de conversaciones.
4. El modo instalado requiere rutas de runtime independientes del
   workspace (fase 6).
5. No hay firma de código ni certificado; los builds no firmados deben
   identificarse como tales.
6. La licencia principal no está decidida; no se redistribuye públicamente
   hasta que el propietario la confirme.
7. La extensión solicita acceso a todos los sitios http/https porque la
   selección puede ocurrir en cualquier página; es una necesidad funcional
   documentada, no un permiso accidental.

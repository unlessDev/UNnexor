# Arquitectura de Extdator

## Visión general

Extdator conecta **tres módulos independientes** para lograr una experiencia
de asistente local inmediata: seleccionas texto en el navegador, pulsas un
botón contextual y la respuesta de tu LLM local (Ollama) aparece
progresivamente en un panel flotante siempre visible.

```
BROWSER (Chrome/Brave/Edge)
    |
    | selección de texto (Selection API)
    v
CONTENT SCRIPT (content.js)
    |  mensaje interno chrome.runtime.sendMessage
    v
BACKGROUND SERVICE WORKER (background.js)
    |  HTTP POST JSON  http://127.0.0.1:5000/api/selection
    v
SERVIDOR LOCAL PYTHON (FastAPI + uvicorn en un hilo)
    |  validación + auth + rate limit
    |  cola thread-safe (Bus)
    v
GENERATION WORKER (QThread)
    |  POST /api/chat  (NDJSON streaming)  ← API HTTP de Ollama
    v
OLLAMA LOCAL (llama3, mistral, ...)
    |  tokens en streaming
    v
PANEL FLOTANTE PySide6 (hilo principal de Qt)
    |  señales Qt (chunkReceived) + autoscroll
    v
USUARIO (navegador conserva el foco)
```

## Responsabilidades por módulo

### 1. Extensión de navegador (`browser-extension/`, Manifest V3)

| Archivo | Responsabilidad |
|---|---|
| `manifest.json` | Permisos mínimos: `storage` + host permissions solo para `127.0.0.1`/`localhost`. Content scripts en `http/https` (no en `file://` ni páginas internas). |
| `content.js` | Detección de selección (`selectionchange`, `mouseup`), posicionamiento del botón con `Range.getClientRects()`, aislamiento con **Shadow DOM**, toasts. Jamás habla con el backend: envía por `chrome.runtime.sendMessage`. |
| `background.js` | Único punto de red de la extensión. `fetch` al puente con `Authorization: Bearer`, timeout con `AbortSignal.timeout`, mapeo de errores a mensajes en español. |
| `options.html/js` | URL del puente + token compartido (en `chrome.storage.local`), prueba de conexión y visor del ID de la extensión. |

Decisión: **no existe `styles.css` global** — todo el estilo del botón/toast
vive dentro del Shadow DOM (`adopted` en `content.js`) para no contaminar el
CSS de las páginas; la página de opciones lleva su CSS embebido.

**Decisiones verificadas con diagnóstico CDP (v1.1.0):**

1. **Shadow root cerrado + retargeting.** Con `mode: "closed"`, los listeners
   externos ven los eventos internos con el target retargetizado al host:
   `composedPath()` NO contiene el botón. Por eso el guard de `mousedown`
   compara `e.target === host` (y no `includes(btn)`): con la comparación
   equivocada, cada clic ocultaba el botón antes del `click` y el envío nunca
   ocurría (bug de la v1.0.0, reproducido y corregido).
2. **Re-inyección en pestañas abiertas.** Los content scripts del manifest
   solo aplican a cargas nuevas; `onInstalled` re-inyecta `content.js` con
   `chrome.scripting.executeScript` en todas las pestañas http/https ya
   abiertas (requiere permiso `scripting` + host permissions de los matches).
   Probado: pestaña abierta → instalar extensión → botón presente sin recargar.
3. **`all_frames: true`** para que el botón funcione también dentro de iframes
   (el guard anti-doble-inyección de `content.js` hace que sea seguro).

### 2. Servidor local (`local-bridge/app/`)

| Archivo | Responsabilidad |
|---|---|
| `config.py` | Config desde `.env` + variables de entorno. Valida tipos y rangos; **rechaza cualquier host distinto de 127.0.0.1/localhost**. |
| `security.py` | Token compartido (comparación en tiempo constante, `secrets.compare_digest`), validación de `Origin` con patrones comodín, rate limiting por ventana deslizante. |
| `server.py` | Endpoints FastAPI. `POST /api/selection` valida, autentica, limita y **encola** (202) sin bloquear. CORS limitado a `chrome-extension://*` (configurable con el ID exacto). Nunca `Access-Control-Allow-Origin: *`. |
| `queue_manager.py` | `Bus` (cola FIFO thread-safe) + `RuntimeState` (banderas compartidas con lock: generando / detener / limpiar). |
| `ollama_client.py` | Cliente httpx contra la **API HTTP nativa de Ollama** (`POST /api/chat`, streaming NDJSON). Errores tipificados con mensajes de usuario. Inyección de transporte para tests. |
| `conversation.py` | Historial conversacional en memoria (como una sesión de `ollama run`) con recorte por `MAX_HISTORY_MESSAGES`. |
| `desktop/*` | GUI PySide6 (ver abajo). |
| `main.py` | Orquestación de modos: puente completo, `--server-only`, `--check`. |

**Decisiones clave:**

1. **Un solo proceso, tres hilos.** El hilo principal ejecuta Qt; uvicorn corre
   en un hilo daemon (`uvicorn.Server` + `server.run()`); la generación corre
   en un `QThread`. Comunicación:
   * Servidor → worker: cola FIFO (`Bus`), sin estado mutable compartido sin
     sincronización.
   * Worker → GUI: señales Qt (automáticamente *queued* entre hilos).
   * Acciones del usuario (limpiar/detener) → banderas atómicas en
     `RuntimeState`, comprobadas entre fragmentos del stream.
2. **Nunca se usa la CLI de Ollama.** El texto viaja por HTTP/JSON UTF-8 a
   `http://127.0.0.1:11434`. No hay inyección de teclado, ni shell, ni
   comillas que escapar.
3. **El texto seleccionado nunca sale del equipo** y, por defecto
   (`LOG_PRIVACY=1`), los logs solo contienen longitudes, no contenido.

### 3. Panel flotante (`local-bridge/app/desktop/`)

| Archivo | Responsabilidad |
|---|---|
| `window.py` | `BridgeWindow`: frameless, arrastrable, redimensionable (banda de 6 px), persistencia con `QSettings`, autoscroll inteligente, menú, opacidad, copiar/limpiar/detener. |
| `widgets.py` | `TitleBar` (drag manual que no activa la ventana), mensajes user/assistant/notas, `AssistantMessage` (texto plano durante el stream → Markdown al finalizar) y `ChatScroller`. |
| `workers.py` | `GenerationWorker` (consume la cola, streaming, cancelación) y `StatusWorker` (sondeo de Ollama cada 4 s). |
| `window_manager.py` | Integración Win32 con ctypes: hook de rueda, filtro `WM_MOUSEACTIVATE`, refuerzo `WS_EX_NOACTIVATE`. |
| `styles.py` | QSS terminal oscura, fuentes monoespaciadas (Cascadia Mono → Consolas). |

## Hover Scroll y no-activación (el requisito crítico)

Objetivo: usar la rueda sobre el panel **mientras el navegador conserva el
foco**, sin clic previo.

Se combinan tres mecanismos (los tres activos por defecto):

1. **No-activación**: `Qt.WindowDoesNotAcceptFocus` (→ `WS_EX_NOACTIVATE`)
   + refuerzo nativo con `GetWindowLongPtrW/SetWindowLongPtrW` + filtro de
   eventos nativos que responde `MA_NOACTIVATE` a `WM_MOUSEACTIVATE`.
   Resultado: hacer clic en el panel no le roba el foco al navegador.
2. **Enrutamiento nativo de Windows 10/11**: con el ajuste del sistema
   *"Desplazar ventanas inactivas al pasar el ratón por encima de ellas"*
   (activado por defecto), Windows envía `WM_MOUSEWHEEL` a la ventana bajo el
   cursor aunque no tenga el foco. Qt procesa ese mensaje y desplaza el
   contenido.
3. **`HoverScrollHook` (respaldo, `HOVER_SCROLL_HOOK=1`)**: hook global de
   bajo nivel `WH_MOUSE_LL`. Cuando el cursor está sobre el HWND del panel,
   intercepta `WM_MOUSEWHEEL`/`WM_MOUSEHWHEEL`, reenvía el mensaje al panel
   con `PostMessageW` y lo **traga** (retorno `1`) para que la ventana
   enfocada no lo procese. Esto garantiza el comportamiento incluso con el
   ajuste del sistema desactivado. El callback es O(1) para no provocar
   timeouts del hook.

El proceso `tests/integration/hover_scroll_probe.py` verifica esto de forma
empírica con `SendInput`/`SetCursorPos`: rueda sin clic (scroll cambia),
foco intacto (`GetForegroundWindow` constante), clic sin activación y
arrastre de la barra de título.

Limitaciones documentadas: si el hook está desactivado (`HOVER_SCROLL_HOOK=0`)
y el ajuste del sistema también lo está, el hover scroll no funcionará (es una
decisión del SO). Para ese caso existe `scripts/hover_scroll_helper.ahk` (v2).

## Seguridad

* Servidor obligatoriamente en `127.0.0.1` (validado en `config.py`).
* Token compartido generado con `secrets.token_urlsafe(32)`; se exige en
  `Authorization: Bearer` (o campo `token` del JSON como respaldo).
* CORS limitado a los orígenes de la lista `ALLOWED_ORIGINS`
  (`chrome-extension://*` por defecto; se recomienda fijar el ID exacto).
* Orígenes web (http/https) siempre rechazados con 403 en `/api/*` — bloquea
  peticiones desde páginas maliciosas aunque el navegador no aplique CORS.
* Límites: tamaño máximo de texto (`MAX_TEXT_LENGTH` → 413) y tasa
  (`RATE_LIMIT_PER_MINUTE` → 429 con `Retry-After`).
* Sin servicios externos: solo Ollama local.

## Flujo de una selección

1. `mouseup`/`selectionchange` → botón ✦ sobre el inicio de la selección.
2. Clic (el `mousedown` del botón se cancela para **conservar la selección**).
3. Content script envía `{type, text, sourceUrl}` al service worker.
4. SW hace `POST /api/selection` con `Authorization: Bearer` y JSON UTF-8.
5. El servidor valida/autentica/encola y responde `202` con mensaje.
6. El SW responde al content script → toast discreto.
7. El `GenerationWorker` recoge el evento en orden FIFO:
   añade el turno `user`, llama a `POST /api/chat` (stream) y emite
   `queryStarted` → `chunkReceived*` → `generationFinished`.
8. La GUI inserta el turno en el panel, acumula texto en el bloque
   `assistant` y re-rendiza Markdown al terminar; el autoscroll sigue el
   final salvo que el usuario haya subido a leer.

# Configuración de UNnexor

Toda la configuración vive en `local-bridge\.env` (creado desde
`.env.example`) como valores **base**. Desde la v1.2.0 también se puede
configurar desde la propia aplicación (menú ☰ del panel), y esos ajustes
del usuario se guardan en `local-bridge\data\user_settings.json` y
**prevalecen sobre el .env**. No hay valores hardcodeados en el código:
cambiar de modelo o puerto se hace editando el archivo o desde la UI.

## Configurar desde la aplicación (v1.2.0)

El menú ☰ del panel flotante ofrece:

* **Modelos de IA** — selector con semáforo 🟢🟡🟠🔴 según tu hardware
  (ver [`docs/MODELS_CATALOG.md`](MODELS_CATALOG.md)). Instalados y
  catálogo sugerido; los 🔴 están deshabilitados; descargar uno nuevo
  pide confirmación con el tamaño.
* **Proveedores de IA** — conexión a proveedores remotos con clave en el
  keyring del sistema (ver [`docs/PROVIDERS.md`](PROVIDERS.md)).
* **Ajustes** — pestañas Modelo / Ventana / Ollama / Avanzado (y captura):
  edita los mismos valores del .env con validación y aplicación en vivo.
* **Emparejar extensión** — genera el código de 6 dígitos que la página
  de opciones de la extensión usa para obtener su token (sin copiar
  tokens a mano).
* **Asistente de inicio** y **Exportar diagnóstico** — reabre el asistente
  de primer uso o genera un ZIP saneado para soporte (ver más abajo).

Los ajustes desde la UI se aplican **en caliente** cuando es posible
(modelo, prompt, temperatura, keep_alive, opacidad, always-on-top,
proveedor) y marcan las claves que requieren reiniciar (timeout, host,
hooks, logs...). `POST /api/settings/reset` elimina todos los overrides
del usuario y vuelve a los valores del `.env`.

## Referencia de variables

### Servidor HTTP local

| Variable | Por defecto | Descripción |
|---|---|---|
| `BRIDGE_HOST` | `127.0.0.1` | Solo se acepta `127.0.0.1` o `localhost`; el puente nunca se expone a la red. |
| `BRIDGE_PORT` | `5000` | Puerto del endpoint de la extensión. |
| `BRIDGE_TOKEN` | *(auto)* | Token compartido con la extensión. Generado en la primera ejecución si está vacío. **No lo subas a repositorios.** |
| `AUTH_REQUIRED` | `1` | Exigir token en `/api/*`. `/health` nunca lo requiere. |
| `ALLOWED_ORIGINS` | `chrome-extension://*` | Lista separada por comas de orígenes autorizados; `*` actúa como comodín. Recomendado: fijar tu ID exacto: `chrome-extension://abcdefghijklmnop`. |
| `MAX_TEXT_LENGTH` | `32000` | Tamaño máximo del texto seleccionado (caracteres; se responde 413 si excede). |
| `RATE_LIMIT_PER_MINUTE` | `30` | Peticiones por minuto por cliente (429 + `Retry-After`). |

### Ollama

| Variable | Por defecto | Descripción |
|---|---|---|
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | Dirección de la API local de Ollama. |
| `OLLAMA_MODEL` | `llama3` | Modelo a usar. **Cámbialo aquí, no en el código.** Ejemplos: `llama3`, `llama3.1`, `mistral`, `qwen2.5-coder`, `CyberCrew/notmythos-8b` (8B, 128K contexto, poco propenso a rechazar consultas académicas legítimas). |
| `OLLAMA_SYSTEM_PROMPT` | *(rol de tutor académico)* | Prompt de sistema **multilínea** enviado en cada conversación: rol de **tutor académico universitario** de la Licenciatura en Ciberdefensa (FADENA-UNDEF) que exige respuestas de examen fundamentadas (definición técnica, fundamentos, mecánica operativa, defensa, enfoque estratégico y «Puntos clave que aseguran el 10»). Declara que todas las consultas son académicas y legítimas para evitar rechazos por seguridad. Se edita como valor multilínea entre comillas dobles en el `.env`. |
| `OLLAMA_TEMPERATURE` | `0.7` | 0.0–2.0. Déjalo vacío para usar el valor por defecto del modelo. Con NotMythos se recomienda `0.35` (salida técnica estable). |
| `OLLAMA_EXTRA_OPTIONS` | *(vacío)* | Parámetros extra del `/api/chat` de Ollama en JSON: `{"top_p":0.9,"top_k":40,"repeat_penalty":1.15,"num_ctx":8192,"num_predict":4096}` (top_* y repeat_penalty de la ficha de NotMythos; `num_predict` ampliado para respuestas largas de examen). |
| `OLLAMA_KEEP_ALIVE` | `5m` | Cuánto tiempo Ollama mantiene el modelo cargado (`5m`, `1h`, `-1`...). |
| `OLLAMA_TIMEOUT` | `900` | Timeout de lectura en segundos (incluye la carga en frío del modelo; margen amplio para respuestas largas de examen). |
| `OLLAMA_THINK` | *(vacío)* | Habilita/deshabilita el "thinking" de modelos que lo soportan (`qwen3`, `deepseek-r1`): `1` razona antes de responder (más rigor, más lento), `0` responde directo, vacío usa el comportamiento por defecto del modelo. Con modelos sin thinking el parámetro se ignora. |

### Conversación

| Variable | Por defecto | Descripción |
|---|---|---|
| `MAX_HISTORY_MESSAGES` | `20` | Mensajes `user`+`assistant` conservados (los más viejos se descartan; el prompt de sistema permanece). Evita consumo excesivo de memoria. |

### Ventana flotante

| Variable | Por defecto | Descripción |
|---|---|---|
| `WINDOW_WIDTH` / `WINDOW_HEIGHT` | `480` / `600` | Tamaño inicial (y de restauración si no hay geometría guardada). |
| `WINDOW_OPACITY` | `95` | Opacidad inicial (40–100). También editable desde el menú ☰. |
| `ALWAYS_ON_TOP` | `1` | Iniciar siempre visible sobre las demás ventanas (conmutable con 📌). |
| `HOVER_SCROLL_HOOK` | `1` | Hook Win32 de bajo nivel que garantiza el hover scroll aunque el ajuste del sistema esté desactivado. |

### Logs

| Variable | Por defecto | Descripción |
|---|---|---|
| `LOG_LEVEL` | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`. |
| `LOG_PRIVACY` | `1` | `1` = los logs **nunca** contienen el texto de las selecciones (solo longitudes). |

Los logs rotan en `local-bridge\logs\bridge.log` (1 MB × 3) en desarrollo y
en `%LOCALAPPDATA%\UNnexor\logs\` en la aplicación instalada.

### Rutas de datos (2.1.0)

| Variable | Por defecto | Descripción |
|---|---|---|
| `UNNEXOR_DATA_DIR` | `%LOCALAPPDATA%\UNnexor` (instalado) · `local-bridge\app\data` (desarrollo) | Directorio de ajustes, `.env`, perfiles y registros. Con este valor forzado no se ejecuta la migración. |
| `UNNEXOR_ENV_FILE` | `<datos>\.env` (instalado) · `local-bridge\.env` (desarrollo) | Archivo de configuración. |
| `UNNEXOR_MODELS_DIR` | `%LOCALAPPDATA%\UNnexor\models` | Modelos de voz descargados con consentimiento. |

Los nombres anteriores `EXTDATOR_DATA_DIR`, `EXTDATOR_ENV_FILE` y
`EXTDATOR_MODELS_DIR` se siguen aceptando (el nombre nuevo tiene prioridad).

**Migración desde Extdator/UNnexor 2.0:** al arrancar, si existe
`%LOCALAPPDATA%\Extdator`, sus archivos se copian a
`%LOCALAPPDATA%\UNnexor` (sin los logs antiguos). Nunca se sobrescribe un
archivo que ya exista en la carpeta nueva ni se borra la antigua; cada
copia se verifica por tamaño. El resultado queda en
`%LOCALAPPDATA%\UNnexor\migration.json` y en el registro; si hubo errores
(por ejemplo, un archivo bloqueado) se reintenta en el siguiente arranque.
Cuando compruebes que todo funciona puedes borrar la carpeta antigua (la
desinstalación con limpieza completa también lo hace).

## Configuración en caliente: `POST /api/config`

Sin reiniciar, puedes cambiar modelo, prompt de sistema, temperatura y
keep_alive:

```powershell
$token = (Get-Content local-bridge\.env | Select-String "^BRIDGE_TOKEN=(.+)").Matches[0].Groups[1].Value
Invoke-RestMethod -Uri "http://127.0.0.1:5000/api/config" `
  -Method Post -Headers @{ Authorization = "Bearer $token" } `
  -ContentType "application/json; charset=utf-8" `
  -Body ([System.Text.Encoding]::UTF8.GetBytes('{"model":"mistral","temperature":0.4}'))
```

Los cambios aplicados así **viven en memoria**: para hacerlos permanentes,
edítalos en `.env`. El endpoint devuelve `"note"` recordándolo.

## Ajustes persistentes del usuario: `/api/settings` (v1.2.0)

* `GET /api/settings` — esquema completo: valor efectivo de cada clave,
  `live` (aplicable en caliente), `restart` (requiere reiniciar), tipo y
  límites.
* `POST /api/settings` — `{"changes": {...}}`: valida cada clave
  (tipo, min/max, choices), aplica lo live en caliente y **persiste** el
  resto en `data\user_settings.json`. Devuelve `applied` y
  `restart_required`.
* `POST /api/settings/reset` — borra los overrides del usuario (vuelve a
  mandar todo al `.env`; el `.env` nunca se modifica).

Claves admitidas (además de las variables Ollama/ventana/logs del .env):
`provider` y `remote_model` (proveedor remoto activo), con validación
estricta; valor inválido → `422` con mensaje orientado al usuario.

Otros endpoints:

| Endpoint | Auth | Descripción |
|---|---|---|
| `GET /health` | no | Estado del puente y de Ollama (usado por start.ps1 y las opciones). |
| `GET /api/status` | sí | Modelo, generando, pendientes, historial, uptime. |
| `GET /api/models` | sí | Modelos instalados en Ollama + modelo configurado. |
| `POST /api/clear` | sí | Nueva conversación (vacía historial, aborta generación en curso). |
| `POST /api/config` | sí | Cambios de configuración en caliente (en memoria). |
| `GET /api/settings` | sí | Esquema + valores efectivos de ajustes. |
| `POST /api/settings` | sí | Validar/aplicar/persistir ajustes del usuario. |
| `POST /api/settings/reset` | sí | Eliminar overrides del usuario. |
| `GET /api/hardware` | sí | CPU/RAM/GPU/disco + `ollama_gpu_active` de terreno. |
| `GET /api/models/eval` | sí | Modelos instalados con semáforo según hardware. |
| `POST /api/pair/confirm` | no (código) | Emparejamiento: código de 6 dígitos → token. Un solo uso, 10 min, límite de intentos, solo orígenes `chrome-extension://`. |
| `POST /api/image` | sí | Captura base64 → cola de imagen (visión u OCR). |
| `GET /api/providers` | sí | Presets + estado de conexión (sin claves). |
| `POST /api/providers` | sí | Conectar/definir proveedor personalizado. |

## Captura por región y multimodalidad (v1.2.0)

El botón 📷 del panel abre un overlay por pantalla para seleccionar una
región. Después, un diálogo de vista previa permite:

* Escribir la instrucción (Enter = enviar, Shift+Enter = salto de línea).
* Elegir el método: **visión** (modelo multimodal activo) u **OCR**
  local (ver [`docs/OCR.md`](OCR.md)); si el modelo no tiene visión, el
  panel lo avisa en lugar de enviar la imagen a ciegas.
* Repetir la selección o cancelar.

Las imágenes temporales (`%TEMP%\unnexor_capture_*.png`) se borran tras
procesar. La imagen no sale de la máquina salvo que uses un proveedor
remoto con un modelo de visión (y solo hacia ese proveedor).

## Asistente de primer inicio (escritorio)

En el primer arranque real, el panel abre un asistente modal de tres pasos
(se puede reabrir desde **☰ → Asistente de inicio**):

1. **Bienvenida y privacidad** — qué hace UNnexor y qué datos salen del
   equipo (por defecto, solo hacia Ollama en `127.0.0.1`).
2. **Requisitos** — comprueba Ollama y el modelo activo en segundo plano,
   sin descargar nada. Permite reintentar, elegir otro modelo o abrir
   Ajustes; se puede continuar aunque falte algo para configurarlo luego.
3. **Conectar extensión** — muestra el código efímero de 6 dígitos y
   detecta automáticamente cuándo la extensión completa la vinculación.

Se muestra una sola vez (clave `onboarding/seen` en `QSettings`). En
pruebas y smoke tests con plataforma `offscreen` no se abre. No modifica
el `.env`, no descarga modelos y no envía datos a terceros.

## Exportar diagnóstico (soporte)

**☰ → Exportar diagnóstico…** genera un ZIP (por ejemplo
`UNnexor-diagnostico-20261124-1030.zip`) con `LEEME.txt`,
`resumen.json`, `diagnostico.txt` y `logs/bridge.log*` si existen. La
recogida de estado de Ollama y la lectura de logs ocurren en segundo plano;
la interfaz sigue respondiendo.

* Incluye: versión, SO, rutas normalizadas, configuración sin secretos,
  comprobaciones, estado de Ollama/modelo, emparejamiento (solo sí/no),
  recuento de historial y logs redactados.
* No incluye: `BRIDGE_TOKEN`, claves API, `.env`, `user_settings.json`
  completo, historial de conversación ni capturas.
* Los logs se limitan a los 4 archivos más recientes (~200 KB por archivo,
  ~600 KB en total) y se redactan tokens, claves, correos, rutas de usuario,
  IDs de extensión y parámetros de URL.
* Con `LOG_PRIVACY=0` se omiten las líneas de selección/imagen conocidas,
  pero conviene revisar el ZIP antes de compartirlo.

## Extensión (opciones — asistente v1.2.0)

La página de opciones es un asistente de 4 pasos:

1. **Bienvenida** — cómo funciona UNnexor.
2. **Conectar** — pega el código de 6 dígitos del panel (☰ → Emparejar
   extensión). La extensión obtiene su token por `POST /api/pair/confirm`
   (el código vale 10 minutos y un solo uso). Abajo queda un panel
   colapsado **manual avanzado** para pegar URL + token a mano (como en
   v1.1.0), con botón de mostrar/ocultar.
3. **Verificar** — diagnóstico de tres pasos: puente activo (`/health`),
   autenticación (`/api/status`) y envío de prueba real
   (`POST /api/selection`).
4. **Preferencias** — toggle *Mostrar el botón ✦ al seleccionar texto*
   (`showFab`; el content script lo respeta en vivo vía
   `chrome.storage.onChanged`).

La configuración técnica (modelo, temperatura, prompt...) **no se edita
aquí**: la fuente única es la aplicación de escritorio (menú ☰), para
no duplicar valores.

## Cambiar de modelo

**Desde la aplicación (recomendado):** menú ☰ → **Centro de control** →
**Modelos de IA**. Pulsa **Descargar** en un modelo compatible (se pide
confirmación con el tamaño; el progreso es real y la descarga continúa
aunque cierres la ventana) y después **Seleccionar**. O bien **Recomendar
configuración → Aplicar**, que elige, descarga y activa los modelos
adecuados para tu equipo tras tu autorización.

**Manual:** `ollama pull mistral`, edita `OLLAMA_MODEL=mistral` en el
`.env` y reinicia (`scripts\stop.ps1` y `scripts\start.ps1`), o usa
`POST /api/config` sin reiniciar.

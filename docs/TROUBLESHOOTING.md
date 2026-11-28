# Solución de problemas (UNnexor)

## La extensión no conecta con el puente

**Toast: "No se pudo conectar con el puente local"**

1. ¿Arrancaste el puente? `powershell -File scripts\start.ps1`
2. Verifica: `curl http://127.0.0.1:5000/health` → debe responder JSON con
   `"app":"unnexor-bridge"` (`"extdator-bridge"` en versiones anteriores a 2.1).
3. ¿Cambiaste `BRIDGE_PORT`? Actualiza también la URL en las opciones de la
   extensión.

**Toast: "El puente local no respondió (timeout)"** — el puente tardó más de
15 s; probablemente se colgó. Revisa `local-bridge\logs\bridge.log` y reinicia.

## Token y origen

**Toast: "Token inválido" (HTTP 401)**

* El token de las opciones de la extensión debe coincidir con la línea
  `BRIDGE_TOKEN` de `local-bridge\.env` (sin espacios). Si regeneraste el
  `.env`, vuelve a copiar el token.

**Toast: "Origen no autorizado" (HTTP 403)**

* Alguien (¿una página web?) intentó usar el puente sin ser la extensión, o
  tu `ALLOWED_ORIGINS` no incluye `chrome-extension://<TU_ID>`. Copia el ID
  desde las opciones de la extensión y ponlo en el `.env`.

## Instalador, SmartScreen y desinstalación

**SmartScreen muestra "aplicación desconocida"**

El instalador no está firmado con Authenticode. Verifica el SHA-256 con el
`SHA256SUMS.txt` que acompaña al instalador; si coincide, elige
**Más información > Ejecutar de todos modos**. No ejecutes un instalador
cuya suma no coincida.

**Quiero borrar el token y las claves API sin desinstalar**

```powershell
& "$env:LOCALAPPDATA\Programs\UNnexor\UNnexor.exe" --purge-secrets
```

Elimina las claves de proveedores del Administrador de credenciales,
vacía `BRIDGE_TOKEN` y retira `*_API_KEY` del `.env`; conserva el resto de
ajustes. Al volver a abrir UNnexor se genera un token nuevo y hay que
volver a emparejar la extensión.

**Al desinstalar se conservan mis datos**

Es el comportamiento por defecto. Si querías borrarlos, desinstala de nuevo
(o usa Configuración > Aplicaciones) y elige la limpieza completa. También
puedes borrar manualmente `%LOCALAPPDATA%\UNnexor` (y la copia antigua `%LOCALAPPDATA%\Extdator`, si existe).

## Ollama

**"No se pudo conectar con Ollama" / indicador rojo**

* Abre la aplicación de Ollama (Windows) o ejecuta `ollama serve`.
* Verifica `curl http://127.0.0.1:11434/api/version`.
* Si usas otra dirección/puerto (`OLLAMA_HOST`), ajústalo en el `.env`.

**"El modelo 'x' no está disponible en Ollama"**

* Instálalo: `ollama pull x` — o cambia `OLLAMA_MODEL` a uno que ya tengas
  (`ollama list`). El panel lista los modelos disponibles cuando ocurre.

**La generación tarda mucho o corta por timeout**

* La primera petición carga el modelo (puede tardar decenas de segundos con
  modelos grandes). Sube `OLLAMA_TIMEOUT` o usa un modelo más ligero.
* `OLLAMA_KEEP_ALIVE` más alto evita recargas repetidas.

## Puerto 5000 ocupado

* `scripts\start.ps1` detecta el puerto ocupado y te da el PID:
  `Get-Process -Id <PID>`.
* Si es otra aplicación, cámbialo en el `.env` (`BRIDGE_PORT=5001`) y
  actualiza la URL en las opciones de la extensión.
* Si quedó un UNnexor huérfano: `scripts\stop.ps1`.

## El Hover Scroll no funciona (rueda sobre el panel sin clic)

UNnexor usa tres capas; revisa cuál falta:

1. **Hook interno** (`HOVER_SCROLL_HOOK=1`, por defecto activo): es el
   mecanismo principal y funciona aunque el sistema esté configurado al
   contrario. Comprueba que no lo pusiste a `0`.
2. **Ajuste de Windows**: Configuración → Bluetooth y dispositivos → Ratón →
   "Desplazar ventanas inactivas al pasar el ratón por encima de ellas" →
   activado. (Si el hook está activo, no hace falta tocarlo.)
3. **Respaldo AHK**: ejecuta `scripts\hover_scroll_helper.ahk` con
   [AutoHotkey v2](https://www.autohotkey.com/) solo si desactivaste el hook
   y el ajuste del sistema.

Verificación automática:
`.\local-bridge\.venv\Scripts\python.exe -m pytest local-bridge\tests\test_hover_scroll_integration.py -v`
(abre el panel real unos segundos y valida rueda + foco + clic).

## El botón ✦ no aparece

Verificado con diagnóstico CDP sobre Chromium real (v1.1.0, `tests-manual/`):

**1. Pestañas abiertas ANTES de instalar la extensión** (causa más
común). Los content scripts de MV3 solo se inyectan en páginas cargadas a
partir de ese momento. Para no solicitar permisos de host globales, la
extensión no re-inyecta en pestañas existentes: recarga esas pestañas (F5)
o abre una nueva. Las páginas nuevas reciben el script automáticamente.

**2. Contenido dentro de iframes** (Google Docs, algunos foros/editores).
Desde v1.1.0 el content script corre con `all_frames: true`; en la v1.0.0
(`all_frames: false`) el botón no aparecía dentro de iframes.

**3. Páginas donde Chromium no permite content scripts** (limitación de la
plataforma, no es un bug): `chrome://`, `edge://`, `brave://`, la Chrome Web
Store, visores de PDF internos y vistas `file://` (fuera de los `matches` del
manifest: solo `http://` y `https://`).

**4. Selección vacía o de solo espacios** — por diseño no hay botón.

**5. La extensión no está activa en ese navegador** — verifica en
`chrome://extensions` que la tarjeta de UNnexor esté activada y que no haya
errores ("Errores" en rojo). Tras copiar/pegar archivos, usa el botón de
recargar (⟳) de la tarjeta.

## El botón aparece pero al pulsarlo no envía nada

Causa histórica (v1.0.0, corregida en v1.1.0): el botón vive en un Shadow DOM
**cerrado**, y el listener global de `mousedown` usaba
`composedPath().includes(btn)` — con un root cerrado ese check es siempre
false, así que el clic ocultaba el botón en la fase de captura y el `click`
nunca llegaba (sin toast, sin POST). El fix compara el target retargetizado
(`e.target === host`). Si observas ese síntoma, actualiza a v1.1.0 y recarga
la extensión.

## El botón aparece y la selección se pierde al hacer clic

No debería: el botón cancela el `mousedown` para preservar la selección y
captura el texto en el momento del clic. Si ocurre en algún sitio concreto,
probablemente ese sitio escucha `mousedown` en captura a nivel de documento
y borra la selección; reporta el caso (workaround: usar el menú contextual
del botón derecho no está implementado).

## La ventana flotante no aparece

* ¿Arrancaste con `-ServerOnly`? Ese modo no abre panel.
* ¿Opacidad muy baja? Menú ☰ del panel o `WINDOW_OPACITY` en el `.env`.
* Revisa `local-bridge\logs\bridge.log` por errores de PySide6.

## Emparejamiento de la extensión (v1.2.0)

**"Código inválido o caducado" (403)**

* Cada código vale **10 minutos y un solo uso**. Genera uno nuevo:
  panel → ☰ → Emparejar extensión → "Generar código nuevo".
* Verifica que el código sea el actual (el diálogo lo refresca).

**"Demasiados intentos" (429)**

* Seis intentos fallidos activan el límite. Espera un minuto.

**"Origen no autorizado para emparejar" (403)**

* El emparejamiento solo se acepta desde una extensión
  (`chrome-extension://`): una página web no puede obtener el token por
  este camino (protección anti-phishing).

**Aun así prefiero el token a mano**: opciones de la extensión → paso 2 →
"Conexión manual avanzada" → pega `BRIDGE_TOKEN` del `.env`.

## Captura por región (botón 📷, v1.2.0)

**El overlay no cubre el monitor correcto / la imagen sale desplazada**

* Hay un overlay por pantalla; el escalado de Windows (DPI) se tiene en
  cuenta, pero multimonitor con escalados mezclados es el caso menos
  probado: si ves un desplazamiento, repórtalo con tu configuración de
  pantallas.

**"El modelo activo no procesa imágenes"**

* El modelo configurado no tiene visión (p. ej. `qwen3:8b`). Instala un
  modelo multimodal, p. ej. `ollama pull qwen2.5vl:7b` (~6 GB; se pide
  confirmación antes de descargar nada) y elígelo en ☰ → Centro de control → Modelos de IA. En una
  RX 590 de 8 GB qwen2.5vl se clasifica 🟠 ALTO CONSUMO (cabe justo).

**"Ollama devolvió HTTP 400: ... does not support thinking" (corregido)**

* Con `OLLAMA_THINK=1`, versiones anteriores enviaban el parámetro
  `think` a modelos sin esa capacidad y Ollama rechazaba el chat. El
  cliente actual comprueba las capacidades del modelo (`/api/show`,
  cacheado) y solo envía `think` a modelos "thinking"; si Ollama aun así
  lo rechaza, reintenta una vez sin el parámetro. Si ves este error,
  actualiza a esta versión.

**No aparece la opción OCR**

* Ningún motor OCR está instalado. Instala uno (ver abajo). UNnexor
  nunca los instala por su cuenta.

**Los archivos `unnexor_capture_*.png` se quedan en %TEMP%**

* Solo debería quedar alguno si el proceso se cerró a mitad de flujo;
  se borran tras procesar. Puedes borrarlos sin riesgo.

## OCR: instalación y versiones compatibles (v1.2.0)

* Fórmulas matemáticas → Markdown/LaTeX:
  `local-bridge\.venv\Scripts\pip.exe install pix2text`
  (descarga ~120 MB de pesos ONNX al primer uso; el resto de
  dependencias ya se instala con el paquete).
* **Importante**: pix2text arrastra `rapidocr` y el instalador puede
  resolver una versión 3.10+ **incompatible** con `cnstd` (error
  `cannot import name 'resolve_model_key'`). Fija la serie 3.9:
  `pip install "rapidocr==3.9.2"`.
* Solo texto plano (más ligero): `pip install rapidocr-onnxruntime`.
* Verificación rápida sin interfaz:
  `local-bridge\.venv\Scripts\python.exe local-bridge\scripts\ocr_e2e.py`
  genera una imagen con fórmulas, la transcribe y muestra el Markdown
  resultante.

## Fórmulas y código en las respuestas

Desde 2.1.0 las fórmulas se **componen** (fracciones apiladas, raíces,
sumatorias, integrales, matrices, sistemas) con ziamath y el código se
resalta con Pygments, sin motor web ni JavaScript (ADR-0006).

* **Una fórmula aparece en Unicode (`(-b ± √(b² - 4ac))/(2a)`) en lugar de
  compuesta**: la expresión estaba mal formada o usa un comando que
  ziamath no soporta. Es el respaldo legible; el resto de la respuesta se
  muestra igual. Pasa el ratón por encima para ver el LaTeX original.
* **Se detectan como fórmula**: `$…$`, `$$…$$`, `\(…\)`, `\[…\]` y entornos
  `\begin{cases|pmatrix|bmatrix|aligned|…}`. Lo que está dentro de bloques
  de código (```` ``` ````) o de código en línea (`` `…` ``) nunca se toma
  como fórmula, y "cuesta $5 y $10" se queda igual.
* **El código no se colorea**: sin etiqueta de lenguaje (```` ```python ````)
  se intenta adivinar; si la confianza es baja se muestra sin colores pero
  con fondo, fuente monoespaciada y «Copiar código».
* **Qué se copia**: «Copiar código» copia el código original exacto; el
  botón ⧉ de la cabecera copia la respuesta en texto legible (fórmulas en
  Unicode). El historial que se envía al modelo conserva el LaTeX original.
* **Comprobar el renderizado en un EXE**: `UNnexor.exe --check` escribe en
  `self-check.json` la sección `rendering` (`math`, `code`, `sql`).

**El texto de una respuesta se corta al redimensionar la ventana (corregido)**

* El alto de cada burbuja se recalcula al cambiar el ancho
  (`resizeEvent` + actualización diferida). Si lo ves en una versión
  anterior, reinicia el puente con `scripts\stop.ps1` +
  `scripts\start.ps1` para cargar el código nuevo.

## Proveedores de IA remotos (v1.2.0)

**"Clave inválida" / 401 al conectar**

* Copia la clave completa del proveedor (a veces se copia con un espacio
  final). La clave se valida con una petición real antes de guardarse.

**402/403 (sin crédito o sin permiso) · 429 (cuota)**

* Estados del propio proveedor; el diálogo lo traduce. No es un problema
  local. En pruebas usa una clave de capa gratuita (Groq, Mistral...).

**La respuesta tarda o falla solo con proveedor remoto**

* Revisa tu conexión y el estado del proveedor. El streaming es SSE; si
  el proveedor corta, el panel muestra el error y la cola queda libre.

**Quiero volver a Ollama**

* ☰ → Centro de control → Modelos de IA y elige un modelo local; o borra el proveedor en
  ☰ → Proveedores (la clave se elimina del keyring).

**¿Dónde está mi clave?**

* En el Windows Credential Manager (servicio `UNnexor`, cuenta
  `provider-key:<id>`). `data\providers.json` nunca contiene claves.

## Los ajustes de la UI no se aplican (v1.2.0)

* Ajustes marcados "requiere reiniciar" (timeout, host, hooks, logs...)
  solo aplican tras `scripts\stop.ps1` + `scripts\start.ps1`. El diálogo
  te lo avisa.
* Para volver a los valores del `.env`: ☰ → Ajustes → Restaurar, o
  `POST /api/settings/reset`. El archivo `data\user_settings.json` se
  vacía (el `.env` nunca se modifica).

## La extensión no carga en Edge

* `edge://extensions` → Modo de desarrollador → si Edge muestra
  "Extensiones de otros orígenes", acéptalo.
* Edge/Brave usan el mismo Manifest V3; no se necesita código distinto.

## Errores HTTP que devuelve el puente

| Código | Significado | Qué hacer |
|---|---|---|
| 401 | Token ausente/inválido | Copiar `BRIDGE_TOKEN` a las opciones. |
| 403 | Origen no autorizado | Revisar `ALLOWED_ORIGINS`. |
| 413 | Texto demasiado extenso | Reducir la selección o subir `MAX_TEXT_LENGTH`. |
| 422 | JSON inválido / texto vacío | Reintenta la selección. |
| 429 | Límite de tasa | Espera unos segundos (`Retry-After`). |
| 503 | Cola saturada / Ollama caído | Espera la generación o arranca Ollama. |

## Paquete de diagnóstico para soporte (v1.2.0)

1. Abre el menú **☰ → Exportar diagnóstico…**.
2. Elige dónde guardar el ZIP y espera el aviso de confirmación.
3. Revisa `diagnostico.txt` y adjunta el ZIP a tu informe junto con una
   descripción de lo que ocurrió.

Incluye versión, SO, rutas normalizadas, configuración sin secretos, estado
de Ollama/modelo, emparejamiento (solo sí/no), recuento de historial y logs
redactados. **No incluye** el token del puente, claves API, `.env`,
`user_settings.json` completo, historial de conversación ni capturas.

La redacción elimina tokens, claves, correos, rutas de usuario, IDs de
extensión y parámetros de URL. Si tienes `LOG_PRIVACY=0`, el exportador
omite las líneas de selección/imagen conocidas, pero revisa siempre el ZIP
antes de compartirlo. Para un diagnóstico por consola, sin interfaz:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\check_environment.ps1
```

## Diagnóstico rápido

```powershell
powershell -ExecutionPolicy Bypass -File scripts\check_environment.ps1
```

imprime versiones, dependencias, estado de Ollama/modelos, puertos, puente y
navegadores detectados.

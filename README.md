# Extdator

**Asistente local inmediato**: selecciona texto en Chrome, Brave o Edge,
pulsa el botón ✦ y recibe la respuesta de tu modelo local (Ollama) en un
panel flotante **siempre visible** — sin perder el foco del navegador. Por
defecto no usa la nube; los proveedores remotos son opcionales.

> **v1.2.0** — selector de modelos con semáforo según tu hardware,
> proveedores de IA remotos (clave en el keyring del sistema),
> configuración desde la propia app (menú ⚙), captura por región con
> visión u OCR local, emparejamiento de la extensión por código de 6
> dígitos y asistentes de primer inicio en la extensión y en el escritorio.

## Estado del proyecto

| Área | Estado |
|---|---|
| Extensión MV3 | Funcional; paquete de tienda generado y validado localmente |
| Puente local + panel | Funcional; ejecutable one-folder generado y validado |
| Pruebas | `262 passed, 1 skipped` (2026-11-23) |
| Instalador Windows | **Generado y probado localmente** (Inno Setup por usuario) |
| Publicación en tiendas | No iniciada; requiere cuentas, URL de privacidad, capturas y licencia |
| Licencia | **Pendiente de decisión del propietario** (ver abajo) |

El repositorio remoto es privado. No se publican releases ni se cambia la
visibilidad sin autorización expresa.

```
Selección en el navegador → Botón ✦ → Puente local (FastAPI)
                              → Ollama local (streaming HTTP)
                              → Panel flotante PySide6 (always on top + hover scroll)
```

## Módulos

| Carpeta | Qué es |
|---|---|
| `browser-extension/` | Extensión Manifest V3 (Chrome/Brave/Edge): botón contextual sobre la selección + envío al puente. |
| `local-bridge/` | Servidor local Python (FastAPI) + panel flotante PySide6 + cliente Ollama con streaming real. |
| `deployment/` | Empaquetado reproducible de la extensión, ejecutable one-folder e instalador Windows, con pruebas de humo e integridad. |
| `scripts/` | PowerShell: `install.ps1`, `start.ps1`, `stop.ps1`, `check_environment.ps1` (+ helper opcional AHK v2). |
| `docs/` | Instalación, configuración, proveedores, OCR, guías de tienda, ADR y auditoría. |

## Inicio rápido

```powershell
cd G:\Proyectos\Extdator

# 1) Instalar (venv + dependencias + .env con token + chequeo de Ollama)
powershell -ExecutionPolicy Bypass -File scripts\install.ps1

# 2) Arrancar (servidor + panel flotante)
powershell -ExecutionPolicy Bypass -File scripts\start.ps1

# 3) Cargar la extensión
#    chrome://extensions → Modo desarrollador → Cargar descomprimida
#    → carpeta browser-extension → abre "Opciones" y sigue el asistente:
#    panel ⚙ → Emparejar extensión → código de 6 dígitos → Conectar.
#    (Alternativa manual: pegar el token de local-bridge\.env.)
```

Detalles completos: [`docs/INSTALLATION.md`](docs/INSTALLATION.md).

### Empaquetar la extensión

```powershell
python deployment\extension\build.py --check   # validación de manifest y recursos
python deployment\extension\build.py           # ZIPs + SHA-256 + informe
```

Genera `release/browser-extension/chrome.zip` y `edge.zip` (ignorados por
Git) con `SHA256SUMS.txt` y `build-report.json`. Los paquetes solo incluyen
archivos de producción. Estado de tiendas:
[Chrome Web Store](docs/stores/CHROME_WEB_STORE.md),
[Edge Add-ons](docs/stores/EDGE_ADDONS.md),
[permisos](docs/stores/PERMISSIONS.md) y
[matriz de cumplimiento](docs/stores/COMPLIANCE_MATRIX.md).

### Empaquetar la aplicación de escritorio

```powershell
cd local-bridge
.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.venv\Scripts\python.exe ..\deployment\windows\build.py
```

Genera `release/windows/Extdator/Extdator.exe` (one-folder, sin consola),
un ZIP `Extdator-<versión>-win64-unsigned.zip`, `SHA256SUMS.txt` y un
informe con pruebas de humo headless y GUI offscreen. El build no está
firmado con Authenticode y no incluye modelos, claves ni OCR pesado.

### Generar el instalador de Windows

```powershell
cd local-bridge
.venv\Scripts\python.exe ..\deployment\installer\build.py
```

Requiere Inno Setup 6. Compila y prueba el ciclo completo en directorios
temporales: instalación silenciosa por usuario, `Extdator.exe --check`,
desinstalación con datos preservados, `--purge-secrets` y limpieza
completa. Genera `release/installer/Extdator-<versión>-win64-setup.exe`,
`SHA256SUMS.txt` y un informe. El instalador se instala por usuario en
`%LOCALAPPDATA%\Programs\Extdator` y guarda los datos en
`%LOCALAPPDATA%\Extdator`.

No hay release pública todavía. Para borrar solo credenciales y el token
sin desinstalar:

```powershell
& "$env:LOCALAPPDATA\Programs\Extdator\Extdator.exe" --purge-secrets
```

El token se regenera en el siguiente arranque y la extensión deberá
volver a emparejarse.

### CI, SBOM y verificación de artefactos

GitHub Actions ejecuta en Windows la suite de pruebas y el empaquetado
completo (extensión, escritorio, instalador y SBOM) en cada push o pull
request a `main` y `develop`. El flujo está en `.github/workflows/ci.yml`.
No publica releases ni firma artefactos: solo construye, prueba y sube
artefactos temporales de CI.

Para reproducirlo en local, después de compilar los tres paquetes:

```powershell
cd local-bridge
.venv\Scripts\python.exe ..\deployment\sbom\build.py
.venv\Scripts\python.exe ..\deployment\verify_artifacts.py
```

El SBOM es CycloneDX 1.5 e inventaría el entorno Python de build/runtime,
la extensión y el puente. La verificación comprueba los SHA-256, los
informes de build y el propio SBOM, y escribe
`release/verification-report.json`. Detalles y límites:
[`docs/CI.md`](docs/CI.md).

## Características

**Extensión**
* Botón ✦ minimalista (Shadow DOM) sobre el inicio de la selección, con
  animación, hover/active, reposicionado en scroll y ocultado automático.
* Texto enviado tal cual: acentos, símbolos, saltos de línea.
* Estados de error claros (backend caído, timeout, token, 413, 429...).
* Permisos mínimos: `storage` + acceso solo a `127.0.0.1`/`localhost`.
* Primer arranque guiado: abre el asistente de vinculación si aún no hay
  conexión.

**Puente local**
* `POST /api/selection` con validación estricta, token compartido, CORS por
  origen autorizado, límite de tamaño y de tasa. Solo `127.0.0.1`.
* Cola FIFO thread-safe → Ollama nunca bloquea el servidor HTTP.
* Streaming real de `/api/chat` (NDJSON), historial conversacional en
  memoria (como `ollama run`) y limpieza de contexto.
* Cambio de modelo en caliente (`POST /api/config`) o por `.env`.
* Ajustes persistentes desde la UI (`GET/POST /api/settings`) con
  validación, aplicación en vivo y `reset` a los valores del `.env`.
* Detección de hardware real (`/api/hardware`) y evaluador de modelos con
  semáforo 🟢🟡🟠🔴 (`/api/models/eval`; rojo solo fundado y deshabilitado).
* Emparejamiento seguro: `POST /api/pair/confirm` con código efímero de
  6 dígitos (un solo uso, 10 min, límite de intentos, solo orígenes de
  extensión).
* Asistente de primer inicio del escritorio: privacidad, comprobación de
  Ollama y del modelo activo con reintento, y emparejamiento con detección
  de la conexión. Se muestra una vez y se reabre desde ⚙.
* Capturas por región: `POST /api/image` (PNG validado, tamaño limitado)
  → cola de imagen → visión multimodal u OCR local opcional.
* Logs sin contenido privado por defecto (`LOG_PRIVACY=1`).

**Proveedores remotos (opcional)**
* 12 presets OpenAI-compatible verificados (OpenAI, Groq, OpenRouter,
  Mistral, Cerebras, Together, xAI, Moonshot, HF Router, Z.ai, NVIDIA
  NIM, Gemini) + proveedor `custom`.
* Claves conectadas desde el panel en el Windows Credential Manager
  (keyring); `data\providers.json` guarda estado sin claves. Las
  `*_API_KEY` que pongas manualmente en `.env` se migran sin borrarse y
  pueden retirarse con `--purge-secrets`.
* Streaming SSE, errores traducidos a mensajes claros, cambio
  local↔remoto en caliente desde el panel.

**Panel flotante**
* Estética terminal oscura, Markdown + bloques de código al finalizar.
* **Always on Top** y **no-activación**: nunca roba el foco.
* **Hover Scroll verificado empíricamente**: rueda sobre el panel sin clic
  (hook `WH_MOUSE_LL` + enrutamiento nativo de Windows).
* Autoscroll inteligente (se pausa si subes a leer; se reanuda al final).
* Copiar respuesta, nueva conversación, detener generación, opacidad,
  minimizar/cerrar, persistencia de posición/tamaño.
* Menú ⚙: Modelos (semáforo), Proveedores, Ajustes y Emparejar extensión;
  botón 📷 para capturar una región de la pantalla.

## Privacidad

El texto seleccionado va **solo** a tu Ollama local (`127.0.0.1`),
salvo que conectes expresamente un proveedor remoto (entonces va a ese
proveedor y únicamente a él). Las claves API conectadas desde el panel
viven en el keyring del sistema; las que pongas manualmente en `.env`
pueden retirarse con `--purge-secrets`. Las capturas de pantalla se borran
de `%TEMP%` tras procesarlas. Ninguna petición sale de tu máquina sin que tú lo configures.
Los logs no registran el contenido de las selecciones por defecto. No hay
analítica ni publicidad. Política completa:
[`browser-extension/privacy.html`](browser-extension/privacy.html) (su
publicación en una URL pública para las tiendas está pendiente).

## Documentación

* [Arquitectura y decisiones técnicas](ARCHITECTURE.md)
* [Instalación paso a paso](docs/INSTALLATION.md)
* [Configuración completa](docs/CONFIGURATION.md)
* [Proveedores de IA remotos](docs/PROVIDERS.md)
* [OCR local para capturas](docs/OCR.md)
* [Catálogo de modelos y evaluador](docs/MODELS_CATALOG.md)
* [Solución de problemas](docs/TROUBLESHOOTING.md)
* [Mapa de dependencias](docs/DEPENDENCY_MAP.md)
* [CI, SBOM y verificación](docs/CI.md)
* [Preparación para Chrome Web Store](docs/stores/CHROME_WEB_STORE.md)
* [Preparación para Edge Add-ons](docs/stores/EDGE_ADDONS.md)
* [Permisos de la extensión](docs/stores/PERMISSIONS.md)
* [Matriz de cumplimiento](docs/stores/COMPLIANCE_MATRIX.md)
* [Auditoría inicial](docs/AUDIT_2026-11-19.md)
* [Registro de cambios](CHANGELOG.md)

## Pruebas

```powershell
.\local-bridge\.venv\Scripts\python.exe -m pytest local-bridge\tests -v
```

Cubre: validación/seguridad del servidor, CORS, streaming de Ollama
(simulado), historial, configuración, ajustes persistentes, hardware y
evaluador de modelos, emparejamiento, proveedores (transporte simulado),
OCR (motores opcionales con mocks), límites de arquitectura, rutas de
runtime, validación/empaquetado de la extensión, purga de secretos y una
prueba de integración real de Hover Scroll/no-activación sobre Windows.
El instalador añade su propia prueba de ciclo completo (instalar,
auto-chequeo, preservar datos, purgar y desinstalar). Complemento GUI
offscreen: `local-bridge\scripts\gui_smoke.py` (abre los cinco diálogos sin
pantalla).

## Licencia

El proyecto **no tiene todavía una licencia principal aprobada**. Hasta que
el propietario lo decida, el código queda bajo copyright y no se concede
permiso de redistribución pública. Ver
[ADR-0004](docs/adr/0004-project-license-pending.md).

Las dependencias de terceros conservan sus licencias; el inventario y los
avisos se mantienen en [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

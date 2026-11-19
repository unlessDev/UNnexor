# Extdator

**Asistente local inmediato**: selecciona texto en Chrome, Brave o Edge,
pulsa el botón ✦ y recibe la respuesta de tu modelo local (Ollama) en un
panel flotante **siempre visible** — sin perder el foco del navegador, sin
nube, sin servicios externos.

> **v1.2.0** — selector de modelos con semáforo según tu hardware,
> proveedores de IA remotos (clave en el keyring del sistema),
> configuración desde la propia app (menú ⚙), captura por región con
> visión u OCR local, emparejamiento de la extensión por código de 6
> dígitos y asistente de puesta en marcha en options.

## Estado del proyecto

| Área | Estado |
|---|---|
| Extensión MV3 | Funcional; empaquetado de tienda en preparación |
| Puente local + panel | Funcional; rutas de runtime para instalación en preparación |
| Pruebas | `202 passed, 1 skipped` (2026-11-19) |
| Instalador Windows | En preparación (PyInstaller + Inno Setup) |
| Publicación en tiendas | No iniciada; requiere IDs, licencia y revisión |
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
| `scripts/` | PowerShell: `install.ps1`, `start.ps1`, `stop.ps1`, `check_environment.ps1` (+ helper opcional AHK v2). |
| `docs/` | `INSTALLATION.md`, `CONFIGURATION.md`, `TROUBLESHOOTING.md`, `PROVIDERS.md`, `OCR.md`, `MODELS_CATALOG.md`, ADR y auditoría. |

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

## Características

**Extensión**
* Botón ✦ minimalista (Shadow DOM) sobre el inicio de la selección, con
  animación, hover/active, reposicionado en scroll y ocultado automático.
* Texto enviado tal cual: acentos, símbolos, saltos de línea.
* Estados de error claros (backend caído, timeout, token, 413, 429...).
* Permisos mínimos: `storage` + host solo para `127.0.0.1`.

**Puente local**
* `POST /api/selection` con validación estricta, token compartido, CORS por
  origen autorizado, límite de tamaño y de tasa. Solo `127.0.0.1`.
* Cola FIFO thread-safe → Ollama nunca bloquea el servidor HTTP.
* Streaming real de `/api/chat` (NDJSON), historial conversacional
  persistente (como `ollama run`) y limpieza de contexto.
* Cambio de modelo en caliente (`POST /api/config`) o por `.env`.
* Ajustes persistentes desde la UI (`GET/POST /api/settings`) con
  validación, aplicación en vivo y `reset` a los valores del `.env`.
* Detección de hardware real (`/api/hardware`) y evaluador de modelos con
  semáforo 🟢🟡🟠🔴 (`/api/models/eval`; rojo solo fundado y deshabilitado).
* Emparejamiento seguro: `POST /api/pair/confirm` con código efímero de
  6 dígitos (un solo uso, 10 min, límite de intentos, solo orígenes de
  extensión).
* Capturas por región: `POST /api/image` (PNG validado, tamaño limitado)
  → cola de imagen → visión multimodal u OCR local opcional.
* Logs sin contenido privado por defecto (`LOG_PRIVACY=1`).

**Proveedores remotos (opcional)**
* 12 presets OpenAI-compatible verificados (OpenAI, Groq, OpenRouter,
  Mistral, Cerebras, Together, xAI, Moonshot, HF Router, Z.ai, NVIDIA
  NIM, Gemini) + proveedor `custom`.
* Claves solo en el Windows Credential Manager (keyring);
  `data\providers.json` guarda estado sin claves.
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
proveedor y únicamente a él). Las claves API viven en el keyring del
sistema y las capturas de pantalla se borran de `%TEMP%` tras
procesarlas. Ninguna petición sale de tu máquina sin que tú lo configures.
Los logs no registran el contenido de las selecciones por defecto.

## Documentación

* [Arquitectura y decisiones técnicas](ARCHITECTURE.md)
* [Instalación paso a paso](docs/INSTALLATION.md)
* [Configuración completa](docs/CONFIGURATION.md)
* [Proveedores de IA remotos](docs/PROVIDERS.md)
* [OCR local para capturas](docs/OCR.md)
* [Catálogo de modelos y evaluador](docs/MODELS_CATALOG.md)
* [Solución de problemas](docs/TROUBLESHOOTING.md)
* [Auditoría inicial](docs/AUDIT_2026-11-19.md)
* [Registro de cambios](CHANGELOG.md)

## Pruebas

```powershell
.\local-bridge\.venv\Scripts\python.exe -m pytest local-bridge\tests -v
```

Cubre: validación/seguridad del servidor, CORS, streaming de Ollama
(simulado), historial, configuración, ajustes persistentes, hardware y
evaluador de modelos, emparejamiento, proveedores (transporte simulado),
OCR (motores opcionales con mocks) y una prueba de integración real de
Hover Scroll/no-activación sobre Windows. Complemento GUI offscreen:
`local-bridge\scripts\gui_smoke.py` (abre los cinco diálogos sin pantalla).

## Licencia

El proyecto **no tiene todavía una licencia principal aprobada**. Hasta que
el propietario lo decida, el código queda bajo copyright y no se concede
permiso de redistribución pública. Ver
[ADR-0004](docs/adr/0004-project-license-pending.md).

Las dependencias de terceros conservan sus licencias; el inventario y los
avisos se mantienen en [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

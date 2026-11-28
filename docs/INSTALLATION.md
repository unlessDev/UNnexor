# Instalación de UNnexor

## Requisitos

| Componente | Versión mínima | Comprobación |
|---|---|---|
| Windows | 10 / 11 de 64 bits | — |
| Python | 3.10+ (solo desarrollo) | `python --version` |
| Ollama | cualquier versión con API HTTP (0.1.x+) | `ollama --version` |
| Un modelo en Ollama | p. ej. `llama3` | `ollama list` |
| Navegador | Chrome, Brave o Edge (Chromium ≥ 110) | — |

Puedes ejecutar `scripts\check_environment.ps1` para un diagnóstico completo.

## Descarga directa (Windows)

> **[⬇ Descargar UNnexor para Windows — instalador `.exe`](https://github.com/unlessDev/UNnexor/releases/latest/download/UNnexor-Setup-Windows-x64.exe)**
>
> Windows 10/11 de 64 bits · no requiere administrador · ~85 MB.
> Otras descargas (ZIP portable, SHA-256, SBOM):
> [última versión](https://github.com/unlessDev/UNnexor/releases/latest).
> macOS y Linux: próximamente.

1. Descarga y ejecuta el instalador. Como todavía no está firmado
   digitalmente, Windows SmartScreen puede avisar: pulsa **Más información →
   Ejecutar de todas formas**. Puedes comprobar el archivo con
   `Get-FileHash UNnexor-Setup-Windows-x64.exe` y el `SHA256SUMS.txt` de la
   release.
2. Instala [Ollama](https://ollama.com/download) si aún no lo tienes
   (UNnexor lo iniciará cuando lo necesite).
3. Abre **UNnexor** desde el menú Inicio; usa **☰ → Centro de control → Recomendar
   configuración** → **Aplicar**: propone modelos adecuados a tu equipo y,
   si lo autorizas, los descarga e instala con progreso real.
4. Carga la extensión (sección 4) y emparéjala con el código de 6 dígitos.

## Generar el instalador desde el código

El instalador permite usar UNnexor sin Python ni el entorno de desarrollo.

```powershell
cd <carpeta del repositorio>\local-bridge
.venv\Scripts\python.exe ..\deployment\installer\build.py
```

Requiere Inno Setup 6 instalado (o la variable `ISCC` apuntando a
`ISCC.exe`). El comando compila, prueba el ciclo completo en directorios
temporales y deja el resultado en `release\installer\`:

* `UNnexor-<versión>-win64-setup.exe`
* `SHA256SUMS.txt`
* `build-report.json` y `build.log`

Pasos:

1. Ejecuta el instalador. Es **por usuario**, no pide administrador y
   instala en `%LOCALAPPDATA%\Programs\UNnexor`.
2. Si SmartScreen advierte que no hay firma, comprueba el SHA-256 con
   `SHA256SUMS.txt` antes de continuar. El instalador no está firmado con
   Authenticode.
3. Los datos (ajustes, logs, `.env`, estado de proveedores) se guardan en
   `%LOCALAPPDATA%\UNnexor` (desde 2.1.0; los datos de `%LOCALAPPDATA%\Extdator` se copian en el primer arranque y la carpeta antigua se conserva). Las claves conectadas desde el panel viven en
   el Administrador de credenciales de Windows; las que pongas manualmente
   en `.env` pueden retirarse con `--purge-secrets`.
4. Continúa con el **paso 2** (Ollama) y el **paso 4** (cargar la extensión)
   de esta guía. El paso 1 es solo para desarrollo.

### Desinstalar y limpiar datos

* Desinstala desde **Configuración > Aplicaciones** o desde el menú Inicio.
* Por defecto, tus datos y credenciales se conservan para una futura
  reinstalación.
* Si eliges la **limpieza completa**, el desinstalador ejecuta
  `UNnexor.exe --purge-secrets` y borra `%LOCALAPPDATA%\UNnexor` y, si existe, `%LOCALAPPDATA%\Extdator`.

Para borrar solo credenciales y el token sin desinstalar:

```powershell
& "$env:LOCALAPPDATA\Programs\UNnexor\UNnexor.exe" --purge-secrets
```

El token se regenera en el siguiente arranque y la extensión deberá volver
a emparejarse. Los ajustes no sensibles se conservan.

## 1. Instalar el puente (solo desarrollo)

Abre PowerShell **en la carpeta del proyecto** y ejecuta:

```powershell
cd G:\Proyectos\UNnexor
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

El script:

1. Detecta Python 3.10+ (`py -3` o `python`).
2. Crea el entorno virtual `local-bridge\.venv`.
3. Instala las dependencias (`requirements.txt`).
4. Crea `local-bridge\.env` desde `.env.example`.
5. **Genera el token compartido** y lo guarda en `BRIDGE_TOKEN`.
6. Comprueba si Ollama está instalado, en ejecución y si el modelo
   configurado existe; te indica los comandos exactos si falta algo
   (`ollama pull llama3`).

Si tu política de ejecución lo bloquea: `powershell -ExecutionPolicy Bypass -File ...`
(o ajusta `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`).

## 2. Instalar Ollama (si no lo tienes)

1. Descarga el instalador: <https://ollama.com/download/windows>
2. Tras instalarlo, abre una terminal:
   ```powershell
   ollama pull llama3
   ```
3. Verifica: `curl http://127.0.0.1:11434/api/version`

## 3. Arrancar todo

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start.ps1
```

Esto:

* valida la configuración,
* comprueba Ollama (solo aviso si no responde),
* arranca el servidor HTTP y el **panel flotante** (esquina inferior
  derecha, siempre visible),
* espera el health check y guarda el PID en `local-bridge\logs\bridge.pid`.

Para detenerlo: `scripts\stop.ps1` (solo mata procesos de *este* proyecto).

## 4. Cargar la extensión

### Chrome

1. Abre `chrome://extensions`.
2. Activa **Modo de desarrollador** (esquina superior derecha).
3. Pulsa **Cargar descomprimida** (*Load unpacked*).
4. Selecciona la carpeta `G:\Proyectos\UNnexor\browser-extension`.
5. Copia el **ID** de la extensión de la tarjeta (p. ej. `abcdefghijklmnop`).

### Brave

Idéntico a Chrome con `brave://extensions`.

### Edge

1. Abre `edge://extensions`.
2. Activa **Modo de desarrollador** (menú lateral izquierdo).
3. Pulsa **Cargar descomprimida**.
4. Selecciona `G:\Proyectos\UNnexor\browser-extension`.
   (Si Edge pregunta "Permitir extensiones de otros orígenes", acepta.)
5. Copia el ID de la tarjeta de la extensión.

### Conectar la extensión con el puente

1. Clic derecho en el icono de UNnexor → **Opciones** (o detalles → opciones).
2. Pega el token: la primera línea `BRIDGE_TOKEN=...` de
   `local-bridge\.env`.
3. Pulsa **Guardar** y luego **Probar conexión**: debe aparecer
   `Conectado (200). Ollama <versión> · modelo: llama3`.
4. (Opcional, recomendado) Pega el ID copiado en el paso 5 en
   `ALLOWED_ORIGINS` del `.env`:
   ```
   ALLOWED_ORIGINS=chrome-extension://TU_ID_AQUI
   ```
   y reinicia el puente (`stop.ps1` + `start.ps1`).

## 5. Uso

1. Selecciona texto en cualquier página web.
2. Pulsa el botón ✦ que aparece sobre el inicio de la selección.
3. El toast confirma el envío y la respuesta aparece progresivamente en el
   panel flotante (sin perder el foco del navegador).
4. Rueda del ratón sobre el panel → desplazamiento sin clic.
5. Botones del panel: 📌 siempre visible · ⧉ copiar · ⟲ nueva conversación ·
   ☰ menú (centro de control, opacidad…) · — minimizar · ✕ cerrar.
6. En el primer arranque, el panel abre el **asistente de inicio**:
   privacidad, comprobación de Ollama/modelo y código de emparejamiento.
   Se puede reabrir desde `☰ → Asistente de inicio`.
7. Si algo falla, `☰ → Exportar diagnóstico…` guarda un ZIP saneado para
   soporte (sin token, claves, `.env`, historial ni capturas). Revisa su
   contenido antes de compartirlo.

## Modo solo servidor (sin GUI)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -ServerOnly
```

## Auto-verificación headless

```powershell
.\local-bridge\.venv\Scripts\python.exe local-bridge\run.py --check
```

Arranca el servidor, prueba `/health`, `POST /api/selection` y
`POST /api/clear`, imprime el resultado y termina.

En la versión instalada (sin consola):

```powershell
& "$env:LOCALAPPDATA\Programs\UNnexor\UNnexor.exe" --check
```

El resultado se escribe en
`%LOCALAPPDATA%\UNnexor\self-check.json`; el código de salida es 0 si
todo funciona.

## Pruebas

```powershell
.\local-bridge\.venv\Scripts\python.exe -m pytest local-bridge\tests -v
```

La prueba de integración `test_hover_scroll_and_no_activate_integration`
abre brevemente el panel real y verifica con `SendInput` el hover scroll y
la no-activación.

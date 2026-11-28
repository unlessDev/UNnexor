# Identidad de UNnexor

**UNnexor** — *AI Bridge* — by **UnlessDev**.

- **UN** proviene de *UnlessDev*.
- **Nexor** remite a la indexación (*indexing*): cómo se organizan,
  identifican y alcanzan los datos y las rutas de un sistema.

## Recursos gráficos

| Archivo | Uso |
|---|---|
| `branding/source/unnexor-logo-reference.png` | Imagen original de la identidad (antes `newDesign/`). Referencia de diseño y presentación del README. |
| `branding/unnexor-x.svg` | **SVG maestro** de la X (fondo transparente). |
| `branding/unnexor-icon.svg` | La X centrada en un lienzo cuadrado. |
| `branding/unnexor-badge.svg` | La X dentro de un contenedor redondeado (botón flotante, iconos pequeños, ejecutable). |
| `branding/png/` | Rasterizados: `unnexor-x-{256,512,1024}.png`, `unnexor-icon-*.png`, `unnexor-badge-*.png` (16–512 px). |
| `local-bridge/assets/unnexor.ico` | Icono del ejecutable, ventanas y barra de tareas (16–256 px). |
| `local-bridge/assets/unnexor.png`, `unnexor-x.svg` | Icono y logo empaquetados con la aplicación. |
| `browser-extension/assets/icon{16,32,48,128}.png` | Iconos de la extensión. |
| `browser-extension/content.js` (bloque `UNNEXOR_X_SVG`) | La X en línea dentro del botón flotante. |

Todo se genera desde **una única geometría** con:

```powershell
local-bridge\.venv\Scripts\python.exe scripts\make_brand_assets.py
```

No edites a mano los PNG, el ICO ni el bloque de `content.js`: cambia la
geometría en el script y vuelve a generarlos.

### Cómo se construyó la X

El SVG se dibujó desde cero sobre la imagen de referencia, no es un
calco ni una imagen incrustada:

1. Barrido por filas de la silueta (umbral de brillo) y muestreo de color
   sobre la referencia de 1672×941 px para medir cada borde.
2. Cada borde es una recta: las del trazo "/" tienen pendiente
   dx/dy = −1,06 y las del trazo "\\" +1,0, como en la referencia.
3. Los polígonos se obtienen intersecando esas rectas: el marco hueco del
   "/" con su ranura y la banda magenta exterior; los dos brazos huecos del
   "\\" que pasan por debajo con una separación negra de ~12 px; el chaflán
   del hueco superior izquierdo; el suelo en sombra de la barra inferior.
4. Colores muestreados (cian `#00F4FF` → azul `#1C4CF6` → violeta
   `#A000FA` → magenta `#FF2CE8`) en degradados lineales.

Única diferencia buscada: **sin los destellos de las esquinas** (ni
brillos, ni filtros, ni efectos). La comparación lado a lado con la
referencia se hizo al generar los recursos.

## Identificadores técnicos (2.1.0: todos con el nombre UNnexor)

Hasta 2.0.0 varios identificadores internos conservaban el nombre histórico
"Extdator" para no perder datos. En 2.1.0 se renombraron **con migración**,
de modo que una actualización no pierde nada:

| Identificador actual | Antes | Compatibilidad / migración |
|---|---|---|
| `%LOCALAPPDATA%\UNnexor` (`paths.APP_DIR_NAME`) | `%LOCALAPPDATA%\Extdator` | `app/migration.py` copia los datos en el primer arranque (verificado, sin sobrescribir ni borrar el origen; registro en `migration.json`). |
| Servicio `UNnexor` del keyring | Servicio `Extdator` | La primera lectura de una clave la copia al servicio nuevo (`providers/manager.py`); la purga borra ambos. |
| `QSettings("UNnexor", …)` | `QSettings("Extdator", …)` | `migrate_qsettings()` copia geometría y preferencias si el destino está vacío. |
| Variables `UNNEXOR_*` | `EXTDATOR_*` | Los nombres antiguos se aceptan como alias (`paths.env_override`). |
| `"app": "unnexor-bridge"` en `/health` | `"extdator-bridge"` | La extensión y `start.ps1` aceptan ambos valores. |
| Mensaje `unnexor:send-selection`, `__unnexorInjected`, `data-unnexor` | `extdator:*` | Internos de la extensión (content script ↔ service worker del mismo paquete). |
| Loggers `unnexor.*` | `extdator.*` | Sin efecto visible. |
| `AppId` del instalador | sin cambios | Se conserva a propósito: la nueva versión actualiza la instalación previa en lugar de duplicarla. |

El instalador, al actualizar desde Extdator, retira `Extdator.exe` y los
accesos directos con el nombre anterior. El nombre antiguo solo aparece en
el código de migración y en notas históricas.

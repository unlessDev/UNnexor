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

## Nombres técnicos que se conservan (compatibilidad)

El cambio de nombre es visible en toda la interfaz, pero estos
identificadores mantienen el valor histórico "Extdator" a propósito. Cambiarlos
perdería datos del usuario o rompería la comunicación entre versiones:

| Identificador | Dónde | Motivo |
|---|---|---|
| `%LOCALAPPDATA%\Extdator` (`paths.APP_DIR_NAME`) | Datos: `.env`, ajustes, registros, perfiles, modelos de voz | Una actualización conserva configuración, perfiles y cientos de MB de modelos ya descargados. |
| Servicio `Extdator` del keyring (`providers/manager.py`) | Claves API de proveedores | Las credenciales guardadas siguen funcionando. |
| `QSettings("Extdator", …)` y organización Qt | Geometría de ventanas, preferencias de UI | Las ventanas vuelven a su posición. |
| Variables `EXTDATOR_*` | Overrides para pruebas y modo portable | Scripts y CI existentes. |
| `"app": "extdator-bridge"` en `/health` | Contrato extensión ↔ puente | Una extensión nueva sigue reconociendo un puente antiguo y viceversa. |
| Mensaje `extdator:send-selection`, `__extdatorInjected` | Interno de la extensión | Sin efecto visible. |
| Loggers `extdator.*` | Nombres internos de registro | Sin efecto visible. |
| `AppId` del instalador | Inno Setup | La nueva versión actualiza la instalación previa en lugar de duplicarla. |
| URL `github.com/unlessDev/Extdator` | Enlaces al repositorio | Es la dirección real del repositorio; renombrarlo es decisión del propietario. |

El instalador, al actualizar desde Extdator, retira `Extdator.exe` y los
accesos directos con el nombre anterior; los datos no se tocan.

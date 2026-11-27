# Permisos de la extensión y justificación

El manifest declara únicamente lo necesario para el propósito del producto:
enviar una selección de texto a la aplicación local y mostrar la respuesta.

## Permisos declarados

| Permiso / campo | Valor | Por qué |
|---|---|---|
| `permissions` | `storage` | Guardar dirección local, token de vinculación y preferencias de UI en `chrome.storage.local`. No se usa para datos personales ni analítica. |
| `host_permissions` | `http://127.0.0.1/*`, `http://localhost/*` | Único destino de red de la extensión: la aplicación UNnexor en el mismo equipo. |
| `content_scripts.matches` | `http://*/*`, `https://*/*` | La selección puede ocurrir en cualquier página; el script solo muestra el botón ✦ y lee la selección cuando existe. |
| `content_scripts.all_frames` | `true` | Permite seleccionar texto dentro de iframes legítimos (editores, documentación). |
| `content_security_policy.extension_pages` | `script-src 'self'; object-src 'self'` | Bloquea scripts remotos, `eval` y objetos embebidos. |
| `background.service_worker` | `background.js` | Único punto de red; recibe la selección y hace el `POST` local. |
| `options_page` | `options.html` | Asistente de vinculación, diagnóstico y preferencias. |
| `action` | icono + título | Abre la configuración al pulsar el icono; no hay popup ni permisos extra. |

## Permisos retirados y por qué

| Permiso | Estado | Motivo |
|---|---|---|
| `scripting` | Retirado | Solo se usaba para re-inyectar el content script en pestañas ya abiertas. Es una comodidad, no una necesidad: las páginas nuevas reciben el script por el manifest. Se eliminó para reducir la superficie y facilitar la revisión. |
| `http://*/*`, `https://*/*` en `host_permissions` | Retirado | La extensión no hace peticiones a webs arbitrarias; solo al puente local. El acceso a páginas se cubre con `content_scripts.matches`, no con permisos de host globales. |
| `tabs` | No solicitado | No se leen pestañas ni URLs fuera de la selección; el `source_url` lo aporta el remitente del mensaje. |
| `activeTab` | No solicitado | No hay acción puntual sobre la pestaña activa. |
| `<all_urls>` | No usado | Se prefieren patrones explícitos http/https para excluir `file:`, `chrome:` y tiendas. |

## Flujo de datos

```text
content.js (página)
   │  chrome.runtime.sendMessage (texto + URL de origen)
   ▼
background.js
   │  POST http://127.0.0.1:5000/api/selection
   │  Authorization: Bearer <token local>
   ▼
Aplicación UNnexor (mismo equipo)
   ├── Ollama local (por defecto)
   └── Proveedor remoto (solo si el usuario lo configura)
```

- La extensión no habla con proveedores de IA directamente.
- El token se obtiene por emparejamiento con código efímero y se guarda en
  `chrome.storage.local`; no se inyecta en páginas web.
- `options.js` rechaza direcciones que no sean `127.0.0.1` o `localhost`.
- No se usa CORS como única defensa: el backend valida token, origen, tamaño
  y tasa (ver `ARCHITECTURE.md`).

## Verificación automática

`python deployment/extension/build.py --check` comprueba, entre otras cosas:

- que no haya permisos fuera de la lista blanca;
- que los hosts sean solo loopback;
- que la CSP no permita `unsafe-eval` ni `unsafe-inline`;
- que no existan scripts remotos en HTML;
- que no se use `eval`/`new Function` en JavaScript;
- que los iconos existan y tengan el tamaño declarado;
- que la versión coincida con la aplicación Python.

Las pruebas `local-bridge/tests/test_extension_package.py` verifican lo mismo
desde pytest y generan paquetes en un directorio temporal.

## Nota para la revisión de la tienda

El acceso a todos los sitios http/https se justifica por la funcionalidad
principal: el usuario debe poder seleccionar texto en cualquier página. El
content script no recopila contenido de forma continua; solo reacciona a una
selección y a la pulsación del botón. No hay analítica ni publicidad.

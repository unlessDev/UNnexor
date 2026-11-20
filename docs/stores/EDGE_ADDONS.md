# Microsoft Edge Add-ons — preparación

> Estado: **preparación técnica completada; envío no realizado**. La
> publicación requiere cuenta de Partner Center, URL pública de privacidad,
> capturas reales y autorización del propietario.

## Compatibilidad

Extdator usa Manifest V3 y APIs estándar de Chromium (`chrome.storage`,
`chrome.runtime`, `chrome.action`, content scripts y `fetch`). El paquete de
Edge se genera con el mismo código que Chrome:

| Elemento | Valor |
|---|---|
| Fuente | `browser-extension/` |
| Paquete | `release/browser-extension/edge.zip` |
| Generación | `python deployment/extension/build.py` |
| Validación | `python deployment/extension/build.py --check` |
| Checksum | `release/browser-extension/SHA256SUMS.txt` |

No se usa `browser_specific_settings` ni APIs exclusivas de un navegador.
`minimum_chrome_version` es una pista para Chromium; Edge la ignora y aplica
su propia versión mínima. La extensión requiere Chromium ≥ 110.

## Ficha propuesta

| Campo | Contenido |
|---|---|
| Nombre | Extdator |
| Descripción corta | Selecciona texto en cualquier web y recibe la respuesta de tu asistente de IA en un panel flotante. |
| Categoría | Productividad |
| Idioma | Español |
| Página principal | Pendiente de URL pública |
| Soporte | Pendiente de canal público (repositorio o formulario) |
| Política de privacidad | Pendiente de URL pública; HTML en `browser-extension/privacy.html` |

La descripción extensa puede reutilizar el borrador de
[CHROME_WEB_STORE.md](CHROME_WEB_STORE.md), adaptando la mención al
instalador de Windows y a la tienda de Edge.

## Recursos

| Recurso | Requisito | Estado |
|---|---|---|
| Icono | 128×128 PNG | Disponible |
| Capturas | 1280×800 o 640×400 | **Pendientes de capturas reales** |
| Imagen promocional | Opcional | No generada |

## Particularidades de Edge

- El registro es gratuito mediante Microsoft Partner Center; el propietario
  debe completar la cuenta y la verificación.
- Edge ejecuta extensiones Chromium, pero la certificación revisa permisos,
  privacidad y comportamiento. La justificación de permisos es la misma que
  en Chrome: ver [PERMISSIONS.md](PERMISSIONS.md).
- Edge puede mostrar advertencias adicionales si el paquete solicita acceso
  amplio a sitios; el content script lo necesita para detectar selecciones
  en cualquier página.
- No se debe registrar un host de Native Messaging: el transporte es HTTP
  local autenticado (ADR-0002).
- La actualización la gestiona la tienda; la extensión no se autoactualiza.

## Checklist de envío

- [x] Paquete MV3 validado y reproducible.
- [x] Permisos minimizados y justificados.
- [x] Sin código remoto.
- [x] Política de privacidad redactada.
- [ ] URL pública de privacidad.
- [ ] Capturas reales.
- [ ] Cuenta de Partner Center y verificación.
- [ ] Contacto de soporte público.
- [ ] Licencia principal aprobada.
- [ ] Autorización expresa para enviar.

## Prueba local

1. Abrir `edge://extensions`, activar modo desarrollador y cargar
   `release/browser-extension/edge/`.
2. Comprobar el asistente de opciones y la vinculación con la aplicación.
3. Probar una selección en una página y verificar el panel flotante.
4. Revisar que las peticiones van solo a `127.0.0.1` o `localhost`, salvo el
   proveedor remoto configurado en la aplicación.

La prueba en el navegador real se documenta cuando se ejecute; no se afirma
compatibilidad verificada con la tienda antes de la certificación.

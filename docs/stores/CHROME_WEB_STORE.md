# Chrome Web Store — preparación

> Estado: **preparación técnica completada; envío no realizado**. La
> publicación requiere cuenta de desarrollador, URL pública de privacidad,
> capturas reales y autorización del propietario.

## Artefacto

| Elemento | Valor |
|---|---|
| Fuente | `browser-extension/` |
| Paquete | `release/browser-extension/chrome.zip` |
| Generación | `python deployment/extension/build.py` |
| Validación | `python deployment/extension/build.py --check` |
| Checksums | `release/browser-extension/SHA256SUMS.txt` |
| Informe | `release/browser-extension/build-report.json` |
| Firma | No aplica: la tienda firma y distribuye el paquete |

El ZIP contiene solo `manifest.json`, `background.js`, `content.js`,
`options.html`, `options.js`, `privacy.html` y los iconos PNG. No incluye
`.env`, logs, capturas, dependencias ni datos de usuario.

## Ficha propuesta

| Campo | Contenido |
|---|---|
| Nombre | UNnexor |
| Descripción corta (≤132) | Selecciona texto en cualquier web y recibe la respuesta de tu asistente de IA en un panel flotante. |
| Categoría | Productividad |
| Idioma | Español |
| Página principal | Pendiente de URL pública |
| Soporte | `https://github.com/unlessDev/UNnexor/issues` (el repositorio debe ser público o habilitarse un canal público) |
| Política de privacidad | Pendiente de URL pública; el HTML está en `browser-extension/privacy.html` |

### Descripción extensa (borrador)

```text
UNnexor convierte cualquier selección de texto en una consulta a tu
asistente de IA, sin salir de la página y sin perder el foco del navegador.

Selecciona texto, pulsa el botón ✦ y la respuesta aparece en un panel
flotante siempre visible. La aplicación de escritorio se ejecuta en tu
equipo y puede usar Ollama de forma local o un proveedor de IA remoto que
tú configures.

Funciones:
• Selección de texto en cualquier página web.
• Panel flotante con streaming de la respuesta.
• Modelos locales mediante Ollama.
• Proveedores remotos OpenAI-compatible, configurables por ti.
• Captura de pantalla por región con visión u OCR local opcional.
• Conversión de fórmulas LaTeX a matemática Unicode legible.

Privacidad:
• Por defecto, el texto no sale de tu equipo.
• Sin analítica, publicidad ni rastreadores.
• Si conectas un proveedor remoto, el texto se envía solo a ese proveedor.
• Las claves de proveedores se guardan en el almacén seguro de Windows y
  nunca en la extensión.

Requisito: instalar la aplicación de escritorio UNnexor para Windows.
```

No se incluyen capturas ni enlaces de descarga hasta que existan y estén
verificados.

## Recursos gráficos

| Recurso | Requisito | Estado |
|---|---|---|
| Icono de tienda | 128×128 PNG | Disponible (`assets/icon128.png`) |
| Capturas | 1280×800 o 640×400, mínimo 1 | **Pendientes de capturas reales** |
| Icono promocional pequeño | 440×280 | Opcional, no generado |
| Imagen promocional grande | 1400×560 | Opcional, no generada |

Las capturas deben mostrar la extensión y el panel reales; no se generan
mockups que aparenten funcionalidades no verificadas.

## Declaraciones de privacidad (pestaña de prácticas)

Respuestas recomendadas, sujetas a revisión final del propietario:

1. **Propósito único**: enviar el texto seleccionado a la aplicación local
   UNnexor y mostrar la respuesta en un panel flotante.
2. **Uso de datos**: el texto seleccionado se procesa para prestar la
   funcionalidad. No se vende, no se usa para publicidad ni para evaluar
   solvencia. Si el usuario configura un proveedor remoto en la aplicación,
   el texto puede transmitirse a ese proveedor; se divulga en la política.
3. **Analítica**: no se recopila.
4. **Código remoto**: no se usa.
5. **Permisos**: ver [PERMISSIONS.md](PERMISSIONS.md).

La calificación exacta de "recopilación" en el formulario debe revisarse
contra la definición vigente de Google; la política de privacidad declara
el flujo real, incluido el proveedor remoto opcional.

## Checklist de envío

- [x] Manifest V3 válido y empaquetado reproducible.
- [x] Permisos minimizados: `storage` y loopback.
- [x] Sin código remoto ni `eval`.
- [x] Política de privacidad redactada en HTML.
- [ ] URL pública de la política (requiere alojamiento y decisión del propietario).
- [ ] Capturas reales de la extensión y del panel.
- [ ] Cuenta de desarrollador de Chrome Web Store (alta y pago de la tasa).
- [ ] Verificación de identidad y contacto de soporte.
- [ ] Licencia principal aprobada (ADR-0004).
- [ ] Repositorio/soporte accesible públicamente o canal alternativo.
- [ ] Revisión final y autorización expresa para enviar.

## Prueba local antes de enviar

1. Cargar `release/browser-extension/chrome/` como extensión desempaquetada.
2. Verificar que el icono abre Opciones y que el asistente de vinculación
   funciona con la aplicación abierta.
3. Seleccionar texto en una página y comprobar el botón ✦ y el panel.
4. Confirmar que no hay peticiones a hosts distintos de `127.0.0.1` /
   `localhost` (salvo el proveedor remoto configurado en la aplicación).
5. Repetir con Brave; para Edge usar `edge.zip` o la misma carpeta.

No se declara compatibilidad verificada con la tienda hasta completar la
revisión oficial.

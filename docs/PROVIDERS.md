# Proveedores de IA remotos (Extdator)

Además de Ollama (local), Extdator puede usar proveedores de IA en la nube
compatibles con la API de OpenAI. El panel de escritorio administra las
conexiones (⚙ → **Proveedores de IA**); la clave nunca sale del equipo
salvo hacia el propio proveedor al generar.

## Seguridad: dónde viven las claves

* Las claves API se guardan **solo** en el almacén de credenciales del
  sistema operativo (Windows Credential Manager), vía la librería
  `keyring`. Servicio: `Extdator`, cuenta: `provider-key:<id>`.
* El archivo `local-bridge\data\providers.json` guarda **solo** el estado:
  proveedor conectado, `base_url` personalizada (proveedor "custom") y el
  modelo elegido. **Nunca contiene claves.**
* No hay telemetría: la única petición con la clave es la que va al
  proveedor que elegiste.

## Proveedores preconfigurados (12)

Todos usan el adaptador OpenAI-compatible (`chat/completions` con
streaming SSE). `base_url` verificada a fecha 2026-11-16.

| ID | Etiqueta | Base URL | Notas |
|---|---|---|---|
| `openai` | OpenAI | `https://api.openai.com/v1` | GPT, imágenes con `image_url` (data URL). |
| `groq` | Groq | `https://api.groq.com/openai/v1` | Inferencia rápida, capa gratuita. |
| `openrouter` | OpenRouter | `https://openrouter.ai/api/v1` | Agregador de cientos de modelos. |
| `mistral` | Mistral AI | `https://api.mistral.ai/v1` | Capa gratuita «La Plateforme». |
| `cerebras` | Cerebras | `https://api.cerebras.ai/v1` | Velocidades muy altas. |
| `together` | Together AI | `https://api.together.ai/v1` | Catálogo open-source amplio. |
| `xai` | xAI (Grok) | `https://api.x.ai/v1` | API OpenAI-compatible oficial. |
| `moonshot` | Moonshot AI (Kimi) | `https://api.moonshot.ai/v1` | Kimi K2/kimi-latest. |
| `hf_router` | Hugging Face Router | `https://router.huggingface.co/v1` | Un token para muchos modelos. |
| `zai` | Z.ai (GLM) | `https://api.z.ai/api/paas/v4/` | Familia GLM; termina en `/` a propósito. |
| `nvidia_nim` | NVIDIA NIM | `https://integrate.api.nvidia.com/v1` | Catálogo NIM con crédito gratuito. |
| `gemini` | Google AI Studio (Gemini) | `https://generativelanguage.googleapis.com/v1beta/openai/` | Compatibilidad OpenAI de Gemini; termina en `/`. |

Además existe un proveedor **`custom`** para cualquier servicio
OpenAI-compatible: pegas la `base_url` y la clave. También está
**ollama** (el proveedor local por defecto, no requiere clave).

> **GitHub Models**: retirado de la lista. GitHub anunció el cierre del
> producto GitHub Models (julio de 2026); su endpoint anterior
> (`models.github.ai`) ya no se mantiene.

## Cómo se conecta un proveedor

1. Panel → menú ⚙ → **Proveedores de IA**.
2. Elige la tarjeta del proveedor → **Conectar**.
3. Pega tu clave API (se guarda en el keyring al instante).
4. El diálogo valida la conexión con una petición real y lista los
   modelos de tu cuenta (`GET /models` del proveedor).
5. Elige el modelo → queda registrado en `providers.json`.

Errores habituales que el diálogo traduce a mensajes claros:
`401` clave inválida · `402/403` sin crédito o sin permiso · `404` base
URL incorrecta · `413` mensaje demasiado grande · `429` cuota o rate
limit · errores de red/DNS.

## Cómo se usa

Con un proveedor conectado, el botón 🤖 del panel (o ⚙ → Modelos) muestra
ambas fuentes: modelos locales de Ollama y el modelo remoto del
proveedor. Al elegir uno remoto, las selecciones del navegador se envían
a ese proveedor en lugar de Ollama. El cambio es en caliente y se
restaura al arrancar (el proveedor activo se guarda en el estado).

Los envíos con imagen (capturas) solo usan visión si el modelo elegido
declara soporte multimodal; si no, el panel avisa antes de gastar
tokens.

## Migración desde variables de entorno (opcional)

`scripts`/API: `migrate_from_env()` lee variables tipo
`OPENAI_API_KEY` del `.env` **si existen** y las traslada al keyring.
Nunca borra las variables originales (migración no destructiva).

## Adaptadores nativos pendientes

Estos proveedores **no** usan el formato OpenAI y aún no tienen
adaptador (se integrarían como clases propias en `app/providers/`):

| Proveedor | Qué exige su API | Estado |
|---|---|---|
| Anthropic | Header `x-api-key` + `anthropic-version`, `POST /v1/messages`, formato de contenido propio | Pendiente |
| Cohere | `POST /v2/chat` con esquema propio | Pendiente |
| Cloudflare Workers AI | Endpoint REST por cuenta y modelo | Pendiente |

Mientras tanto, varios de ellos exponen puertas compatibles con OpenAI
(p. ej. OpenRouter o HF Router) que sí funcionan hoy.

## Referencia técnica

* Adaptador: `local-bridge/app/providers/openai_compat.py`
  (SSE streaming, keep-alive `:`, errores mapeados, imágenes data-URL).
* Presets: `local-bridge/app/providers/presets.py`.
* Gestión: `local-bridge/app/providers/manager.py`
  (keyring, `providers.json`, `connect`/`disconnect`/`get_client_for`).
* Diálogo: `local-bridge/app/desktop/providers_dialog.py`.
* Pruebas: `local-bridge/tests/test_providers.py` (20 pruebas con
  transporte simulado; no tocan la red).

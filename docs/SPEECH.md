# Reconocimiento de voz del copiloto

Motores disponibles, cómo funcionan de verdad, cuánto cuestan y qué rinde
cada uno en un equipo real. Fuentes oficiales consultadas el 2026-11-24;
planes, precios y límites pueden cambiar: verifica en la documentación del
proveedor antes de depender de ellos.

## Modalidades (no son equivalentes)

| Modalidad | Qué significa | Motores en Extdator |
|---|---|---|
| Streaming real | El servicio recibe un flujo continuo y devuelve resultados parciales nativos | Ninguno integrado todavía (Deepgram y OpenAI Realtime lo ofrecen) |
| Por fragmentos | Extdator detecta cada intervención (VAD) y envía el fragmento | Todos los integrados |
| Lotes / archivos | Archivos completos, latencia alta | No se usa en el copiloto |

El texto **provisional** que aparece mientras alguien habla se obtiene
re-transcribiendo la intervención en curso cada 1,5 s. Solo se hace con
motores locales rápidos: con servicios remotos consumiría cuota.

## Motores

### Locales (faster-whisper / CTranslate2, licencia MIT)

El audio no sale del equipo. Los modelos se descargan solo con
consentimiento a `%LOCALAPPDATA%\Extdator\models\faster-whisper`.

| Modelo | Descarga | Español | Notas |
|---|---|---|---|
| tiny | ~75 MB | básico | muy rápido |
| base | 148 MB (medido) | bueno | recomendado en equipos modestos |
| small | 486 MB (medido) | muy bueno | equipos de 6+ núcleos, sin texto provisional |
| medium | ~1,5 GB | muy bueno | lento en CPU |
| large-v3-turbo | ~1,6 GB | excelente | GPU NVIDIA o CPU potente |
| large-v3 | ~3,1 GB | excelente | GPU NVIDIA |

Los modelos `distil-*` son solo inglés y no se ofrecen para español.
CTranslate2 acelera con **CUDA (NVIDIA)**; con GPU AMD o Intel funciona en
CPU (`int8`). El usuario puede elegir modelos más grandes si su equipo lo
permite: el catálogo indica la latencia esperada y avisa si no seguirá el
ritmo de la conversación.

### Remotos

| Servicio | Modelo | Acceso | Datos clave |
|---|---|---|---|
| Groq | whisper-large-v3-turbo | Gratuito con límites | 0,04 $/h en plan de pago; mínimo facturado 10 s/petición; plan gratuito 20 RPM, 2.000/día, 7.200 s de audio/hora; 25 MB/archivo. No traduce. |
| Groq | whisper-large-v3 | Gratuito con límites | 0,111 $/h; traduce solo a inglés. |
| OpenAI | gpt-transcribe | Pago por consumo | Recomendado por OpenAI; `languages[]` en lugar de `language`. `whisper-1` y `gpt-4o-*-transcribe` se apagan el 2027-02-26. |
| Deepgram | nova-3 | Créditos iniciales | 200 $ de crédito sin caducidad, luego 0,0043 $/min (0,0052 multilingüe); `es` y `es-419`; idioma por defecto `en` (Extdator siempre envía idioma o detección). |

Con Groq, cada intervención corta se factura como 10 s: una conversación
continua consume más cuota de la que dura. El catálogo lo advierte.

## Resultados medidos (2026-11-24)

Equipo: AMD Ryzen 5 2600X (6 núcleos), 16 GB, RX 590 (sin CUDA → CPU int8,
5 hilos). Audio: 4 frases en español con la voz TTS "Sabina" de Windows,
segmentadas por Silero VAD (`scripts/stt_benchmark.py`).

| Motor | Carga | Latencia media por intervención | RTF agregado | WER español | Memoria extra |
|---|---|---|---|---|---|
| faster-whisper base | 0,5 s | 1,03 s | 0,25 | 6,8 % | +114 MB |
| faster-whisper small | 1,3 s | 3,48 s | 0,83 | 6,8 % | +276 MB |
| Groq whisper-large-v3-turbo | — | 0,47 s | 0,11 | 6,8 % | — |

El 6,8 % de WER de los tres es la misma diferencia de formato ("quince por
ciento" → "15%"); las transcripciones son por lo demás exactas, con
acentos y signos `¿?`. Whisper procesa ventanas de 30 s: la latencia es casi
constante por fragmento (base ≈ 1 s, small ≈ 3,5 s), por eso el
recomendador estima segundos por llamada y no solo RTF.

Prueba en vivo (`scripts/audio_e2e.py --loopback --transcribe
faster-whisper:base`): locución reproducida por los altavoces, capturada
por WASAPI loopback y transcrita con 1,3–2,0 s de retraso tras cada
intervención, 7 textos provisionales, 0 bloques perdidos.

Limitación de la prueba: la voz TTS en inglés ("Zira") es poco natural y
Whisper base/small cometen errores con ella (WER 30–40 %); no es
representativo de voz humana.

## Recomendación automática

`app/stt/recommend.py` ordena los motores según el equipo (núcleos, RAM,
CUDA), la preferencia (equilibrio, calidad, velocidad, privacidad), si el
modelo ya está descargado y si el usuario permite servicios remotos (por
defecto **no**). Cada recomendación incluye razones y advertencias. En el
equipo de referencia: con remotos permitidos, Groq turbo; solo local, base
(admite texto provisional) seguido de small (sigue el ritmo sin
provisional).

## Errores y privacidad

- Clave inválida, saldo insuficiente, motor no instalado o modelo ausente
  detienen la transcripción con un mensaje claro: Extdator **no** cambia a
  otro motor (ni a uno remoto) por su cuenta.
- Límite de uso (429) o fallo de red: un reintento respetando
  `retry-after`; si falla, se informa y se continúa con la siguiente
  intervención.
- Si el motor no sigue el ritmo (más de 20 s de audio pendiente) se avisa
  y se sugiere uno más ligero.
- Se filtran las frases que Whisper inventa con silencio o música
  ("Subtítulos realizados por la comunidad de Amara.org"…).

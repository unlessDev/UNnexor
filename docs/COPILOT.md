# Guía del copiloto en tiempo real

El copiloto escucha, transcribe y ayuda durante conversaciones, traduce en
vivo y resuelve desafíos visibles en pantalla. Se abre desde el panel
flotante con el botón **🧭** o desde **☰ → Copiloto en tiempo real…**.

> Estado: funciona desde el código fuente (validado en Windows 11 con
> Whisper local, Ollama y Groq). El ejecutable/instalador todavía no
> incluye sus dependencias de audio y voz: ver `docs/COPILOT_STATUS.md`.

## Uso responsable

- Avisa y obtén el consentimiento de las personas cuando grabar o
  transcribir una conversación lo requiera la ley o la plataforma.
- Respeta las reglas de cada entrevista, examen o evaluación.
- UNnexor **no** se oculta de capturas de pantalla ni de la compartición
  de pantalla, y su indicador de captura es siempre visible.
- Las sugerencias no inventan experiencias ni datos: lo que falte aparece
  como `[completar: …]` para que lo aportes tú.

## Primeros pasos

1. **Inicio → Recomendar configuración**: analiza tu equipo, elige el motor
   de voz y muestra qué modelo atenderá cada tarea. No descarga nada.
2. **Motores y modelos → Reconocimiento de voz**: si el motor elegido es
   local y no está descargado, pulsa **Descargar modelo…** (se indica el
   tamaño y se pide confirmación).
3. Asegúrate de que Ollama está en marcha y tiene un modelo de chat (y,
   para desafíos, uno con visión como `qwen2.5vl:7b`).
4. Opcional: crea un **perfil** con tu rol, experiencia y documentos.

## Modalidades

### 🎧 Asistencia en conversaciones

- Fuentes: **audio del sistema** (lo que suena en el equipo: la otra parte
  de una videollamada) y/o **micrófono** (tu voz). Se pueden usar a la vez.
- **Iniciar** comienza la captura; el panel flotante muestra el estado
  (capturando, transcribiendo, generando, pausado…), lo que dice el
  interlocutor (las frases definitivas en claro, la que aún se transcribe
  en cursiva con «transcribiendo…») y, separada y destacada, **tu
  respuesta para decir en voz alta**. **Pausar** y **Finalizar** detienen
  al instante.
- El panel flotante se **cierra** con ✕ (o Alt+F4): si hay una sesión en
  curso se finaliza de forma segura y se libera el audio; UNnexor sigue
  abierto y el panel vuelve a aparecer con la siguiente sesión. Minimizar
  (—) lo deja en la barra de tareas sin detener nada.
- Las respuestas se redactan **en primera persona**, como una intervención
  tuya lista para decir, sin «Podrías responder…», «Te recomiendo que
  digas…» ni alternativas. La primera persona no autoriza a inventar:
  experiencias, cifras u opiniones que no estén en tu perfil o tus
  documentos no se afirman; si falta un dato personal la respuesta es
  neutral o lo marca como `[completar: …]`. Si un modelo antepone igualmente
  una introducción de ese tipo, se retira antes de mostrarla.
- Las preguntas del interlocutor se detectan cuando termina de hablar (no
  por cada fragmento). Ajusta la **sensibilidad** o desactiva la respuesta
  automática y usa la **pregunta manual**.
- En cada sugerencia: **Otra respuesta**, **Ampliar**, **Útil**, **Copiar**.

#### Control del tiempo de escucha

Cuando el interlocutor habla mucho tiempo seguido, la pregunta puede quedar
enterrada entre explicaciones. Elige en **Escucha** cómo quieres trabajar:

| Modo | Cómo funciona |
|---|---|
| **Continua** (por defecto) | Detecta preguntas turno a turno, como siempre. |
| **Temporizada** | Pulsa **🎙 Escuchar** (o el atajo) y el copiloto escucha durante la duración elegida (10 s, 20 s, 30 s, 1 min, 2 min o la que indiques, de 5 s a 10 min). Al terminar, todo lo escuchado se envía junto al modelo, que identifica la pregunta dentro del fragmento. |
| **Manual con atajo** | El atajo inicia la escucha y **el mismo atajo la termina**; entonces se responde. |

- Fuera de la escucha la captura queda **en pausa**: no se guarda audio en
  memoria ni se transcribe nada.
- Se usan las mismas fuentes de la sesión: si solo capturas el audio del
  sistema, el micrófono **no** se activa.
- **■ Terminar y responder** cierra la escucha antes de tiempo; **✕ Cancelar
  escucha** (o su atajo) la descarta sin enviar nada.
- El estado se ve en la página y en el panel flotante: 🔴 escuchando (con
  cuenta atrás), ⏳ terminando de transcribir, en espera.
- Los atajos se cambian en la misma fila y se validan al aplicar: no se
  aceptan combinaciones repetidas entre funciones de UNnexor ni reservadas
  por Windows (Win+L, Alt+Tab, Ctrl+C…); si otra aplicación ya usa la
  combinación, Windows la rechaza y se avisa.

### 🌐 Traducción en tiempo real

- Elige idioma de origen (o detección automática) y de destino.
- El texto en gris y cursiva es provisional; solo se traduce lo confirmado.
  ⚠️ marca transcripciones de baja confianza.
- **Copiar**, **Exportar** (Markdown, texto o PDF) y **Resumir**.

### 🧩 Desafíos en pantalla

- **Capturar región**, **Pantalla completa** o **Añadir región** (hasta 4).
  Las ventanas del copiloto se ocultan durante la captura.
- **Analizar** usa un modelo con visión si está permitido; si no, OCR local
  + modelo de texto. La respuesta incluye problema, consigna, enfoque,
  plan, pasos, código, explicación, resultado, advertencias y alternativas.
- **Análisis periódico**: vuelve a capturar la última región cada N
  segundos y solo analiza si la pantalla cambió de forma relevante.

## Perfiles y documentos

Rol, experiencia, objetivo, empresa, idioma, tono, nivel de detalle y un
glosario (mejora el reconocimiento de términos técnicos). Documentos TXT,
Markdown, PDF y DOCX o notas manuales. **Búsqueda semántica** requiere el
modelo `nomic-embed-text` en Ollama; sin él, la búsqueda por palabras
funciona igual.

## Motores, servicios externos y costes

- Por defecto todo se ejecuta **en tu equipo**.
- En **Motores y modelos** eliges la estrategia (equilibrado, velocidad,
  calidad, local, bajo costo o solo los modelos que elijas) y puedes fijar
  un modelo por tarea.
- Para usar un servicio externo, conecta su clave (☰ → Proveedores de API)
  y marca sus permisos: **Enviar datos**, **Costos** (si puede cobrar o
  consumir saldo) y **Documentos** (extractos de tus documentos). Sin esos
  permisos el servicio no se usa y UNnexor explica por qué.
- Si un modelo falla, se prueba una alternativa permitida y se avisa en el
  panel; nunca se pasa en silencio de local a remoto.
- El historial de uso muestra solo contadores y los límites restantes que
  informa el proveedor.

## Atajos globales (configurables)

| Acción | Atajo por defecto |
|---|---|
| Pausar / reanudar la captura | `Ctrl+Alt+Shift+C` |
| Escribir una pregunta | `Ctrl+Alt+Shift+A` |
| Capturar región y analizar | `Ctrl+Alt+Shift+S` |
| Escuchar / terminar la escucha (modos temporizado y manual) | `Ctrl+Alt+Shift+L` |
| Cancelar la escucha | `Ctrl+Alt+Shift+X` |

Si otra aplicación ya usa una combinación, Windows la rechaza y UNnexor lo
indica en el registro.

## Privacidad

El audio se procesa en memoria y nunca se guarda. Las transcripciones y
sugerencias viven en memoria hasta que las borras o cierras la app (salvo
que las exportes). Las capturas no quedan en disco. **Privacidad** permite
borrar la sesión, el historial de uso, los permisos y los perfiles.

## Rendimiento de referencia

Ryzen 5 2600X, 16 GB, RX 590 (Ollama usa Vulkan):

| Paso | Tiempo |
|---|---|
| Transcripción local (Whisper base) | ~1 s por intervención |
| Pregunta detectada tras terminar de hablar | ~2,3 s |
| Primer token (qwen3:8b cargado) | ~0,3 s |
| Respuesta completa | ~5–8 s |
| Precarga del modelo en frío (al iniciar la sesión) | ~16 s |

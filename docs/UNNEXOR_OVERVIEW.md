<p align="center">
  <img src="../branding/png/unnexor-x-512.png" alt="La X de UNnexor" width="220">
</p>

# Tu propia IA, en tu equipo, conectada a todo lo que ya usas

### UNnexor convierte los modelos de inteligencia artificial que corren en tu PC en un asistente que te acompaña en el navegador, en tus conversaciones y en tu pantalla, sin pagar por cada consulta.

<sub>UNnexor AI Bridge · desarrollado y mantenido por **UnlessDev**</sub>

---

## 🤔 ¿Te suena familiar?

- Pagas una suscripción de IA… y aun así te topas con **límites de uso** justo cuando más la necesitas.
- Para cada duda **copias** el texto, cambias de pestaña, lo **pegas** en un chat, esperas y vuelves.
- Estás estudiando, investigando o en una reunión y **no puedes cortar el ritmo** para escribir una pregunta.
- Tus apuntes, tu currículum o tus documentos **no salen de tu equipo** sin que lo pienses dos veces.
- Quieres automatizar tareas con IA, pero **sin depender siempre** de un servicio externo y de su facturación.

Si te reconoces en alguna, UNnexor está pensado para ti.

---

## 🧩 ¿Qué es exactamente UNnexor y cómo funciona?

**1. Conecta tu entorno.**
Instalas UNnexor (aplicación de escritorio para Windows) y su extensión para
Chrome, Brave o Edge. UNnexor usa [Ollama](https://ollama.com) para ejecutar
modelos de IA en tu propio equipo: lo inicia si hace falta y te ayuda a elegir
modelos que tu hardware pueda mover.

**2. Selecciona o proporciona información.**
Selecciona texto en cualquier web y pulsa la **X**: envíalo tal cual o
**consulta** con tu propia pregunta. Inicia el **copiloto** para que escuche
una conversación o traduzca en vivo, o **captura una región** de la pantalla.
Añade tu perfil y tus documentos en `.md` o `.txt` para dar contexto.

**3. Obtén asistencia desde tu propio equipo.**
El puente local de UNnexor envía cada petición al modelo configurado y la
respuesta aparece en un panel flotante que no te roba el foco y conserva el
historial para que lo leas con calma.

```
Navegador ──► X (Enviar / Consultar) ──► Puente local UNnexor ──► Ollama (tu PC)
                                                   │
Audio del equipo / micrófono ──► Whisper local ────┤──► Panel flotante y copiloto
Captura de pantalla ──► visión u OCR local ────────┘
```

---

## 🚀 ¿Qué vas a lograr con UNnexor?

| Situación | Lo que hace UNnexor |
|---|---|
| 📚 **Estudias** un texto difícil | Selecciona el párrafo → *Consultar…* → «Explícamelo con un ejemplo sencillo». |
| 📝 **Tomas apuntes** | Copia la respuesta del panel o exporta la transcripción/traducción (TXT, Markdown, PDF). |
| 🎧 **Estás en una entrevista o reunión** | El copiloto escucha (de forma continua, durante un tiempo fijo o cuando pulsas tu tecla) y te sugiere respuestas basadas en *tu* perfil, sin inventar experiencia. |
| 🌐 **Hablan en otro idioma** | Traducción en tiempo real con texto provisional y confirmado, y un resumen al final. |
| 🧮 **Tienes un ejercicio en pantalla** | Captura la región: un modelo con visión (o el OCR local) lo lee y lo resuelve paso a paso. |
| ⚙️ **No sabes qué modelo usar** | *Recomendar configuración* analiza tu CPU, RAM, GPU y disco y propone modelos y parámetros por función, en tres perfiles de consumo. |

Menos cambios de ventana, menos copiar y pegar, y más control sobre qué
modelo responde y con qué recursos.

---

## 💡 ¿Por qué UNnexor y no otra alternativa?

- **Navegador + escritorio en un solo flujo**: la extensión y la aplicación
  hablan entre sí por un canal local autenticado (código de emparejamiento).
- **Procesamiento local por defecto** con Ollama; los proveedores remotos son
  opcionales, se activan con tu consentimiento y nunca reciben tus documentos
  sin que lo permitas en el perfil.
- **Elección de modelos según tu hardware**: cada modelo se clasifica como
  óptimo, compatible, exigente o no compatible para *tu* equipo; nunca se
  recomienda uno que lo sature.
- **Asistencia contextual** con tu perfil y tus documentos (búsqueda local
  BM25 y, si quieres, embeddings).
- **Control del entorno**: tú decides qué ventanas quedan siempre visibles, si
  ver la consola del puente, qué atajos usar y qué se guarda.
- **Independencia** de los servicios de inferencia en la nube para el uso
  diario.

> No publicamos comparativas de rendimiento con otros productos: los
> resultados dependen de tu equipo y del modelo que elijas. UNnexor incluye
> mediciones para que veas la velocidad real *en tu PC*.

---

## 🔄 El antes y el después

**Antes de UNnexor**
> Lees un artículo técnico, seleccionas un párrafo, abres otra pestaña con un
> chatbot, pegas el texto, escribes la pregunta, esperas, vuelves… y al día
> siguiente llegas al límite de mensajes de tu plan. En la videollamada en
> inglés tomas notas a mano mientras intentas seguir el hilo.

**Después de UNnexor**
> Seleccionas el párrafo y pulsas la X → *Consultar…*: la respuesta aparece en
> el panel sin salir de la página. En la videollamada, el copiloto traduce en
> vivo y al terminar exportas la transcripción. Todo con un modelo que corre
> en tu equipo, sin pagar por cada pregunta.

---

## 🌱 IA local: privacidad, costes y sostenibilidad

### 🔒 Privacidad y control
Con modelos locales, el texto, el audio y las capturas se procesan en tu
equipo y no se envían a un proveedor de inferencia. Para ser transparentes,
estas son las **comunicaciones externas** que UNnexor puede realizar:

| Conexión | Cuándo | Qué se envía |
|---|---|---|
| `ollama.com/library` y la API de GitHub (versión de Ollama) | Al iniciar, si «Consultar el catálogo» está activo (desactivable) | Una petición de lectura; ningún dato tuyo |
| Descarga de modelos (Ollama, Whisper, embeddings) | Solo cuando tú lo pides | La petición de descarga |
| Proveedores remotos de IA (Groq, OpenAI, Anthropic, Deepgram…) | Solo si los configuras **y** das tu consentimiento | Lo necesario para la consulta; los documentos solo si lo permites |

### 💸 Sin costes por token de una API externa
Con modelos locales **no pagas por cada consulta** a un proveedor. Los
modelos siguen trabajando con tokens internamente y tienen límites de
contexto, memoria y velocidad; y ejecutarlos consume **recursos y electricidad
de tu propio equipo**.

### 🌍 Menor dependencia de infraestructura externa
Procesar en tu equipo reduce la necesidad de centros de datos externos para
la inferencia y puede ser una alternativa más eficiente en algunos
escenarios. **No es impacto cero**: tu PC consume energía, y el balance real
depende del hardware, de la fuente de electricidad y de cuánto lo uses.

---

## ⏰ Disponible cuando lo necesites

Sin cuota por petición: puedes usar UNnexor todo el día, siempre que:

- el equipo esté encendido,
- Ollama y los servicios necesarios estén disponibles,
- el modelo correspondiente esté instalado, y
- haya recursos suficientes (memoria y, idealmente, una GPU compatible).

---

## ⭐ Pruébalo y súmate

- 📥 **Instálalo**: sigue la [guía de instalación](INSTALLATION.md) o la
  sección *Inicio rápido* del [README](../README.md).
- 🧭 **Aprende el copiloto**: [docs/COPILOT.md](COPILOT.md).
- 🏗️ **Explora el código**: [ARCHITECTURE.md](../ARCHITECTURE.md) y la
  carpeta [`local-bridge/`](../local-bridge).
- 🛠️ **Propón mejoras**: [CONTRIBUTING.md](../CONTRIBUTING.md).
- 🐞 **Reporta errores**: usa las *Issues* del repositorio (plantilla
  incluida) y adjunta el diagnóstico (⚙ → Diagnóstico).
- 🌟 **Dale una estrella** al proyecto en GitHub si te resulta útil.

<p align="center"><b>UNnexor AI Bridge</b><br><sub>Developed by UnlessDev</sub></p>

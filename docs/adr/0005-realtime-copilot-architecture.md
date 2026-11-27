# ADR-0005 — Arquitectura del copiloto en tiempo real

- **Estado:** aceptado (implementación incremental; ver `docs/COPILOT_STATUS.md`)
- **Fecha:** 2026-11-24

## Contexto

El copiloto añade tres modalidades —asistencia en conversaciones,
traducción en tiempo real y resolución de desafíos en pantalla— sobre el
monolito modular existente (ADR-0001). Requisitos que condicionan el diseño:

- Audio continuo (sistema y/o micrófono), transcripción progresiva y
  generación con LLM sin bloquear la interfaz Qt.
- Motores intercambiables: STT local o remoto y LLM local o remoto, de
  forma independiente (p. ej. Whisper local + Groq, o Groq STT + Ollama).
- Privacidad: nada se envía a un servicio remoto sin una elección
  consciente; ningún cambio silencioso de local a remoto.
- Debe funcionar en equipos modestos (el de referencia: Ryzen 5 2600X,
  16 GB, RX 590 sin CUDA) y aprovechar equipos mejores.
- No reemplazar funciones existentes (selección de texto, captura, OCR,
  panel flotante, proveedores, selector de modelos).

## Decisión

### Paquetes (todos dentro de `local-bridge/app/`)

| Paquete | Capa | Contenido |
|---|---|---|
| `audio/` | Dominio + infraestructura | DTO, DSP, VAD, segmentador, puerto de captura y adaptador SoundCard (WASAPI), pipeline multi-fuente. |
| `stt/` | Puerto + adaptadores | `SpeechRecognizer` y adaptadores: faster-whisper (local), OpenAI-compatible `/audio/transcriptions` (Groq, OpenAI), Deepgram. Catálogo y recomendación. |
| `copilot/` | Dominio + casos de uso | Sesiones, transcripción con segmentos provisionales/confirmados, detección de preguntas, construcción de prompts, traducción, desafíos, exportes. |
| `knowledge/` | Dominio + infraestructura | Perfiles de contexto, ingesta (TXT/MD/PDF/DOCX), troceado, BM25 y embeddings opcionales, SQLite. |
| `routing/` | Dominio | Model Router: tareas, candidatos, estrategias, política de privacidad/coste y alternativas. |
| `desktop/copilot/` | Presentación | Ventana del copiloto (tres modalidades), panel flotante, controladores Qt que traducen callbacks a señales. |

Reglas: `audio`, `stt`, `copilot`, `knowledge` y `routing` no importan Qt,
FastAPI ni el servidor; se comunican por callbacks/DTO. Mypy es estricto
en esos paquetes. Las vistas no contienen lógica de negocio.

### Concurrencia

- Un hilo de captura por fuente (`CaptureWorker`), un hilo de
  segmentación (`AudioPipeline`), un hilo de STT por sesión (cola en serie:
  los provisionales obsoletos se descartan, los finales nunca) y un hilo de
  generación por sesión con cancelación cooperativa.
- `threading` + colas acotadas en el núcleo (probado sin Qt); `QObject`
  adaptadores emiten señales con conexión en cola hacia la GUI.
- La captura nunca se bloquea: si el consumidor se retrasa se descarta lo
  más antiguo y se contabiliza.

### Inferencia

- Se reutilizan los adaptadores existentes (`OllamaClient`,
  `OpenAICompatClient`), que ya comparten `chat_stream(...)`. El copiloto
  crea sus propias instancias para no competir con el panel principal.
- El Model Router elige por tarea (respuesta, traducción, resumen, visión,
  detección de preguntas) según estrategia, capacidades, recursos, coste y
  privacidad. Las alternativas solo se usan dentro del conjunto que el
  usuario autorizó; si la única alternativa es remota y no está autorizada,
  se informa el error en lugar de cambiar de proveedor.

### Privacidad y uso legítimo

- La captura empieza solo por acción explícita, muestra indicadores
  visibles y se detiene al instante.
- Los documentos del perfil solo se envían a un proveedor remoto si el
  perfil lo permite expresamente.
- **No** se implementa exclusión de captura de pantalla
  (`SetWindowDisplayAffinity`/`WDA_EXCLUDEFROMCAPTURE`) ni ningún mecanismo
  para ocultar el copiloto a terceros o a sistemas de supervisión: sería un
  mecanismo de engaño. La ventana flotante es una ventana normal visible.
- La documentación recuerda obtener consentimiento para grabar
  conversaciones donde la ley lo exija y respetar las reglas de cada
  plataforma o evaluación.

### Persistencia

- Modelos descargados (con consentimiento): `%LOCALAPPDATA%\Extdator\models`
  en desarrollo e instalado (`paths.models_dir()`).
- Perfiles, base de conocimiento e historial de uso: `paths.copilot_dir()`
  (JSON + SQLite). Transcripciones solo en memoria salvo exportación
  explícita; borrado desde la interfaz.

## Alternativas descartadas

- **asyncio en todo el núcleo**: los backends (SoundCard, CTranslate2,
  httpx síncrono existente) son bloqueantes; mezclar bucles con Qt añade
  complejidad sin beneficio.
- **Proceso separado para STT**: aislaría fallos nativos, pero duplica
  arranque y memoria; se revisará si CTranslate2 provoca cierres.
- **Captura con PyAudioWPatch o Qt Multimedia**: Qt no ofrece loopback;
  PyAudioWPatch es válido, pero SoundCard (BSD-3) evita binarios de
  PortAudio y se verificó en el equipo de referencia.
- **WebRTC VAD**: requiere compilación; Silero v6 viene en faster-whisper
  y supera al VAD por energía (que queda como respaldo).

## Consecuencias

- Cada motor es reemplazable y se prueba con dobles sin hardware.
- El ejecutable crecerá (CTranslate2, ONNX Runtime, PyAV); se medirá en la
  etapa de empaquetado.
- Más hilos: la parada ordenada y la liberación de dispositivos están
  cubiertas por pruebas.

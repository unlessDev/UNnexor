# Estado del copiloto en tiempo real

Registro de avance del programa por etapas (prompt maestro del
2026-11-24). Solo refleja lo comprobado: cada fila indica cómo se validó.
Diseño: [ADR-0005](adr/0005-realtime-copilot-architecture.md).

Leyenda: ✅ implementado y probado · 🧪 implementado, pendiente de
validación real · 🟡 parcial · ⏳ pendiente · ⛔ no viable (motivo).

## Etapas

| Etapa | Estado | Evidencia |
|---|---|---|
| 1–2 Auditoría y documentación inicial | ✅ | `docs/AUDIT_2026-11-19.md`, PR #1 |
| 3 Diseño de arquitectura del copiloto | ✅ | ADR-0005 |
| 4 Estabilización + calidad (Black/Ruff/Mypy en CI) | ✅ | PR #9: 3 bugs reales corregidos con regresión |
| 5 Captura y segmentación de audio | ✅ | ver abajo |
| 6 Reconocimiento de voz | ✅ | ver abajo y `docs/SPEECH.md` |
| 7 Catálogo de proveedores | ✅ | ver abajo |
| 8 Compatibilidad de modelos y hardware | ⏳ | |
| 9 Model Router | ✅ | ver abajo |
| 10 Perfiles y base de conocimiento (RAG) | ⏳ | |
| 11 Asistencia en conversaciones | ⏳ | |
| 12 Traducción en tiempo real | ⏳ | |
| 13 Desafíos en pantalla | ⏳ | |
| 14 Interfaz y experiencia | ⏳ | |
| 15–20 Seguridad, pruebas integrales, distribución, CI, auditoría final | ⏳ | |

## Etapa 5 — Captura y segmentación de audio

| Capacidad | Estado | Cómo se validó |
|---|---|---|
| Audio del sistema (WASAPI loopback) | ✅ | `scripts/audio_e2e.py --loopback`: locución TTS reproducida por los altavoces y capturada; 3 intervenciones, 0 bloques perdidos (2026-11-24, M-Audio M-Track) |
| Micrófono independiente | ✅ | Sonda en hilo de trabajo con Logitech StreamCam / M-Track; pruebas con backend simulado |
| Sistema + micrófono simultáneos | 🟡 | Pipeline probado con dos fuentes simuladas; falta prueba con ambas fuentes reales a la vez |
| Selección y validación de dispositivos | 🟡 | `SoundCardBackend.list_devices()` verificado (4 dispositivos); falta la interfaz |
| Remuestreo a 16 kHz y mezcla a mono | ✅ | Pruebas de frecuencia, longitud, continuidad entre bloques y anti-aliasing |
| VAD Silero v6 (sin descargas) + respaldo por energía | ✅ | Locución real segmentada igual con ambos VAD |
| Segmentos provisionales/finales, corte de monólogos, descarte de ruidos | ✅ | `tests/test_audio.py` |
| Reconexión y liberación de dispositivos | ✅ | Backend simulado: reconexión, reintentos agotados, pausa, cierre |
| Empaquetado de SoundCard en el EXE | ⏳ | Validar en la etapa 17 (archivos `.h` de cffi) |

Limitaciones conocidas: SoundCard emite "data discontinuity" al abrir el
loopback sin reproducción (se filtra ese aviso); en Windows 8 SoundCard usa
STA (no probado).

## Etapa 6 — Reconocimiento de voz

| Capacidad | Estado | Cómo se validó |
|---|---|---|
| Whisper local (faster-whisper base/small, CPU int8) | ✅ | Benchmark con locución TTS: WER 6,8 % (solo "15%"), 1,03 / 3,48 s por intervención |
| Whisper local en GPU NVIDIA (CUDA float16) | 🧪 | Implementado con retorno automático a CPU; sin GPU NVIDIA para probar |
| Modelos medium / large-v3-turbo / large-v3 | 🧪 | Catálogo y descarga implementados; no descargados (no autorizado) |
| Groq whisper-large-v3-turbo | ✅ | 4 peticiones reales: 0,47 s de media, confianza 0,82–0,97 |
| Groq whisper-large-v3 | 🧪 | Mismo adaptador; no probado con petición real |
| OpenAI gpt-transcribe / whisper-1 | 🧪 | Adaptador probado con respuestas simuladas; sin clave de OpenAI |
| Deepgram nova-3 (pregrabado por fragmentos) | 🧪 | Adaptador probado con respuestas simuladas; sin clave de Deepgram |
| Streaming nativo (Deepgram WS, OpenAI Realtime) | ⏳ | No integrado; documentado |
| Texto provisional + confirmado en vivo | ✅ | Loopback + base: 7 provisionales, 3 finales correctos, 1,3–2,0 s de retraso |
| Recomendación por hardware | ✅ | Pruebas + ejecución sobre el hardware real detectado |
| Filtro de alucinaciones y bucles | ✅ | Pruebas unitarias y silencio real con Whisper base |
| Variantes regionales del español | 🟡 | Solo voz TTS es-MX; falta voz humana de distintas regiones |

## Etapas 7 y 9 — Catálogo de proveedores y Model Router

| Capacidad | Estado | Cómo se validó |
|---|---|---|
| Niveles de acceso estructurados y estados honestos | ✅ | Pruebas; diálogo agrupado por nivel |
| Groq (chat) a través del router y el ejecutor | ✅ | 1 petición real: `openai/gpt-oss-20b`, 27 fragmentos, 0,43 s |
| Anthropic (adaptador nativo) | 🧪 | Respuestas simuladas (SSE, errores, paginación); sin clave real |
| Deepgram: validación de clave sin coste | 🧪 | Simulado; sin clave real |
| Consentimientos remote_data / costs / documents | ✅ | Pruebas de persistencia y del router |
| Historial de uso y límites informados | ✅ | Pruebas + registro real tras la petición a Groq |
| Router: solo local por defecto, estrategias, fijación, explicación | ✅ | `tests/test_routing.py` |
| Alternativas sin cambios silenciosos; sin cambio tras emitir texto | ✅ | `tests/test_routing.py` |
| Candidatos de Ollama con capacidades reales | 🧪 | Ollama simulado; validar con Ollama en marcha en la etapa 11 |
| Interfaz del catálogo/consentimientos en el copiloto | ⏳ | Etapa 14 |

## Bloqueantes registrados para la distribución

- `deployment/windows/Extdator.spec` excluye hoy `numpy` y `onnxruntime`
  (por el OCR). Cuando el copiloto se conecte a la interfaz, el ejecutable
  debe incluir NumPy, SoundCard (y sus `.h` de cffi), CTranslate2, PyAV,
  ONNX Runtime y los assets de faster-whisper; medir el tamaño resultante.
- Revisar la configuración LGPL de las DLL de FFmpeg de PyAV.

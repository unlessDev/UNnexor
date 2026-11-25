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
| 6 Reconocimiento de voz | ⏳ | |
| 7 Catálogo de proveedores | ⏳ | |
| 8 Compatibilidad de modelos y hardware | ⏳ | |
| 9 Model Router | ⏳ | |
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

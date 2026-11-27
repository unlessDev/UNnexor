# HANDOFF — estado técnico y próximos pasos

Última actualización: 2026-11-27. Documento para retomar el trabajo sin
contexto previo. Complementa `CHANGELOG.md`, `ARCHITECTURE.md`,
`docs/COPILOT_STATUS.md` y `docs/BRANDING.md`.

## 1. Qué es el proyecto

**UNnexor** (antes *Extdator*): extensión de navegador MV3 + puente local
Python (FastAPI en `127.0.0.1:5000`) + aplicación de escritorio PySide6 +
modelos locales en Ollama. Monolito modular con límites Clean/Hexagonal
(ver `ARCHITECTURE.md` y ADR 0001–0005).

| Carpeta | Contenido |
|---|---|
| `browser-extension/` | `content.js` (botón X, menú Enviar/Consultar), `background.js` (POST `/api/selection`), opciones y emparejamiento |
| `local-bridge/app/` | servidor, panel, copiloto (`audio/`, `stt/`, `copilot/`, `knowledge/`, `routing/`, `providers/`, `compat/`) |
| `branding/` | identidad: referencia original, SVG maestro de la X y rasterizados |
| `deployment/` | PyInstaller (`windows/UNnexor.spec`), Inno Setup (`installer/UNnexor.iss`), SBOM, verificación |
| `scripts/` | `install.ps1`, `start.ps1` (`-Console`, `-ServerOnly`), `stop.ps1`, `make_brand_assets.py` |

## 2. Flujo de trabajo acordado

- Ramas `feature/*` o `fix/*` → PR a `develop` → merge tras CI verde.
  **Nunca tocar `main`** ni reescribir historia; publicar una versión
  requiere autorización expresa.
- Si `gh pr create/merge` falla con errores GraphQL, usar la API REST:
  `gh api -X POST repos/unlessDev/Extdator/pulls …` /
  `gh api -X PUT repos/unlessDev/Extdator/pulls/<n>/merge`.
- Puertas de calidad (desde `local-bridge/`): `python -m black --check .
  ../deployment ../scripts`, `python -m ruff check . ../deployment
  ../scripts`, `python -m mypy`, `python -m pytest -q`. **Comprobar el
  código de salida**, no solo la última línea (un `| tail` ocultó un fallo).
- En este equipo los heredocs de Bash alteran las barras invertidas: escribir
  parches con contenido `\n`, `\x..` o rutas de Windows con la herramienta de
  escritura de archivos, no con heredocs.

## 3. Programa 1 — Copiloto en tiempo real (2026-11-24/25, en `develop`)

Implementado y probado: captura WASAPI loopback + micrófono, Silero VAD,
Whisper local (faster-whisper) y remoto (Groq/OpenAI/Deepgram), detección de
preguntas, respuestas con perfil + RAG (BM25 + embeddings), traducción en
vivo con exportación, desafíos en pantalla (visión/OCR), catálogo de
proveedores con niveles de coste y consentimientos, compatibilidad en cinco
clases, benchmarks, router de modelos, ejecutable + instalador. Detalle por
etapa en `docs/COPILOT_STATUS.md`.

Correcciones de campo (PR #18): recuperación ante falta de VRAM en Ollama,
bucles de STT (prompt solo con glosario, antirrepetición, umbral −50 dBFS),
historial persistente en panel y desafíos, redimensionado sin marco,
minimizar sin desaparecer de la barra de tareas.

## 4. Programa 2 — UNnexor (2026-11-26/27)

| Etapa | Estado | Dónde |
|---|---|---|
| Rebranding visible Extdator → UNnexor | ✅ fusionado (PR #19) | identificadores técnicos conservados: `docs/BRANDING.md` |
| X en SVG creada desde cero + PNG/ICO/iconos | ✅ PR #19 | `scripts/make_brand_assets.py`, `branding/` |
| Extensión: Enviar directamente / Consultar… | ✅ PR #19 | `content.js`, campo `question` en `/api/selection` |
| ASCII «UNnexor · AI BRIDGE · by UnlessDev» | ✅ PR #19 | `app/banner.py` (start.ps1 y run.py) |
| Consola del puente opcional | ✅ PR #19 | `app/desktop/log_console.py`; `start.ps1` sin terminal salvo `-Console` |
| Arranque/parada de Ollama con propiedad | ✅ PR #19 | `app/ollama_service.py` (`OLLAMA_AUTOSTART`) |
| Escucha temporizada / manual con tecla | ✅ rama `feature/window-pin-listen` | `app/copilot/listening.py`, controlador, página Conversación, panel flotante |
| «Siempre visible» real por ventana | ✅ misma rama | `app/desktop/topmost.py` |
| Documentos del perfil .md/.txt | ✅ misma rama | `knowledge/extract.py`, página Perfiles |
| Catálogo en línea + versión de Ollama | ✅ misma rama | `app/ollama_library.py`, diálogo Modelos de IA |
| Recomendar configuración (3 perfiles, por función) | ✅ misma rama | `app/compat/advisor.py`, `app/desktop/recommend_dialog.py` |
| Presentación y README | ✅ misma rama | `docs/UNNEXOR_OVERVIEW.md`, `README.md` |

Pruebas: 492 aprobadas, 1 omitida. Validaciones reales hechas:
consulta con pregunta respondida por `qwen2.5vl:7b`; cierre limpio (puerto
libre, sin procesos, Ollama del usuario intacto, código de salida 0);
`SetWindowPos` real conservando el mismo HWND y los estilos de barra de
tareas; escucha temporizada de 45 s con audio TTS real → transcripción →
respuesta local (`scripts/listen_window_e2e.py`); recomendaciones sobre el
equipo real (Ryzen 5 2600X, 16 GB, RX 590 8 GB).

## 5. Artefactos generados (2026-11-27, sin firma)

| Artefacto | Resultado |
|---|---|
| `release/windows/UNnexor/UNnexor.exe` (+ ZIP 121,6 MB) | Self-check OK (incluye componentes del copiloto); prueba real: ventana con la X, 📌 con `WS_EX_TOPMOST` real, cierre con código 0, puerto libre, Ollama del usuario intacto |
| `release/installer/UNnexor-1.2.0-win64-setup.exe` (84,8 MB) | Instalación, self-check, conservación de datos, purga y desinstalación OK. **Actualización desde Extdator 1.2.0 probada**: misma carpeta, retira `Extdator.exe` y el grupo antiguo del menú Inicio, una sola entrada de desinstalación |
| `release/browser-extension/{chrome,edge}.zip` | Regenerado con la X (antes era el paquete del 19/11 llamado Extdator) |
| `release/sbom/unnexor-1.2.0.cdx.json` | Regenerado; `verify_artifacts.py`: 4/4 OK |

La página de opciones de la extensión tiene cabecera animada con la X y
«UNnexor · AI BRIDGE · by UnlessDev» (PR #21).

## 6. Pendiente / no verificado

1. Fusionar el PR #21 cuando la CI esté verde.
2. Prueba manual en el navegador: recargar la extensión en
   `chrome://extensions` (o cargar `release/browser-extension/chrome`),
   recargar las pestañas y probar el menú de la X y *Consultar…*.
3. Arranque automático de Ollama **cuando no está abierto** (solo con
   simulación; en este equipo Ollama ya estaba abierto y no se cerró).
4. Pulsar físicamente los atajos de escucha con la aplicación real.
5. Velocidad del copiloto: el router eligió `deepseek-r1:8b` (~70 s por
   respuesta); aplicar «Recomendar configuración».
6. Ollama 0.40.17 instalado; disponible 0.40.18 (la app solo avisa).
7. Decisiones del propietario: licencia, renombrar el repositorio, número de
   versión (sigue en 1.2.0), firma Authenticode, publicación en tiendas,
   publicar una release (`develop` → `main`).
8. Programa 1 aún abierto: auditoría de seguridad final, prueba en un equipo
   limpio, CUDA/NVIDIA y claves reales de OpenAI/Deepgram/Anthropic (ver
   `docs/COPILOT_STATUS.md`).

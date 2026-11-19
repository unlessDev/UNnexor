# Catálogo de modelos y evaluador de compatibilidad

El selector de modelos del panel (⚙ → **Modelos**) clasifica cada modelo
de Ollama con un semáforo. Este documento explica **cómo se calcula** y
cuáles son los límites de la estimación.

## Categorías

| Emoji | Etiqueta | Significado |
|---|---|---|
| 🟢 | PERFECTO | Cabe con margen razonable; velocidad esperable completa. |
| 🟡 | REGULAR | Ejecutable, pero usará parte de la RAM del sistema o irá más lento. |
| 🟠 | ALTO CONSUMO | Ejecutable, pero consumirá una proporción elevada de recursos. |
| 🔴 | IMPOSIBLE | Incompatible fundamentada con el equipo actual. **La selección está deshabilitada.** |

El rojo **solo** aparece con una incompatibilidad fundada: no cabe ni en
VRAM+RAM disponibles, no hay datos suficientes de forma segura, o el
disco no tiene espacio para la descarga.

## Cómo se estima la memoria

Datos de entrada (nunca inventados):

* **Pesos**: tamaño en disco del modelo (`GET /api/tags` de Ollama).
  Aproxima los pesos cuantizados cargados (desvío típico <10 %).
* **Parámetros**: `parameter_size` de `/api/show` (p. ej. `8B`); si la
  API no lo da, se deduce del nombre (`qwen3:8b` → 8.0); como último
  recurso se estima del tamaño con ~0.55 GB por 1B (cuantización Q4_K_M)
  y se marca como *estimado*. Si nada funciona, queda "desconocido".
* **KV cache**: `kv_gb ≈ ctx_tokens × 16 KB × params_B / 1024²` (fp16,
  arquitectura GQA densa típica, contexto por defecto 8192). Desvío
  esperado ±30 % según arquitectura real y cache cuantizado.
* **Sobrecarga del runtime**: 0.5 GB (buffers de cómputo y del grafo a
  ~8k de contexto; verificado empíricamente con `ollama ps`).

## Umbrales de clasificación

Con GPU (VRAM usable = total × 0.95, reserva de escritorio/driver):

| Caso | Condición | Categoría |
|---|---|---|
| Cabe completo en GPU | margen ≥ 20 % | 🟢 |
| Cabe completo en GPU | margen 8–20 % | 🟡 |
| Cabe completo en GPU | margen < 8 % | 🟠 |
| Híbrido GPU+RAM | ≥ 70 % del modelo en GPU | 🟡 |
| Híbrido GPU+RAM | 40–70 % en GPU | 🟠 |
| Híbrido GPU+RAM | < 40 % en GPU | 🔴 |
| No cabe en VRAM+RAM | falta memoria | 🔴 |

Solo CPU (sin GPU usable; RAM disponible menos 1 GB de reserva del
sistema): 🟢 margen ≥ 35 % · 🟡 ≥ 12 % · 🟠 ≥ 3 % · 🔴 por debajo.

Además, modelos **no instalados** se evalúan contra el espacio en disco
(peso × 1.1 por descarga/descompresión); sin espacio → 🔴.

## Límites honestos de la estimación

* Todo el cálculo es una **estimación**, no una medición: los `metrics`
  del resultado lo indican (`estimated: true`, `params_source`).
* La fórmula del KV cache asume arquitectura densa GQA típica; MoE,
  GQA atípico o contextos cuantizados desvían el resultado.
* La VRAM se lee del registro de Windows
  (`HardwareInformation\qwMemorySize`); si no se puede leer, se evalúa
  como solo-CPU y se advierte.
* El dato de terreno `ollama_gpu_active` (¿el modelo cargado está
  realmente en GPU?) proviene de `GET /api/ps` de Ollama y es `null`
  cuando no hay modelos cargados.
* Ejemplo verificado en el equipo de desarrollo (RX 590 8 GB,
  16 GB RAM): `qwen3:8b` → 🟡 REGULAR (cabe con 19 % de margen, coincide
  con el uso real), `qwen2.5vl:7b` → 🟠 ALTO CONSUMO (cabe apenas en
  GPU) y `gemma4:26b` → 🔴 IMPOSIBLE (faltan ~6 GB). qwen2.5vl:7b se
  verificó también en terreno: carga y generación con imagen funcionan
  (la categoría 🟠 refleja que conviene cerrar otras apps con GPU).

## Endpoints y UI

* `GET /api/hardware` — CPU, RAM, GPU(s), disco, `models_dir`,
  `ollama_gpu_active` (auth por token).
* `GET /api/models/eval` — modelos instalados con su `Assessment`
  completo (tier, razones, métricas) + hardware usado.
* Panel: ⚙ → **Modelos** lista instalados y catálogo sugerido; los 🔴
  están deshabilitados; descargar un modelo nuevo **siempre pide
  confirmación** con el tamaño estimado.

## Referencia técnica

* Evaluador: `local-bridge/app/model_evaluator.py`
  (`assess_model`, `estimate_requirements`, `gpu_budget`,
  `parse_parameter_count`).
* Catálogo estático de sugerencias: `local-bridge/app/model_catalog.py`.
* Hardware: `local-bridge/app/hardware.py`.
* Pruebas: `local-bridge/tests/test_hardware_models.py`.

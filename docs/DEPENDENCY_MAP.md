# Mapa de dependencias

> Regla: las flechas apuntan hacia la capa de la que se depende. El dominio
> no debe depender de la presentación ni de frameworks de infraestructura.

```text
browser-extension ──HTTP/JSON──▶ server (API)
                                    │
                                    ▼
                              queue_manager
                                    │
                                    ▼
                             desktop/workers
                              │            │
                              ▼            ▼
                         domain       providers/ollama
                         (config,     (adaptadores HTTP)
                          conversation,
                          math, models)
                                    │
                                    ▼
                              desktop/window
                              (Qt, presentación)
```

## Dependencias permitidas

| Origen | Puede depender de | No debe depender de |
|---|---|---|
| `conversation.py`, `math_unicode.py`, `model_catalog.py`, `model_evaluator.py`, `paths.py` | Biblioteca estándar; otros módulos de dominio | PySide6, FastAPI, httpx, psutil, keyring |
| `hardware.py` | Biblioteca estándar; psutil (lazy); puerto `RunningModelsSource` | Cliente Ollama concreto, Qt |
| `ollama_client.py`, `providers/` | httpx, keyring, contratos del proveedor | PySide6, ventanas |
| `server.py` | FastAPI, dominio, cola, seguridad | PySide6, `desktop/` |
| `desktop/*` | Qt, dominio, cola, adaptadores | `server.py`, `main.py`, FastAPI |
| `main.py` | Composición de todos los módulos | — (es el composition root) |
| `browser-extension` | `chrome.*`, `fetch` al puente | Claves de proveedores, código remoto |

## Contratos principales

| Contrato | Productor | Consumidor | Pruebas |
|---|---|---|---|
| `POST /api/selection` | extensión | `server.py` → `Bus` | `test_server_api.py`, `test_server_new_api.py` |
| Eventos de cola (`SelectionRequest`, `ImageRequest`, `ClearConversation`, `ConfigUpdate`) | API/GUI | `GenerationWorker` | `test_workers.py` |
| `chat_stream(messages, model, ...)` | `OllamaClient`, `OpenAICompatClient` | worker | `test_ollama_client.py`, `test_providers.py` |
| `RuntimeState.provider()/model` | GUI | worker | `test_workers.py`, `test_gui_logic.py` |
| Persistencia de ajustes | UI | `SettingsStore` | `test_settings_store.py` |
| Rutas de runtime | composición | config/settings/providers | `test_paths.py` |

## Verificación automática

`local-bridge/tests/test_architecture.py` comprueba:

- que los módulos de dominio no importen frameworks ni Qt;
- que `server.py` no importe Qt;
- que la extensión no use `eval` ni `new Function`;
- que `desktop/` no importe `server.py` ni `main.py`.

Estas pruebas son deliberadamente estáticas y rápidas. No sustituyen a las
pruebas de integración.

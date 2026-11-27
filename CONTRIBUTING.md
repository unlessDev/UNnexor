# Contribuir a UNnexor

## Requisitos

- Windows 10/11.
- Python 3.10+.
- Git y, opcionalmente, GitHub CLI.
- Ollama para pruebas de integración reales (las pruebas unitarias usan
  transportes simulados).

## Puesta en marcha

```powershell
git clone https://github.com/unlessDev/Extdator.git
cd UNnexor
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
powershell -ExecutionPolicy Bypass -File scripts\start.ps1
```

Las pruebas:

```powershell
cd local-bridge
.venv\Scripts\python.exe -m pytest tests -q
```

Calidad (lo mismo que ejecuta la CI; configuración en
`local-bridge/pyproject.toml`):

```powershell
cd local-bridge
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m black --check . ..\deployment
.venv\Scripts\python.exe -m ruff check . ..\deployment
.venv\Scripts\python.exe -m mypy
```

- Black da formato; Ruff revisa errores, imports y simplificaciones.
- Mypy verifica `app/`; los paquetes del copiloto (`app.audio`, `app.stt`,
  `app.copilot`, `app.knowledge`, `app.routing`) exigen anotaciones
  completas. El código nuevo usa `list[str]`/`X | None`.

## Ramas y commits

- `main`: estable, solo releases y merges revisados.
- `develop`: integración.
- `feature/<tema>`, `fix/<tema>`, `docs/<tema>`, `security/<tema>`.
- Commits con Conventional Commits: `feat:`, `fix:`, `refactor:`, `docs:`,
  `test:`, `build:`, `ci:`, `chore:`, `perf:`, `security:`.

Cada PR debe indicar objetivo, cambios, pruebas ejecutadas, riesgos y
documentación actualizada. No se fusiona con fallos críticos.

## Reglas técnicas

- No introduzcas secretos, `.env` reales, claves, capturas personales ni
  artefactos de build en el repositorio.
- Mantén el backend en `127.0.0.1`; no añadas endpoints sin autenticación.
- Las claves API solo en el keyring.
- Añade o actualiza pruebas para cada cambio funcional.
- Actualiza `ARCHITECTURE.md` y `CHANGELOG.md` cuando corresponda.
- No cambies la licencia principal, la visibilidad ni las protecciones de
  GitHub sin autorización del propietario.
- No descargues modelos ni pesos OCR sin consentimiento.

## Reportes

Usa las plantillas de issue del repositorio. Para vulnerabilidades, sigue
`SECURITY.md`.

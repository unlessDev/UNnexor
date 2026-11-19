# Changelog

Todos los cambios relevantes se registran aquí. El formato sigue
[Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/) y el proyecto
usa [Semantic Versioning](https://semver.org/lang/es/).

## [Unreleased]

### Architecture
- Documento canónico `ARCHITECTURE.md` en la raíz, con módulos, capas,
  contratos, flujos, seguridad y límites reales del proyecto.
- ADR-0001: monolito modular con límites Clean/Hexagonal pragmáticos.
- ADR-0002: se mantiene HTTP local autenticado; Native Messaging queda
  como alternativa evaluada, no como migración inmediata.
- ADR-0003: PyInstaller + Inno Setup como estrategia de distribución.
- ADR-0004: licencia principal pendiente de aprobación del propietario.
- `app/paths.py` separa rutas de desarrollo e instalación
  (`%LOCALAPPDATA%\Extdator`), con overrides para tests/portable.
- El detector de hardware depende de un puerto (`RunningModelsSource`) y
  no del cliente HTTP concreto.
- Pruebas de arquitectura para límites de imports y ausencia de código
  remoto/eval en la extensión.

### Fixed
- `app/__init__.py` declaraba `1.0.0` mientras el manifest declaraba
  `1.2.0`; la versión queda unificada en `1.2.0`.

### Added
- Auditoría inicial con línea base de pruebas y riesgos conocidos.
- Higiene de repositorio: `.editorconfig`, `.gitattributes` y `.gitignore`
  ampliado para datos de usuario, artefactos y credenciales.

### Changed
- `docs/ARCHITECTURE.md` se traslada a `ARCHITECTURE.md` (raíz) para que
  sea el documento canónico; se actualizan los enlaces.

### Tests
- Línea base: `202 passed, 1 skipped` (2026-11-19).

## [1.2.0] - 2026-11-19

### Added
- Selector de modelos con evaluación según hardware.
- Proveedores remotos OpenAI-compatible con claves en keyring.
- Configuración desde la UI, emparejamiento por código, captura por región.
- OCR opcional (Pix2Text/RapidOCR/Tesseract) y visión multimodal.
- Conversión de LaTeX a matemática Unicode legible y corrección de altura.

### Security
- Token de puente, validación de origen, límites y logs sin contenido.
- Claves de proveedores fuera de archivos y de la extensión.

[Unreleased]: https://github.com/unlessDev/Extdator/compare/v1.2.0...HEAD
[1.2.0]: https://github.com/unlessDev/Extdator/releases/tag/v1.2.0

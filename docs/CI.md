# Integración continua, SBOM y verificación

UNnexor se construye y verifica en GitHub Actions sobre `windows-latest`.
Este documento describe qué hace la cadena, cómo reproducirla en local y
qué **no** cubre. No hay publicación automática de releases ni de tiendas.

## Flujo de CI

Archivo: `.github/workflows/ci.yml`.

Se ejecuta en:

- `push` a `main` y `develop`;
- `pull_request` hacia `main` y `develop`;
- `workflow_dispatch` manual.

Permisos: `contents: read`. No usa secretos, certificados de firma ni
credenciales de tiendas. La concurrencia cancela ejecuciones duplicadas de
la misma referencia.

| Trabajo | Contenido |
|---|---|
| `Pruebas` | Python 3.12; Black, Ruff y Mypy (config en `local-bridge/pyproject.toml`, herramientas en `requirements-dev.txt`) y `pytest tests -q`. |
| `Empaquetar y verificar` | Depende de las pruebas; compila extensión, EXE one-folder, instalador y SBOM; verifica todo. |

El trabajo de artefactos ejecuta, en orden:

1. `python deployment/extension/build.py` — ZIP Chrome/Edge + hashes.
2. `python deployment/windows/build.py` — EXE one-folder, ZIP, smoke
   headless y GUI offscreen. Ambos sondeos esperan 5 s por petición porque
   `/health` consulta Ollama; el auto-chequeo tolera hasta 30 s en total.
   Si el smoke falla, el error incluye el final de `bridge.log`.
3. `choco install innosetup --yes --no-progress` — Inno Setup 6.
4. `python deployment/installer/build.py` — instalador y prueba de ciclo
   completo (instalar, self-check, preservar datos, purgar, desinstalar).
5. `python deployment/sbom/build.py` — SBOM CycloneDX 1.5.
6. `python deployment/verify_artifacts.py` — checksums, informes y SBOM.

Los artefactos verificados se adjuntan al flujo durante 14 días con el
nombre `extdator-windows-artifacts`. Si algo falla se adjuntan además los
informes y logs con el nombre `extdator-ci-reports`. Nada se publica en
Releases, tiendas ni servidores externos.

## Reproducción local

Desde la raíz del repositorio, con el entorno virtual de `local-bridge`
y los paquetes ya compilados:

```powershell
cd local-bridge
.venv\Scripts\python.exe ..\deployment\sbom\build.py
.venv\Scripts\python.exe ..\deployment\verify_artifacts.py
```

La verificación devuelve código 0 solo si los cuatro productos
(extensión, escritorio, instalador y SBOM) son coherentes. El informe se
escribe en `release/verification-report.json`.

## SBOM

`deployment/sbom/build.py` genera `release/sbom/extdator-<versión>.cdx.json`
y su `SHA256SUMS.txt` sin dependencias externas:

- formato CycloneDX 1.5 con `serialNumber` determinista para el mismo
  conjunto de componentes;
- componente raíz `UNnexor` con versión, repositorio, estado de licencia
  (`Apache-2.0`), estado de distribución (`not-published`) y
  firma (`unsigned`);
- componentes de producto: extensión, puente local y entorno de build;
- bibliotecas Python instaladas en el entorno que ejecuta el script, con
  `purl`, licencias declaradas por el paquete y referencia de proyecto
  cuando existe;
- dependencias directas de `requirements.txt` y `requirements-build.txt`
  enlazadas desde el puente y el entorno de build.

Decisiones y límites:

- la licencia del proyecto (Apache-2.0, decidida por el propietario) se
  declara en el SBOM y se verifica;
- el SBOM describe el entorno Python de build/runtime; la extensión no
  tiene dependencias empaquetadas ni código remoto, y los modelos de
  Ollama, pesos OCR y datos de usuario quedan fuera por diseño;
- no se incluyen rutas locales, nombres de usuario, claves ni variables de
  entorno;
- el hash se calcula sobre los bytes reales del archivo, no sobre el texto
  antes de escribirlo.

## Verificación de artefactos

`deployment/verify_artifacts.py` comprueba:

- que exista `SHA256SUMS.txt` en cada producto y que cada hash coincida
  con el archivo real;
- que las rutas de los checksums sean relativas, sin `..`, sin symlinks y
  sin separadores de Windows;
- que los informes de build existan, declaren la versión correcta y sus
  hashes/tamaños coincidan con los archivos;
- en escritorio: build sin firma declarado, smoke headless y GUI correctos;
- en instalador: ciclo completo superado, self-check correcto, datos y
  token preservados, purga y desinstalación correctas;
- en SBOM: CycloneDX 1.5, versión correcta, licencia Apache-2.0 declarada y
  componentes presentes.

El informe resultante (`release/verification-report.json`) resume cada
producto y sus errores. La verificación es local: no descarga artefactos
ni consulta servicios externos.

## Lo que la CI no hace

- No firma con Authenticode (no hay certificado).
- No crea tags, releases ni PRs.
- No publica en Chrome Web Store ni Edge Add-ons.
- No descarga modelos de Ollama, pesos OCR ni datos de usuario.
- No sustituye una instalación manual en un equipo limpio.

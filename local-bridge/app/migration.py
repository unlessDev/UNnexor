"""Migración de datos de instalaciones anteriores (Extdator → UNnexor).

Hasta la versión 2.0.0 los datos de usuario se guardaban en
``%LOCALAPPDATA%\\Extdator`` (configuración, credenciales no secretas,
perfiles del copiloto, modelos de voz…). A partir de 2.1.0 la carpeta es
``%LOCALAPPDATA%\\UNnexor``.

Reglas (ver ``docs/CONFIGURATION.md``):

* Se **copia**, nunca se mueve: la carpeta antigua se conserva intacta.
* Nunca se sobrescribe un archivo que ya exista en la ruta nueva (los
  datos nuevos ganan a los antiguos).
* Cada archivo se copia a un temporal ``*.migrating`` y se renombra solo
  si el tamaño coincide; un corte a mitad no deja archivos truncados.
* El resultado se registra en ``migration.json`` dentro de la carpeta
  nueva. Si hubo errores, la migración se reintenta en el siguiente
  arranque (solo los archivos que falten).
* Los registros antiguos (``logs``) no se migran: no son necesarios.

También se migran las preferencias de ventana guardadas con QSettings
(``HKCU\\Software\\Extdator``) y, de forma perezosa, las credenciales del
almacén seguro (``providers/manager.py``).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from . import paths

log = logging.getLogger("unnexor.migration")

MARKER_NAME = "migration.json"
NOTE_NAME = "LEEME - datos migrados a UNnexor.txt"
SKIP_DIRS = frozenset({"logs"})
TMP_SUFFIX = ".migrating"
QSETTINGS_GROUPS = ("Bridge", "CopilotOverlay", "WindowPins")


@dataclass
class MigrationReport:
    status: str  # not_needed | skipped_override | already_done | completed | partial
    legacy: str = ""
    target: str = ""
    copied: int = 0
    copied_bytes: int = 0
    kept_existing: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status in (
            "not_needed",
            "skipped_override",
            "already_done",
            "completed",
        )

    def summary(self) -> str:
        if self.status == "not_needed":
            return "Sin datos de versiones anteriores (Extdator): nada que migrar."
        if self.status == "skipped_override":
            return "Migración omitida: el directorio de datos está forzado por variable de entorno."
        if self.status == "already_done":
            return f"Datos de Extdator ya migrados anteriormente a {self.target}."
        text = (
            f"Migración Extdator → UNnexor: {self.copied} archivos copiados "
            f"({self.copied_bytes / 1_048_576:.1f} MB), {self.kept_existing} ya existían "
            f"y se conservaron. Origen conservado: {self.legacy}."
        )
        if self.errors:
            text += (
                f" {len(self.errors)} errores; se reintentará en el próximo arranque."
            )
        return text


def _read_marker(target: Path) -> dict:
    try:
        return json.loads((target / MARKER_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _iter_files(root: Path) -> Iterable[Path]:
    for current, dirs, files in os.walk(root):
        rel_dir = Path(current).relative_to(root)
        if rel_dir == Path("."):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if name.endswith(TMP_SUFFIX) or name in (MARKER_NAME, NOTE_NAME):
                continue
            yield Path(current) / name


def _copy_verified(source: Path, dest: Path) -> int:
    """Copia ``source`` a ``dest`` vía temporal y verifica el tamaño."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + TMP_SUFFIX)
    try:
        shutil.copy2(source, tmp)
        expected = source.stat().st_size
        if tmp.stat().st_size != expected:
            raise OSError(
                f"tamaño distinto tras copiar ({tmp.stat().st_size} != {expected})"
            )
        os.replace(tmp, dest)
        return expected
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def migrate_legacy_data(
    legacy: Optional[Path] = None,
    target: Optional[Path] = None,
    *,
    respect_overrides: bool = True,
) -> MigrationReport:
    """Copia ``%LOCALAPPDATA%\\Extdator`` a ``%LOCALAPPDATA%\\UNnexor``.

    Idempotente y segura ante fallos; nunca lanza excepciones.
    """
    overridden = bool(paths.env_override("DATA_DIR"))
    if respect_overrides and legacy is None and target is None and overridden:
        return MigrationReport("skipped_override")
    legacy = legacy or paths.legacy_data_root()
    target = target or paths.app_data_root()
    report = MigrationReport("not_needed", legacy=str(legacy), target=str(target))
    try:
        if not legacy.is_dir() or legacy.resolve() == target.resolve():
            return report
    except OSError:
        return report
    marker = _read_marker(target)
    if marker.get("status") == "completed" and marker.get("legacy") == str(legacy):
        report.status = "already_done"
        return report

    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        report.status = "partial"
        report.errors.append(f"No se pudo crear {target}: {exc}")
        return report

    for source in _iter_files(legacy):
        rel = source.relative_to(legacy)
        dest = target / rel
        try:
            if dest.exists():
                report.kept_existing += 1
                continue
            report.copied_bytes += _copy_verified(source, dest)
            report.copied += 1
        except OSError as exc:
            report.errors.append(f"{rel}: {exc}")

    # Verificación final: todo archivo del origen tiene su equivalente.
    for source in _iter_files(legacy):
        dest = target / source.relative_to(legacy)
        if not dest.exists():
            message = f"{source.relative_to(legacy)}: no está en la carpeta nueva"
            if message not in report.errors:
                report.errors.append(message)

    report.status = "partial" if report.errors else "completed"
    record = {
        "status": report.status,
        "legacy": str(legacy),
        "target": str(target),
        "copied": report.copied,
        "copied_bytes": report.copied_bytes,
        "kept_existing": report.kept_existing,
        "errors": report.errors[:50],
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        (target / MARKER_NAME).write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as exc:
        report.errors.append(f"No se pudo escribir {MARKER_NAME}: {exc}")
    if report.status == "completed":
        try:
            (legacy / NOTE_NAME).write_text(
                "Los datos de esta carpeta se copiaron a:\n"
                f"{target}\n\n"
                "UNnexor ya usa la carpeta nueva. Esta copia se conserva por "
                "seguridad: puedes borrarla cuando compruebes que todo funciona.\n",
                encoding="utf-8",
            )
        except OSError:
            pass  # carpeta antigua de solo lectura: no es un error de migración
    return report


def migrate_qsettings(groups: Iterable[str] = QSETTINGS_GROUPS) -> list[str]:
    """Copia preferencias de QSettings ``Extdator/<grupo>`` → ``UNnexor/<grupo>``.

    Solo si el grupo nuevo está vacío (nunca pisa preferencias nuevas).
    Devuelve los grupos migrados.
    """
    try:
        from PySide6.QtCore import QSettings
    except Exception:  # pragma: no cover - PySide6 siempre presente en la app
        return []
    migrated: list[str] = []
    for group in groups:
        try:
            old = QSettings(paths.LEGACY_APP_DIR_NAME, group)
            keys = old.allKeys()
            if not keys:
                continue
            new = QSettings(paths.APP_DIR_NAME, group)
            if new.allKeys():
                continue
            for key in keys:
                new.setValue(key, old.value(key))
            new.sync()
            migrated.append(group)
        except Exception as exc:  # nunca impedir el arranque
            log.warning("No se pudieron migrar las preferencias '%s': %s", group, exc)
    return migrated

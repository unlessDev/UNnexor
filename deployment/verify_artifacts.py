"""Verifica checksums, informes y coherencia de los artefactos de UNnexor.

Uso:
    python deployment/verify_artifacts.py
    python deployment/verify_artifacts.py --release-dir release

Comprueba, para cada producto empaquetado:
  * que exista su ``SHA256SUMS.txt`` y que todos los hashes coincidan;
  * que el informe de build exista, tenga la versión correcta y declare
    las pruebas de humo/instalación superadas;
  * que el SBOM CycloneDX generado sea válido y tenga su checksum.

Escribe ``release/verification-report.json`` y devuelve código 1 si algo
falla. No publica ni mueve artefactos.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_INIT = PROJECT_ROOT / "local-bridge" / "app" / "__init__.py"
DEFAULT_RELEASE = PROJECT_ROOT / "release"
CHECKSUM_LINE = re.compile(r"^([0-9a-fA-F]{64})\s+\*?(\S.*)$")


def app_version() -> str:
    text = APP_INIT.read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', text)
    if not match:
        raise ValueError("No se pudo leer __version__ de app/__init__.py")
    return match.group(1)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(name: str) -> str:
    if not name or name.startswith(("/", "\\")):
        raise ValueError(f"ruta no relativa en checksums: {name!r}")
    if "\\" in name:
        raise ValueError(f"ruta con separador Windows en checksums: {name!r}")
    path = PurePosixPath(name)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"ruta insegura en checksums: {name!r}")
    return path.as_posix()


def _read_checksums(base: Path) -> Dict[str, str]:
    checksum_file = base / "SHA256SUMS.txt"
    if not checksum_file.is_file():
        raise FileNotFoundError(f"Falta {checksum_file}")
    result: Dict[str, str] = {}
    for line_no, line in enumerate(
        checksum_file.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = CHECKSUM_LINE.match(line)
        if not match:
            raise ValueError(f"Línea inválida en {checksum_file}:{line_no}")
        digest, name = match.group(1).lower(), _safe_relative(match.group(2))
        if name in result:
            raise ValueError(f"Checksum duplicado para {name} en {checksum_file}")
        result[name] = digest
    if not result:
        raise ValueError(f"{checksum_file} no contiene checksums")
    return result


def _verify_checksums(base: Path) -> List[Dict[str, object]]:
    checksums = _read_checksums(base)
    root = base.resolve()
    entries: List[Dict[str, object]] = []
    for name, expected in sorted(checksums.items()):
        path = base.joinpath(*PurePosixPath(name).parts)
        if path.is_symlink():
            raise ValueError(f"Symlink no permitido en artefactos: {name}")
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            raise ValueError(f"Ruta fuera del directorio de artefactos: {name}")
        if not path.is_file():
            raise FileNotFoundError(f"Falta el artefacto {path}")
        actual = _sha256(path)
        entries.append(
            {
                "file": name,
                "sha256": actual,
                "expected": expected,
                "bytes": path.stat().st_size,
                "ok": actual == expected,
            }
        )
    return entries


def _read_report(base: Path) -> dict:
    report_file = base / "build-report.json"
    if not report_file.is_file():
        raise FileNotFoundError(f"Falta {report_file}")
    data = json.loads(report_file.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{report_file} no contiene un objeto JSON")
    return data


def _check(condition: bool, message: str, errors: List[str]) -> None:
    if not condition:
        errors.append(message)


def _product_result(
    name: str,
    base: Path,
    checksums: List[Dict[str, object]],
    errors: List[str],
    report: Optional[dict] = None,
) -> dict:
    result = {
        "product": name,
        "directory": base.name,
        "ok": not errors and all(item["ok"] for item in checksums),
        "files": checksums,
        "errors": errors,
    }
    if report is not None:
        result["report"] = {
            "version": report.get("version"),
            "signed": report.get("signed"),
        }
    return result


def verify_extension(release: Path, version: str) -> dict:
    base = release / "browser-extension"
    errors: List[str] = []
    report = _read_report(base)
    checksums = _verify_checksums(base)
    by_name = {item["file"]: item for item in checksums}

    _check(report.get("validated") is True, "La extensión no está validada.", errors)
    _check(
        report.get("version") == version,
        f"Versión de extensión {report.get('version')!r} != {version!r}.",
        errors,
    )
    targets = report.get("targets")
    _check(isinstance(targets, dict), "El informe no contiene targets.", errors)
    if isinstance(targets, dict):
        for browser in ("chrome", "edge"):
            target = targets.get(browser)
            if not isinstance(target, dict):
                errors.append(f"Falta el target {browser} en el informe.")
                continue
            filename = str(target.get("file", ""))
            try:
                filename = _safe_relative(filename)
            except ValueError as exc:
                errors.append(str(exc))
                continue
            item = by_name.get(filename)
            _check(item is not None, f"{filename} no está en SHA256SUMS.", errors)
            if item is not None:
                _check(
                    item["sha256"] == target.get("sha256"),
                    f"El informe no coincide con el hash real de {filename}.",
                    errors,
                )
                _check(
                    item["bytes"] == target.get("bytes"),
                    f"El tamaño del informe no coincide con {filename}.",
                    errors,
                )
    for item in checksums:
        _check(bool(item["ok"]), f"Checksum incorrecto: {item['file']}.", errors)
    return _product_result("UNnexor browser extension", base, checksums, errors, report)


def verify_windows(release: Path, version: str) -> dict:
    base = release / "windows"
    errors: List[str] = []
    report = _read_report(base)
    checksums = _verify_checksums(base)
    by_name = {item["file"]: item for item in checksums}

    _check(report.get("version") == version, "Versión del EXE incorrecta.", errors)
    _check(
        report.get("signed") is False, "El informe no declara build sin firma.", errors
    )
    smoke = report.get("smoke") or {}
    gui = report.get("gui_smoke") or {}
    _check(smoke.get("ok") is True, "El smoke test headless no pasó.", errors)
    _check(gui.get("ok") is True, "El smoke test GUI no pasó.", errors)

    for key, hash_key in (
        ("archive", "archive_sha256"),
        ("exe", "exe_sha256"),
    ):
        filename = str(report.get(key, ""))
        try:
            filename = _safe_relative(filename)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        item = by_name.get(filename)
        _check(item is not None, f"{filename} no está en SHA256SUMS.", errors)
        if item is not None:
            _check(
                item["sha256"] == report.get(hash_key),
                f"El informe no coincide con el hash real de {filename}.",
                errors,
            )
    for item in checksums:
        _check(bool(item["ok"]), f"Checksum incorrecto: {item['file']}.", errors)
    return _product_result("UNnexor desktop", base, checksums, errors, report)


def verify_installer(release: Path, version: str) -> dict:
    base = release / "installer"
    errors: List[str] = []
    report = _read_report(base)
    checksums = _verify_checksums(base)
    by_name = {item["file"]: item for item in checksums}

    _check(
        report.get("version") == version, "Versión del instalador incorrecta.", errors
    )
    _check(
        report.get("desktop_version") == version,
        "El instalador no corresponde a la versión del escritorio.",
        errors,
    )
    _check(
        report.get("signed") is False,
        "El informe no declara instalador sin firma.",
        errors,
    )
    test = report.get("test") or {}
    for flag in (
        "installed",
        "data_preserved_on_uninstall",
        "token_preserved_on_uninstall",
        "purge_secrets_ok",
        "full_purge_removed_data",
        "install_dir_removed",
    ):
        _check(
            test.get(flag) is True,
            f"Prueba del instalador no superada: {flag}.",
            errors,
        )
    self_check = test.get("self_check") or {}
    _check(self_check.get("ok") is True, "Self-check del instalador no pasó.", errors)

    filename = str(report.get("setup", ""))
    try:
        filename = _safe_relative(filename)
    except ValueError as exc:
        errors.append(str(exc))
        filename = ""
    if filename:
        item = by_name.get(filename)
        _check(item is not None, f"{filename} no está en SHA256SUMS.", errors)
        if item is not None:
            _check(
                item["sha256"] == report.get("setup_sha256"),
                f"El informe no coincide con el hash real de {filename}.",
                errors,
            )
    for item in checksums:
        _check(bool(item["ok"]), f"Checksum incorrecto: {item['file']}.", errors)
    return _product_result("UNnexor installer", base, checksums, errors, report)


def verify_sbom(release: Path, version: str) -> dict:
    base = release / "sbom"
    errors: List[str] = []
    checksums = _verify_checksums(base)
    expected = f"unnexor-{version}.cdx.json"
    by_name = {item["file"]: item for item in checksums}
    _check(expected in by_name, f"Falta {expected} en SHA256SUMS.", errors)
    sbom_file = base / expected
    sbom: dict = {}
    if sbom_file.is_file():
        sbom = json.loads(sbom_file.read_text(encoding="utf-8"))
        _check(sbom.get("bomFormat") == "CycloneDX", "El SBOM no es CycloneDX.", errors)
        _check(
            sbom.get("specVersion") == "1.5", "El SBOM no usa specVersion 1.5.", errors
        )
        component = (sbom.get("metadata") or {}).get("component") or {}
        _check(
            component.get("name") == "UNnexor",
            "El SBOM no identifica a UNnexor.",
            errors,
        )
        _check(
            component.get("version") == version,
            "La versión del SBOM no coincide.",
            errors,
        )
        properties = {
            item.get("name"): item.get("value")
            for item in component.get("properties") or []
            if isinstance(item, dict)
        }
        declared = [
            (item.get("license") or {}).get("id")
            for item in component.get("licenses") or []
            if isinstance(item, dict)
        ]
        _check(
            properties.get("unnexor:license-status") == "Apache-2.0"
            and declared == ["Apache-2.0"],
            "El SBOM no declara la licencia del proyecto (Apache-2.0).",
            errors,
        )
        _check(bool(sbom.get("components")), "El SBOM no contiene componentes.", errors)
    for item in checksums:
        _check(bool(item["ok"]), f"Checksum incorrecto: {item['file']}.", errors)
    return _product_result("UNnexor SBOM", base, checksums, errors, sbom)


def verify_release(release_dir: Path) -> dict:
    version = app_version()
    products: List[dict] = []
    verifiers = (
        ("UNnexor browser extension", verify_extension),
        ("UNnexor desktop", verify_windows),
        ("UNnexor installer", verify_installer),
        ("UNnexor SBOM", verify_sbom),
    )
    for name, verifier in verifiers:
        try:
            products.append(verifier(release_dir, version))
        except Exception as exc:
            products.append(
                {
                    "product": name,
                    "ok": False,
                    "files": [],
                    "errors": [str(exc)],
                }
            )
    ok = all(product["ok"] for product in products)
    report = {
        "product": "UNnexor artifact verification",
        "version": version,
        "ok": ok,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "release_dir": release_dir.name,
        "products": products,
        "notes": [
            "Los hashes se calculan sobre los archivos locales; no se publica nada.",
            "Un build sin firma Authenticode se considera válido solo si el informe lo declara.",
        ],
    }
    report_file = release_dir / "verification-report.json"
    release_dir.mkdir(parents=True, exist_ok=True)
    report_file.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return report


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--release-dir",
        type=Path,
        default=DEFAULT_RELEASE,
        help=f"directorio release (por defecto: {DEFAULT_RELEASE})",
    )
    args = parser.parse_args(argv)
    try:
        report = verify_release(args.release_dir)
    except Exception as exc:
        print(f"Verificación fallida: {exc}", file=sys.stderr)
        return 1

    for product in report["products"]:
        status = "OK" if product["ok"] else "FALLO"
        print(f"[{status}] {product['product']}")
        for error in product["errors"]:
            print(f"  - {error}")
    if report["ok"]:
        print(
            f"Verificación completa en {args.release_dir / 'verification-report.json'}"
        )
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

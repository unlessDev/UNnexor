"""Genera un SBOM CycloneDX 1.5 del entorno Python de Extdator.

Uso:
    python deployment/sbom/build.py
    python deployment/sbom/build.py --out release/sbom
    python deployment/sbom/build.py --timestamp 2026-11-21T00:00:00+00:00

El SBOM describe el producto, la extensión, el puente local y los paquetes
Python instalados en el entorno que ejecuta este script (runtime y build).
No incluye rutas locales, claves, modelos ni datos de usuario.

Salida en ``release/sbom/`` (ignorado por Git):
  * extdator-<versión>.cdx.json
  * SHA256SUMS.txt

La licencia principal del proyecto está pendiente de decisión; el SBOM la
marca explícitamente como ``pending-owner-decision`` y no inventa una.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import sys
import uuid
from datetime import datetime, timezone
from importlib.metadata import distributions
from pathlib import Path
from typing import Dict, Iterable, List, Optional
from urllib.parse import quote

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCAL_BRIDGE = PROJECT_ROOT / "local-bridge"
APP_INIT = LOCAL_BRIDGE / "app" / "__init__.py"
REQUIREMENTS = LOCAL_BRIDGE / "requirements.txt"
BUILD_REQUIREMENTS = LOCAL_BRIDGE / "requirements-build.txt"
DEFAULT_OUT = PROJECT_ROOT / "release" / "sbom"
REPO_URL = "https://github.com/unlessDev/Extdator"
TOOL_VERSION = "1.0"


def app_version() -> str:
    text = APP_INIT.read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', text)
    if not match:
        raise ValueError("No se pudo leer __version__ de app/__init__.py")
    return match.group(1)


def _canonical_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _purl(name: str, version: str) -> str:
    return f"pkg:pypi/{_canonical_name(name)}@{quote(version, safe='._-+')}"


def _license_names(metadata) -> List[str]:
    """Extrae licencias declaradas sin inventar valores ausentes."""
    values: List[str] = []
    expression = (metadata.get("License-Expression") or "").strip()
    if expression and expression.upper() not in {"UNKNOWN", "NOASSERTION", "NONE"}:
        values.append(expression)

    license_field = (metadata.get("License") or "").strip()
    if (
        license_field
        and license_field.upper() not in {"UNKNOWN", "NOASSERTION", "NONE"}
        and len(license_field) <= 120
        and "\n" not in license_field
    ):
        values.append(license_field)

    for classifier in metadata.get_all("Classifier") or []:
        if not classifier.startswith("License ::"):
            continue
        label = classifier.rsplit("::", 1)[-1].strip()
        if label and label != "OSI Approved":
            values.append(label)

    unique: List[str] = []
    for value in values:
        if value not in unique:
            unique.append(value)
    return unique


def _homepage(metadata) -> str:
    homepage = (metadata.get("Home-page") or "").strip()
    if homepage:
        return homepage
    for project_url in metadata.get_all("Project-URL") or []:
        if "," in project_url:
            label, url = project_url.split(",", 1)
            if label.strip().lower() in {"homepage", "source", "repository"}:
                return url.strip()
    return ""


def _direct_requirements(path: Path) -> List[str]:
    if not path.is_file():
        return []
    names: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)", line)
        if match:
            names.append(_canonical_name(match.group(1)))
    return sorted(set(names))


def _installed_distributions() -> Dict[str, object]:
    result: Dict[str, object] = {}
    for dist in distributions():
        metadata = dist.metadata
        name = (metadata.get("Name") or "").strip()
        version = (dist.version or "").strip()
        if not name or not version:
            continue
        key = _canonical_name(name)
        # El mismo paquete puede aparecer en varias rutas; conserva el primero.
        result.setdefault(key, dist)
    return result


def _library_component(dist) -> Dict[str, object]:
    metadata = dist.metadata
    name = metadata["Name"]
    version = dist.version
    purl = _purl(name, version)
    component: Dict[str, object] = {
        "type": "library",
        "bom-ref": purl,
        "name": name,
        "version": version,
        "purl": purl,
        "properties": [
            {"name": "extdator:package-manager", "value": "pip"},
            {"name": "extdator:environment", "value": "build-and-runtime"},
        ],
    }
    licenses = _license_names(metadata)
    if licenses:
        component["licenses"] = [
            {"license": {"name": license_name}} for license_name in licenses
        ]
    homepage = _homepage(metadata)
    if homepage:
        component["externalReferences"] = [{"type": "website", "url": homepage}]
    return component


def _product_component(
    bom_ref: str,
    name: str,
    version: str,
    description: str,
) -> Dict[str, object]:
    return {
        "type": "application",
        "bom-ref": bom_ref,
        "name": name,
        "version": version,
        "description": description,
    }


def _dependency_refs(
    direct_names: Iterable[str], installed: Dict[str, object]
) -> List[str]:
    refs: List[str] = []
    for name in direct_names:
        dist = installed.get(name)
        if dist is not None:
            refs.append(_purl(dist.metadata["Name"], dist.version))
    return sorted(set(refs))


def build(
    out_dir: Path = DEFAULT_OUT, timestamp: Optional[str] = None
) -> Dict[str, object]:
    version = app_version()
    installed = _installed_distributions()
    libraries = [_library_component(installed[name]) for name in sorted(installed)]

    extension_ref = f"extdator-browser-extension@{version}"
    bridge_ref = f"extdator-local-bridge@{version}"
    build_ref = f"extdator-build-environment@{version}"
    root_ref = f"extdator@{version}"

    runtime_direct = _direct_requirements(REQUIREMENTS)
    build_direct = _direct_requirements(BUILD_REQUIREMENTS)

    components: List[Dict[str, object]] = [
        _product_component(
            extension_ref,
            "Extdator browser extension",
            version,
            "Extensión Manifest V3 para Chrome, Edge y navegadores Chromium.",
        ),
        _product_component(
            bridge_ref,
            "Extdator local bridge",
            version,
            "Puente HTTP local, panel flotante Qt y cliente de Ollama.",
        ),
        _product_component(
            build_ref,
            "Extdator build environment",
            version,
            "Entorno Python usado para empaquetar y verificar los artefactos.",
        ),
    ]
    components.extend(libraries)

    bom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "timestamp": timestamp
            or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "tools": [
                {
                    "vendor": "Extdator",
                    "name": "deployment/sbom/build.py",
                    "version": TOOL_VERSION,
                }
            ],
            "component": {
                "type": "application",
                "bom-ref": root_ref,
                "name": "Extdator",
                "version": version,
                "description": (
                    "Asistente local para el navegador: extensión MV3, puente "
                    "HTTP, panel flotante y modelos en Ollama."
                ),
                "properties": [
                    {
                        "name": "extdator:license-status",
                        "value": "pending-owner-decision",
                    },
                    {
                        "name": "extdator:distribution-status",
                        "value": "not-published",
                    },
                    {"name": "extdator:signing", "value": "unsigned"},
                    {
                        "name": "extdator:python-version",
                        "value": platform.python_version(),
                    },
                    {
                        "name": "extdator:sbom-scope",
                        "value": "build-and-runtime-python-environment",
                    },
                ],
            },
            "properties": [
                {
                    "name": "extdator:repository",
                    "value": REPO_URL,
                },
                {
                    "name": "extdator:license-status",
                    "value": "pending-owner-decision",
                },
            ],
        },
        "components": components,
        "dependencies": [
            {
                "ref": root_ref,
                "dependsOn": [extension_ref, bridge_ref, build_ref],
            },
            {
                "ref": bridge_ref,
                "dependsOn": _dependency_refs(runtime_direct, installed),
            },
            {
                "ref": build_ref,
                "dependsOn": _dependency_refs(build_direct, installed),
            },
        ],
    }

    digest_source = json.dumps(components, sort_keys=True, ensure_ascii=True)
    serial_seed = f"{REPO_URL}/sbom/{version}/{hashlib.sha256(digest_source.encode()).hexdigest()}"
    bom["serialNumber"] = f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, serial_seed)}"

    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"extdator-{version}.cdx.json"
    text = json.dumps(bom, indent=2, ensure_ascii=False) + "\n"
    # write_bytes evita que Windows convierta LF en CRLF después de calcular
    # el hash; el checksum debe corresponder a los bytes reales del archivo.
    target.write_bytes(text.encode("utf-8"))
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    (out_dir / "SHA256SUMS.txt").write_text(
        f"{digest}  {target.name}\n", encoding="utf-8"
    )
    return {
        "file": target,
        "sha256": digest,
        "bytes": target.stat().st_size,
        "component_count": len(components),
        "runtime_direct": runtime_direct,
        "build_direct": build_direct,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"directorio de salida (por defecto: {DEFAULT_OUT})",
    )
    parser.add_argument(
        "--timestamp",
        default=None,
        help="timestamp ISO-8601 fijo para builds reproducibles",
    )
    args = parser.parse_args(argv)
    try:
        report = build(args.out, args.timestamp)
    except Exception as exc:
        print(f"SBOM fallido: {exc}", file=sys.stderr)
        return 1

    print(f"SBOM CycloneDX generado en {args.out}")
    print(f"  {report['file'].name}: {report['bytes']} bytes")
    print(f"  Componentes: {report['component_count']}")
    print(f"  SHA-256: {report['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

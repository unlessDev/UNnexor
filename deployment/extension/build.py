"""Valida y empaqueta la extensión Manifest V3 para Chrome y Edge.

Uso:
    python deployment/extension/build.py --check
    python deployment/extension/build.py
    python deployment/extension/build.py --out release/browser-extension

El paquete se construye desde una lista blanca de archivos de producción:
nada de `.env`, logs, capturas, entornos ni artefactos de desarrollo.
Los ZIP son reproducibles (fechas y orden fijos) y se acompañan de
`SHA256SUMS.txt` y `build-report.json`.

La validación es estática y no sustituye la carga real en cada navegador.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import struct
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = PROJECT_ROOT / "browser-extension"
DEFAULT_OUT = PROJECT_ROOT / "release" / "browser-extension"
APP_INIT = PROJECT_ROOT / "local-bridge" / "app" / "__init__.py"

PRODUCTION_FILES = (
    "manifest.json",
    "background.js",
    "content.js",
    "options.html",
    "options.js",
    "privacy.html",
)
ASSET_DIR = "assets"
ALLOWED_PERMISSIONS = {"storage"}
ALLOWED_HOST_PERMISSIONS = {
    "http://127.0.0.1/*",
    "http://localhost/*",
}
EXPECTED_ICON_SIZES = {16, 32, 48, 128}
VERSION_RE = re.compile(r"^\d{1,5}(\.\d{1,5}){0,3}$")
REMOTE_SCRIPT_RE = re.compile(r"<script[^>]+src\s*=\s*[\"']https?://", re.IGNORECASE)
REMOTE_STYLESHEET_RE = re.compile(
    r"<link[^>]+href\s*=\s*[\"']https?://[^\"']*[\"'][^>]*"
    r"rel\s*=\s*[\"']stylesheet|"
    r"<link[^>]+rel\s*=\s*[\"']stylesheet[\"'][^>]*"
    r"href\s*=\s*[\"']https?://",
    re.IGNORECASE,
)
FORBIDDEN_EXTENSIONS = {".env", ".pem", ".key", ".pfx", ".p12", ".map"}


def _read_manifest(root: Path) -> dict:
    return json.loads((root / "manifest.json").read_text(encoding="utf-8"))


def _app_version() -> Optional[str]:
    if not APP_INIT.exists():
        return None
    match = re.search(
        r'__version__\s*=\s*["\']([^"\']+)["\']',
        APP_INIT.read_text(encoding="utf-8"),
    )
    return match.group(1) if match else None


def _png_size(path: Path) -> Optional[Tuple[int, int]]:
    """Lee ancho/alto de un PNG sin dependencias externas."""
    try:
        data = path.read_bytes()[:24]
    except OSError:
        return None
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def _iter_js(root: Path) -> Iterable[Path]:
    return sorted(root.glob("*.js"))


def _node_syntax_check(root: Path) -> Tuple[List[str], bool]:
    """Ejecuta `node --check` si Node está disponible.

    Devuelve (errores, omitido). La ausencia de Node no invalida el paquete:
    la comprobación de sintaxis es complementaria.
    """
    node = shutil.which("node")
    if not node:
        return [], True
    problems: List[str] = []
    for js_file in _iter_js(root):
        result = subprocess.run(
            [node, "--check", str(js_file)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().splitlines()
            problems.append(
                f"{js_file.name}: sintaxis inválida: "
                + (detail[0] if detail else "error desconocido")
            )
    return problems, False


def _check_referenced_files(root: Path, manifest: dict, problems: List[str]) -> None:
    referenced: List[str] = []

    service_worker = manifest.get("background", {}).get("service_worker")
    if service_worker:
        referenced.append(service_worker)

    options_page = manifest.get("options_page")
    if options_page:
        referenced.append(options_page)
    options_ui = manifest.get("options_ui", {}).get("page")
    if options_ui:
        referenced.append(options_ui)

    for entry in manifest.get("content_scripts", []):
        referenced.extend(entry.get("js", []))
        referenced.extend(entry.get("css", []))

    for icon in manifest.get("icons", {}).values():
        referenced.append(icon)
    for icon in manifest.get("action", {}).get("default_icon", {}).values():
        referenced.append(icon)

    for relative in referenced:
        if not (root / relative).is_file():
            problems.append(
                f"manifest.json referencia un archivo inexistente: {relative}"
            )


def validate(root: Path, *, check_app_version: bool = True) -> List[str]:
    """Devuelve la lista de problemas; vacía significa paquete válido."""
    problems: List[str] = []
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        return [f"no existe {manifest_path.name} en {root}"]

    try:
        manifest = _read_manifest(root)
    except (OSError, json.JSONDecodeError) as exc:
        return [f"manifest.json no es JSON válido: {exc}"]

    if manifest.get("manifest_version") != 3:
        problems.append("manifest_version debe ser 3 (Manifest V3)")
    if not str(manifest.get("name", "")).strip():
        problems.append("name es obligatorio")
    version = str(manifest.get("version", ""))
    if not VERSION_RE.match(version):
        problems.append(f"version no válida para la tienda: {version!r}")
    description = str(manifest.get("description", ""))
    if not 1 <= len(description) <= 132:
        problems.append(
            f"description debe tener entre 1 y 132 caracteres (tiene {len(description)})"
        )

    permissions = set(manifest.get("permissions", []))
    extra_permissions = permissions - ALLOWED_PERMISSIONS
    if extra_permissions:
        problems.append(
            "permisos no justificados: " + ", ".join(sorted(extra_permissions))
        )

    hosts = set(manifest.get("host_permissions", []))
    extra_hosts = hosts - ALLOWED_HOST_PERMISSIONS
    if extra_hosts:
        problems.append(
            "host_permissions fuera de loopback: " + ", ".join(sorted(extra_hosts))
        )
    if not hosts:
        problems.append("host_permissions debe incluir la aplicación local")

    content_scripts = manifest.get("content_scripts", [])
    if not content_scripts:
        problems.append("content_scripts no puede estar vacío")
    for entry in content_scripts:
        matches = entry.get("matches", [])
        if not matches:
            problems.append("content_scripts sin matches")
        for pattern in matches:
            if not pattern.startswith(("http://", "https://")):
                problems.append(f"match no admitido en la tienda: {pattern}")
        if not entry.get("js"):
            problems.append("content_scripts sin archivos js")

    csp = manifest.get("content_security_policy", {})
    policy = str(csp.get("extension_pages", ""))
    if "script-src 'self'" not in policy:
        problems.append("CSP extension_pages debe declarar script-src 'self'")
    if "unsafe-eval" in policy or "unsafe-inline" in policy:
        problems.append("CSP no puede permitir unsafe-eval ni unsafe-inline")

    _check_referenced_files(root, manifest, problems)

    icons = manifest.get("icons", {})
    missing_sizes = EXPECTED_ICON_SIZES - {int(key) for key in icons}
    if missing_sizes:
        problems.append(
            "faltan iconos para tamaños: "
            + ", ".join(str(size) for size in sorted(missing_sizes))
        )
    for key, relative in icons.items():
        size = _png_size(root / relative)
        if size is None:
            problems.append(f"icono no es PNG válido: {relative}")
        elif size != (int(key), int(key)):
            problems.append(
                f"icono {relative} mide {size[0]}x{size[1]}, se esperaba {key}x{key}"
            )

    for html_file in sorted(root.glob("*.html")):
        source = html_file.read_text(encoding="utf-8", errors="replace")
        if REMOTE_SCRIPT_RE.search(source):
            problems.append(f"{html_file.name}: script remoto prohibido")
        if REMOTE_STYLESHEET_RE.search(source):
            problems.append(f"{html_file.name}: hoja de estilos remota prohibida")

    for js_file in _iter_js(root):
        source = js_file.read_text(encoding="utf-8", errors="replace")
        if "eval(" in source or "new Function(" in source:
            problems.append(f"{js_file.name}: eval/new Function prohibidos en MV3")
        if "http://" in source:
            for match in re.findall(r"https?://[^\s\"'`]+", source):
                if not match.startswith(
                    ("http://127.0.0.1", "http://localhost", "https://127.0.0.1")
                ):
                    problems.append(
                        f"{js_file.name}: URL remota embebida no permitida: {match}"
                    )
                    break

    privacy = root / "privacy.html"
    if not privacy.is_file():
        problems.append("falta privacy.html (requerido por las tiendas)")
    else:
        privacy_text = privacy.read_text(encoding="utf-8", errors="replace")
        if "Política de privacidad" not in privacy_text:
            problems.append("privacy.html no incluye el título de la política")

    if check_app_version:
        app_version = _app_version()
        if app_version and app_version != version:
            problems.append(
                f"version del manifest ({version}) no coincide con la app "
                f"({app_version})"
            )

    syntax_problems, _ = _node_syntax_check(root)
    problems.extend(syntax_problems)
    return problems


def production_files(root: Path) -> List[Path]:
    """Lista blanca de archivos que entran en el paquete de tienda."""
    files: List[Path] = []
    for name in PRODUCTION_FILES:
        path = root / name
        if not path.is_file():
            raise FileNotFoundError(f"archivo de producción ausente: {name}")
        files.append(path)
    for path in sorted((root / ASSET_DIR).glob("*.png")):
        files.append(path)
    for path in files:
        if path.suffix.lower() in FORBIDDEN_EXTENSIONS:
            raise ValueError(f"archivo prohibido en el paquete: {path.name}")
    return files


def _zip_entry(name: str, data: bytes) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info


def _write_zip(target: Path, files_with_names: List[Tuple[str, bytes]]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as zf:
        for name, data in sorted(files_with_names):
            zf.writestr(_zip_entry(name, data), data)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build(
    root: Path, out_dir: Path, *, check_app_version: bool = True
) -> Dict[str, object]:
    problems = validate(root, check_app_version=check_app_version)
    if problems:
        raise ValueError(
            "La extensión no supera la validación:\n- " + "\n- ".join(problems)
        )

    manifest = _read_manifest(root)
    files = production_files(root)
    relative_files = [path.relative_to(root).as_posix() for path in files]
    payload = [(path.relative_to(root).as_posix(), path.read_bytes()) for path in files]

    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    targets: Dict[str, object] = {}
    for browser in ("chrome", "edge"):
        folder = out_dir / browser
        for relative, data in payload:
            destination = folder / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        archive = out_dir / f"{browser}.zip"
        _write_zip(archive, payload)
        targets[browser] = {
            "file": archive.name,
            "sha256": _sha256(archive),
            "bytes": archive.stat().st_size,
            "entries": relative_files,
        }

    checksums = out_dir / "SHA256SUMS.txt"
    lines = [
        f"{targets[browser]['sha256']}  {targets[browser]['file']}"
        for browser in ("chrome", "edge")
    ]
    checksums.write_text("\n".join(lines) + "\n", encoding="utf-8")

    report = {
        "product": "Extdator browser extension",
        "version": manifest.get("version"),
        "validated": True,
        "targets": targets,
        "files": relative_files,
        "notes": [
            "Paquetes sin firmar: la firma y distribución las realiza cada tienda.",
            "No se incluyen modelos, claves, logs ni datos de usuario.",
            "La política de privacidad empaquetada debe publicarse en una URL pública.",
        ],
    }
    (out_dir / "build-report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return report


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="solo valida el manifest y los recursos, no genera paquetes",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"directorio de salida (por defecto: {DEFAULT_OUT})",
    )
    args = parser.parse_args(argv)

    if args.check:
        problems = validate(SOURCE_DIR)
        if problems:
            print("Extensión NO válida:")
            for problem in problems:
                print(f"  - {problem}")
            return 1
        syntax_problems, skipped = _node_syntax_check(SOURCE_DIR)
        note = " (node no disponible: sintaxis JS no comprobada)" if skipped else ""
        print(f"Extensión válida: manifest MV3 y recursos coherentes{note}.")
        return 0

    try:
        report = build(SOURCE_DIR, args.out)
    except (ValueError, FileNotFoundError) as exc:
        print(exc, file=sys.stderr)
        return 1

    print(f"Paquetes generados en {args.out}")
    for browser in ("chrome", "edge"):
        target = report["targets"][browser]
        print(f"  {browser}.zip  {target['bytes']} bytes  sha256={target['sha256']}")
    print(f"  checksums: {args.out / 'SHA256SUMS.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

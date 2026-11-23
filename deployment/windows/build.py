"""Genera el ejecutable one-folder de Extdator y lo valida en frío.

Uso:
    python deployment/windows/build.py
    python deployment/windows/build.py --skip-smoke

Requiere PyInstaller instalado (ver local-bridge/requirements-build.txt).
El resultado se escribe en `release/windows/` (ignorado por Git):
  * Extdator/Extdator.exe y _internal/
  * Extdator-<version>-win64-unsigned.zip
  * SHA256SUMS.txt
  * build-report.json y build.log

La prueba de humo ejecuta el EXE con `--check` y un directorio de datos
temporal, y verifica que las rutas empaquetadas y el servidor funcionan.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCAL_BRIDGE = PROJECT_ROOT / "local-bridge"
SPEC_FILE = PROJECT_ROOT / "deployment" / "windows" / "Extdator.spec"
DIST_ROOT = PROJECT_ROOT / "release" / "windows"
WORK_ROOT = PROJECT_ROOT / "build" / "windows"
APP_INIT = LOCAL_BRIDGE / "app" / "__init__.py"


def app_version() -> str:
    import re

    text = APP_INIT.read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', text)
    if not match:
        raise ValueError("No se pudo leer __version__ de app/__init__.py")
    return match.group(1)


def _version_tuple(version: str) -> tuple[int, int, int, int]:
    parts = [int(part) for part in version.split(".")[:3]]
    while len(parts) < 3:
        parts.append(0)
    return (parts[0], parts[1], parts[2], 0)


def write_version_file(version: str, target: Path) -> Path:
    """Genera el recurso de versión de Windows sin duplicarlo en el repo."""
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )

    numbers = _version_tuple(version)
    info = VSVersionInfo(
        ffi=FixedFileInfo(
            filevers=numbers,
            prodvers=numbers,
            mask=0x3F,
            flags=0x0,
            OS=0x40004,
            fileType=0x1,
            subtype=0x0,
            date=(0, 0),
        ),
        kids=[
            StringFileInfo(
                [
                    StringTable(
                        "040904B0",
                        [
                            StringStruct("CompanyName", "Extdator"),
                            StringStruct(
                                "FileDescription",
                                "Extdator - asistente local para el navegador",
                            ),
                            StringStruct("FileVersion", version),
                            StringStruct("InternalName", "Extdator"),
                            StringStruct("OriginalFilename", "Extdator.exe"),
                            StringStruct("ProductName", "Extdator"),
                            StringStruct("ProductVersion", version),
                        ],
                    )
                ]
            ),
            VarFileInfo([VarStruct("Translation", [1033, 1200])]),
        ],
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(str(info), encoding="utf-8")
    return target


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _folder_size(folder: Path) -> int:
    return sum(path.stat().st_size for path in folder.rglob("*") if path.is_file())


def _zip_folder(folder: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(folder).as_posix())


def _bridge_log_tail(data_dir: Path, lines: int = 40) -> str:
    """Devuelve el final del log del puente para diagnósticos de build."""
    log_file = data_dir / "logs" / "bridge.log"
    if not log_file.is_file():
        return ""
    try:
        content = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(content[-lines:])


def _with_log(message: str, data_dir: Path) -> str:
    tail = _bridge_log_tail(data_dir)
    return f"{message}\nbridge.log:\n{tail}" if tail else message


def smoke_test(exe: Path, timeout: int = 180) -> Dict[str, object]:
    """Ejecuta el EXE con --check en un directorio de datos aislado."""
    with tempfile.TemporaryDirectory(prefix="extdator-smoke-") as tmp:
        data_dir = Path(tmp) / "data"
        env = os.environ.copy()
        env["EXTDATOR_DATA_DIR"] = str(data_dir)
        env["EXTDATOR_ENV_FILE"] = str(data_dir / ".env")
        port = find_free_port()
        started = time.monotonic()
        try:
            result = subprocess.run(
                [str(exe), "--check", "--port", str(port)],
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"La prueba de humo excedió {timeout}s sin terminar"
            ) from exc

        report_path = data_dir / "self-check.json"
        if not report_path.is_file():
            raise RuntimeError(
                _with_log(
                    "El EXE no escribió self-check.json. "
                    f"exit={result.returncode}; stderr={result.stderr[-800:]}",
                    data_dir,
                )
            )
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if not report.get("ok"):
            raise RuntimeError(_with_log(f"Auto-chequeo falló: {report}", data_dir))
        if report.get("frozen") is not True:
            raise RuntimeError("El EXE no detecta modo empaquetado (frozen)")
        if Path(report.get("data_dir", "")) != data_dir:
            raise RuntimeError(
                f"Rutas empaquetadas incorrectas: {report.get('data_dir')} != {data_dir}"
            )
        if report.get("env_file") != str(data_dir / ".env"):
            raise RuntimeError(
                f"El .env no se resolvió en datos: {report.get('env_file')}"
            )
        return {
            "ok": True,
            "exit_code": result.returncode,
            "seconds": round(time.monotonic() - started, 2),
            "port": port,
            "data_dir_isolated": True,
        }


def _health_ok(port: int) -> bool:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/health", timeout=1.5
        ) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def gui_smoke(exe: Path, timeout: int = 90) -> Dict[str, object]:
    """Arranca la GUI con la plataforma offscreen y comprueba que sigue viva.

    Detecta DLL/plugins Qt eliminados por error en la poda del paquete.
    """
    with tempfile.TemporaryDirectory(prefix="extdator-gui-") as tmp:
        data_dir = Path(tmp) / "data"
        env = os.environ.copy()
        env["EXTDATOR_DATA_DIR"] = str(data_dir)
        env["EXTDATOR_ENV_FILE"] = str(data_dir / ".env")
        env["QT_QPA_PLATFORM"] = "offscreen"
        port = find_free_port()
        started = time.monotonic()
        process = subprocess.Popen(
            [str(exe), "--port", str(port)],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + timeout
            healthy = False
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(
                        _with_log(
                            "La GUI terminó antes de estar lista "
                            f"(exit={process.returncode})",
                            data_dir,
                        )
                    )
                if _health_ok(port):
                    healthy = True
                    break
                time.sleep(0.4)
            if not healthy:
                raise RuntimeError(
                    _with_log(f"La GUI no respondió en {timeout}s", data_dir)
                )
            # El servidor arranca antes que QApplication: damos margen a Qt.
            time.sleep(1.5)
            if process.poll() is not None:
                raise RuntimeError(
                    _with_log(
                        "Qt falló al crear la ventana; el proceso terminó "
                        f"(exit={process.returncode})",
                        data_dir,
                    )
                )
            return {
                "ok": True,
                "seconds": round(time.monotonic() - started, 2),
                "port": port,
                "platform": "offscreen",
            }
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)


def _prune_runtime(app_dir: Path) -> Dict[str, object]:
    """Elimina módulos Qt no usados por una app solo-Widgets.

    Se ejecuta antes de las pruebas de humo; si la GUI necesitara alguno,
    el arranque offscreen fallaría y el build no se publicaría.
    """
    targets = [
        "_internal/PySide6/Qt6Qml.dll",
        "_internal/PySide6/Qt6QmlModels.dll",
        "_internal/PySide6/Qt6Quick.dll",
        "_internal/PySide6/Qt6Pdf.dll",
        "_internal/PySide6/Qt6Network.dll",
        "_internal/PySide6/Qt6OpenGL.dll",
        "_internal/PySide6/opengl32sw.dll",
        "_internal/PySide6/plugins/imageformats/qpdf.dll",
    ]
    directories = [
        "_internal/PySide6/translations",
    ]
    removed: list[str] = []
    freed = 0
    for relative in targets:
        path = app_dir / relative
        if path.is_file():
            freed += path.stat().st_size
            path.unlink()
            removed.append(relative)
    for relative in directories:
        path = app_dir / relative
        if path.is_dir():
            freed += _folder_size(path)
            shutil.rmtree(path)
            removed.append(relative + "/")
    return {"removed": removed, "freed_bytes": freed}


def build(*, skip_smoke: bool = False) -> Dict[str, object]:
    version = app_version()
    if not SPEC_FILE.is_file():
        raise FileNotFoundError(f"No existe el spec: {SPEC_FILE}")

    if DIST_ROOT.exists():
        shutil.rmtree(DIST_ROOT)
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    version_file = write_version_file(version, WORK_ROOT / "version_info.txt")

    env = os.environ.copy()
    env["EXTDATOR_VERSION_FILE"] = str(version_file)
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(DIST_ROOT),
        "--workpath",
        str(WORK_ROOT / "pyinstaller"),
        str(SPEC_FILE),
    ]
    started = time.monotonic()
    result = subprocess.run(
        command,
        cwd=str(PROJECT_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1800,
    )
    (DIST_ROOT / "build.log").parent.mkdir(parents=True, exist_ok=True)
    (DIST_ROOT / "build.log").write_text(
        (result.stdout or "") + "\n" + (result.stderr or ""), encoding="utf-8"
    )
    if result.returncode != 0:
        tail = "\n".join((result.stderr or result.stdout or "").splitlines()[-40:])
        raise RuntimeError(f"PyInstaller falló (exit={result.returncode}):\n{tail}")

    app_dir = DIST_ROOT / "Extdator"
    exe = app_dir / "Extdator.exe"
    if not exe.is_file():
        raise RuntimeError(f"PyInstaller no generó {exe}")

    prune = _prune_runtime(app_dir)

    smoke = {"ok": False, "skipped": True}
    gui = {"ok": False, "skipped": True}
    if not skip_smoke:
        smoke = smoke_test(exe)
        gui = gui_smoke(exe)

    archive = DIST_ROOT / f"Extdator-{version}-win64-unsigned.zip"
    _zip_folder(app_dir, archive)
    exe_hash = _sha256(exe)
    zip_hash = _sha256(archive)
    (DIST_ROOT / "SHA256SUMS.txt").write_text(
        f"{zip_hash}  {archive.name}\n{exe_hash}  Extdator/Extdator.exe\n",
        encoding="utf-8",
    )

    report = {
        "product": "Extdator desktop",
        "version": version,
        "pyinstaller": package_version("pyinstaller"),
        "mode": "one-folder",
        "signed": False,
        "unsigned_notice": "Build sin Authenticode; SmartScreen puede advertir.",
        "exe": "Extdator/Extdator.exe",
        "exe_sha256": exe_hash,
        "exe_bytes": exe.stat().st_size,
        "folder_bytes": _folder_size(app_dir),
        "archive": archive.name,
        "archive_sha256": zip_hash,
        "archive_bytes": archive.stat().st_size,
        "smoke": smoke,
        "gui_smoke": gui,
        "prune": prune,
        "build_seconds": round(time.monotonic() - started, 2),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "notes": [
            "PySide6/Qt se enlaza dinámicamente (LGPL); ver THIRD_PARTY_NOTICES.md.",
            "No se incluyen modelos de Ollama, pesos OCR, claves ni datos de usuario.",
            "Ollama y los proveedores remotos son servicios externos opcionales.",
        ],
    }
    (DIST_ROOT / "build-report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return report


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-smoke",
        action="store_true",
        help="omite la ejecución del EXE con --check (no recomendado)",
    )
    args = parser.parse_args(argv)
    try:
        report = build(skip_smoke=args.skip_smoke)
    except Exception as exc:
        print(f"Build fallido: {exc}", file=sys.stderr)
        return 1

    print(f"Extdator {report['version']} empaquetado en {DIST_ROOT}")
    print(
        f"  EXE: {report['exe_bytes'] / 1024 / 1024:.1f} MB; "
        f"carpeta: {report['folder_bytes'] / 1024 / 1024:.1f} MB"
    )
    print(
        f"  ZIP: {report['archive']} ({report['archive_bytes'] / 1024 / 1024:.1f} MB)"
    )
    print(f"  SHA-256 ZIP: {report['archive_sha256']}")
    print(f"  Smoke test: {report['smoke']}")
    print(f"  GUI offscreen: {report['gui_smoke']}")
    print("  Build sin firma Authenticode.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

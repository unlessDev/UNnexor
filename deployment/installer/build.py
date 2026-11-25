"""Compila y prueba el instalador Windows de Extdator con Inno Setup 6.

Uso:
    python deployment/installer/build.py
    python deployment/installer/build.py --skip-test
    python deployment/installer/build.py --build-desktop

Requiere:
  * El ejecutable one-folder en ``release/windows/Extdator`` (o usar
    ``--build-desktop`` para generarlo antes).
  * Inno Setup 6 instalado (ISCC.exe). Se puede indicar la ruta con la
    variable de entorno ``ISCC``.

Salida en ``release/installer/`` (ignorado por Git):
  * Extdator-<versión>-win64-setup.exe
  * SHA256SUMS.txt
  * build-report.json y build.log

La prueba por defecto instala en un directorio temporal con /VERYSILENT,
ejecuta ``Extdator.exe --check`` con datos aislados, desinstala y comprueba
que los datos se conservan. Después reinstala y prueba ``--purge-secrets``
y la limpieza completa con ``/PURGEDATA``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCAL_BRIDGE = PROJECT_ROOT / "local-bridge"
INSTALLER_DIR = PROJECT_ROOT / "deployment" / "installer"
ISS_FILE = INSTALLER_DIR / "Extdator.iss"
DESKTOP_BUILD = PROJECT_ROOT / "deployment" / "windows" / "build.py"
DESKTOP_DIR = PROJECT_ROOT / "release" / "windows" / "Extdator"
DESKTOP_EXE = DESKTOP_DIR / "Extdator.exe"
DESKTOP_REPORT = PROJECT_ROOT / "release" / "windows" / "build-report.json"
OUT_DIR = PROJECT_ROOT / "release" / "installer"
APP_INIT = LOCAL_BRIDGE / "app" / "__init__.py"


def app_version() -> str:
    text = APP_INIT.read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', text)
    if not match:
        raise ValueError("No se pudo leer __version__ de app/__init__.py")
    return match.group(1)


def find_iscc() -> Path:
    candidates = []
    env_value = os.environ.get("ISCC", "").strip()
    if env_value:
        candidates.append(Path(env_value))
    found = shutil.which("ISCC")
    if found:
        candidates.append(Path(found))
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        candidates.append(
            Path(local_appdata) / "Programs" / "Inno Setup 6" / "ISCC.exe"
        )
    for var in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(var)
        if base:
            candidates.append(Path(base) / "Inno Setup 6" / "ISCC.exe")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "No se encontró ISCC.exe. Instala Inno Setup 6 o define la variable "
        "de entorno ISCC con la ruta al compilador."
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_until(predicate, timeout: float = 60.0, interval: float = 0.5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _run(
    command: list[str],
    *,
    env: Optional[dict] = None,
    timeout: float = 600.0,
    cwd: Optional[Path] = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _desktop_report(version: str) -> dict:
    if not DESKTOP_EXE.is_file():
        raise FileNotFoundError(
            f"No existe {DESKTOP_EXE}. Ejecuta primero "
            "deployment/windows/build.py o usa --build-desktop."
        )
    if not DESKTOP_REPORT.is_file():
        raise FileNotFoundError(
            f"No existe {DESKTOP_REPORT}; vuelve a generar el ejecutable."
        )
    report = json.loads(DESKTOP_REPORT.read_text(encoding="utf-8"))
    if report.get("version") != version:
        raise RuntimeError(
            "La versión del ejecutable no coincide con la de la aplicación: "
            f"{report.get('version')} != {version}. Reconstruye el escritorio."
        )
    if not report.get("smoke", {}).get("ok"):
        raise RuntimeError("El ejecutable de escritorio no superó su smoke test.")
    return report


def compile_installer(iscc: Path, version: str) -> tuple[Path, str, str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for stale in OUT_DIR.glob("Extdator-*-win64-setup.exe"):
        stale.unlink()
    command = [
        str(iscc),
        f"/DMyAppVersion={version}",
        f"/O{OUT_DIR}",
        f"/FExtdator-{version}-win64-setup",
        str(ISS_FILE),
    ]
    result = _run(command, timeout=1800, cwd=PROJECT_ROOT)
    log = (result.stdout or "") + "\n" + (result.stderr or "")
    if result.returncode != 0:
        tail = "\n".join(log.splitlines()[-40:])
        raise RuntimeError(f"ISCC falló (exit={result.returncode}):\n{tail}")

    setup = OUT_DIR / f"Extdator-{version}-win64-setup.exe"
    if not setup.is_file():
        raise RuntimeError(f"ISCC no generó {setup}")
    # El log incluye "Compiler engine version: Inno Setup 6.7.3"; la primera
    # línea solo muestra la versión mayor.
    match = re.search(r"Inno Setup (\d+\.\d+\.\d+)", log)
    compiler_version = match.group(1) if match else "desconocida"
    return setup, compiler_version, log


def _install(setup: Path, install_dir: Path, log_file: Path, timeout: float) -> None:
    if install_dir.exists():
        shutil.rmtree(install_dir)
    result = _run(
        [
            str(setup),
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/SP-",
            "/NOICONS",
            f"/DIR={install_dir}",
            f"/LOG={log_file}",
        ],
        timeout=timeout,
    )
    if result.returncode != 0:
        tail = "\n".join(
            ((result.stdout or "") + "\n" + (result.stderr or "")).splitlines()[-30:]
        )
        raise RuntimeError(f"La instalación falló (exit={result.returncode}):\n{tail}")
    if not _wait_until(lambda: (install_dir / "Extdator.exe").is_file(), 30):
        raise RuntimeError("El instalador no creó Extdator.exe.")
    if not _wait_until(lambda: (install_dir / "unins000.exe").is_file(), 30):
        raise RuntimeError("El instalador no creó el desinstalador.")


def _run_installed_check(exe: Path, data_dir: Path, timeout: float) -> dict:
    env = os.environ.copy()
    env["EXTDATOR_DATA_DIR"] = str(data_dir)
    env["EXTDATOR_ENV_FILE"] = str(data_dir / ".env")
    port = _free_port()
    started = time.monotonic()
    process = subprocess.Popen(
        [str(exe), "--check", "--port", str(port)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        raise RuntimeError(
            "El auto-chequeo del ejecutable instalado no terminó a tiempo. "
            f"stdout={stdout[-400:]!r} stderr={stderr[-400:]!r}"
        ) from None

    report_file = data_dir / "self-check.json"
    if not report_file.is_file():
        raise RuntimeError(
            "El ejecutable instalado no escribió self-check.json. "
            f"exit={process.returncode}; stderr={stderr[-800:]}"
        )
    report = json.loads(report_file.read_text(encoding="utf-8"))
    if not report.get("ok"):
        raise RuntimeError(f"Auto-chequeo fallido: {report}")
    if report.get("frozen") is not True:
        raise RuntimeError("El ejecutable instalado no detecta modo empaquetado.")
    if Path(report.get("data_dir", "")) != data_dir:
        raise RuntimeError(
            f"El ejecutable instalado no respetó EXTDATOR_DATA_DIR: "
            f"{report.get('data_dir')} != {data_dir}"
        )
    return {
        "ok": True,
        "exit_code": process.returncode,
        "seconds": round(time.monotonic() - started, 2),
        "port": port,
        "data_dir": str(data_dir),
        "report": report,
    }


def _uninstall(
    uninstaller: Path,
    install_dir: Path,
    data_dir: Path,
    log_file: Path,
    *,
    purge: bool,
    timeout: float,
) -> None:
    env = os.environ.copy()
    env["EXTDATOR_DATA_DIR"] = str(data_dir)
    command = [
        str(uninstaller),
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        f"/LOG={log_file}",
    ]
    if purge:
        command.append("/PURGEDATA")
    result = _run(command, env=env, timeout=timeout)
    if result.returncode != 0:
        tail = "\n".join(
            ((result.stdout or "") + "\n" + (result.stderr or "")).splitlines()[-30:]
        )
        raise RuntimeError(
            f"La desinstalación falló (exit={result.returncode}):\n{tail}"
        )
    # Un directorio vacío residual no invalida la desinstalación.
    if (
        not _wait_until(lambda: not install_dir.exists(), 60)
        and (install_dir / "Extdator.exe").exists()
    ):
        raise RuntimeError("La desinstalación no eliminó Extdator.exe.")
    # El desinstalador de Inno puede copiarse a %TEMP% y terminar en segundo
    # plano después de borrar el directorio. Damos margen para que cierre su
    # clave de registro y no compita con la siguiente instalación.
    time.sleep(2.0)


def _purge_secrets(exe: Path, data_dir: Path, timeout: float) -> None:
    env = os.environ.copy()
    env["EXTDATOR_DATA_DIR"] = str(data_dir)
    env["EXTDATOR_ENV_FILE"] = str(data_dir / ".env")
    process = subprocess.Popen(
        [str(exe), "--purge-secrets"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise RuntimeError("--purge-secrets no terminó a tiempo.") from None
    if process.returncode != 0:
        raise RuntimeError(
            f"--purge-secrets falló (exit={process.returncode}): " f"{stderr[-800:]}"
        )


def test_installer(
    setup: Path, version: str, timeout: float, keep: bool = False
) -> dict:
    root = Path(tempfile.mkdtemp(prefix="extdator-installer-test-"))
    install_dir = root / "app"
    data_dir = root / "data"
    result: dict = {}
    try:
        _install(setup, install_dir, root / "install.log", timeout)
        result["installed"] = True
        result["self_check"] = _run_installed_check(
            install_dir / "Extdator.exe", data_dir, timeout
        )

        # Datos de usuario que deben sobrevivir a una desinstalación normal.
        data_dir.mkdir(parents=True, exist_ok=True)
        marker = data_dir / "user_settings.json"
        marker.write_text('{"ollama_model": "prueba-conservada"}\n', encoding="utf-8")
        env_file = data_dir / ".env"
        with env_file.open("a", encoding="utf-8") as handle:
            handle.write("OPENAI_API_KEY=sk-solo-para-prueba\n")

        _uninstall(
            install_dir / "unins000.exe",
            install_dir,
            data_dir,
            root / "uninstall-preserve.log",
            purge=False,
            timeout=timeout,
        )
        result["data_preserved_on_uninstall"] = marker.is_file()
        result["token_preserved_on_uninstall"] = bool(
            re.search(
                r"^BRIDGE_TOKEN=.+$",
                env_file.read_text(encoding="utf-8"),
                re.MULTILINE,
            )
        )
        if not result["data_preserved_on_uninstall"]:
            raise RuntimeError("La desinstalación normal borró los datos del usuario.")
        if not result["token_preserved_on_uninstall"]:
            raise RuntimeError("La desinstalación normal alteró el .env del usuario.")

        # Segunda vuelta: purga explícita de secretos y limpieza completa.
        # Directorios nuevos para no competir con el desinstalador anterior.
        install_dir2 = root / "app2"
        data_dir2 = root / "data2"
        env_file2 = data_dir2 / ".env"
        _install(setup, install_dir2, root / "install-2.log", timeout)
        data_dir2.mkdir(parents=True, exist_ok=True)
        marker2 = data_dir2 / "user_settings.json"
        marker2.write_text('{"ollama_model": "se-borrará"}\n', encoding="utf-8")
        env_file2.write_text(
            "BRIDGE_TOKEN=token-de-prueba\n"
            "OLLAMA_MODEL=qwen2.5vl:7b\n"
            "OPENAI_API_KEY=sk-solo-para-prueba\n",
            encoding="utf-8",
        )
        (data_dir2 / "providers.json").write_text(
            json.dumps({"version": 1, "providers": {"custom": {}}}),
            encoding="utf-8",
        )

        _purge_secrets(install_dir2 / "Extdator.exe", data_dir2, timeout)
        env_after = env_file2.read_text(encoding="utf-8")
        providers_after = json.loads(
            (data_dir2 / "providers.json").read_text(encoding="utf-8")
        )
        result["purge_secrets_ok"] = (
            "BRIDGE_TOKEN=\n" in env_after
            and "OPENAI_API_KEY" not in env_after
            and "OLLAMA_MODEL=qwen2.5vl:7b" in env_after
            and providers_after.get("providers") == {}
        )
        if not result["purge_secrets_ok"]:
            raise RuntimeError("--purge-secrets no limpió el .env o providers.json.")
        if not marker2.is_file():
            raise RuntimeError("--purge-secrets borró ajustes que no son secretos.")

        _uninstall(
            install_dir2 / "unins000.exe",
            install_dir2,
            data_dir2,
            root / "uninstall-purge.log",
            purge=True,
            timeout=timeout,
        )
        result["full_purge_removed_data"] = _wait_until(
            lambda: not data_dir2.exists(), 30
        )
        if not result["full_purge_removed_data"]:
            raise RuntimeError(
                "La limpieza completa no eliminó el directorio de datos."
            )
        result["install_dir_removed"] = not (install_dir2 / "Extdator.exe").exists()
        return result
    finally:
        if keep:
            print(f"Directorio de prueba conservado: {root}")
        else:
            shutil.rmtree(root, ignore_errors=True)


def build(
    *,
    skip_test: bool = False,
    build_desktop: bool = False,
    keep_test: bool = False,
) -> dict:
    version = app_version()
    iscc = find_iscc()

    if build_desktop:
        result = _run(
            [sys.executable, str(DESKTOP_BUILD)],
            timeout=3600,
            cwd=PROJECT_ROOT,
        )
        if result.returncode != 0:
            tail = "\n".join(
                ((result.stdout or "") + "\n" + (result.stderr or "")).splitlines()[
                    -40:
                ]
            )
            raise RuntimeError(f"El build de escritorio falló:\n{tail}")
    desktop = _desktop_report(version)
    setup, compiler_version, compile_log = compile_installer(iscc, version)

    test: dict = {"skipped": True}
    if not skip_test:
        test = test_installer(setup, version, timeout=240, keep=keep_test)

    setup_hash = _sha256(setup)
    (OUT_DIR / "SHA256SUMS.txt").write_text(
        f"{setup_hash}  {setup.name}\n", encoding="utf-8"
    )
    report = {
        "product": "Extdator installer",
        "version": version,
        "inno_setup": compiler_version,
        "setup": setup.name,
        "setup_sha256": setup_hash,
        "setup_bytes": setup.stat().st_size,
        "signed": False,
        "unsigned_notice": ("Instalador sin Authenticode; SmartScreen puede advertir."),
        "install_scope": "per-user",
        "default_install_dir": "%LOCALAPPDATA%\\Programs\\Extdator",
        "data_dir": "%LOCALAPPDATA%\\Extdator",
        "desktop_version": desktop.get("version"),
        "test": test,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "notes": [
            "No incluye modelos de Ollama, pesos OCR, claves ni historiales.",
            "Los datos se conservan al desinstalar salvo limpieza completa.",
            "PrivilegesRequired=lowest: no solicita administrador.",
        ],
    }
    (OUT_DIR / "build-report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (OUT_DIR / "build.log").write_text(
        compile_log + "\n" + json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return report


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-test",
        action="store_true",
        help="compila sin instalar/desinstalar (no recomendado)",
    )
    parser.add_argument(
        "--build-desktop",
        action="store_true",
        help="genera también el ejecutable one-folder antes del instalador",
    )
    parser.add_argument(
        "--keep-test",
        action="store_true",
        help="conserva el directorio temporal de la prueba",
    )
    args = parser.parse_args(argv)
    try:
        report = build(
            skip_test=args.skip_test,
            build_desktop=args.build_desktop,
            keep_test=args.keep_test,
        )
    except Exception as exc:
        print(f"Build del instalador fallido: {exc}", file=sys.stderr)
        return 1

    print(f"Instalador {report['version']} en {OUT_DIR}")
    print(f"  {report['setup']}: " f"{report['setup_bytes'] / 1024 / 1024:.1f} MB")
    print(f"  SHA-256: {report['setup_sha256']}")
    print(f"  Inno Setup: {report['inno_setup']}")
    if report["test"].get("skipped"):
        print("  Prueba de instalación: omitida")
    else:
        print(
            "  Prueba: instalado, self-check OK, datos conservados, "
            "purga de secretos y limpieza completa OK"
        )
    print("  Instalador sin firma Authenticode.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

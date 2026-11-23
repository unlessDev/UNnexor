"""Pruebas del verificador de artefactos y de la generación de su informe."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
VERIFY_SCRIPT = PROJECT_ROOT / "deployment" / "verify_artifacts.py"

_spec = importlib.util.spec_from_file_location("verify_artifacts", VERIFY_SCRIPT)
verify_artifacts = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(verify_artifacts)

VERSION = verify_artifacts.app_version()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(path: Path, data: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return _sha256(data)


def _write_checksums(base: Path, files: dict[str, str]) -> None:
    lines = [f"{digest}  {name}" for name, digest in sorted(files.items())]
    (base / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_report(base: Path, data: dict) -> None:
    (base / "build-report.json").write_text(
        json.dumps(data, indent=2) + "\n", encoding="utf-8"
    )


def _extension(release: Path) -> None:
    base = release / "browser-extension"
    targets = {}
    files = {}
    for browser in ("chrome", "edge"):
        name = f"{browser}.zip"
        digest = _write(base / name, f"paquete-{browser}".encode())
        files[name] = digest
        targets[browser] = {
            "file": name,
            "sha256": digest,
            "bytes": len(f"paquete-{browser}".encode()),
        }
    _write_checksums(base, files)
    _write_report(
        base,
        {
            "product": "Extdator browser extension",
            "version": VERSION,
            "validated": True,
            "targets": targets,
        },
    )


def _windows(release: Path) -> None:
    base = release / "windows"
    exe_digest = _write(base / "Extdator" / "Extdator.exe", b"exe-sintetico")
    archive_name = f"Extdator-{VERSION}-win64-unsigned.zip"
    archive_digest = _write(base / archive_name, b"zip-sintetico")
    _write_checksums(
        base,
        {"Extdator/Extdator.exe": exe_digest, archive_name: archive_digest},
    )
    _write_report(
        base,
        {
            "product": "Extdator desktop",
            "version": VERSION,
            "signed": False,
            "exe": "Extdator/Extdator.exe",
            "exe_sha256": exe_digest,
            "archive": archive_name,
            "archive_sha256": archive_digest,
            "smoke": {"ok": True},
            "gui_smoke": {"ok": True},
        },
    )


def _installer(release: Path) -> None:
    base = release / "installer"
    name = f"Extdator-{VERSION}-win64-setup.exe"
    digest = _write(base / name, b"instalador-sintetico")
    _write_checksums(base, {name: digest})
    _write_report(
        base,
        {
            "product": "Extdator installer",
            "version": VERSION,
            "desktop_version": VERSION,
            "signed": False,
            "setup": name,
            "setup_sha256": digest,
            "test": {
                "installed": True,
                "self_check": {"ok": True},
                "data_preserved_on_uninstall": True,
                "token_preserved_on_uninstall": True,
                "purge_secrets_ok": True,
                "full_purge_removed_data": True,
                "install_dir_removed": True,
            },
        },
    )


def _sbom(release: Path) -> None:
    base = release / "sbom"
    data = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "name": "Extdator",
                "version": VERSION,
                "properties": [
                    {
                        "name": "extdator:license-status",
                        "value": "pending-owner-decision",
                    }
                ],
            }
        },
        "components": [{"type": "library", "name": "fastapi", "version": "0.115"}],
    }
    name = f"extdator-{VERSION}.cdx.json"
    digest = _write(base / name, json.dumps(data, indent=2).encode())
    _write_checksums(base, {name: digest})


@pytest.fixture()
def release(tmp_path: Path) -> Path:
    root = tmp_path / "release"
    _extension(root)
    _windows(root)
    _installer(root)
    _sbom(root)
    return root


def _product(report: dict, name: str) -> dict:
    return next(item for item in report["products"] if item["product"] == name)


def test_verification_passes_on_complete_release(release: Path) -> None:
    report = verify_artifacts.verify_release(release)

    assert report["ok"] is True
    assert len(report["products"]) == 4
    assert {item["product"] for item in report["products"]} == {
        "Extdator browser extension",
        "Extdator desktop",
        "Extdator installer",
        "Extdator SBOM",
    }
    written = json.loads(
        (release / "verification-report.json").read_text(encoding="utf-8")
    )
    assert written["ok"] is True


def test_tampered_artifact_fails(release: Path) -> None:
    (release / "browser-extension" / "chrome.zip").write_bytes(b"manipulado")

    report = verify_artifacts.verify_release(release)

    assert report["ok"] is False
    extension = _product(report, "Extdator browser extension")
    assert extension["ok"] is False
    assert any("Checksum incorrecto" in error for error in extension["errors"])


def test_missing_artifact_fails(release: Path) -> None:
    (release / "windows" / "Extdator" / "Extdator.exe").unlink()

    report = verify_artifacts.verify_release(release)

    assert report["ok"] is False
    desktop = _product(report, "Extdator desktop")
    assert any("Falta el artefacto" in error for error in desktop["errors"])


def test_unsafe_checksum_path_is_rejected(release: Path) -> None:
    checksum_file = release / "installer" / "SHA256SUMS.txt"
    checksum_file.write_text("0" * 64 + "  ../fuera.exe\n", encoding="utf-8")

    report = verify_artifacts.verify_release(release)

    assert report["ok"] is False
    installer = _product(report, "Extdator installer")
    assert any(
        "insegura" in error or "no relativa" in error for error in installer["errors"]
    )


def test_report_hash_mismatch_fails(release: Path) -> None:
    path = release / "browser-extension" / "build-report.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["targets"]["chrome"]["sha256"] = "0" * 64
    path.write_text(json.dumps(data), encoding="utf-8")

    report = verify_artifacts.verify_release(release)

    assert report["ok"] is False
    extension = _product(report, "Extdator browser extension")
    assert any("no coincide con el hash real" in error for error in extension["errors"])


def test_sbom_must_declare_pending_license(release: Path) -> None:
    base = release / "sbom"
    name = f"extdator-{VERSION}.cdx.json"
    path = base / name
    data = json.loads(path.read_text(encoding="utf-8"))
    data["metadata"]["component"]["properties"][0]["value"] = "MIT"
    path.write_text(json.dumps(data), encoding="utf-8")
    digest = _sha256(path.read_bytes())
    _write_checksums(base, {name: digest})

    report = verify_artifacts.verify_release(release)

    assert report["ok"] is False
    sbom = _product(report, "Extdator SBOM")
    assert any("licencia pendiente" in error for error in sbom["errors"])


def test_missing_build_report_fails(release: Path) -> None:
    (release / "windows" / "build-report.json").unlink()

    report = verify_artifacts.verify_release(release)

    assert report["ok"] is False
    desktop = _product(report, "Extdator desktop")
    assert any("build-report.json" in error for error in desktop["errors"])

"""Pruebas del empaquetado y la validación de la extensión de navegador."""

from __future__ import annotations

import importlib.util
import json
import shutil
import zipfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE = PROJECT_ROOT / "browser-extension"
BUILD_SCRIPT = PROJECT_ROOT / "deployment" / "extension" / "build.py"

_spec = importlib.util.spec_from_file_location("extension_build", BUILD_SCRIPT)
extension_build = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(extension_build)


@pytest.fixture()
def extension_copy(tmp_path: Path) -> Path:
    destination = tmp_path / "browser-extension"
    shutil.copytree(SOURCE, destination)
    return destination


def _manifest(path: Path) -> dict:
    return json.loads((path / "manifest.json").read_text(encoding="utf-8"))


def _write_manifest(path: Path, data: dict) -> None:
    (path / "manifest.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def test_extension_passes_validation() -> None:
    assert extension_build.validate(SOURCE, check_app_version=False) == []


def test_manifest_permissions_are_minimal() -> None:
    manifest = _manifest(SOURCE)
    assert manifest["permissions"] == ["storage"]
    assert set(manifest["host_permissions"]) == {
        "http://127.0.0.1/*",
        "http://localhost/*",
    }
    assert manifest["manifest_version"] == 3
    assert manifest["content_security_policy"]["extension_pages"] == (
        "script-src 'self'; object-src 'self'"
    )


def test_privacy_page_exists_and_is_linked() -> None:
    assert (SOURCE / "privacy.html").is_file()
    options = (SOURCE / "options.html").read_text(encoding="utf-8")
    assert "privacy.html" in options
    files = [
        path.relative_to(SOURCE).as_posix()
        for path in extension_build.production_files(SOURCE)
    ]
    assert "privacy.html" in files


def test_validator_rejects_broad_host_permissions(extension_copy: Path) -> None:
    manifest = _manifest(extension_copy)
    manifest["host_permissions"].append("https://*/*")
    _write_manifest(extension_copy, manifest)
    problems = extension_build.validate(extension_copy, check_app_version=False)
    assert any("host_permissions fuera de loopback" in problem for problem in problems)


def test_validator_rejects_extra_permissions(extension_copy: Path) -> None:
    manifest = _manifest(extension_copy)
    manifest["permissions"].append("scripting")
    _write_manifest(extension_copy, manifest)
    problems = extension_build.validate(extension_copy, check_app_version=False)
    assert any("scripting" in problem for problem in problems)


def test_validator_rejects_remote_script(extension_copy: Path) -> None:
    options = extension_copy / "options.html"
    options.write_text(
        options.read_text(encoding="utf-8").replace(
            "</body>", '<script src="https://example.com/x.js"></script></body>'
        ),
        encoding="utf-8",
    )
    problems = extension_build.validate(extension_copy, check_app_version=False)
    assert any("script remoto" in problem for problem in problems)


def test_build_creates_zip_checksums_and_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(extension_build, "_node_syntax_check", lambda root: ([], True))
    out = tmp_path / "release"
    report = extension_build.build(SOURCE, out, check_app_version=False)

    for browser in ("chrome", "edge"):
        archive = out / f"{browser}.zip"
        assert archive.is_file()
        with zipfile.ZipFile(archive) as zf:
            names = set(zf.namelist())
        assert "manifest.json" in names
        assert "privacy.html" in names
        assert all(not name.startswith((".", "tests")) for name in names)
        assert report["targets"][browser]["sha256"] == extension_build._sha256(archive)

    checksums = (out / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines()
    assert len(checksums) == 2
    for line, browser in zip(checksums, ("chrome", "edge"), strict=True):
        digest, name = line.split("  ")
        assert name == f"{browser}.zip"
        assert digest == report["targets"][browser]["sha256"]

    saved_report = json.loads((out / "build-report.json").read_text(encoding="utf-8"))
    assert saved_report["version"] == _manifest(SOURCE)["version"]
    assert saved_report["validated"] is True


def test_build_is_reproducible(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(extension_build, "_node_syntax_check", lambda root: ([], True))
    first = extension_build.build(SOURCE, tmp_path / "first", check_app_version=False)
    second = extension_build.build(SOURCE, tmp_path / "second", check_app_version=False)
    assert first["targets"] == second["targets"]


def test_build_excludes_unlisted_files(
    extension_copy: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(extension_build, "_node_syntax_check", lambda root: ([], True))
    (extension_copy / "notes.txt").write_text("temporal", encoding="utf-8")
    (extension_copy / "debug.log").write_text("log", encoding="utf-8")
    report = extension_build.build(
        extension_copy, tmp_path / "release", check_app_version=False
    )
    assert "notes.txt" not in report["files"]
    assert "debug.log" not in report["files"]


def test_build_rejects_invalid_manifest(extension_copy: Path, tmp_path: Path) -> None:
    manifest = _manifest(extension_copy)
    manifest["manifest_version"] = 2
    _write_manifest(extension_copy, manifest)
    with pytest.raises(ValueError, match="Manifest V3"):
        extension_build.build(
            extension_copy, tmp_path / "release", check_app_version=False
        )

"""Pruebas del generador de SBOM CycloneDX."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BUILD_SCRIPT = PROJECT_ROOT / "deployment" / "sbom" / "build.py"

_spec = importlib.util.spec_from_file_location("sbom_build", BUILD_SCRIPT)
sbom_build = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(sbom_build)


def test_sbom_is_cyclonedx_and_discloses_pending_license(tmp_path: Path) -> None:
    report = sbom_build.build(tmp_path, timestamp="2026-11-21T00:00:00+00:00")
    sbom = json.loads(report["file"].read_text(encoding="utf-8"))

    assert sbom["bomFormat"] == "CycloneDX"
    assert sbom["specVersion"] == "1.5"
    assert sbom["serialNumber"].startswith("urn:uuid:")
    root = sbom["metadata"]["component"]
    assert root["name"] == "UNnexor"
    assert root["version"] == sbom_build.app_version()
    properties = {item["name"]: item["value"] for item in root["properties"]}
    assert properties["unnexor:license-status"] == "pending-owner-decision"
    assert "licenses" not in root

    names = {component["name"] for component in sbom["components"]}
    assert "UNnexor browser extension" in names
    assert "UNnexor local bridge" in names
    assert "fastapi" in names
    assert all(component.get("bom-ref") for component in sbom["components"])


def test_sbom_checksum_matches_file_bytes(tmp_path: Path) -> None:
    report = sbom_build.build(tmp_path, timestamp="2026-11-21T00:00:00+00:00")
    actual = hashlib.sha256(report["file"].read_bytes()).hexdigest()
    checksums = (tmp_path / "SHA256SUMS.txt").read_text(encoding="utf-8")

    assert actual == report["sha256"]
    assert f"{actual}  {report['file'].name}" in checksums


def test_sbom_serial_is_deterministic_for_same_environment(tmp_path: Path) -> None:
    first = sbom_build.build(tmp_path / "uno", timestamp="2026-11-21T00:00:00+00:00")
    second = sbom_build.build(tmp_path / "dos", timestamp="2026-11-21T00:00:00+00:00")
    first_sbom = json.loads(first["file"].read_text(encoding="utf-8"))
    second_sbom = json.loads(second["file"].read_text(encoding="utf-8"))

    assert first_sbom["serialNumber"] == second_sbom["serialNumber"]
    assert first_sbom["components"] == second_sbom["components"]

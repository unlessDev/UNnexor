"""Pruebas de arquitectura (límites de dependencias y contratos básicos).

No sustituyen a las pruebas funcionales: detectan que la presentación y el
dominio no empiecen a importar frameworks de infraestructura por accidente.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent / "app"
EXTENSION_DIR = APP_DIR.parent.parent / "browser-extension"

CORE_MODULES = [
    "conversation.py",
    "math_unicode.py",
    "model_catalog.py",
    "model_evaluator.py",
    "paths.py",
]

FORBIDDEN_IN_CORE = {
    "PySide6",
    "fastapi",
    "uvicorn",
    "httpx",
    "psutil",
    "keyring",
    "dotenv",
}


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_core_does_not_import_frameworks_or_qt():
    violations: dict[str, set[str]] = {}
    for name in CORE_MODULES:
        found = _imported_roots(APP_DIR / name) & FORBIDDEN_IN_CORE
        if found:
            violations[name] = found
    assert not violations, f"El núcleo importa infraestructura: {violations}"


def test_server_does_not_import_qt():
    assert "PySide6" not in _imported_roots(APP_DIR / "server.py")


def test_extension_has_no_remote_code_or_eval():
    manifest = json.loads((EXTENSION_DIR / "manifest.json").read_text(encoding="utf-8"))
    csp = manifest.get("content_security_policy", {})
    policy = " ".join(str(value) for value in csp.values())
    assert "unsafe-eval" not in policy
    for js_file in EXTENSION_DIR.glob("*.js"):
        source = js_file.read_text(encoding="utf-8")
        assert "eval(" not in source
        assert "new Function(" not in source


def test_desktop_does_not_import_server_or_main():
    desktop = APP_DIR / "desktop"
    for js_file in desktop.glob("*.py"):
        roots = _imported_roots(js_file)
        source = js_file.read_text(encoding="utf-8")
        assert "from ..server" not in source, js_file.name
        assert "from ..main" not in source, js_file.name
        assert "fastapi" not in roots, js_file.name


COPILOT_CORE_PACKAGES = ["audio", "stt", "copilot", "knowledge", "routing"]
FORBIDDEN_IN_COPILOT_CORE = {"PySide6", "fastapi", "uvicorn"}


def test_copilot_core_packages_stay_framework_free():
    """ADR-0005: el núcleo del copiloto no depende de Qt ni del servidor."""
    violations: dict[str, set[str]] = {}
    for package in COPILOT_CORE_PACKAGES:
        folder = APP_DIR / package
        if not folder.is_dir():
            continue
        for module in folder.rglob("*.py"):
            source = module.read_text(encoding="utf-8")
            found = _imported_roots(module) & FORBIDDEN_IN_COPILOT_CORE
            if "from ..server" in source or "from ..desktop" in source:
                found.add("server/desktop")
            if found:
                violations[str(module.relative_to(APP_DIR))] = found
    assert not violations, f"Núcleo del copiloto acoplado: {violations}"

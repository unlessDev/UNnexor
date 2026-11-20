# -*- mode: python ; coding: utf-8 -*-
"""Especificación PyInstaller one-folder de Extdator para Windows.

Uso normal (genera el informe y el ZIP):
    python deployment/windows/build.py

El archivo de versión se inyecta con la variable EXTDATOR_VERSION_FILE
para no duplicar la versión en el repositorio.
"""

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

PROJECT_ROOT = Path(SPECPATH).resolve().parents[1]
LOCAL_BRIDGE = PROJECT_ROOT / "local-bridge"
ICON = LOCAL_BRIDGE / "assets" / "extdator.ico"

version_file = os.environ.get("EXTDATOR_VERSION_FILE") or None

datas = [
    (str(LOCAL_BRIDGE / ".env.example"), "."),
    (str(ICON), "assets"),
]
datas += collect_data_files("certifi")

hiddenimports = collect_submodules("app") + [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "anyio._backends._asyncio",
    "keyring.backends.Windows",
]

# OCR opcional: no se empaqueta en el instalador base (evita cientos de MB
# de torch/onnxruntime). La visión del modelo sigue disponible.
# Qt: solo se usan Core, Gui y Widgets; el resto de módulos no aporta nada
# al producto y engorda el instalador.
excludes = [
    "tkinter",
    "pytest",
    "black",
    "pix2text",
    "rapidocr",
    "onnxruntime",
    "torch",
    "torchvision",
    "cv2",
    "matplotlib",
    "IPython",
    "notebook",
    "jupyter",
    "PIL",
    "numpy",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuickWidgets",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtNetwork",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebChannel",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
]

a = Analysis(
    [str(LOCAL_BRIDGE / "run.py")],
    pathex=[str(LOCAL_BRIDGE)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Extdator",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON),
    version=version_file,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Extdator",
)

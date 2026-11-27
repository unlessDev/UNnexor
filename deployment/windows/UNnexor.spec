# -*- mode: python ; coding: utf-8 -*-
"""Especificación PyInstaller one-folder de UNnexor para Windows.

Uso normal (genera el informe y el ZIP):
    python deployment/windows/build.py

El archivo de versión se inyecta con la variable EXTDATOR_VERSION_FILE
para no duplicar la versión en el repositorio.
"""

import os
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)

PROJECT_ROOT = Path(SPECPATH).resolve().parents[1]
LOCAL_BRIDGE = PROJECT_ROOT / "local-bridge"
ICON = LOCAL_BRIDGE / "assets" / "unnexor.ico"

version_file = os.environ.get("EXTDATOR_VERSION_FILE") or None

datas = [
    (str(LOCAL_BRIDGE / ".env.example"), "."),
    (str(ICON), "assets"),
    (str(LOCAL_BRIDGE / "assets" / "unnexor.png"), "assets"),
    (str(LOCAL_BRIDGE / "assets" / "unnexor-x.svg"), "assets"),
]
datas += collect_data_files("certifi")
# Copiloto en tiempo real (ADR-0005): modelo Silero VAD de faster-whisper y
# cabeceras cffi que SoundCard lee al importarse.
datas += collect_data_files("faster_whisper")
datas += collect_data_files("soundcard")
# CTranslate2 no tiene hook: sus DLL (ctranslate2, libiomp5md, cudnn) se
# copian en su carpeta para que el paquete las encuentre.
binaries = collect_dynamic_libs("ctranslate2")

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
    # Copiloto: se importan de forma diferida, se declaran explícitamente.
    "soundcard",
    "faster_whisper",
    "faster_whisper.utils",
    "ctranslate2",
    "av",
    "pypdf",
    "tokenizers",
]

# OCR opcional: no se empaqueta en el instalador base (evita cientos de MB
# de torch). La visión del modelo sigue disponible. NumPy y ONNX Runtime SÍ
# se incluyen: los necesita el copiloto (audio y Silero VAD).
# Qt: solo se usan Core, Gui y Widgets; el resto de módulos no aporta nada
# al producto y engorda el instalador.
excludes = [
    "tkinter",
    "pytest",
    "black",
    "pix2text",
    "rapidocr",
    "torch",
    "torchvision",
    "cv2",
    "matplotlib",
    "IPython",
    "notebook",
    "jupyter",
    "PIL",
    # Arrastrados por dependencias opcionales del entorno de desarrollo; el
    # copiloto no los usa (el auto-chequeo del build lo verifica).
    "scipy",
    "pandas",
    "sympy",
    "sklearn",
    "transformers",
    "optimum",
    "onnx",
    "polars",
    "pyarrow",
    "seaborn",
    "ultralytics",
    "wandb",
    "tensorflow",
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
    binaries=binaries,
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
    name="UNnexor",
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
    name="UNnexor",
)

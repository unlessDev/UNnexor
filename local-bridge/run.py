#!/usr/bin/env python3
"""Launcher de consola del puente UNnexor.

Uso:
    python run.py                → puente completo (servidor + panel flotante)
    python run.py --server-only  → solo servidor HTTP
    python run.py --check        → auto-verificación headless
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.main import main

if __name__ == "__main__":
    sys.exit(main())

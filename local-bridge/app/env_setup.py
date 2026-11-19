"""Creación y reparación de ``local-bridge/.env``.

* Copia ``.env.example`` si el .env no existe.
* Genera un token compartido seguro (``secrets.token_urlsafe``) si falta.

Uso CLI: ``python -m app.env_setup`` (lo llaman install.ps1 y start.ps1).
"""

from __future__ import annotations

import secrets
import shutil
import sys
from pathlib import Path
from typing import Optional, Tuple

from .config import DEFAULT_ENV_FILE
from . import paths

EXAMPLE_FILE = paths.resource_path(".env.example")


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def ensure_env_file(
    env_path: Optional[Path] = None,
) -> Tuple[Path, Optional[str], bool]:
    """Garantiza que exista el .env y que BRIDGE_TOKEN tenga valor.

    Devuelve ``(ruta, token_generado|None, fue_creado)``.
    """
    env_path = Path(env_path) if env_path else DEFAULT_ENV_FILE
    env_path.parent.mkdir(parents=True, exist_ok=True)
    created = False
    if not env_path.exists():
        if EXAMPLE_FILE.exists():
            shutil.copyfile(EXAMPLE_FILE, env_path)
        else:
            env_path.write_text("# Extdator bridge\nBRIDGE_TOKEN=\n", encoding="utf-8")
        created = True

    text = env_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    token: Optional[str] = None
    token_seen = False

    for index, line in enumerate(lines):
        if line.strip().lower().startswith("bridge_token"):
            token_seen = True
            value = line.split("=", 1)[1].strip() if "=" in line else ""
            if not value:
                token = generate_token()
                lines[index] = f"BRIDGE_TOKEN={token}"
            break

    if not token_seen:
        token = generate_token()
        lines.append(f"BRIDGE_TOKEN={token}")

    if token is not None:
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return env_path, token, created


def main() -> int:
    env_path, token, created = ensure_env_file()
    if created:
        print(f"[env] Creado {env_path} desde .env.example")
    else:
        print(f"[env] {env_path} ya existe")
    if token:
        print("[env] Token generado y guardado en BRIDGE_TOKEN:")
        print(token)
        print("[env] Copia este token en las opciones de la extensión.")
    else:
        print("[env] BRIDGE_TOKEN ya está configurado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

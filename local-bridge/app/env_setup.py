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


def clear_env_secrets(env_path: Optional[Path] = None) -> dict:
    """Retira secretos del ``.env`` conservando el resto de la configuración.

    Vacía ``BRIDGE_TOKEN`` (se regenera en el siguiente arranque) y elimina
    las líneas ``*_API_KEY``. Devuelve un resumen sin incluir valores.
    """
    path = Path(env_path) if env_path else paths.env_file()
    result = {
        "found": path.is_file(),
        "token_cleared": False,
        "keys_removed": [],
        "error": "",
    }
    if not result["found"]:
        return result
    try:
        original = path.read_text(encoding="utf-8")
    except OSError as exc:
        # El archivo existe pero no se pudo leer: hay que informar del fallo.
        # Devolver "no encontrado" haría creer que no quedan secretos.
        result["error"] = str(exc)
        return result

    kept = []
    for line in original.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            kept.append(line)
            continue
        name = stripped.split("=", 1)[0].strip().upper()
        if name == "BRIDGE_TOKEN":
            kept.append("BRIDGE_TOKEN=")
            result["token_cleared"] = True
        elif name.endswith("_API_KEY"):
            result["keys_removed"].append(name)
        else:
            kept.append(line)

    new_text = "\n".join(kept) + ("\n" if kept else "")
    if new_text != original:
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            tmp.write_text(new_text, encoding="utf-8")
            shutil.copymode(path, tmp)
            tmp.replace(path)
        except OSError as exc:
            result["error"] = str(exc)
            try:
                tmp.unlink()
            except OSError:
                pass
    return result


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

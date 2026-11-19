"""Arquitectura de proveedores de IA remotos (adaptadores).

* ``base``         — errores y estructuras comunes.
* ``openai_compat``— adaptador genérico OpenAI-compatible (SSE + /models).
* ``presets``      — catálogo de proveedores con base_urls verificados.
* ``manager``      — conexión/desconexión, keyring y selección del cliente.

Claves: SOLO en el keyring del sistema (Windows Credential Manager).
El archivo ``data/providers.json`` guarda estado (fechas, nº de modelos)
pero NUNCA credenciales.
"""

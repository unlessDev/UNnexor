"""UNnexor local bridge — versión del paquete."""

__version__ = "2.1.0"
APP_NAME = "UNnexor"
# Identificador que publica /health. La extensión y start.ps1 aceptan
# también LEGACY_APP_IDS para reconocer puentes de versiones anteriores.
APP_ID = "unnexor-bridge"
LEGACY_APP_IDS = ("extdator-bridge",)
KNOWN_APP_IDS = (APP_ID, *LEGACY_APP_IDS)

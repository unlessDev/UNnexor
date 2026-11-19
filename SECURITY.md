# Seguridad

## Modelo y alcance

Extdator es una aplicación local para Windows. El puente escucha solo en
`127.0.0.1`, exige token y valida el origen de la extensión. Las claves de
proveedores se guardan en el Windows Credential Manager (keyring) y nunca
en el repositorio, en `providers.json` ni en la extensión.

## Cómo reportar una vulnerabilidad

No publiques credenciales, datos personales ni detalles explotables en un
issue público. Usa **GitHub Security Advisories** del repositorio
(`Security` → `Advisories` → `Report a vulnerability`) o contacta al
propietario por los canales privados de GitHub.

Incluye, si es posible:

- versión de Extdator, Windows y navegador;
- pasos de reproducción;
- impacto observado;
- logs sin contenido sensible;
- si afecta a la extensión, al puente o al instalador.

No adjuntes tokens reales, claves API ni capturas con datos personales.

## Prácticas del proyecto

- No se aceptan secretos en commits; `.env`, certificados y datos de usuario
  están excluidos por `.gitignore`.
- Las dependencias se revisan antes de actualizar; Dependabot no fusiona
  automáticamente.
- Los builds públicos deben verificarse con SHA-256 y, cuando exista
  certificado, firmarse con Authenticode.
- Los artefactos no firmados se identifican como tales.
- Las vulnerabilidades confirmadas se corrigen en una rama `security/*` y
  se registran en `CHANGELOG.md` sin revelar explotación activa.

## Limitaciones conocidas

Un proceso local con los mismos permisos del usuario puede leer el `.env`.
El token protege frente a páginas web y orígenes no autorizados, no frente a
malware local. Esta limitación es inherente a una aplicación de escritorio
sin privilegios separados.

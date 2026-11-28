# ADR-0004 — Licencia principal (Apache-2.0)

- **Estado:** aceptado (2026-11-27) — licencia **Apache-2.0**
- **Fecha:** 2026-11-19

## Contexto

El repositorio no contiene un archivo `LICENSE`. Sin una licencia explícita,
el código queda bajo copyright y no se concede permiso de redistribución.
El proyecto usa PySide6/Qt (LGPLv3/GPLv3/comercial), dependencias MIT/BSD y
herramientas de build con sus propias licencias.

## Opciones

1. **Propietaria / todos los derechos reservados**: máxima protección
   comercial; impide contribuciones y redistribución pública, y complica
   que las tiendas entiendan el modelo.
2. **MIT**: máxima adopción; permite uso comercial y forks, sin garantía ni
   concesión explícita de patentes.
3. **Apache-2.0**: adopción amplia, con concesión explícita de patentes y
   obligaciones de aviso; suele ser una opción equilibrada para una
   aplicación que quiere crecer.
4. **GPL/LGPL/AGPL**: obliga a compartir modificaciones; puede ser
   incompatible con un modelo comercial o con la distribución en tiendas.

## Recomendación

Si el propietario quiere un producto de código abierto con protección de
patentes, **Apache-2.0** es la opción recomendada. Si planea venderlo o
mantener el código cerrado, debe elegir una licencia propietaria y ajustar
la distribución de PySide6/Qt (LGPL dinámica, avisos y posibilidad de
sustituir las bibliotecas).

## Decisión

El propietario eligió **Apache License 2.0** el 2026-11-27 (versión 2.0.0).
`LICENSE` contiene el texto oficial y `NOTICE` el aviso de copyright
(«Copyright 2026 UnlessDev»). El repositorio es público y se publican
releases en GitHub.

## Consecuencias

- Cualquiera puede usar, modificar y redistribuir el código, también con
  fines comerciales, conservando `LICENSE` y `NOTICE`; la licencia incluye
  una concesión explícita de patentes.
- Qt/PySide6 (LGPL-3.0) se distribuye como bibliotecas dinámicas en la
  carpeta del programa (build one-folder), por lo que el usuario puede
  sustituirlas; sus licencias se listan en `THIRD_PARTY_NOTICES.md`.
- La publicación en tiendas sigue requiriendo cuentas de desarrollador y la
  URL pública de la política de privacidad.

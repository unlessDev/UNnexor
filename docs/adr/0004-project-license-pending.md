# ADR-0004 — Licencia principal pendiente

- **Estado:** propuesto; requiere aprobación del propietario
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

No se elige ninguna licencia en esta fase. Hasta que el propietario lo
apruebe por escrito:

- el repositorio permanece privado;
- no se publican releases públicas;
- `README.md` y `THIRD_PARTY_NOTICES.md` indican que la licencia está
  pendiente.

## Consecuencias

La publicación en Chrome Web Store/Edge Add-ons y la distribución del
instalador quedan bloqueadas hasta resolver esta decisión y revisar las
obligaciones de LGPL de Qt/PySide6.

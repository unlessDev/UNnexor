# ADR-0001 — Monolito modular con límites Clean/Hexagonal pragmáticos

- **Estado:** aceptado
- **Fecha:** 2026-11-19

## Contexto

Extdator es una aplicación local de escritorio con una extensión de
navegador. El código actual funciona y tiene 202 pruebas. Una extracción
física completa a `apps/`, `core/` e `infrastructure/` obligaría a mover
casi todos los módulos, reescribir imports y asumir un riesgo alto sin una
necesidad funcional inmediata.

## Alternativas

1. **Microservicios**: descartado. Aumenta instalación, procesos y fallos
   para un producto monousuario local.
2. **Reescritura con capas físicas completas**: descartado por riesgo y por
   el principio de preservar el proyecto.
3. **Monolito modular con límites explícitos**: elegido. Se mantienen los
   módulos actuales, se documentan sus contratos y se extraen piezas solo
   cuando reducen acoplamiento real.

## Decisión

- Un proceso, tres hilos (Qt, uvicorn, generación).
- Dependencias dirigidas hacia el dominio: la UI y la infraestructura
  dependen de contratos; el dominio no importa Qt, FastAPI ni httpx.
- Adaptadores para inferencia, OCR, persistencia y seguridad.
- La presentación publica eventos en la cola; no llama a proveedores.
- Cualquier nueva abstracción debe venir acompañada de una prueba y de una
  necesidad concreta.

## Consecuencias

- Menor riesgo de regresión y despliegue sencillo.
- La separación es conceptual y por módulos, no por carpetas ceremoniales.
- `server.py` y `window.py` siguen siendo puntos de concentración de
  responsabilidades; se dividirán si crecen o si una prueba necesita aislar
  una parte.

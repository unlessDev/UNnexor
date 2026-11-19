# ADR-0002 — Transporte local: HTTP autenticado frente a Native Messaging

- **Estado:** aceptado
- **Fecha:** 2026-11-19

## Contexto

La extensión necesita enviar la selección a la aplicación local. El
sistema existente usa `POST http://127.0.0.1:5000/api/selection` con token,
validación de `Origin`, límite de tasa y emparejamiento por código.

Native Messaging evitaría abrir un puerto, pero exige registrar un host
nativo por navegador, manifiestos con IDs concretos y un proceso auxiliar;
además complica el diagnóstico para el usuario y la distribución en Chrome
Web Store/Edge Add-ons.

## Alternativas

### A. HTTP local (actual)

- Ventajas: funciona ya, es depurable, no requiere registro por navegador,
  permite el panel independiente y el modo `--check`.
- Riesgos: cualquier proceso local puede intentar conectarse. Se mitiga con
  loopback obligatorio, token, origen de extensión, rate limit y CORS
  restrictivo. No se usa CORS como única defensa.

### B. Native Messaging

- Ventajas: sin puerto TCP y origin validado por el navegador.
- Costes: manifiesto e instalación por navegador, IDs definitivos,
  mensajes por stdin/stdout, integración con el ciclo de vida del host y
  más superficie de soporte. No elimina el riesgo de un host mal
  registrado.

## Decisión

Mantener HTTP local autenticado en esta etapa. El emparejamiento por código
efímero y la validación de origen se conservan. Native Messaging queda como
alternativa documentada para una versión futura si el modelo de amenaza o
los requisitos de las tiendas lo justifican.

## Consecuencias

- El instalador no necesita registrar hosts nativos.
- Los IDs de tienda se configurarán en `ALLOWED_ORIGINS` cuando existan; el
  valor comodín actual es una conveniencia de desarrollo.
- Se debe mantener el puerto en loopback y no añadir endpoints que acepten
  orígenes web.

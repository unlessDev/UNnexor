# Matriz de cumplimiento para tiendas

> Revisión: 2026-11-19. Legislación y políticas pueden cambiar; esta matriz
> debe revisarse antes de cada envío. No declara aprobación de ninguna tienda.

| Política / requisito | Chrome Web Store | Edge Add-ons | Estado | Evidencia / pendiente |
|---|---|---|---|---|
| Single Purpose (propósito único) | Sí | Sí | **Cumple** | Asistente de selección de texto; funciones agrupadas en torno a ese propósito. |
| Limited Use (uso limitado de datos) | Sí | Sí | **Cumple con divulgación** | Texto usado solo para la funcionalidad; sin venta, publicidad ni analítica. Proveedor remoto opcional divulgado. |
| User Data Policy | Sí | Sí | **Cumple con divulgación** | `privacy.html` describe datos, finalidad y destino. |
| Privacy Policy pública | Obligatoria | Obligatoria | **Pendiente** | HTML listo; falta URL pública y decisión de alojamiento. |
| Remote Code | Prohibido | Prohibido | **Cumple** | Sin scripts remotos, `eval` ni `new Function`; CSP explícita y prueba automática. |
| Permisos mínimos | Sí | Sí | **Cumple** | Solo `storage` y loopback; `scripting` y hosts globales retirados. |
| Consentimiento / transparencia | Sí | Sí | **Cumple** | Asistente de configuración, aviso de proveedor remoto y panel de diagnóstico. |
| Seguridad | Sí | Sí | **Cumple** | Token, origen, límites, loopback, claves en keyring. |
| Código ofuscado / minificado | Prohibido sin revisión | Prohibido sin revisión | **Cumple** | JavaScript legible, sin build ni minificación. |
| Publicidad / seguimiento | Prohibido sin divulgación | Prohibido sin divulgación | **Cumple** | No hay anuncios, analítica ni rastreadores. |
| Capturas y ficha reales | Obligatorias | Obligatorias | **Pendiente** | Deben capturarse de la app real; no se generan mockups. |
| Contacto de soporte | Obligatorio | Obligatorio | **Pendiente** | Repositorio privado; falta canal público o decisión del propietario. |
| Licencia del producto | Recomendada / coherencia | Recomendada / coherencia | **Pendiente** | ADR-0004: licencia principal sin aprobar. |
| Cuenta de desarrollador | De pago | Gratuita con verificación | **Pendiente del propietario** | No se crean cuentas ni se aceptan acuerdos por el agente. |
| Firma del paquete | La tienda firma | La tienda firma | **No aplica** | El instalador de escritorio sí requiere Authenticode (pendiente). |
| Actualizaciones | Por la tienda | Por la tienda | **Cumple** | La extensión no se autoactualiza. |
| Native Messaging | No usado | No usado | **No aplica** | Transporte HTTP local autenticado (ADR-0002). |

## Bloqueos actuales para enviar

1. URL pública de la política de privacidad.
2. Capturas reales de la extensión y del panel.
3. Cuenta de desarrollador y verificación de identidad.
4. Licencia principal aprobada.
5. Canal de soporte público.
6. Autorización expresa del propietario.

## Comprobaciones ejecutables

```powershell
# Validación estática del manifest y recursos
python deployment/extension/build.py --check

# Pruebas del paquete (con pytest)
cd local-bridge
.venv\Scripts\python.exe -m pytest tests/test_extension_package.py -q
```

La validación automática no sustituye la revisión de la tienda ni una prueba
manual en cada navegador.

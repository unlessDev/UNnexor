# ADR-0003 — Empaquetado de escritorio e instalador

- **Estado:** aceptado (implementado y probado localmente)
- **Fecha:** 2026-11-19

## Contexto

El usuario final no debe instalar Python, crear un `.venv`, ejecutar pip ni
editar archivos. La aplicación usa PySide6/Qt con plugins nativos, FastAPI,
uvicorn, psutil, keyring y httpx. Hay que producir un ejecutable y un
instalador para Windows 10/11, preferiblemente por usuario y sin admin.

## Alternativas de empaquetado

| Herramienta | Ventajas | Costes |
|---|---|---|
| **PyInstaller** | Madurez, hooks para PySide6, one-folder, CI sencillo | Binarios grandes; hay que declarar datos ocultos |
| Nuitka | Compilación y arranque potencialmente mejores | Compilación lenta, toolchain C, más complejidad |
| cx_Freeze | Simple | Menos experiencia con Qt/FastAPI y menos usado |
| Briefcase | Flujo de proyecto | Añade estructura y convenciones que no aportan aquí |

## Alternativas de instalador

| Herramienta | Ventajas | Costes |
|---|---|---|
| **Inno Setup** | Gratuito, instalador por usuario, desinstalador, scripts claros, sin runtime .NET | Pascal; requiere instalar la herramienta de build |
| WiX | Estándar MSI, integración empresarial | Curva alta, más XML y runtime de build |
| NSIS | Pequeño y flexible | Scripts frágiles y menos mantenibles |
| MSIX | Actualizaciones y sandbox | Requiere certificado/Store y más fricción para side-loading |

## Decisión

- **PyInstaller en modo one-folder** para la aplicación. One-file se
  descarta por el coste de extracción en cada arranque y los falsos
  positivos de antivirus; one-folder permite sustituir binarios y reducir
  el tiempo de inicio.
- **Inno Setup** con instalación por usuario en
  `%LOCALAPPDATA%\Programs\UNnexor`, sin privilegios administrativos.
- Datos en `%LOCALAPPDATA%\UNnexor` (hasta 2.0.0, `%LOCALAPPDATA%\Extdator`; migración automática en 2.1.0); el instalador no incluye modelos,
  pesos OCR, claves ni historiales.
- El desinstalador conserva datos personales por defecto y ofrece una
  limpieza explícita.

## Consecuencias

- El build debe ejecutarse en Windows y verificarse fuera del workspace.
  `deployment/installer/build.py` automatiza la instalación silenciosa en
  un directorio temporal, el auto-chequeo, la preservación de datos, la
  purga de secretos y la desinstalación.
- La firma Authenticode queda pendiente de certificado del propietario.
- Los artefactos no firmados se etiquetarán como tales.
- No se publica nada hasta que el propietario lo autorice.

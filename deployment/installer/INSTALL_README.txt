UNnexor
=======

Gracias por instalar UNnexor, el asistente local para tu navegador.

QUÉ SE HA INSTALADO
-------------------
* UNnexor.exe y sus bibliotecas, en:
  %LOCALAPPDATA%\Programs\UNnexor
* Accesos directos en el menú Inicio (y en el escritorio si lo marcaste).
* Un desinstalador en el menú Inicio y en Configuración > Aplicaciones.

REQUISITOS
----------
* Windows 10 u 11 de 64 bits.
* Ollama instalado y en ejecución para usar modelos locales:
  https://ollama.com/download/windows
* Después abre UNnexor > menú > Centro de control > Recomendar
  configuración: propone los modelos adecuados para tu equipo y, si lo
  autorizas, los descarga e instala automáticamente (con progreso real).
* La extensión de Chrome, Brave o Edge, cargada desde el paquete
  correspondiente. En las opciones de la extensión, usa el asistente de
  emparejamiento por código de 6 dígitos que muestra el panel.

DÓNDE ESTÁN TUS DATOS
---------------------
Ajustes, registro, perfiles y modelos de voz se guardan en:
  %LOCALAPPDATA%\UNnexor

Si usabas una versión anterior (Extdator o UNnexor 2.0), en el primer
arranque UNnexor copia tus datos de %LOCALAPPDATA%\Extdator a la carpeta
nueva, sin sobrescribir nada y sin borrar la carpeta antigua. El resultado
queda registrado en %LOCALAPPDATA%\UNnexor\migration.json. Cuando
compruebes que todo funciona, puedes borrar la carpeta antigua.

El instalador no incluye modelos de Ollama, modelos de voz, pesos de OCR,
claves API ni historiales de conversaciones.

DESINSTALAR
-----------
Al desinstalar, tus datos se conservan por defecto para una futura
reinstalación. El desinstalador te preguntará si quieres eliminarlos
también; si eliges Sí, borrará %LOCALAPPDATA%\UNnexor (y la copia antigua
%LOCALAPPDATA%\Extdator, si existe) y las credenciales de proveedores
guardadas en el Administrador de credenciales de Windows.

Para borrar solo los secretos (por ejemplo, antes de vender el equipo)
sin desinstalar:
  "%LOCALAPPDATA%\Programs\UNnexor\UNnexor.exe" --purge-secrets

AVISO DE SEGURIDAD
------------------
Este instalador no está firmado con Authenticode. Windows SmartScreen
puede mostrar una advertencia; verifica el SHA-256 publicado junto al
instalador antes de ejecutarlo.

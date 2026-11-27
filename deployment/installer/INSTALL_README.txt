UNnexor
========

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
  Después descarga un modelo, por ejemplo:  ollama pull llama3
* La extensión de Chrome, Brave o Edge, cargada desde el paquete
  correspondiente. En las opciones de la extensión, usa el asistente de
  emparejamiento por código de 6 dígitos que muestra el panel.

DÓNDE ESTÁN TUS DATOS
---------------------
Ajustes, registro y configuración se guardan en:
  %LOCALAPPDATA%\Extdator

El instalador no incluye modelos de Ollama, pesos de OCR, claves API ni
historiales de conversaciones.

DESINSTALAR
-----------
Al desinstalar, tus datos se conservan por defecto para una futura
reinstalación. El desinstalador te preguntará si quieres eliminarlos
también; si eliges Sí, borrará %LOCALAPPDATA%\Extdator y las credenciales
de proveedores guardadas en el Administrador de credenciales de Windows.

Para borrar solo los secretos (por ejemplo, antes de vender el equipo)
sin desinstalar:
  "%LOCALAPPDATA%\Programs\UNnexor\UNnexor.exe" --purge-secrets

AVISO DE SEGURIDAD
------------------
Este instalador no está firmado con Authenticode. Windows SmartScreen
puede mostrar una advertencia; verifica el SHA-256 publicado junto al
instalador antes de ejecutarlo.

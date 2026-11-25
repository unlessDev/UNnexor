"""Interfaz del copiloto en tiempo real (PySide6).

* ``controller`` — ViewModel: posee la sesión (audio, STT, asistente,
  traducción, desafíos) y convierte los callbacks de los hilos de trabajo
  en señales Qt. Las vistas no contienen lógica de negocio.
* ``window``     — ventana del copiloto con sus secciones.
* ``overlay``    — panel flotante siempre visible, sin robar el foco.
* ``hotkeys``    — atajos globales configurables (RegisterHotKey).
* ``tasks``      — ejecución de trabajo pesado fuera del hilo de la GUI.
"""

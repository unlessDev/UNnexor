"""Captura y preprocesado de audio del copiloto.

Capas:

* ``types``     — DTO del dominio de audio (sin Qt ni backends).
* ``dsp``       — mezcla a mono, remuestreo en streaming y niveles.
* ``vad``       — detectores de voz (Silero ONNX en streaming o energía).
* ``segmenter`` — corta el flujo en intervenciones provisionales/finales.
* ``capture``   — puerto de captura y adaptador SoundCard (WASAPI loopback
  y micrófono en Windows), con reconexión y liberación de dispositivos.
* ``pipeline``  — orquesta varias fuentes simultáneas en hilos propios.
* ``wav``       — codificación PCM16 en memoria (STT remoto y exportes).

Formato interno: mono, ``float32`` en [-1, 1], 16 kHz.
"""

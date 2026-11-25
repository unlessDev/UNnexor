"""Reconocimiento de voz (STT) del copiloto.

* ``base``      — puerto ``SpeechRecognizer``, resultados y errores.
* ``catalog``   — motores y modelos con su modalidad real (streaming,
  fragmentos, lotes), coste y requisitos; fecha de verificación.
* ``faster_whisper_engine`` — Whisper local con CTranslate2 (CPU int8 o
  CUDA), modelos en ``paths.models_dir()`` descargados con consentimiento.
* ``openai_compat_engine`` — ``POST /audio/transcriptions`` (Groq, OpenAI).
* ``deepgram_engine`` — API REST de audio pregrabado de Deepgram.
* ``hallucinations`` — filtro de frases que Whisper inventa en silencio.
* ``recommend`` — recomendación según hardware, idioma y privacidad.
* ``factory``   — construye el motor elegido con la clave del keyring.
"""

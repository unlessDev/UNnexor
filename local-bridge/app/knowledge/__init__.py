"""Perfiles de contexto y base de conocimiento del copiloto (RAG).

* ``profiles``   — perfil del usuario (rol, experiencia, objetivo, tono,
  glosario, permiso para enviar documentos a servicios remotos).
* ``extract``    — texto de TXT/Markdown/PDF/DOCX con límites de tamaño.
* ``chunker``    — fragmentos con solapamiento y metadatos de origen.
* ``store``      — SQLite: documentos, fragmentos y vectores.
* ``bm25``       — recuperación léxica sin modelos (siempre disponible).
* ``embeddings`` — embeddings opcionales vía Ollama (``/api/embed``).
* ``service``    — caso de uso: añadir/eliminar/reindexar/buscar y
  construir el contexto que se inyecta en el modelo con sus fuentes.

Los documentos nunca salen del equipo salvo que el perfil lo permita y el
proveedor tenga el consentimiento ``documents``.
"""

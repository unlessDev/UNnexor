"""Historial conversacional del puente.

Simula el comportamiento de una sesión persistente de ``ollama run``: cada
selección se envía como mensaje ``user`` consecutivo y la respuesta como
``assistant``, conservando el contexto. El límite de memoria se controla con
``MAX_HISTORY_MESSAGES`` (los mensajes más antiguos se descartan; el prompt de
sistema se mantiene siempre).
"""

from __future__ import annotations

from collections import deque
from typing import Deque, Dict, List


class Conversation:
    def __init__(self, system_prompt: str = "", max_messages: int = 20) -> None:
        self.system_prompt = (system_prompt or "").strip()
        self._max = max(2, int(max_messages))
        self._messages: Deque[Dict[str, str]] = deque(maxlen=self._max)

    # ------------------------------------------------------------------
    def add_user(self, text: str) -> None:
        self._messages.append({"role": "user", "content": text})

    def add_assistant(self, text: str) -> None:
        self._messages.append({"role": "assistant", "content": text})

    def drop_last_user(self) -> None:
        """Elimina el último mensaje user (usado cuando la generación falla)."""
        for index in range(len(self._messages) - 1, -1, -1):
            if self._messages[index]["role"] == "user":
                del self._messages[index]
                return

    def payload(self) -> List[Dict[str, str]]:
        """Lista de mensajes lista para POST /api/chat (sistema primero)."""
        messages: List[Dict[str, str]] = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.extend(self._messages)
        return messages

    def clear(self) -> None:
        self._messages.clear()

    def __len__(self) -> int:
        return len(self._messages)

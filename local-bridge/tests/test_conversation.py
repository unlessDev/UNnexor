"""Pruebas del historial conversacional."""

from __future__ import annotations

from app.conversation import Conversation


def test_payload_includes_system_first():
    conv = Conversation("Eres un asistente.", max_messages=10)
    conv.add_user("hola")
    conv.add_assistant("¡hola!")
    payload = conv.payload()
    assert payload[0] == {"role": "system", "content": "Eres un asistente."}
    assert payload[1]["role"] == "user"
    assert payload[2]["role"] == "assistant"


def test_no_system_prompt_is_allowed():
    conv = Conversation("", max_messages=4)
    conv.add_user("hola")
    assert [m["role"] for m in conv.payload()] == ["user"]


def test_history_trims_to_max():
    conv = Conversation("sys", max_messages=4)
    for i in range(10):
        conv.add_user(f"u{i}")
        conv.add_assistant(f"a{i}")
    payload = conv.payload()
    non_system = payload[1:]
    assert len(non_system) == 4
    assert non_system[-1]["content"] == "a9"
    assert non_system[0]["content"] == "u8"


def test_clear_resets_history_but_keeps_system():
    conv = Conversation("sys", max_messages=5)
    conv.add_user("hola")
    conv.clear()
    assert len(conv) == 0
    assert conv.payload() == [{"role": "system", "content": "sys"}]


def test_drop_last_user_removes_pending_turn():
    conv = Conversation("", max_messages=10)
    conv.add_user("hola")
    conv.add_assistant("respuesta")
    conv.add_user("nuevo")
    conv.drop_last_user()
    assert [m["role"] for m in conv.payload()] == ["user", "assistant"]


def test_multibyte_text_preserved():
    conv = Conversation("", max_messages=5)
    text = "Árbol ñandú 🚀 €\nlínea dos"
    conv.add_user(text)
    assert conv.payload()[-1]["content"] == text

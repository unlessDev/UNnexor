"""Construcción de prompts del copiloto (respuestas, traducción, resumen,
desafíos). Texto en español; el idioma de salida lo fija el perfil.

Regla de honestidad: el modelo no debe inventar experiencias, cifras ni
credenciales que no estén en el perfil o en los documentos; si faltan
datos, propone una respuesta honesta y marca ``[completar: …]``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..knowledge.profiles import ContextProfile, Detail

# Muletillas de «asesor» que algunos modelos anteponen pese a las
# instrucciones («Podrías responder: …»). Se retiran para que la respuesta
# pueda decirse tal cual; el contenido nunca se modifica.
_META_PREFIX = re.compile(
    r"^\s*(?:\*\*)?(?:"
    r"(?:una\s+)?(?:posible|buena)\s+respuesta(?:\s+(?:podría\s+ser|sería))?"
    r"|respuesta\s+sugerida"
    r"|(?:tú\s+)?podrías\s+(?:responder|decir|contestar|explicarle)"
    r"(?:\s+(?:algo\s+como|lo\s+siguiente|que))?"
    r"|puedes\s+(?:responder|decir|contestar|explicarle)"
    r"(?:\s+(?:algo\s+como|lo\s+siguiente|que))?"
    r"|te\s+recomiendo\s+(?:que\s+)?(?:digas|respondas|contestes|le\s+expliques)"
    r"(?:\s+(?:algo\s+como|lo\s+siguiente|que))?"
    r"|deberías\s+(?:decir|responder|contestar|explicarle)(?:\s+que)?"
    r"|aquí\s+tienes\s+(?:una|la)\s+(?:posible\s+)?respuesta"
    r")\s*(?:\*\*)?\s*[:：]?\s*(?:\*\*)?\s*",
    re.IGNORECASE,
)
_QUOTE_PAIRS = (("«", "»"), ('"', '"'), ("“", "”"))


def clean_spoken_answer(text: str) -> str:
    """Quita introducciones de «asesor» y comillas que envuelven la respuesta."""
    cleaned = text.strip()
    match = _META_PREFIX.match(cleaned)
    prefix = match.group(0).replace("*", "").strip().lower() if match else ""
    introduces = prefix.endswith((":", "：", " que", "algo como", "lo siguiente"))
    rest = cleaned[match.end() :] if match else ""
    if match and introduces and any(ch.isalpha() for ch in rest):
        cleaned = cleaned[match.end() :].lstrip()
        if cleaned[:1].islower():
            cleaned = cleaned[0].upper() + cleaned[1:]
    for opening, closing in _QUOTE_PAIRS:
        inner = cleaned[1:-1]
        if (
            len(cleaned) > 2
            and cleaned.startswith(opening)
            and cleaned.endswith(closing)
            and opening not in inner
            and closing not in inner
        ):
            cleaned = inner.strip()
            break
    return cleaned or text.strip()


LANGUAGE_NAMES = {
    "es": "español",
    "en": "inglés",
    "pt": "portugués",
    "fr": "francés",
    "de": "alemán",
    "it": "italiano",
}


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code.split("-")[0].lower(), code)


@dataclass(frozen=True)
class TranscriptLine:
    speaker: str  # "Interlocutor" | "Tú"
    text: str


def _transcript_block(lines: list[TranscriptLine], max_chars: int) -> str:
    out: list[str] = []
    used = 0
    for line in reversed(lines):
        entry = f"{line.speaker}: {line.text.strip()}"
        if used + len(entry) > max_chars:
            break
        out.append(entry)
        used += len(entry) + 1
    return "\n".join(reversed(out))


def answer_messages(
    question: str,
    profile: ContextProfile | None,
    *,
    transcript: list[TranscriptLine] | None = None,
    knowledge: str = "",
    mode: str = "answer",  # answer | alternative | expand
    previous_answer: str = "",
    max_transcript_chars: int = 2500,
) -> list[dict[str, str]]:
    language = language_name(profile.language if profile else "es")
    detail = (profile.detail if profile else Detail.BRIEF).instruction
    tone = profile.tone_instruction() if profile else ""
    system = (
        "Hablas EN NOMBRE de una persona durante una conversación en vivo. Tu "
        "texto es exactamente lo que esa persona va a decir en voz alta a su "
        "interlocutor: escribe en primera persona, como si fueras ella, con un "
        "tono natural y conversacional, y contesta directamente a lo que el "
        "interlocutor dijo o preguntó. "
        f"Responde en {language}. {detail} {tone}\n"
        "PROHIBIDO dirigirte a la persona o explicarle qué decir: nunca empieces "
        "con «Podrías responder…», «Te recomiendo que digas…», «Una posible "
        "respuesta sería…», «Puedes contestar que…», «Deberías explicarle que…» "
        "ni fórmulas parecidas; no ofrezcas varias alternativas, no añadas "
        "introducciones, títulos, comillas, notas ni preguntas para quien te "
        "consulta. Empieza directamente por el contenido de la respuesta.\n"
        "Mantén la precisión técnica, conserva el contexto de la conversación y "
        "ajusta la extensión a la complejidad de la pregunta.\n"
        "Hablar en primera persona NO te autoriza a inventar: usa solo la "
        "información del perfil, de sus documentos y de la conversación. No "
        "inventes experiencias, opiniones, empresas, cifras, títulos, "
        "compromisos ni resultados. Si la pregunta pide un dato personal que no "
        "tienes, responde de forma neutral sin afirmarlo o indica brevemente "
        "solo lo que no puede determinarse, marcándolo como [completar: …]. "
        "No menciones que eres una IA ni que hay un copiloto."
    ).strip()
    sections: list[str] = []
    if profile is not None:
        facts = [
            ("Rol o puesto de la persona", profile.role),
            ("Experiencia y conocimientos", profile.about),
            ("Objetivo de la conversación", profile.goal),
            ("Empresa o contraparte", profile.company),
        ]
        block = "\n".join(
            f"- {label}: {value.strip()}" for label, value in facts if value.strip()
        )
        if block:
            sections.append(f"PERFIL\n{block}")
    if knowledge.strip():
        sections.append(
            f"CONTEXTO DE SUS DOCUMENTOS (cita la fuente si la usas)\n{knowledge.strip()}"
        )
    if transcript:
        sections.append(
            "CONVERSACIÓN RECIENTE\n"
            + _transcript_block(transcript, max_transcript_chars)
        )
    if mode == "alternative" and previous_answer:
        task = (
            f"Pregunta: «{question.strip()}»\nYa se sugirió esta respuesta:\n"
            f"«{previous_answer.strip()}»\nDa otra respuesta distinta (otro "
            "enfoque o ejemplo), igual de honesta, en primera persona y lista "
            "para decirla en voz alta."
        )
    elif mode == "expand" and previous_answer:
        task = (
            f"Pregunta: «{question.strip()}»\nRespuesta sugerida:\n"
            f"«{previous_answer.strip()}»\nAmplíala con más detalle, ejemplos y "
            "explicación, manteniendo los mismos hechos, en primera persona y "
            "lista para decirla en voz alta."
        )
    elif mode == "window":
        task = (
            "Fragmento de la conversación escuchado a petición de la persona "
            f"(puede ser largo y mezclar explicaciones):\n«{question.strip()}»\n"
            "Identifica la pregunta o petición principal que el interlocutor "
            "le hace (suele estar al final) y contéstala tú, en primera "
            "persona, como si hablaras con él. Si hay varias preguntas, "
            "responde a todas en orden, brevemente."
        )
    else:
        task = (
            f"El interlocutor acaba de decir: «{question.strip()}»\n"
            "Contéstale ahora, en primera persona, con las palabras exactas que "
            "vas a decir."
        )
    sections.append(task)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n\n".join(sections)},
    ]


def translation_messages(
    text: str, target: str, source: str | None = None, context: str = ""
) -> list[dict[str, str]]:
    src = f" del {language_name(source)}" if source else ""
    system = (
        f"Eres un intérprete profesional. Traduce{src} al {language_name(target)} "
        "el texto que recibas, de forma fiel y natural. Devuelve SOLO la "
        "traducción, sin comillas, notas ni explicaciones. Si el texto ya está "
        "en el idioma de destino, devuélvelo igual."
    )
    user = text.strip()
    if context.strip():
        user = (
            f"(Contexto previo, NO lo traduzcas: {context.strip()[-600:]})\n\n"
            f"Texto a traducir:\n{text.strip()}"
        )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def summary_messages(transcript: str, language: str = "es") -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                f"Resume en {language_name(language)} la conversación transcrita. "
                "Estructura: temas tratados, decisiones o acuerdos, preguntas "
                "abiertas y próximos pasos. No inventes nada que no aparezca."
            ),
        },
        {"role": "user", "content": transcript.strip()},
    ]


CHALLENGE_SECTIONS = (
    "Identificación del problema",
    "Interpretación de la consigna",
    "Enfoque recomendado",
    "Plan de resolución",
    "Procedimiento paso a paso",
    "Código (si corresponde)",
    "Explicación técnica",
    "Resultado esperado",
    "Advertencias y posibles errores",
    "Alternativas",
)


def challenge_messages(
    instruction: str,
    *,
    ocr_text: str = "",
    knowledge: str = "",
    language: str = "es",
    images: list[str] | None = None,
) -> list[dict[str, object]]:
    sections = "\n".join(f"## {name}" for name in CHALLENGE_SECTIONS)
    system = (
        f"Eres un tutor experto. Analiza el desafío que aparece en la captura de "
        f"pantalla y responde en {language_name(language)} con Markdown usando "
        f"estas secciones (omite las que no apliquen):\n{sections}\n"
        "Usa bloques de código con el lenguaje indicado y LaTeX ($…$) para "
        "fórmulas. Si la imagen es ilegible o falta información, dilo."
    )
    parts = [instruction.strip() or "Resuelve el desafío de la captura."]
    if ocr_text.strip():
        parts.append(f"Texto reconocido por OCR:\n{ocr_text.strip()}")
    if knowledge.strip():
        parts.append(f"Material de referencia del usuario:\n{knowledge.strip()}")
    user: dict[str, object] = {"role": "user", "content": "\n\n".join(parts)}
    if images:
        user["images"] = list(images)
    return [{"role": "system", "content": system}, user]

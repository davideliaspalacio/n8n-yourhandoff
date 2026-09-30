"""Normaliza lo que devuelve GPT-4.1 en JSON mode.

JSON mode garantiza JSON válido, no un esquema: el modelo a veces responde
`{"summary": ...}`, a veces `{"briefing": ...}` o `{"result": ...}`. Esta es la
única función que decide cómo sacar el texto, en vez de las cuatro variantes
distintas que había repartidas por los Code nodes de n8n.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

PREFERRED_KEYS = ("summary", "briefing", "brief", "text", "content")


@dataclass(frozen=True)
class Summary:
    text: str
    # True si el modelo no usó la clave "summary" y hubo que adivinar.
    flagged: bool


def _pick(obj: dict) -> tuple[str, bool]:
    for key in PREFERRED_KEYS:
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip(), key != "summary"
    for value in obj.values():
        if isinstance(value, str) and value.strip():
            return value.strip(), True
    return "", True


def extract_summary(content: object) -> Summary:
    if content is None:
        return Summary("", True)
    if isinstance(content, dict):
        text, flagged = _pick(content)
        return Summary(text, flagged)
    if not isinstance(content, str):
        return Summary(str(content), True)
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return Summary(content.strip(), True)
    if isinstance(parsed, dict):
        text, flagged = _pick(parsed)
        return Summary(text or content.strip(), flagged)
    if isinstance(parsed, str):
        # Un string JSON ("\"Nido is...\""): el bug de las comillas en HubSpot.
        return Summary(parsed.strip(), True)
    return Summary(content.strip(), True)

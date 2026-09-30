"""Validación de direcciones y limpieza de correos de Unipile.

Porta los Code nodes "Validate Email", "Validate Input", "Parse External
Emails" y "Process & Clean Emails" de n8n, con el mismo comportamiento.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, datetime

from pydantic import BaseModel

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

# Invitación automática de Calendly para la Discovery Call: no es conversación.
DISCOVERY_CALL_SNIPPET = "Event Name: Discovery Call\nIn this 30-minute conversation"
CALENDAR_SUBJECT_PREFIXES = ("accepted:", "declined:", "tentative:", "invitation:")


def normalize_emails(raw: Iterable[object]) -> list[str]:
    """Recorta, pasa a minúsculas, descarta lo que no es un email y deduplica
    conservando el orden de llegada."""
    seen: dict[str, None] = {}
    for value in raw:
        if not isinstance(value, str):
            continue
        email = value.strip().lower()
        if EMAIL_RE.match(email):
            seen.setdefault(email, None)
    return list(seen)


def split_email_string(value: str) -> list[str]:
    """`"a@x.com, b@y.com"` → lista validada (entrada de Enrich External Attendees)."""
    return normalize_emails((value or "").split(","))


def external_emails(raw: Iterable[object], internal_domain: str) -> list[str]:
    """Como `normalize_emails`, sin las direcciones del propio equipo."""
    suffix = f"@{internal_domain.lower()}"
    return [e for e in normalize_emails(raw) if not e.endswith(suffix)]


def attendee_emails(primary_email: str | None, attendees: Iterable[dict]) -> list[str]:
    """Primario primero, luego los asistentes externos del evento, sin repetir."""
    raw: list[object] = [primary_email] if primary_email else []
    raw.extend(a.get("email") for a in attendees if isinstance(a, dict))
    return normalize_emails(raw)


# --- Limpieza de correos ----------------------------------------------------


class CleanEmail(BaseModel):
    message_id: str
    thread_id: str | None = None
    sender: str = ""
    recipients: str = ""
    cc_recipients: str = "None"
    subject: str = ""
    date_sent: str = ""
    body: str = ""


_ENTITIES = (
    ("&nbsp;", " "),
    ("&amp;", "&"),
    ("&lt;", "<"),
    ("&gt;", ">"),
    ("&quot;", '"'),
    ("&#39;", "'"),
)


def clean_text(text: str | None) -> str:
    """Quita HTML, normaliza saltos de línea y decodifica entidades comunes."""
    if not text:
        return ""
    text = re.sub(r"<[^>]*>", "", text)
    text = text.replace("\r\n", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" {2,}", " ", text)
    for entity, char in _ENTITIES:
        text = text.replace(entity, char)
    return text.strip()


_REPLY_CHAIN_RE = re.compile(r"\r?\nOn [\s\S]{0,150}?wrote:\s*", re.IGNORECASE)


def clean_body(body: str | None) -> str:
    """Se queda solo con el mensaje nuevo: corta la cadena citada ("On ... wrote:"),
    las líneas `>` y el ruido del cliente de correo."""
    if not body or not isinstance(body, str):
        return ""
    body = _REPLY_CHAIN_RE.split(body, maxsplit=1)[0]
    body = "\n".join(line for line in body.split("\n") if not line.strip().startswith(">"))
    body = re.sub(r"\[image:[^\]]*\]", "", body, flags=re.IGNORECASE)
    body = re.sub(r"^@\w+\s*$", "", body, flags=re.MULTILINE)
    body = re.sub(r"^www\.\S+\s*$", "", body, flags=re.MULTILINE)
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body.strip()


def _identifiers(attendees: object) -> str:
    if not isinstance(attendees, list):
        return ""
    return ", ".join(str(a.get("identifier", "")) for a in attendees if isinstance(a, dict))


def _parse_date(value: object) -> datetime:
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            pass
    return datetime.min.replace(tzinfo=UTC)


def clean_messages(raw_messages: Iterable[dict]) -> list[CleanEmail]:
    """Filtra invitaciones de calendario, limpia cuerpos, deduplica por
    `message_id` (se queda la copia más reciente, porque el mismo correo aparece
    en varios buzones del equipo) y ordena de más antiguo a más nuevo."""
    by_id: dict[str, tuple[datetime, CleanEmail]] = {}
    for msg in raw_messages:
        if not isinstance(msg, dict) or msg.get("object") != "Email":
            continue
        subject = msg.get("subject") or ""
        if subject.lower().startswith(CALENDAR_SUBJECT_PREFIXES):
            continue
        plain = msg.get("body_plain")
        raw_body = plain if isinstance(plain, str) and plain.strip() else (msg.get("body") or "")
        if DISCOVERY_CALL_SNIPPET in raw_body:
            continue
        key = msg.get("message_id") or msg.get("id")
        if not key:
            continue
        date = _parse_date(msg.get("date"))
        existing = by_id.get(key)
        if existing and date <= existing[0]:
            continue
        by_id[key] = (
            date,
            CleanEmail(
                message_id=key,
                thread_id=msg.get("thread_id"),
                sender=_identifiers([msg.get("from_attendee")] if msg.get("from_attendee") else []),
                recipients=_identifiers(msg.get("to_attendees")),
                cc_recipients=_identifiers(msg.get("cc_attendees")) or "None",
                subject=subject,
                date_sent=msg.get("date") or "",
                body=clean_body(clean_text(raw_body)),
            ),
        )
    return [email for _, email in sorted(by_id.values(), key=lambda pair: pair[0])]


def email_context(emails: Iterable[CleanEmail]) -> str:
    """Bloque de correos para el prompt, mismo formato que "Create Prompt"."""
    blocks = [
        f"\nDate: {e.date_sent}\nFrom: {e.sender}\nTo: {e.recipients}\n"
        f"Subject: {e.subject}\nBody: {e.body}\n---"
        for e in emails
        if e.body
    ]
    return "\n".join(blocks)

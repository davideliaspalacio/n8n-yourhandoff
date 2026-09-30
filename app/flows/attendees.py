"""Enrich External Attendees: Apollo people/match + bio para la llamada, por contacto.

Cada contacto se enriquece por separado y en paralelo, así su bio no puede
acabar pegada a otro contacto (el bug del índice en n8n) y un fallo en uno no
se lleva por delante a los demás.
"""

from __future__ import annotations

import asyncio
import logging

from pydantic import BaseModel, Field

from ..clients import Clients
from ..domain.emails import normalize_emails

logger = logging.getLogger(__name__)

PROMPT_TITLE = "Contact Analysis (Discovery Prep)"

# El dominio de un correo personal no dice nada del empleador.
GENERIC_DOMAINS = {
    "gmail.com",
    "googlemail.com",
    "outlook.com",
    "hotmail.com",
    "live.com",
    "yahoo.com",
    "icloud.com",
    "me.com",
    "aol.com",
    "proton.me",
    "protonmail.com",
    "msn.com",
    "ymail.com",
}


class ContactsRequest(BaseModel):
    emails: list[str] = Field(..., examples=[["emanuel@staynido.com"]])


class EnrichedContact(BaseModel):
    email: str = Field(description="The email that was looked up (source of truth).")
    enriched: bool = Field(description="Apollo found the person and a bio was generated.")
    name: str = ""
    first_name: str = ""
    last_name: str = ""
    title: str = ""
    linkedin_url: str = ""
    photo_url: str = ""
    twitter_url: str = ""
    facebook_url: str = ""
    city: str = ""
    state: str = ""
    country: str = ""
    ai_summary: str = ""
    ai_summary_flagged: bool = Field(
        False, description="The model did not use the expected `summary` key."
    )
    company_domain_mismatch: bool = False
    error: str | None = None
    llm: dict | None = None


def _normalize(text: str) -> str:
    return "".join(ch for ch in (text or "").lower() if ch.isalnum())


def domain_mismatch_note(email: str, company: str) -> str:
    """Aviso para el modelo cuando Apollo pone a la persona en una empresa que no
    cuadra con el dominio real de su correo (Apollo suele ir desactualizado)."""
    domain = email.split("@", 1)[1] if "@" in email else ""
    if not domain or domain in GENERIC_DOMAINS or company in ("", "N/A"):
        return ""
    core = domain.split(".")[0]
    n_company, n_domain = _normalize(company), _normalize(core)
    if n_company and n_domain and (n_domain in n_company or n_company in n_domain):
        return ""
    return (
        f'\n\n⚠️ DATA VERIFICATION NOTE: The contact\'s real email domain ("{domain}") does not '
        f'match the company returned by the data provider ("{company}"). Treat the '
        "employer/company as UNVERIFIED — do NOT state it as fact. The email domain suggests "
        f'the company may actually be related to "{core}". If unsure, omit the company rather '
        "than guessing."
    )


def build_prompt(base: str, email: str, person: dict) -> tuple[str, bool]:
    org = person.get("organization") or {}
    company = org.get("name") or person.get("company") or "N/A"
    city, state = person.get("city"), person.get("state")
    location = f"{city}, {state}" if city and state else (city or state or "N/A")
    history = [
        f"{i}. {job.get('title')} at {job.get('organization_name')}"
        + (" (Current)" if job.get("current") else "")
        for i, job in enumerate((person.get("employment_history") or [])[:5], 1)
    ]
    career = "Career History:\n" + "\n".join(history) if history else "Career history unavailable"
    note = domain_mismatch_note(email, company)
    prompt = (
        f"{base}\n\nContact: {person.get('name') or 'Unknown'}\n"
        f"Title: {person.get('title') or 'N/A'}\nCompany: {company}\n"
        f"Location: {location}\n{career}{note}"
    )
    return prompt, bool(note)


def _from_person(email: str, person: dict) -> dict:
    return {
        "email": email,
        "name": person.get("name") or "",
        "first_name": person.get("first_name") or "",
        "last_name": person.get("last_name") or "",
        "title": person.get("title") or "",
        "linkedin_url": (person.get("linkedin_url") or "").strip(),
        "photo_url": person.get("photo_url") or "",
        "twitter_url": person.get("twitter_url") or "",
        "facebook_url": person.get("facebook_url") or "",
        "city": person.get("city") or "",
        "state": person.get("state") or "",
        "country": person.get("country") or "",
    }


async def enrich_contact(clients: Clients, email: str, base_prompt: str) -> EnrichedContact:
    try:
        person = await clients.apollo.match_person(email)
    except Exception as exc:
        logger.warning("apollo failed for %s: %s", email, exc)
        return EnrichedContact(email=email, enriched=False, error=f"apollo: {exc}")
    if not person or not person.get("id") or not person.get("name"):
        # Sin coincidencia no se llama al modelo: no hay nada que resumir.
        return EnrichedContact(email=email, enriched=False)

    fields = _from_person(email, person)
    prompt, mismatch = build_prompt(base_prompt, email, person)
    try:
        result = await clients.llm.complete_json(prompt, stage="contact_summary")
    except Exception as exc:
        logger.warning("contact summary failed for %s: %s", email, exc)
        return EnrichedContact(
            **fields, enriched=False, company_domain_mismatch=mismatch, error=f"openai: {exc}"
        )
    return EnrichedContact(
        **fields,
        enriched=True,
        ai_summary=result.summary.text,
        ai_summary_flagged=result.summary.flagged,
        company_domain_mismatch=mismatch,
        llm=result.usage,
    )


async def enrich_contacts(clients: Clients, emails: list[str]) -> list[EnrichedContact]:
    valid = normalize_emails(emails)
    if not valid:
        return []
    base = await clients.alma.get_prompt(PROMPT_TITLE)
    return list(await asyncio.gather(*(enrich_contact(clients, e, base) for e in valid)))

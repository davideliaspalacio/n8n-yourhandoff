"""Search Team Communications: historial de Gmail del equipo + reuniones de
Fireflies con esos contactos, resumido por GPT-4.1.

Cambio respecto a n8n: Fireflies se consulta aunque no haya correos (antes solo
se miraba si había emails, y una reunión sin hilo de correo se perdía).
"""

from __future__ import annotations

import asyncio
import logging

from app.clients import Clients
from app.domain.emails import CleanEmail, clean_messages, email_context, external_emails
from app.flows.registry import FlowSpec
from app.runs import record_step

from .schemas import CommunicationsRequest, CommunicationsResult

logger = logging.getLogger(__name__)

PROMPT_TITLE = "Communications Analysis (Email)"
NO_COMMUNICATIONS = "No communications found"


async def _fetch_emails(clients: Clients, contacts: list[str]) -> list[CleanEmail]:
    accounts = await clients.alma.get_google_accounts()
    limit = clients.settings.unipile_emails_per_account
    results = await asyncio.gather(
        *(clients.unipile.list_emails(a["unipileAccountId"], contacts, limit) for a in accounts),
        return_exceptions=True,
    )
    raw: list[dict] = []
    failures = 0
    for account, result in zip(accounts, results, strict=True):
        if isinstance(result, BaseException):
            failures += 1
            logger.warning("unipile failed for account %s: %s", account.get("id"), result)
        else:
            raw.extend(result)
    if accounts and failures == len(accounts):
        raise RuntimeError("Unipile failed for every team account")
    return clean_messages(raw)


async def _fetch_meetings(clients: Clients, contacts: list[str]) -> list[str]:
    if not clients.settings.fireflies_api_key:
        return []
    try:
        return await clients.fireflies.short_summaries(
            contacts, clients.settings.fireflies_transcripts_per_email
        )
    except Exception as exc:
        # Las reuniones completan el contexto; sin ellas el resumen sigue valiendo.
        logger.warning("fireflies failed: %s", exc)
        await record_step("fireflies", "degraded", {"error": str(exc)})
        return []


def build_prompt(base: str, emails: list[CleanEmail], meetings: list[str]) -> str:
    meetings_block = ""
    if meetings:
        joined = "\n".join(f"\nMeeting {i}: {s}" for i, s in enumerate(meetings, 1))
        meetings_block = f"\nMEETING SUMMARIES:\n{joined}\n---"
    return (
        f"{base}\nEMAIL CONTEXT:\n{email_context(emails)}"
        f"\nMEETING TRANSCRIPT CONTEXT:\n{meetings_block}"
    )


async def search_team_communications(clients: Clients, emails: list[str]) -> CommunicationsResult:
    contacts = external_emails(emails, clients.settings.internal_email_domain)
    if not contacts:
        return CommunicationsResult(
            contacts=[],
            summary=NO_COMMUNICATIONS,
            has_emails=False,
            email_count=0,
            meeting_count=0,
        )

    found, meetings = await asyncio.gather(
        _fetch_emails(clients, contacts), _fetch_meetings(clients, contacts)
    )
    found = [e for e in found if e.body]
    if not found and not meetings:
        return CommunicationsResult(
            contacts=contacts,
            summary=NO_COMMUNICATIONS,
            has_emails=False,
            email_count=0,
            meeting_count=0,
        )

    base = await clients.alma.get_prompt(PROMPT_TITLE)
    result = await clients.llm.complete_json(
        build_prompt(base, found, meetings), stage="team_communications"
    )
    return CommunicationsResult(
        contacts=contacts,
        summary=result.summary.text or NO_COMMUNICATIONS,
        has_emails=bool(found),
        email_count=len(found),
        meeting_count=len(meetings),
        emails=found,
        meeting_summaries=meetings,
        llm=result.usage,
    )


async def run(clients: Clients, data: CommunicationsRequest) -> CommunicationsResult:
    return await search_team_communications(clients, data.emails)


spec = FlowSpec(
    slug="team-communications",
    title="Search Team Communications",
    input_model=CommunicationsRequest,
    response_model=CommunicationsResult,
    run=run,
    replaces="Search Team Communications (+ Fireflies)",
    summary="Search team communications (Gmail via Unipile + Fireflies) and summarize",
    graph=(
        {"id": "unipile", "label": "Gmail (Unipile)", "to": ("summary",)},
        {"id": "fireflies", "label": "Fireflies meetings", "to": ("summary",)},
        {"id": "summary", "label": "LLM summary", "to": ()},
    ),
)

"""Fireflies Summaries: short summaries de las reuniones más recientes con unos emails.

Sustituye al subflujo de n8n «Get Fireflies Short Summaries». La lógica vive en
`app.flows.team_communications.fetch_meetings`; team-communications usa la misma función.
"""

from __future__ import annotations

from app.clients import Clients
from app.flows.registry import FlowSpec
from app.flows.team_communications import fetch_meetings

from .schemas import FirefliesSummariesInput, FirefliesSummariesResult


async def run(clients: Clients, data: FirefliesSummariesInput) -> FirefliesSummariesResult:
    emails = [e.strip() for e in data.emails if e and e.strip()]
    # El step "fireflies" lo registra la función compartida.
    meetings = await fetch_meetings(clients, emails) if emails else []
    return FirefliesSummariesResult(meeting_summaries=meetings, meeting_count=len(meetings))


spec = FlowSpec(
    slug="fireflies-summaries",
    title="Fireflies Summaries",
    input_model=FirefliesSummariesInput,
    response_model=FirefliesSummariesResult,
    run=run,
    kind="node",
    mode="sync",
    replaces="Get Fireflies Short Summaries",
    summary="Get Fireflies short summaries of recent meetings with the given emails",
    description=(
        "Queries Fireflies for the most recent meetings that include each given email "
        "and returns their short summaries, deduplicated and ordered oldest first. It is "
        "the same function team-communications uses. A Fireflies failure (or a missing "
        "key) degrades to an empty list instead of failing the call."
    ),
    graph=(
        {
            "id": "fireflies",
            "label": "Fireflies meetings",
            "about": (
                "Fetches the latest Fireflies transcripts per email and keeps their short "
                "summaries. Degrades to an empty list if it fails."
            ),
            "to": (),
        },
    ),
)

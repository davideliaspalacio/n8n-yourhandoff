"""Modelos públicos del nodo fireflies-summaries."""

from __future__ import annotations

from pydantic import BaseModel, Field


class FirefliesSummariesInput(BaseModel):
    emails: list[str] = Field(
        ..., examples=[["emanuel@staynido.com"]], description="Participant emails to look up."
    )


class FirefliesSummariesResult(BaseModel):
    meeting_summaries: list[str] = Field(
        description=(
            "Fireflies short summaries of the most recent meetings with the given emails "
            "(up to FIREFLIES_TRANSCRIPTS_PER_EMAIL each), deduplicated across emails and "
            "ordered oldest first. Same shape team-communications feeds its prompt with."
        )
    )
    meeting_count: int = Field(description="Number of summaries returned.")

"""Modelos públicos del flow team_communications."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.domain.emails import CleanEmail


class CommunicationsRequest(BaseModel):
    emails: list[str] = Field(
        ...,
        description="Contact emails. Internal team addresses are ignored.",
        examples=[["ana.gazarian@ems-ir.com", "patricia.raventos@ems-ir.com"]],
    )


class CommunicationsResult(BaseModel):
    contacts: list[str] = Field(description="Validated external emails that were searched.")
    summary: str
    has_emails: bool
    email_count: int
    meeting_count: int
    emails: list[CleanEmail] = []
    meeting_summaries: list[str] = []
    llm: dict | None = Field(None, description="Model usage and cost, when the model was called.")

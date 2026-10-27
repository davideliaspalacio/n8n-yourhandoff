"""Modelos públicos del flow attendees."""

from __future__ import annotations

from pydantic import BaseModel, Field


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

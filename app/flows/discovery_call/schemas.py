"""Modelos públicos del flow discovery_call."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class GuestEmail(BaseModel):
    model_config = ConfigDict(extra="allow")

    email: str
    name: str = ""
    firstName: str = ""
    lastName: str = ""


class DiscoveryCallInput(BaseModel):
    companyName: str = ""
    primaryContactName: str = ""
    primaryContactEmail: str
    companyUrl: str = ""
    additionalNotes: str = ""
    guestEmails: list[GuestEmail] = []
    eventTitle: str = ""
    organizerEmail: str = ""
    eventTypeUri: str = ""
    dry_run: bool = Field(
        False,
        description=(
            "Research everything but write nothing outside: skips the event edge function, "
            "HubSpot and Slack, and returns the Slack messages that would have been sent."
        ),
    )
    # Solo se usan con dry_run, en lugar de lo que devolvería la edge function.
    eventStartDateTime: str | None = Field(None, description="dry_run only (ISO 8601).")
    eventTimeZone: str | None = Field(None, description="dry_run only (IANA zone).")

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "companyName": "Nido",
                    "primaryContactName": "Emanuel Ituarte",
                    "primaryContactEmail": "emanuel@staynido.com",
                    "companyUrl": "staynido.com",
                    "guestEmails": [
                        {
                            "email": "emanuel@staynido.com",
                            "firstName": "Emanuel",
                            "lastName": "Ituarte",
                        }
                    ],
                    "eventTitle": "Nido + Handoff | Discovery Call",
                    "organizerEmail": "david@yourhandoff.com",
                    "dry_run": True,
                }
            ]
        }
    )


class DiscoveryCallResult(BaseModel):
    event: dict[str, Any]
    attendee_emails: list[str]
    company: dict[str, Any]
    contacts: list[dict[str, Any]]
    communications_summary: str
    hubspot: dict[str, Any]
    slack: dict[str, Any]

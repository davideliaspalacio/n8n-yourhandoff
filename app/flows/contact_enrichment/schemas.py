"""Modelos públicos del flow contact-enrichment."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from app.flows.crm import ContactCrmInput, ContactCrmResult


class ContactEnrichmentInput(BaseModel):
    email: str = Field(..., examples=["emanuel@staynido.com"], description="Contact to enrich.")
    companyHubspotId: str = Field("", description="Associates the contact to this HubSpot company.")
    dry_run: bool = Field(
        False, description="Enrich but do not write to HubSpot; returns would_upsert instead."
    )

    @field_validator("email")
    @classmethod
    def _email_required(cls, value: str) -> str:
        # Paridad con el Input Mapper de n8n: trim y error si queda vacío.
        value = value.strip()
        if not value:
            raise ValueError("email is required to enrich a contact.")
        return value


class ContactEnrichmentResult(BaseModel):
    contact: ContactCrmResult | None = Field(None, description="HubSpot upsert result.")
    enriched: bool = Field(description="Apollo found the person and a bio was generated.")
    dry_run: bool = False
    would_upsert: ContactCrmInput | None = Field(
        None, description="Only in dry_run: the payload that would have been sent to HubSpot."
    )

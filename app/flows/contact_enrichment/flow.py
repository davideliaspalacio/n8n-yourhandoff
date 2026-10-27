"""Contact Enrichment: enriquece un contacto por email (Apollo + bio con LLM) y lo
upsertea en HubSpot, asociándolo a su empresa si se indica.

Sustituye al workflow de n8n «Contact Enrichment Webhook».
"""

from __future__ import annotations

import logging

from app.clients import Clients
from app.flows.attendees import EnrichedContact, enrich_contacts
from app.flows.crm import ContactCrmInput, upsert_contact
from app.flows.registry import FlowSpec
from app.runs import record_step

from .schemas import ContactEnrichmentInput, ContactEnrichmentResult

logger = logging.getLogger(__name__)


def to_crm_input(enriched: EnrichedContact, data: ContactEnrichmentInput) -> ContactCrmInput:
    """Port del Code «Map To Contact CRM» de n8n."""
    return ContactCrmInput(
        email=enriched.email or data.email,
        firstName=enriched.first_name,
        lastName=enriched.last_name,
        title=enriched.title,
        city=enriched.city,
        state=enriched.state,
        country=enriched.country,
        linkedinUrl=enriched.linkedin_url,
        # EnrichedContact no trae el uid de LinkedIn: igual que el fallback de n8n.
        linkedinUid="",
        aiContactSummary=enriched.ai_summary,
        companyHubspotId=data.companyHubspotId,
    )


async def run(clients: Clients, data: ContactEnrichmentInput) -> ContactEnrichmentResult:
    # 1. Enriquecer (sus steps y costes caen en este run vía ContextVar).
    found = await enrich_contacts(clients, [data.email])
    # Sin resultado válido se sigue con lo que haya: n8n también upsertea vacío.
    enriched = found[0] if found else EnrichedContact(email=data.email, enriched=False)
    await record_step("enrich", "succeeded", {"enriched": enriched.enriched})

    # 2. Mapear al input de Contact CRM.
    payload = to_crm_input(enriched, data)

    # 3. Upsert en HubSpot (o solo reportar en dry_run).
    if data.dry_run:
        await record_step("crm_upsert", "skipped", {"email": payload.email})
        return ContactEnrichmentResult(
            enriched=enriched.enriched, dry_run=True, would_upsert=payload
        )
    contact = await upsert_contact(clients, payload)
    await record_step(
        "crm_upsert",
        "succeeded",
        {"hubspot_id": contact.hubspotId, "associated": contact.associatedCompany},
    )
    return ContactEnrichmentResult(contact=contact, enriched=enriched.enriched)


spec = FlowSpec(
    slug="contact-enrichment",
    kind="workflow",
    title="Contact Enrichment",
    input_model=ContactEnrichmentInput,
    response_model=ContactEnrichmentResult,
    run=run,
    mode="sync",
    replaces="Contact Enrichment Webhook",
    description=(
        "Enriches a contact from its email by matching it in Apollo and writing a short "
        "bio with an LLM, then upserts the contact in HubSpot and associates it to the "
        "given company. Like the n8n original, it upserts even when nothing is found, "
        "using whatever fields it has. dry_run enriches but skips the HubSpot write and "
        "returns the payload it would have sent."
    ),
    summary="Enrich a contact by email and upsert it in HubSpot",
    graph=(
        {
            "id": "enrich",
            "label": "Enrich contact",
            "to": ("crm_upsert",),
            "about": (
                "Runs the attendee enrichment for the email (Apollo match plus LLM bio) and "
                "maps the result to the CRM contact fields."
            ),
        },
        {
            "id": "crm_upsert",
            "label": "HubSpot upsert",
            "to": (),
            "about": (
                "Upserts the contact in HubSpot by email, associates it to the company and "
                "posts the bio as a note. In dry_run this step is skipped."
            ),
        },
    ),
)

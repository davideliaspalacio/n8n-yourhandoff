"""Company CRM Add/Update, Contact CRM Add/Update y Post Note to Hubspot.

Los modelos de entrada conservan los nombres de campo de n8n para que quien
llamaba al webhook viejo pueda llamar a este endpoint igual.
"""

from __future__ import annotations

import logging

from app.clients import Clients
from app.domain.industry import to_hubspot_industry
from app.domain.naming import domain_of
from app.flows.registry import FlowSpec

from .schemas import (
    CompanyCrmInput,
    CompanyCrmResult,
    ContactCrmInput,
    ContactCrmResult,
    NoteInput,
    NoteResult,
)

logger = logging.getLogger(__name__)


def company_properties(data: CompanyCrmInput) -> tuple[dict, str | None]:
    industry = to_hubspot_industry(data.industry)
    props = {
        "name": data.companyName,
        "domain": domain_of(data.websiteUrl),
        "website": data.websiteUrl,
        "linkedin_company_page": data.linkedinUrl,
        "facebook_company_page": data.facebookUrl,
        "phone": data.primaryPhoneNumber,
        "industry": industry,
        "address": data.streetAddress,
        "city": data.city,
        "state": data.state,
        "country": data.country,
        "zip": data.postalCode,
        "about_us": data.aboutUs,
        "numberofemployees": data.employeeCount,
        "annualrevenue": data.annualRevenue,
        "founded_year": data.yearFounded,
        # Propiedades personalizadas del portal de Handoff.
        "ai_summary": data.aiSummary,
        "linkedin_uid": data.linkedinUid,
        "twitter_url": data.twitterUrl,
        "crunchbase_url": data.crunchbaseUrl,
        "headcount_growth_6mo": data.headcountGrowth6Months,
        "headcount_growth_12mo": data.headcountGrowth12Months,
        "headcount_growth_24mo": data.headcountGrowth24Months,
    }
    return props, industry


async def _note(clients: Clients, body: str, kind: str, object_id: str) -> str | None:
    if not body.strip():
        return None
    try:
        return await clients.hubspot.create_note(body, kind, object_id)  # type: ignore[arg-type]
    except Exception as exc:
        # La nota es un extra: el registro ya está guardado.
        logger.warning("hubspot note on %s %s failed: %s", kind, object_id, exc)
        return None


async def upsert_company(clients: Clients, data: CompanyCrmInput) -> CompanyCrmResult:
    props, industry = company_properties(data)
    existing = await clients.hubspot.find_company_by_domain(props["domain"])
    if existing:
        company_id = await clients.hubspot.update_company(existing, props)
    else:
        company_id = await clients.hubspot.create_company(props)
    note_id = await _note(clients, data.aiSummary, "company", company_id)
    return CompanyCrmResult(
        hubspotId=company_id,
        name=data.companyName,
        created=existing is None,
        industryHubspot=industry,
        aiNoteSuccess=note_id is not None,
        noteId=note_id,
    )


async def upsert_contact(clients: Clients, data: ContactCrmInput) -> ContactCrmResult:
    props = {
        "firstname": data.firstName,
        "lastname": data.lastName,
        "jobtitle": data.title,
        "city": data.city,
        "state": data.state,
        "country": data.country,
        clients.settings.hubspot_contact_linkedin_property: data.linkedinUrl.strip(),
        "ai_contact_summary": data.aiContactSummary,
        "linkedin_uid": data.linkedinUid,
    }
    contact_id = await clients.hubspot.upsert_contact(data.email.strip().lower(), props)
    associated = False
    if data.companyHubspotId:
        try:
            await clients.hubspot.associate_contact_company(contact_id, data.companyHubspotId)
            associated = True
        except Exception as exc:
            logger.warning("associating contact %s failed: %s", contact_id, exc)
    note_id = await _note(clients, data.aiContactSummary, "contact", contact_id)
    return ContactCrmResult(
        hubspotId=contact_id,
        email=data.email,
        associatedCompany=associated,
        aiNoteSuccess=note_id is not None,
        noteId=note_id,
    )


async def run_company_crm(clients: Clients, data: CompanyCrmInput) -> CompanyCrmResult:
    return await upsert_company(clients, data)


async def run_contact_crm(clients: Clients, data: ContactCrmInput) -> ContactCrmResult:
    return await upsert_contact(clients, data)


async def run_note(clients: Clients, data: NoteInput) -> NoteResult:
    # Antes vivía inline en routes.py; ahora es un flow como los demás.
    note_id = await clients.hubspot.create_note(
        data.content, data.contact_company_deal, data.associated_hubspot_id
    )
    return NoteResult(noteId=note_id)


specs = (
    FlowSpec(
        slug="crm-company",
        kind="node",
        title="Company CRM Add/Update",
        input_model=CompanyCrmInput,
        response_model=CompanyCrmResult,
        run=run_company_crm,
        replaces="Company CRM Add/Update",
        summary="Create or update a HubSpot company (matched by domain) and post its AI note",
        graph=(
            {"id": "crm-company", "label": "Upsert company", "to": ()},
        ),
    ),
    FlowSpec(
        slug="crm-contact",
        kind="node",
        title="Contact CRM Add/Update",
        input_model=ContactCrmInput,
        response_model=ContactCrmResult,
        run=run_contact_crm,
        replaces="Contact CRM Add/Update",
        summary="Create or update a HubSpot contact (by email) and link it to a company",
        graph=(
            {"id": "crm-contact", "label": "Upsert contact", "to": ()},
        ),
    ),
    FlowSpec(
        slug="crm-note",
        kind="node",
        title="Post Note to HubSpot",
        input_model=NoteInput,
        response_model=NoteResult,
        run=run_note,
        replaces="Post Note to Hubspot",
        summary="Post a note to a HubSpot company or contact",
        graph=(
            {"id": "crm-note", "label": "Post note", "to": ()},
        ),
    ),
)

"""Company CRM Add/Update, Contact CRM Add/Update y Post Note to Hubspot.

Los modelos de entrada conservan los nombres de campo de n8n para que quien
llamaba al webhook viejo pueda llamar a este endpoint igual.
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, Field

from ..clients import Clients
from ..domain.industry import to_hubspot_industry
from ..domain.naming import domain_of

logger = logging.getLogger(__name__)


class CompanyCrmInput(BaseModel):
    companyName: str = Field(..., examples=["Nido"])
    websiteUrl: str = Field("", examples=["http://www.staynido.com"])
    linkedinUrl: str = ""
    linkedinUid: str = ""
    twitterUrl: str = ""
    facebookUrl: str = ""
    primaryPhoneNumber: str = ""
    industry: str = Field("", description="Free text; mapped to HubSpot's industry enum.")
    streetAddress: str = ""
    city: str = ""
    state: str = ""
    country: str = ""
    postalCode: str = ""
    aboutUs: str = ""
    aiSummary: str = Field("", description="Also posted as a note on the company.")
    employeeCount: int | None = None
    annualRevenue: float | None = None
    crunchbaseUrl: str = ""
    headcountGrowth6Months: float | None = None
    headcountGrowth12Months: float | None = None
    headcountGrowth24Months: float | None = None
    yearFounded: int | None = None


class CompanyCrmResult(BaseModel):
    hubspotId: str
    name: str
    created: bool
    industryHubspot: str | None = None
    aiNoteSuccess: bool
    noteId: str | None = None


class ContactCrmInput(BaseModel):
    email: str = Field(..., examples=["emanuel@staynido.com"])
    firstName: str = ""
    lastName: str = ""
    title: str = ""
    city: str = ""
    state: str = ""
    country: str = ""
    linkedinUrl: str = ""
    linkedinUid: str = ""
    aiContactSummary: str = Field("", description="Also posted as a note on the contact.")
    companyHubspotId: str = Field("", description="Associates the contact to this company.")


class ContactCrmResult(BaseModel):
    hubspotId: str
    email: str
    associatedCompany: bool
    aiNoteSuccess: bool
    noteId: str | None = None


class NoteInput(BaseModel):
    content: str
    associated_hubspot_id: str
    contact_company_deal: Literal["company", "contact"]


class NoteResult(BaseModel):
    noteId: str


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

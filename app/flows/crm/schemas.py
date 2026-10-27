"""Modelos públicos del flow crm."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


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

"""Modelos públicos del flow company."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CompanyRequest(BaseModel):
    companyUrl: str = Field(..., examples=["staynido.com"])


class CompanyEnrichment(BaseModel):
    model_config = ConfigDict(extra="allow")

    foundCompany: bool
    Company: dict[str, Any]
    linkedIn: dict[str, Any]
    crm: dict[str, Any]
    industryHubspot: str | None = None
    industryMatched: bool = False
    industryRaw: str = ""

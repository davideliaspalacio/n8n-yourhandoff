"""Modelos públicos del nodo linkedin-data."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class LinkedinDataInput(BaseModel):
    companyUrl: str = Field(
        ..., examples=["staynido.com"], description="Company domain or URL."
    )

    @field_validator("companyUrl")
    @classmethod
    def _url_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("companyUrl is required.")
        return value


class LinkedinDataResult(BaseModel):
    foundLinkedIn: bool = Field(description="Apollo found the organization.")
    linkedIn: dict[str, Any] = Field(description="LinkedIn-style block (name, industry, size...).")
    crm: dict[str, Any] = Field(description="CRM-ready company properties.")

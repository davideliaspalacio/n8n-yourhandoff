"""Modelos públicos del nodo news-jobs-search."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class NewsJobsSearchInput(BaseModel):
    companyName: str = Field(..., examples=["Nido"], description="Company name to search for.")
    companyUrl: str = Field(
        "", description="Optional company URL; its name is used when companyName is blank."
    )


class NewsJobsSearchResult(BaseModel):
    google_news: list[dict[str, Any]] = Field(description="Google News results (SerpApi).")
    google_jobs: list[dict[str, Any]] = Field(description="Google Jobs results (SerpApi).")

"""News + Job Search: noticias y empleos de Google (SerpApi) sobre una empresa.

Sustituye al subflujo de n8n «News + Job Search». La lógica vive en
`app.flows.company.news_jobs_search`; enrich-company usa la misma función.
"""

from __future__ import annotations

from app.clients import Clients
from app.domain.naming import company_name_from_url
from app.flows.company import news_jobs_search
from app.flows.registry import FlowSpec

from .schemas import NewsJobsSearchInput, NewsJobsSearchResult


async def run(clients: Clients, data: NewsJobsSearchInput) -> NewsJobsSearchResult:
    # Como enrich-company: sin nombre se deriva de la URL. Los steps los registra
    # la función compartida.
    name = data.companyName.strip() or company_name_from_url(data.companyUrl)
    news, jobs = await news_jobs_search(clients, name)
    return NewsJobsSearchResult(google_news=news, google_jobs=jobs)


spec = FlowSpec(
    slug="news-jobs-search",
    title="News + Job Search",
    input_model=NewsJobsSearchInput,
    response_model=NewsJobsSearchResult,
    run=run,
    kind="node",
    mode="sync",
    replaces="News + Job Search",
    summary="Search Google News and Google Jobs for a company (SerpApi)",
    description=(
        "Searches Google News and Google Jobs through SerpApi for a company name, in "
        "parallel. It is the same function enrich-company uses. A SerpApi failure (or a "
        "missing key) degrades that list to empty instead of failing the call."
    ),
    graph=(
        {
            "id": "serpapi_google_news",
            "label": "Google News",
            "about": (
                "Searches Google News via SerpApi for recent press about the company. "
                "Degrades to an empty list if it fails."
            ),
            "to": (),
        },
        {
            "id": "serpapi_google_jobs",
            "label": "Google Jobs",
            "about": (
                "Searches Google Jobs via SerpApi for the company's open positions. "
                "Degrades to an empty list if it fails."
            ),
            "to": (),
        },
    ),
)

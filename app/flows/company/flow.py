"""Enrich Company: datos de Apollo + noticias y empleos de Google + resumen GPT-4.1.

Sustituye también a los subflujos "Get LinkedIn Data" (ahora Apollo
organizations/enrich) y "News + Job Search" (ahora SerpApi).

La respuesta conserva la forma del webhook de n8n (`Company`, `linkedIn`,
`crm`) porque el Founders Club Agent ya la consume: puede cambiar de URL sin
tocar su parser. Los campos que Apollo no da (decisores, seguidores de
LinkedIn, evolución mensual) simplemente no aparecen.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from app.clients import Clients
from app.domain.industry import to_hubspot_industry
from app.domain.naming import company_name_from_url, domain_of
from app.flows.registry import FlowSpec
from app.runs import record_step

from .schemas import CompanyEnrichment, CompanyRequest

logger = logging.getLogger(__name__)

PROMPT_TITLE = "Company Analysis (Discovery Prep)"
JSON_INSTRUCTION = (
    'Return ONLY a valid JSON object with exactly one key named "summary" whose value is the '
    'summary text as a string. Do not use any other key name. Example: {"summary": "..."}'
)
GROWTH_WINDOWS = (
    (6, "organization_headcount_six_month_growth"),
    (12, "organization_headcount_twelve_month_growth"),
    (24, "organization_headcount_twenty_four_month_growth"),
)


def _growth(org: dict) -> list[dict]:
    """Apollo da el crecimiento como fracción (0.128); n8n y HubSpot usan %."""
    out = []
    for months, key in GROWTH_WINDOWS:
        value = org.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out.append({"monthRange": months, "growthPercentage": round(value * 100, 4)})
    return out


def _phone(org: dict) -> str:
    primary = org.get("primary_phone") or {}
    return primary.get("sanitized_number") or primary.get("number") or org.get("phone") or ""


def linkedin_uid(org: dict) -> str:
    return str(org.get("linkedin_uid") or "")


def map_organization(org: dict, company_url: str) -> tuple[dict, dict]:
    """Organización de Apollo → bloques `linkedIn` y `crm` del contrato de n8n."""
    name = org.get("name") or ""
    employees = org.get("estimated_num_employees")
    linkedin = {
        "name": name,
        "industry": org.get("industry") or "",
        "employeeCount": employees,
        "locationCity": org.get("city") or "",
        "locationState": org.get("state") or "",
        "locationCountry": org.get("country") or "",
        "description": org.get("short_description") or "",
        "website": org.get("website_url") or "",
        "linkedinUrl": org.get("linkedin_url") or "",
    }
    crm = {
        "companyName": name,
        "companyWebsiteUrl": org.get("website_url") or company_url,
        "companyLinkedinUrl": org.get("linkedin_url") or "",
        "companyLinkedinUid": linkedin_uid(org),
        "companyTwitterUrl": org.get("twitter_url") or "",
        "companyFacebookUrl": org.get("facebook_url") or "",
        "companyPrimaryPhoneNumber": _phone(org),
        "companyIndustry": org.get("industry") or "",
        "companyStreetAddress": org.get("street_address") or "",
        "companyCity": org.get("city") or "",
        "companyState": org.get("state") or "",
        "companyCountry": org.get("country") or "",
        "companyPostalCode": org.get("postal_code") or "",
        "companyAboutUs": org.get("short_description") or "",
        "companyEmployeeCount": employees,
        "companyAnnualRevenue": org.get("annual_revenue"),
        "companyYearFounded": org.get("founded_year"),
        "companyCrunchbaseUrl": org.get("crunchbase_url") or "",
        "companyGrowthMetrics": _growth(org),
    }
    return linkedin, crm


def build_prompt(base: str, linkedin: dict, news: list[dict], jobs: list[dict]) -> str:
    city, state = linkedin.get("locationCity"), linkedin.get("locationState")
    employees = linkedin.get("employeeCount")
    news_info = f"{len(news)} recent articles found" if news else "No recent news"
    if news:
        titles = "; ".join(n.get("title", "") for n in news[:5] if n.get("title"))
        news_info += f": {titles}" if titles else ""
    jobs_info = (
        f"{len(jobs)} job openings: " + ", ".join(j.get("title", "") for j in jobs)
        if jobs
        else "No job postings"
    )
    return (
        f"{base}\n\nCompany: {linkedin.get('name') or 'Unknown Company'}\n"
        f"Industry: {linkedin.get('industry') or 'N/A'}\n"
        f"Location: {f'{city}, {state}' if city and state else 'N/A'}\n"
        f"Employees: {employees if employees else 'N/A'}\n"
        f"Website: {linkedin.get('website') or 'N/A'}\n"
        f"Overview: {linkedin.get('description') or 'N/A'}\n"
        "Decision Makers: No decision makers identified\n"
        f"News: {news_info}\nJobs: {jobs_info}\n\n{JSON_INSTRUCTION}"
    )


async def _search(clients: Clients, engine: str, query: str) -> list[dict]:
    if not clients.settings.serpapi_api_key or not query:
        return []
    try:
        return await clients.serpapi.search(engine, query)
    except Exception as exc:
        logger.warning("serpapi %s failed: %s", engine, exc)
        await record_step(f"serpapi_{engine}", "degraded", {"error": str(exc)})
        return []


async def enrich_company(clients: Clients, company_url: str) -> CompanyEnrichment:
    domain = domain_of(company_url)
    org = await clients.apollo.enrich_organization(domain) if domain else None
    linkedin, crm = map_organization(org or {}, company_url)
    name = linkedin["name"] or company_name_from_url(company_url)

    news, jobs = await asyncio.gather(
        _search(clients, "google_news", f'"{name}"' if name else ""),
        _search(clients, "google_jobs", name),
    )

    base = await clients.alma.get_prompt(PROMPT_TITLE)
    result = await clients.llm.complete_json(
        build_prompt(base, linkedin, news, jobs), stage="company_summary"
    )
    crm["aiSummary"] = {"summary": result.summary.text}

    raw_industry = crm["companyIndustry"]
    hubspot_industry = to_hubspot_industry(raw_industry)
    departmental = {
        k: v
        for k, v in ((org or {}).get("departmental_head_count") or {}).items()
        if isinstance(v, (int, float)) and v > 0
    }
    return CompanyEnrichment(
        foundCompany=bool(org),
        Company={
            "annualRevenue": (org or {}).get("annual_revenue_printed") or "",
            "departmentalHeadCount": departmental,
            "googleNews": news,
            "googleJobs": jobs,
            "prompt": {**result.usage, "prompt": result.prompt},
            "generatedAt": datetime.now(UTC).isoformat(),
        },
        linkedIn=linkedin,
        crm=crm,
        industryHubspot=hubspot_industry,
        industryMatched=hubspot_industry is not None,
        industryRaw=raw_industry,
    )


async def run(clients: Clients, data: CompanyRequest) -> CompanyEnrichment:
    return await enrich_company(clients, data.companyUrl)


spec = FlowSpec(
    slug="enrich-company",
    kind="node",
    title="Enrich Company",
    input_model=CompanyRequest,
    response_model=CompanyEnrichment,
    run=run,
    replaces="Enrich Company (+ Get LinkedIn Data, News + Job Search)",
    description=(
        "Enriches a company from its URL using Apollo organization data, Google News and "
        "Google Jobs results (via SerpApi) and an LLM-generated JSON summary. SerpApi "
        "failures degrade without breaking the flow. It keeps the response shape of the "
        "old n8n webhook (Company, linkedIn, crm) so existing callers keep working, and "
        "it is reused by the discovery call workflow."
    ),
    summary="Enrich a company (Apollo, Google News, Google Jobs) and summarize it",
    graph=(
        {
            "id": "apollo",
            "label": "Apollo organization",
            "to": ("serpapi_google_news", "serpapi_google_jobs"),
        },
        {"id": "serpapi_google_news", "label": "Google News", "to": ("summary",)},
        {"id": "serpapi_google_jobs", "label": "Google Jobs", "to": ("summary",)},
        {"id": "summary", "label": "LLM summary", "to": ()},
    ),
)

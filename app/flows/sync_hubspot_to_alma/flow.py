"""Sync HubSpot to Alma: contactos modificados en HubSpot y sus empresas hacia el
Supabase de Alma (upsert por `hubspot_id`).

Sustituye al workflow de n8n «Sync Hubspot to Alma».
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.clients import Clients
from app.flows.registry import FlowSpec
from app.runs import record_step

from .schemas import SyncHubspotToAlmaInput, SyncHubspotToAlmaResult, since_to_epoch_ms

logger = logging.getLogger(__name__)

# Cuentas de prueba/integración que no deben sincronizarse (ampliable).
EXCLUDED_EMAILS = frozenset({"fred@fireflies.ai"})
CONTACT_PROPERTIES = [
    "email",
    "firstname",
    "lastname",
    "jobtitle",
    "phone",
    "lastmodifieddate",
    "associatedcompanyid",
]
COMPANY_PROPERTIES = ["name", "domain", "hs_logo_url"]


def _is_test_account(email: str | None) -> bool:
    return not email or email.strip().lower() in EXCLUDED_EMAILS


async def run(clients: Clients, data: SyncHubspotToAlmaInput) -> SyncHubspotToAlmaResult:
    result = SyncHubspotToAlmaResult(dry_run=data.dry_run)
    since_ms = since_to_epoch_ms(data.since)

    # 1. Búsqueda en HubSpot; si falla, el run falla (nada que hacer sin contactos).
    contacts = await clients.hubspot.search_contacts_modified_since(
        since_ms, properties=CONTACT_PROPERTIES
    )
    result.contacts_found = len(contacts)
    await record_step("hubspot_search", "succeeded", {"found": len(contacts), "since_ms": since_ms})

    # 2-3. Filtros: cuentas de prueba y contactos sin empresa.
    candidates: list[dict] = []
    for contact in contacts:
        props = contact.get("properties") or {}
        if _is_test_account(props.get("email")):
            result.skipped_test_accounts += 1
        elif not props.get("associatedcompanyid"):
            result.skipped_no_company += 1
        else:
            candidates.append(contact)
    await record_step(
        "filter",
        "succeeded",
        {
            "skipped_test_accounts": result.skipped_test_accounts,
            "skipped_no_company": result.skipped_no_company,
            "remaining": len(candidates),
        },
    )

    # 4-5. Empresa de HubSpot por contacto (una lectura por empresa distinta).
    company_cache: dict[str, dict | None] = {}
    cards: list[tuple[dict, dict]] = []
    for contact in candidates:
        props = contact["properties"]
        company_id = str(props["associatedcompanyid"])
        if company_id not in company_cache:
            try:
                company_cache[company_id] = await clients.hubspot.get_company(
                    company_id, properties=COMPANY_PROPERTIES
                )
            except Exception as exc:
                logger.warning("hubspot company %s failed: %s", company_id, exc)
                await record_step(
                    "hubspot_company", "degraded", {"company_id": company_id, "error": str(exc)}
                )
                company_cache[company_id] = {"_error": True}
        company = company_cache[company_id]
        if company is None:
            result.skipped_no_company += 1
            continue
        if company.get("_error"):
            result.skipped_errors += 1
            continue
        cprops = company.get("properties") or {}
        if not (cprops.get("name") or "").strip():
            result.skipped_no_company_name += 1
            continue
        contact_row = {
            "hubspot_id": str(contact["id"]),
            "email": props.get("email"),
            "first_name": props.get("firstname"),
            "last_name": props.get("lastname"),
            "title": props.get("jobtitle"),
            "phone": props.get("phone"),
        }
        company_row = {
            "hubspot_id": company_id,
            "company_name": cprops["name"],
            "url": cprops.get("domain"),
            "logo_url": cprops.get("hs_logo_url"),
        }
        cards.append((contact_row, company_row))

    # Dedupe de empresas por hubspot_id.
    companies = {c["hubspot_id"]: c for _, c in cards}
    now = datetime.now(UTC).isoformat()

    # 6. Upsert de empresas.
    failed_companies: set[str] = set()
    if data.dry_run:
        result.would_upsert_companies = [c["company_name"] for c in companies.values()]
        await record_step("companies_upsert", "skipped", {"would_upsert": len(companies)})
    else:
        for hubspot_id, row in companies.items():
            try:
                await clients.alma.upsert(
                    "companies", {**row, "last_synced": now}, on_conflict="hubspot_id"
                )
                result.companies_upserted += 1
            except Exception as exc:
                logger.warning("alma company %s failed: %s", hubspot_id, exc)
                failed_companies.add(hubspot_id)
                await record_step(
                    "companies_upsert", "degraded", {"hubspot_id": hubspot_id, "error": str(exc)}
                )
        await record_step("companies_upsert", "succeeded", {"upserted": result.companies_upserted})

    # 7. Upsert de contactos y su vínculo con la empresa.
    if data.dry_run:
        result.would_upsert_contacts = [c["email"] for c, _ in cards]
        await record_step("contacts_upsert", "skipped", {"would_upsert": len(cards)})
        return result

    for contact_row, company_row in cards:
        if company_row["hubspot_id"] in failed_companies:
            result.skipped_errors += 1
            continue
        try:
            found = await clients.alma.find("companies", hubspot_id=company_row["hubspot_id"])
            company_id = found[0]["id"] if found else None
            saved = await clients.alma.upsert(
                "contacts", {**contact_row, "last_synced": now}, on_conflict="hubspot_id"
            )
            result.contacts_upserted += 1
            if company_id is None or saved.get("id") is None:
                result.links_skipped += 1
                continue
            link = await clients.alma.insert(
                "contact_company", {"contact_id": saved["id"], "company_id": company_id}
            )
            # Vínculo ya existente: se ignora, como el continueRegularOutput de n8n.
            if link is None:
                result.links_skipped += 1
            else:
                result.links_upserted += 1
        except Exception as exc:
            logger.warning("alma contact %s failed: %s", contact_row["hubspot_id"], exc)
            result.skipped_errors += 1
            await record_step(
                "contacts_upsert",
                "degraded",
                {"hubspot_id": contact_row["hubspot_id"], "error": str(exc)},
            )
    await record_step(
        "contacts_upsert",
        "succeeded",
        {
            "contacts_upserted": result.contacts_upserted,
            "links_upserted": result.links_upserted,
            "links_skipped": result.links_skipped,
        },
    )
    return result


spec = FlowSpec(
    slug="sync-hubspot-to-alma",
    title="Sync HubSpot to Alma",
    input_model=SyncHubspotToAlmaInput,
    response_model=SyncHubspotToAlmaResult,
    run=run,
    mode="async",
    replaces="Sync Hubspot to Alma",
    summary="Sync recently modified HubSpot contacts and their companies into Alma's Supabase",
    graph=(
        {"id": "hubspot_search", "label": "HubSpot search", "to": ("filter",)},
        {"id": "filter", "label": "Filter", "to": ("companies_upsert",)},
        {"id": "companies_upsert", "label": "Upsert companies", "to": ("contacts_upsert",)},
        {"id": "contacts_upsert", "label": "Upsert contacts", "to": ()},
    ),
)

"""Discovery Call Booked: el flujo principal que une a todos los demás.

    edge function (fatal) → emails de asistentes
      → EN PARALELO: comunicaciones | contactos | empresa (3 intentos)
      → HubSpot empresa → HubSpot contactos (en paralelo)
      → Slack: anuncio + hilo con el detalle

Cada paso queda en el run. Los pasos de investigación degradan en vez de tumbar
el flujo; solo la edge function y Slack lo hacen fallar (ver docs/design.md).
Con `dry_run` no se escribe nada fuera: ni edge function, ni HubSpot, ni Slack.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.clients import Clients
from app.config import NotConfigured
from app.domain.emails import attendee_emails
from app.domain.naming import company_name_from_url, split_full_name
from app.domain.slack_messages import (
    SlackCompany,
    SlackContact,
    SlackMessages,
    build_messages,
    format_event_date,
    hubspot_record_url,
)
from app.flows.attendees import EnrichedContact, enrich_contacts
from app.flows.company import CompanyEnrichment, enrich_company
from app.flows.crm import CompanyCrmInput, ContactCrmInput, upsert_company, upsert_contact
from app.flows.registry import FlowSpec
from app.flows.team_communications import NO_COMMUNICATIONS, search_team_communications
from app.runs import record_step

from .schemas import DiscoveryCallInput, DiscoveryCallResult

logger = logging.getLogger(__name__)

COMPANY_ATTEMPTS = 3
COMPANY_RETRY_SECONDS = 5.0


# --- Paquetes para HubSpot (antes "Prepare Company/Contact CRM Package") -----


def _growth_for(metrics: list[dict], months: int, fallback_index: int) -> float | None:
    for m in metrics:
        if m.get("monthRange") == months and isinstance(m.get("growthPercentage"), (int, float)):
            return float(m["growthPercentage"])
    # Webhooks viejos no traían monthRange: posición 0/1/2 = 6/12/24 meses.
    if len(metrics) > fallback_index and "monthRange" not in metrics[fallback_index]:
        value = metrics[fallback_index].get("growthPercentage")
        return float(value) if isinstance(value, (int, float)) else None
    return None


def resolve_company_name(crm: dict, data: DiscoveryCallInput) -> str:
    trigger_name = "" if data.companyName.strip().upper() in ("", "N/A") else data.companyName
    return crm.get("companyName") or trigger_name or company_name_from_url(data.companyUrl)


def company_package(company: CompanyEnrichment | None, data: DiscoveryCallInput) -> CompanyCrmInput:
    crm = company.crm if company else {}
    metrics = crm.get("companyGrowthMetrics") or []
    return CompanyCrmInput(
        companyName=resolve_company_name(crm, data) or "Unknown Company",
        websiteUrl=crm.get("companyWebsiteUrl") or data.companyUrl,
        linkedinUrl=crm.get("companyLinkedinUrl") or "",
        linkedinUid=crm.get("companyLinkedinUid") or "",
        twitterUrl=crm.get("companyTwitterUrl") or "",
        facebookUrl=crm.get("companyFacebookUrl") or "",
        primaryPhoneNumber=crm.get("companyPrimaryPhoneNumber") or "",
        industry=crm.get("companyIndustry") or "",
        streetAddress=crm.get("companyStreetAddress") or "",
        city=crm.get("companyCity") or "",
        state=crm.get("companyState") or "",
        country=crm.get("companyCountry") or "",
        postalCode=crm.get("companyPostalCode") or "",
        aboutUs=crm.get("companyAboutUs") or "",
        aiSummary=(crm.get("aiSummary") or {}).get("summary") or "",
        employeeCount=crm.get("companyEmployeeCount") or None,
        annualRevenue=crm.get("companyAnnualRevenue") or None,
        crunchbaseUrl=crm.get("companyCrunchbaseUrl") or "",
        yearFounded=crm.get("companyYearFounded") or None,
        headcountGrowth6Months=_growth_for(metrics, 6, 0),
        headcountGrowth12Months=_growth_for(metrics, 12, 1),
        headcountGrowth24Months=_growth_for(metrics, 24, 2),
    )


def trigger_names(data: DiscoveryCallInput) -> dict[str, tuple[str, str]]:
    """Nombres por email según el booking, para cuando Apollo no encuentra a alguien."""
    names: dict[str, tuple[str, str]] = {}
    for g in data.guestEmails:
        first, last = g.firstName, g.lastName
        if not first and not last:
            first, last = split_full_name(g.name)
        names[g.email.strip().lower()] = (first, last)
    primary = data.primaryContactEmail.strip().lower()
    if primary and not any(names.get(primary, ("", ""))):
        names[primary] = split_full_name(data.primaryContactName)
    return names


def contact_package(
    contact: EnrichedContact, names: dict[str, tuple[str, str]], company_id: str | None
) -> ContactCrmInput:
    first, last = names.get(contact.email, ("", ""))
    return ContactCrmInput(
        email=contact.email,
        firstName=contact.first_name or first,
        lastName=contact.last_name or last,
        title=contact.title,
        city=contact.city,
        state=contact.state,
        country=contact.country,
        linkedinUrl=contact.linkedin_url,
        aiContactSummary=contact.ai_summary,
        companyHubspotId=company_id or "",
    )


# --- Pasos ---------------------------------------------------------------------


async def _event(clients: Clients, data: DiscoveryCallInput) -> dict:
    if data.dry_run:
        event = {
            "startDateTime": data.eventStartDateTime,
            "timeZone": data.eventTimeZone,
            "ExternalAttendees": [{"email": g.email} for g in data.guestEmails],
        }
        await record_step("event_function", "skipped", {"reason": "dry_run"})
        return event
    payload = {
        "primaryContactEmail": data.primaryContactEmail,
        "eventTitle": data.eventTitle,
        "organizerEmail": data.organizerEmail,
        "companyUrl": data.companyUrl,
        "guestEmails": [g.model_dump() for g in data.guestEmails],
        "eventTypeUri": data.eventTypeUri,
    }
    event = await clients.event_function.handle_booking(payload)
    await record_step("event_function", "ok", {"startDateTime": event.get("startDateTime")})
    return event


async def _communications(clients: Clients, emails: list[str]) -> str:
    try:
        result = await search_team_communications(clients, emails)
    except Exception as exc:
        logger.warning("team communications failed: %s", exc)
        await record_step("team_communications", "degraded", {"error": str(exc)})
        return NO_COMMUNICATIONS
    await record_step(
        "team_communications",
        "ok",
        {"email_count": result.email_count, "meeting_count": result.meeting_count},
    )
    return result.summary


async def _contacts(clients: Clients, emails: list[str]) -> list[EnrichedContact]:
    try:
        contacts = await enrich_contacts(clients, emails)
    except Exception as exc:
        logger.warning("contact enrichment failed: %s", exc)
        await record_step("enrich_contacts", "degraded", {"error": str(exc)})
        return [EnrichedContact(email=e, enriched=False, error=str(exc)) for e in emails]
    await record_step(
        "enrich_contacts",
        "ok" if all(c.enriched for c in contacts) else "degraded",
        {c.email: c.enriched for c in contacts},
    )
    return contacts


async def _company(clients: Clients, company_url: str) -> CompanyEnrichment | None:
    if not company_url:
        await record_step("enrich_company", "skipped", {"reason": "no companyUrl"})
        return None
    for attempt in range(1, COMPANY_ATTEMPTS + 1):
        try:
            company = await enrich_company(clients, company_url)
        except Exception as exc:
            logger.warning("company enrichment attempt %d failed: %s", attempt, exc)
            # Sin credencial, reintentar solo retrasa el run.
            if attempt == COMPANY_ATTEMPTS or isinstance(exc, NotConfigured):
                await record_step("enrich_company", "degraded", {"error": str(exc)})
                return None
            await asyncio.sleep(COMPANY_RETRY_SECONDS * attempt)
        else:
            await record_step("enrich_company", "ok", {"found": company.foundCompany})
            return company
    return None


async def _hubspot(
    clients: Clients,
    data: DiscoveryCallInput,
    company: CompanyEnrichment | None,
    contacts: list[EnrichedContact],
) -> tuple[str | None, dict[str, str]]:
    """Devuelve el id de la empresa y los ids de contacto por email."""
    company_input = company_package(company, data)
    if data.dry_run:
        await record_step("hubspot", "skipped", {"reason": "dry_run"})
        return None, {}

    company_id: str | None = None
    try:
        result = await upsert_company(clients, company_input)
        company_id = result.hubspotId
        await record_step("hubspot_company", "ok", result)
    except Exception as exc:
        logger.warning("hubspot company failed: %s", exc)
        await record_step("hubspot_company", "degraded", {"error": str(exc)})

    names = trigger_names(data)
    results = await asyncio.gather(
        *(upsert_contact(clients, contact_package(c, names, company_id)) for c in contacts),
        return_exceptions=True,
    )
    contact_ids: dict[str, str] = {}
    errors = {}
    for contact, res in zip(contacts, results, strict=True):
        if isinstance(res, BaseException):
            errors[contact.email] = str(res)
        else:
            contact_ids[contact.email] = res.hubspotId
    await record_step(
        "hubspot_contacts", "degraded" if errors else "ok", {"ids": contact_ids, "errors": errors}
    )
    return company_id, contact_ids


def _slack_messages(
    clients: Clients,
    data: DiscoveryCallInput,
    event: dict,
    company: CompanyEnrichment | None,
    contacts: list[EnrichedContact],
    company_id: str | None,
    contact_ids: dict[str, str],
    communications: str,
) -> SlackMessages:
    portal = clients.settings.hubspot_portal_id
    crm = company.crm if company else {}
    names = trigger_names(data)
    slack_contacts = []
    for c in contacts:
        first, last = names.get(c.email, ("", ""))
        slack_contacts.append(
            SlackContact(
                email=c.email,
                first_name=c.first_name or first,
                last_name=c.last_name or last,
                title=c.title,
                bio=c.ai_summary,
                hubspot_url=hubspot_record_url(portal, "0-1", contact_ids.get(c.email)),
            )
        )
    return build_messages(
        company=SlackCompany(
            name=resolve_company_name(crm, data) or "Unknown Company",
            about=crm.get("companyAboutUs") or "",
            ai_summary=(crm.get("aiSummary") or {}).get("summary") or "",
            linkedin_url=crm.get("companyLinkedinUrl") or None,
            linkedin_uid=crm.get("companyLinkedinUid") or None,
            hubspot_url=hubspot_record_url(portal, "0-2", company_id),
        ),
        event_date=format_event_date(event.get("startDateTime"), event.get("timeZone")),
        primary_email=data.primaryContactEmail.strip().lower(),
        contacts=slack_contacts,
        team_communications=communications,
    )


async def run_discovery_call(clients: Clients, data: DiscoveryCallInput) -> DiscoveryCallResult:
    event = await _event(clients, data)

    # Nadie del equipo es un "asistente externo", diga lo que diga el evento:
    # acabaría enriquecido en Apollo y creado como contacto en HubSpot.
    internal = f"@{clients.settings.internal_email_domain.lower()}"
    emails = [
        e
        for e in attendee_emails(data.primaryContactEmail, event.get("ExternalAttendees") or [])
        if not e.endswith(internal)
    ]
    if not emails:
        raise ValueError("No valid attendee emails in the booking")

    communications, contacts, company = await asyncio.gather(
        _communications(clients, emails),
        _contacts(clients, emails),
        _company(clients, data.companyUrl),
    )

    company_id, contact_ids = await _hubspot(clients, data, company, contacts)
    messages = _slack_messages(
        clients, data, event, company, contacts, company_id, contact_ids, communications
    )

    slack: dict[str, Any] = {"initial": messages.initial, "reply": messages.reply}
    if data.dry_run:
        await record_step("slack", "skipped", {"reason": "dry_run"})
    else:
        ts = await clients.slack.post_message(messages.initial)
        reply_ts = await clients.slack.post_message(messages.reply, thread_ts=ts)
        slack.update(ts=ts, reply_ts=reply_ts)
        await record_step("slack", "ok", {"ts": ts})

    return DiscoveryCallResult(
        event=event,
        attendee_emails=emails,
        company=company.model_dump() if company else {},
        contacts=[c.model_dump() for c in contacts],
        communications_summary=communications,
        hubspot={"companyId": company_id, "contactIds": contact_ids},
        slack=slack,
    )


async def run(clients: Clients, data: DiscoveryCallInput) -> dict:
    return await run_discovery_call(clients, data)


spec = FlowSpec(
    slug="discovery-call-booked",
    kind="workflow",
    title="Discovery Call Booked",
    input_model=DiscoveryCallInput,
    run=run,
    mode="async",
    replaces="Discovery Call Booked",
    description=(
        "Runs end to end when a discovery call is booked: it adds the team to the "
        "calendar event, then researches the company, the external attendees and prior "
        "team communications in parallel. The results are upserted into HubSpot and a "
        "briefing is posted to Slack. Research steps degrade gracefully, and with dry_run "
        "the flow investigates without writing anything outside. Replaces the n8n "
        "workflow \"Discovery Call Booked\"."
    ),
    summary="Run the Discovery Call Booked flow",
    graph=(
        {
            "id": "event_function",
            "label": "Calendar event",
            "to": ("enrich_contacts", "enrich_company", "team_communications"),
        },
        {"id": "enrich_contacts", "label": "Enrich contacts", "to": ("hubspot_company",)},
        {"id": "enrich_company", "label": "Enrich company", "to": ("hubspot_company",)},
        {"id": "team_communications", "label": "Team communications", "to": ("hubspot_company",)},
        {"id": "hubspot_company", "label": "HubSpot company", "to": ("hubspot_contacts",)},
        {"id": "hubspot_contacts", "label": "HubSpot contacts", "to": ("slack",)},
        {"id": "slack", "label": "Slack message", "to": ()},
    ),
)

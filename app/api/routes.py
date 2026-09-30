"""Rutas HTTP. Cada petición abre un run; el id vuelve en la cabecera `X-Run-Id`
(y en el cuerpo del 202 del orquestador) para consultar pasos y coste."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from ..clients import Clients
from ..clients.http import ProviderError
from ..config import NotConfigured
from ..flows.attendees import ContactsRequest, EnrichedContact, enrich_contacts
from ..flows.company import CompanyEnrichment, CompanyRequest, enrich_company
from ..flows.crm import (
    CompanyCrmInput,
    CompanyCrmResult,
    ContactCrmInput,
    ContactCrmResult,
    NoteInput,
    NoteResult,
    upsert_company,
    upsert_contact,
)
from ..flows.discovery_call import DiscoveryCallInput, run_discovery_call
from ..flows.team_communications import (
    CommunicationsRequest,
    CommunicationsResult,
    search_team_communications,
)
from ..runs import FAILED, SUCCEEDED, RunStore, bind_run
from .deps import require_api_key, run_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_key)])

ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"description": "Missing or invalid X-API-Key"},
    502: {"description": "An upstream provider failed"},
    503: {"description": "A required provider credential is not configured"},
}


def clients_dep(request: Request) -> Clients:
    return request.app.state.clients


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, NotConfigured):
        return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    if isinstance(exc, ProviderError):
        return HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))
    return HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal error")


async def _run_sync(
    store: RunStore,
    response: Response,
    flow: str,
    payload: BaseModel,
    work: Callable[[], Awaitable[Any]],
) -> Any:
    run_id = await store.create(flow, payload.model_dump())
    response.headers["X-Run-Id"] = run_id
    with bind_run(store, run_id):
        try:
            result = await work()
        except Exception as exc:
            logger.exception("flow %s failed (run %s)", flow, run_id)
            await store.finish(run_id, FAILED, error=f"{type(exc).__name__}: {exc}")
            error = _http_error(exc)
            error.headers = {"X-Run-Id": run_id}
            raise error from exc
    await store.finish(run_id, SUCCEEDED, output=result)
    return result


# --- Orquestador -----------------------------------------------------------------


class RunAccepted(BaseModel):
    run_id: str
    status: str
    status_url: str


@router.post(
    "/flows/discovery-call-booked",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=RunAccepted,
    responses=ERRORS,
    tags=["Flows"],
    summary="Run the Discovery Call Booked flow",
    description=(
        "Replaces the n8n workflow. Adds the team to the event, researches company, attendees "
        "and past team communications, upserts everything in HubSpot and posts the briefing "
        "to Slack. Runs in the background: poll `status_url` for steps, output and cost."
    ),
)
async def discovery_call_booked(
    data: DiscoveryCallInput,
    request: Request,
    store: RunStore = Depends(run_store),
    clients: Clients = Depends(clients_dep),
) -> RunAccepted:
    run_id = await store.create("discovery_call_booked", data.model_dump(), dry_run=data.dry_run)

    async def work() -> None:
        with bind_run(store, run_id):
            try:
                result = await run_discovery_call(clients, data)
            except Exception as exc:
                logger.exception("discovery call run %s failed", run_id)
                await store.finish(run_id, FAILED, error=f"{type(exc).__name__}: {exc}")
            else:
                await store.finish(run_id, SUCCEEDED, output=result)

    task = asyncio.create_task(work())
    # Referencia fuerte: asyncio solo guarda referencias débiles a las tareas.
    tasks: set = request.app.state.background_tasks
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return RunAccepted(run_id=run_id, status="running", status_url=f"/v1/runs/{run_id}")


@router.get(
    "/runs/{run_id}",
    tags=["Runs"],
    summary="Get a run: status, steps, output and cost",
    responses={404: {"description": "Run not found"}, **ERRORS},
)
async def get_run(run_id: str, store: RunStore = Depends(run_store)) -> dict:
    run = await store.get(run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Run not found")
    return run


# --- Subflujos -----------------------------------------------------------------


@router.post(
    "/communications/search",
    response_model=CommunicationsResult,
    responses=ERRORS,
    tags=["Research"],
    summary="Search team communications (Gmail via Unipile + Fireflies) and summarize",
)
async def communications_search(
    data: CommunicationsRequest,
    response: Response,
    store: RunStore = Depends(run_store),
    clients: Clients = Depends(clients_dep),
):
    return await _run_sync(
        store,
        response,
        "team_communications",
        data,
        lambda: search_team_communications(clients, data.emails),
    )


@router.post(
    "/contacts/enrich",
    response_model=list[EnrichedContact],
    responses=ERRORS,
    tags=["Research"],
    summary="Enrich attendees with Apollo and write a pre-call bio for each",
)
async def contacts_enrich(
    data: ContactsRequest,
    response: Response,
    store: RunStore = Depends(run_store),
    clients: Clients = Depends(clients_dep),
):
    return await _run_sync(
        store, response, "enrich_contacts", data, lambda: enrich_contacts(clients, data.emails)
    )


@router.post(
    "/companies/enrich",
    response_model=CompanyEnrichment,
    responses=ERRORS,
    tags=["Research"],
    summary="Enrich a company (Apollo, Google News, Google Jobs) and summarize it",
    description="Response keeps the shape of the old n8n webhook (`Company`, `linkedIn`, `crm`).",
)
async def companies_enrich(
    data: CompanyRequest,
    response: Response,
    store: RunStore = Depends(run_store),
    clients: Clients = Depends(clients_dep),
):
    return await _run_sync(
        store, response, "enrich_company", data, lambda: enrich_company(clients, data.companyUrl)
    )


@router.post(
    "/crm/companies",
    response_model=CompanyCrmResult,
    responses=ERRORS,
    tags=["CRM"],
    summary="Create or update a HubSpot company (matched by domain) and post its AI note",
)
async def crm_companies(
    data: CompanyCrmInput,
    response: Response,
    store: RunStore = Depends(run_store),
    clients: Clients = Depends(clients_dep),
):
    return await _run_sync(
        store, response, "crm_company", data, lambda: upsert_company(clients, data)
    )


@router.post(
    "/crm/contacts",
    response_model=ContactCrmResult,
    responses=ERRORS,
    tags=["CRM"],
    summary="Create or update a HubSpot contact (by email), link it to a company, post its note",
)
async def crm_contacts(
    data: ContactCrmInput,
    response: Response,
    store: RunStore = Depends(run_store),
    clients: Clients = Depends(clients_dep),
):
    return await _run_sync(
        store, response, "crm_contact", data, lambda: upsert_contact(clients, data)
    )


@router.post(
    "/crm/notes",
    response_model=NoteResult,
    responses=ERRORS,
    tags=["CRM"],
    summary="Post a note to a HubSpot company or contact",
)
async def crm_notes(
    data: NoteInput,
    response: Response,
    store: RunStore = Depends(run_store),
    clients: Clients = Depends(clients_dep),
):
    async def work() -> NoteResult:
        note_id = await clients.hubspot.create_note(
            data.content, data.contact_company_deal, data.associated_hubspot_id
        )
        return NoteResult(noteId=note_id)

    return await _run_sync(store, response, "crm_note", data, work)

"""Rutas HTTP. Cada petición abre un run; el id vuelve en la cabecera `X-Run-Id`
(y en el cuerpo del 202 del orquestador) para consultar pasos y coste."""

from __future__ import annotations

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
    run_note,
    upsert_company,
    upsert_contact,
)
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
        "team-communications",
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
        store, response, "enrich-contacts", data, lambda: enrich_contacts(clients, data.emails)
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
        store, response, "enrich-company", data, lambda: enrich_company(clients, data.companyUrl)
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
        store, response, "crm-company", data, lambda: upsert_company(clients, data)
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
        store, response, "crm-contact", data, lambda: upsert_contact(clients, data)
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
    return await _run_sync(store, response, "crm-note", data, lambda: run_note(clients, data))

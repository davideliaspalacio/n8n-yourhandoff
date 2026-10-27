"""Endpoints que alimentan el dashboard /ui: registro con stats, listado de
runs, resumen de costes y relanzar un run."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import ValidationError

from ..clients import Clients
from ..runs import RunStore
from .deps import require_api_key, run_store
from .flow_routes import RunAccepted, launch_run
from .routes import clients_dep

router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_key)], tags=["Dashboard"])

EMPTY_STATS = {"runs": 0, "succeeded": 0, "cost_usd": 0.0, "last_run_at": None, "last_status": None}


@router.get("/flows", summary="Registered flows with 7-day stats")
async def list_flows(request: Request, store: RunStore = Depends(run_store)) -> dict:
    stats = await store.flow_stats(days=7)
    flows = [
        {
            "slug": spec.slug,
            "title": spec.title,
            "mode": spec.mode,
            "replaces": spec.replaces,
            "endpoint": f"/v1/flows/{spec.slug}",
            "stats": stats.get(spec.slug, EMPTY_STATS),
            "graph": [
                {"id": n["id"], "label": n.get("label", n["id"]), "to": list(n.get("to") or ())}
                for n in spec.graph
            ],
        }
        for spec in request.app.state.flow_registry.values()
    ]
    return {"flows": sorted(flows, key=lambda f: f["slug"])}


@router.get("/runs", summary="List runs, newest first")
async def list_runs(
    flow: str | None = None,
    status: str | None = Query(None, pattern="^(running|succeeded|failed)$"),
    since: str | None = None,
    until: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    store: RunStore = Depends(run_store),
) -> dict:
    return await store.list_runs(
        flow=flow, status=status, since=since, until=until, page=page, per_page=per_page
    )


@router.get("/costs/summary", summary="Cost totals by day or provider")
async def costs_summary(
    group_by: Literal["day", "provider"] = "day",
    days: int = Query(30, ge=1, le=365),
    store: RunStore = Depends(run_store),
) -> dict:
    return {"items": await store.costs_summary(group_by=group_by, days=days)}


@router.post(
    "/runs/{run_id}/replay",
    status_code=202,
    response_model=RunAccepted,
    summary="Re-run a flow with the stored input of a previous run",
    responses={
        404: {"description": "Run not found"},
        409: {"description": "The run's flow is no longer registered"},
        422: {"description": "Stored input no longer matches the flow's input model"},
    },
)
async def replay_run(
    run_id: str,
    request: Request,
    store: RunStore = Depends(run_store),
    clients: Clients = Depends(clients_dep),
) -> RunAccepted:
    run = await store.get(run_id)
    if run is None:
        raise HTTPException(404, detail="Run not found")
    spec = request.app.state.flow_registry.get(run["flow"])
    if spec is None:
        raise HTTPException(409, detail=f"Flow '{run['flow']}' is no longer registered")
    try:
        data = spec.input_model.model_validate(run["input"])
    except ValidationError as exc:
        raise HTTPException(422, detail="Stored input no longer matches the flow") from exc
    return await launch_run(
        spec, data, request=request, store=store, clients=clients, replay_of=run_id
    )

# app/api/flow_routes.py
"""Monta cada FlowSpec del registro como POST /v1/flows/<slug>, con run,
steps, coste y dry_run automáticos: lo que el orquestador hacía a mano."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel

from ..clients import Clients
from ..flows.registry import FlowSpec
from ..runs import FAILED, SUCCEEDED, RunStore, bind_run
from .deps import require_api_key, run_store
from .routes import ERRORS, _run_sync, clients_dep

logger = logging.getLogger(__name__)


class RunAccepted(BaseModel):
    run_id: str
    status: str
    status_url: str


async def launch_run(
    spec: FlowSpec,
    data: BaseModel,
    *,
    request: Request,
    store: RunStore,
    clients: Clients,
    replay_of: str | None = None,
) -> RunAccepted:
    # replay_of solo se pasa si existe, para no exigir que el store lo soporte.
    kwargs: dict = {"dry_run": bool(getattr(data, "dry_run", False))}
    if replay_of is not None:
        kwargs["replay_of"] = replay_of
    run_id = await store.create(spec.slug, data.model_dump(), **kwargs)

    async def work() -> None:
        with bind_run(store, run_id):
            try:
                result = await spec.run(clients, data)
            except Exception as exc:
                logger.exception("flow %s run %s failed", spec.slug, run_id)
                await store.finish(run_id, FAILED, error=f"{type(exc).__name__}: {exc}")
            else:
                await store.finish(run_id, SUCCEEDED, output=result)

    task = asyncio.create_task(work())
    # Referencia fuerte: asyncio solo guarda referencias débiles a las tareas.
    tasks: set = request.app.state.background_tasks
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return RunAccepted(run_id=run_id, status="running", status_url=f"/v1/runs/{run_id}")


def _make_async_endpoint(spec: FlowSpec):
    async def endpoint(
        data: BaseModel,
        request: Request,
        store: RunStore = Depends(run_store),
        clients: Clients = Depends(clients_dep),
    ) -> RunAccepted:
        return await launch_run(spec, data, request=request, store=store, clients=clients)

    endpoint.__annotations__["data"] = spec.input_model
    return endpoint


def _make_sync_endpoint(spec: FlowSpec):
    async def endpoint(
        data: BaseModel,
        response: Response,
        store: RunStore = Depends(run_store),
        clients: Clients = Depends(clients_dep),
    ):
        return await _run_sync(store, response, spec.slug, data, lambda: spec.run(clients, data))

    endpoint.__annotations__["data"] = spec.input_model
    return endpoint


def build_flows_router(registry: dict[str, FlowSpec]) -> APIRouter:
    router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_key)])
    for spec in registry.values():
        common = dict(
            responses=ERRORS,
            tags=["Flows"],
            summary=spec.summary or spec.title,
            description=(
                f"Replaces the n8n workflow *{spec.replaces}*." if spec.replaces else None
            ),
            name=f"flow_{spec.slug.replace('-', '_')}",
        )
        if spec.mode == "async":
            route = router.post(
                f"/flows/{spec.slug}",
                status_code=status.HTTP_202_ACCEPTED,
                response_model=RunAccepted,
                **common,
            )
            route(_make_async_endpoint(spec))
        else:
            route = router.post(
                f"/flows/{spec.slug}", response_model=spec.response_model, **common
            )
            route(_make_sync_endpoint(spec))
    return router

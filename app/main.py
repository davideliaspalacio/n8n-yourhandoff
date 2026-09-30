"""Punto de entrada: `uvicorn app.main:app`. Swagger en /docs, ReDoc en /redoc."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api.routes import router
from .clients import Clients
from .config import Settings, get_settings
from .runs import MemoryRunStore, PostgresRunStore, RunStore

logger = logging.getLogger(__name__)

DESCRIPTION = """
Handoff automation flows, migrated from n8n.

* **Flows** — the full *Discovery Call Booked* pipeline (async, returns a `run_id`).
* **Research** — each enrichment step on its own: team communications, attendees, company.
* **CRM** — HubSpot upserts and notes.
* **Runs** — status, per-step detail and USD cost of every run.

Every `/v1` call needs the `X-API-Key` header and returns an `X-Run-Id` header.
"""


async def _build_store(settings: Settings) -> RunStore:
    if not settings.database_url:
        logger.warning("DATABASE_URL not set: runs are kept in memory and lost on restart")
        return MemoryRunStore()
    store = PostgresRunStore(settings.database_url)
    await store.open()
    orphans = await store.fail_orphans()
    if orphans:
        logger.warning("marked %d interrupted run(s) as failed", orphans)
    return store


def create_app(
    settings: Settings | None = None,
    *,
    clients: Clients | None = None,
    store: RunStore | None = None,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.run_store = store or await _build_store(settings)
        app.state.clients = clients or Clients.build(settings)
        app.state.background_tasks = set()
        yield
        for task in list(app.state.background_tasks):
            task.cancel()
        await app.state.clients.aclose()
        await app.state.run_store.close()

    app = FastAPI(
        title="Handoff Flows",
        version="0.1.0",
        description=DESCRIPTION,
        lifespan=lifespan,
        openapi_tags=[
            {"name": "Flows", "description": "End-to-end pipelines."},
            {"name": "Research", "description": "Enrichment and AI summaries."},
            {"name": "CRM", "description": "HubSpot writes."},
            {"name": "Runs", "description": "Execution log and cost."},
        ],
    )
    app.state.settings = settings
    app.include_router(router)

    @app.get("/health", tags=["Health"], summary="Liveness check")
    async def health() -> dict:
        return {"status": "ok"}

    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app()

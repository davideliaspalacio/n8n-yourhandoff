# tests/test_flow_routes.py
from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI
from pydantic import BaseModel

from app.api.flow_routes import build_flows_router
from app.flows.registry import FlowSpec
from app.runs import MemoryRunStore, record_step

H = {"X-API-Key": "test-key"}


class EchoInput(BaseModel):
    text: str
    dry_run: bool = False


class BoomInput(BaseModel):
    pass


async def _echo(clients, data: EchoInput) -> dict:
    await record_step("echo", "succeeded", {"text": data.text})
    return {"echo": data.text}


async def _boom(clients, data: BoomInput) -> dict:
    raise ValueError("kaput")


ECHO = FlowSpec(slug="echo", title="Echo", input_model=EchoInput, run=_echo, mode="sync")
ECHO_BG = FlowSpec(slug="echo-bg", title="Echo BG", input_model=EchoInput, run=_echo, mode="async")
BOOM = FlowSpec(slug="boom", title="Boom", input_model=BoomInput, run=_boom, mode="sync")


@pytest.fixture
async def client(settings):
    app = FastAPI()
    app.state.settings = settings
    app.state.run_store = MemoryRunStore()
    app.state.clients = None
    app.state.background_tasks = set()
    app.include_router(build_flows_router({s.slug: s for s in (ECHO, ECHO_BG, BOOM)}))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c, app.state.run_store


async def test_sync_flow_runs_and_records(client):
    c, store = client
    r = await c.post("/v1/flows/echo", json={"text": "hola"}, headers=H)
    assert r.status_code == 200 and r.json() == {"echo": "hola"}
    run = await store.get(r.headers["X-Run-Id"])
    assert run["status"] == "succeeded" and run["flow"] == "echo"
    assert run["steps"][0]["name"] == "echo"


async def test_async_flow_returns_202_and_finishes(client):
    c, store = client
    r = await c.post("/v1/flows/echo-bg", json={"text": "bg", "dry_run": True}, headers=H)
    assert r.status_code == 202
    run_id = r.json()["run_id"]
    assert r.json()["status_url"] == f"/v1/runs/{run_id}"
    for _ in range(50):
        run = await store.get(run_id)
        if run["status"] != "running":
            break
        await asyncio.sleep(0.02)
    assert run["status"] == "succeeded" and run["dry_run"] is True


async def test_sync_flow_error_marks_run_failed(client):
    c, store = client
    r = await c.post("/v1/flows/boom", json={}, headers=H)
    assert r.status_code == 422
    run = await store.get(r.headers["X-Run-Id"])
    assert run["status"] == "failed" and "kaput" in run["error"]


async def test_requires_api_key(client):
    c, _ = client
    assert (await c.post("/v1/flows/echo", json={"text": "x"})).status_code == 401

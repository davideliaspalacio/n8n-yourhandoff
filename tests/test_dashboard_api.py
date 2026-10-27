from __future__ import annotations

import asyncio
from decimal import Decimal

import httpx
import pytest
from pydantic import BaseModel

from app.flows.registry import FlowSpec
from app.main import create_app
from app.runs import MemoryRunStore

H = {"X-API-Key": "test-key"}


class EchoInput(BaseModel):
    text: str
    dry_run: bool = False


async def _echo(clients, data: EchoInput) -> dict:
    return {"echo": data.text}


ECHO = FlowSpec(
    slug="echo",
    title="Echo",
    input_model=EchoInput,
    run=_echo,
    replaces="Echo v1",
    graph=(
        {"id": "in", "label": "Input", "to": ("out",)},
        {"id": "out", "label": "Output", "to": ()},
    ),
)


@pytest.fixture
async def dash(settings, clients):
    store = MemoryRunStore()
    app = create_app(settings, clients=clients, store=store, flow_registry={"echo": ECHO})
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            yield c, store


async def test_flows_lists_registry_with_stats(dash):
    c, store = dash
    rid = await c.post("/v1/flows/echo", json={"text": "x"}, headers=H)
    assert rid.status_code == 200
    body = (await c.get("/v1/flows", headers=H)).json()
    [flow] = body["flows"]
    assert flow["kind"] == "workflow"
    assert flow["slug"] == "echo" and flow["endpoint"] == "/v1/flows/echo"
    assert flow["stats"]["runs"] == 1 and flow["stats"]["succeeded"] == 1
    assert flow["graph"] == [
        {"id": "in", "label": "Input", "to": ["out"]},
        {"id": "out", "label": "Output", "to": []},
    ]


async def test_runs_listing_and_costs(dash):
    c, store = dash
    rid = await store.create("echo", {"text": "x"})
    await store.add_cost(rid, "openai", "chat", 10, Decimal("0.05"))
    await store.finish(rid, "failed", error="boom")
    runs = (await c.get("/v1/runs?flow=echo&status=failed", headers=H)).json()
    assert runs["total"] == 1 and runs["items"][0]["error"] == "boom"
    costs = (await c.get("/v1/costs/summary?group_by=provider", headers=H)).json()
    assert costs["items"] == [{"provider": "openai", "cost_usd": 0.05}]


async def test_replay_reruns_with_link(dash):
    c, store = dash
    rid = await store.create("echo", {"text": "otra vez"})
    await store.finish(rid, "failed", error="boom")
    r = await c.post(f"/v1/runs/{rid}/replay", headers=H)
    assert r.status_code == 202
    new_id = r.json()["run_id"]
    for _ in range(50):
        run = await store.get(new_id)
        if run["status"] != "running":
            break
        await asyncio.sleep(0.02)
    assert run["status"] == "succeeded" and run["replay_of"] == rid


async def test_replay_of_unknown_run_is_404(dash):
    c, _ = dash
    assert (await c.post("/v1/runs/00000000-0000-0000-0000-000000000000/replay", headers=H)).status_code == 404


async def test_replay_of_unregistered_flow_is_409(dash):
    c, store = dash
    rid = await store.create("borrado", {})
    await store.finish(rid, "failed")
    assert (await c.post(f"/v1/runs/{rid}/replay", headers=H)).status_code == 409

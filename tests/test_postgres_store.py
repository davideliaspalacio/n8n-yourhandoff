"""Almacén de runs contra un Postgres real. Se salta sin TEST_DATABASE_URL.

    TEST_DATABASE_URL=postgresql://... uv run pytest tests/test_postgres_store.py
(el propio test aplica las migraciones)
"""

import os
import uuid
from decimal import Decimal

import pytest

from app.runs import FAILED, SUCCEEDED, PostgresRunStore

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")


@pytest.fixture
async def pg():
    store = PostgresRunStore(DSN)
    await store.open()
    await store.migrate()
    yield store
    await store.close()


async def test_full_run_lifecycle(pg):
    run_id = await pg.create("enrich_company", {"companyUrl": "staynido.com"}, dry_run=True)
    await pg.step(run_id, "apollo", "ok", {"found": True})
    await pg.step(run_id, "serpapi", "degraded", {"error": "x"})
    await pg.add_cost(run_id, "openai", "company_summary", 1500, Decimal("0.004"), {"model": "gpt-4.1"})
    await pg.add_cost(run_id, "apollo", "organizations_enrich", 1, Decimal("0.02"))
    await pg.finish(run_id, SUCCEEDED, output={"crm": {"companyName": "Nido"}})

    run = await pg.get(run_id)
    assert run["status"] == SUCCEEDED and run["dry_run"] is True
    assert run["input"] == {"companyUrl": "staynido.com"}
    assert [s["name"] for s in run["steps"]] == ["apollo", "serpapi"]
    assert run["output"] == {"crm": {"companyName": "Nido"}}
    assert run["total_cost_usd"] == 0.024
    assert run["finished_at"] is not None


async def test_orphans_are_failed_and_bad_ids_are_none(pg):
    run_id = await pg.create("discovery_call_booked", {})
    assert await pg.fail_orphans() >= 1
    run = await pg.get(run_id)
    assert run["status"] == FAILED and "restart" in run["error"]
    assert await pg.get("not-a-uuid") is None


async def test_replay_of_round_trip(pg):
    original = await pg.create("echo", {"text": "hola"})
    replay = await pg.create("echo", {"text": "hola"}, replay_of=original)
    assert (await pg.get(replay))["replay_of"] == original
    assert (await pg.get(original))["replay_of"] is None


async def test_list_runs_filters_and_paginates(pg):
    # Flow único por ejecución: la base puede tener runs de otros tests.
    flow = f"list_test_{uuid.uuid4().hex}"
    ids = []
    for i in range(3):
        rid = await pg.create(flow, {"i": i})
        await pg.finish(rid, SUCCEEDED if i % 2 == 0 else FAILED)
        ids.append(rid)
    page = await pg.list_runs(flow=flow)
    assert page["total"] == 3 and len(page["items"]) == 3
    assert page["items"][0]["id"] == ids[2]  # más reciente primero
    assert set(page["items"][0]) == {
        "id", "flow", "status", "dry_run", "replay_of", "error", "created_at", "finished_at",
    }
    failed = await pg.list_runs(flow=flow, status=FAILED)
    assert failed["total"] == 1
    p2 = await pg.list_runs(flow=flow, page=2, per_page=2)
    assert p2["total"] == 3 and len(p2["items"]) == 1
    cutoff = page["items"][0]["created_at"]
    assert (await pg.list_runs(flow=flow, since=cutoff))["total"] == 1
    assert (await pg.list_runs(flow=flow, until=cutoff))["total"] == 3


async def test_flow_stats_and_costs_summary(pg):
    # Flow único por ejecución: los agregados se filtran por flow, no por la base entera.
    flow = f"stats_test_{uuid.uuid4().hex}"
    ok = await pg.create(flow, {})
    await pg.add_cost(ok, "stats_openai", "chat", 1000, Decimal("0.01"))
    await pg.finish(ok, SUCCEEDED)
    bad = await pg.create(flow, {})
    await pg.add_cost(bad, "stats_apollo", "enrich", 1, Decimal("0.02"))
    await pg.finish(bad, FAILED)

    stats = (await pg.flow_stats(days=7))[flow]
    assert stats["runs"] == 2 and stats["succeeded"] == 1
    assert stats["last_status"] == FAILED
    assert stats["cost_usd"] == 0.03

    by_provider = [
        r for r in await pg.costs_summary(group_by="provider")
        if r["provider"].startswith("stats_")
    ]
    assert by_provider == [
        {"provider": "stats_apollo", "cost_usd": 0.02},
        {"provider": "stats_openai", "cost_usd": 0.01},
    ]
    by_day = await pg.costs_summary(group_by="day")
    assert all(set(r) == {"day", "cost_usd"} for r in by_day)
    assert by_day == sorted(by_day, key=lambda r: r["day"], reverse=True)

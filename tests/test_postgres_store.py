"""Almacén de runs contra un Postgres real. Se salta sin TEST_DATABASE_URL.

    TEST_DATABASE_URL=postgresql://... uv run pytest tests/test_postgres_store.py
(el propio test aplica las migraciones)
"""

import os
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

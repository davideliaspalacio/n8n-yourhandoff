# tests/test_runs_store.py
from __future__ import annotations

from decimal import Decimal

from app.runs import MemoryRunStore


async def test_replay_of_round_trip():
    store = MemoryRunStore()
    original = await store.create("echo", {"text": "hola"})
    replay = await store.create("echo", {"text": "hola"}, replay_of=original)
    assert (await store.get(replay))["replay_of"] == original
    assert (await store.get(original))["replay_of"] is None


async def _seed(store: MemoryRunStore) -> list[str]:
    ids = []
    for i in range(3):
        rid = await store.create("echo", {"i": i})
        await store.finish(rid, "succeeded" if i % 2 == 0 else "failed")
        ids.append(rid)
    ids.append(await store.create("otro", {}))
    return ids


async def test_list_runs_filters_and_paginates():
    store = MemoryRunStore()
    ids = await _seed(store)
    page = await store.list_runs(flow="echo")
    assert page["total"] == 3 and len(page["items"]) == 3
    assert page["items"][0]["id"] == ids[2]  # más reciente primero
    assert set(page["items"][0]) == {
        "id", "flow", "status", "dry_run", "replay_of", "error", "created_at", "finished_at",
    }
    failed = await store.list_runs(flow="echo", status="failed")
    assert failed["total"] == 1
    p2 = await store.list_runs(flow="echo", page=2, per_page=2)
    assert p2["total"] == 3 and len(p2["items"]) == 1


async def test_list_runs_since_until():
    store = MemoryRunStore()
    await _seed(store)
    cutoff = (await store.list_runs())["items"][0]["created_at"]
    assert (await store.list_runs(since=cutoff))["total"] == 1
    assert (await store.list_runs(until=cutoff))["total"] == 4


async def test_flow_stats_and_costs_summary():
    store = MemoryRunStore()
    ok = await store.create("echo", {})
    await store.add_cost(ok, "openai", "chat", 1000, Decimal("0.01"))
    await store.finish(ok, "succeeded")
    bad = await store.create("echo", {})
    await store.add_cost(bad, "apollo", "enrich", 1, Decimal("0.02"))
    await store.finish(bad, "failed")

    stats = await store.flow_stats(days=7)
    assert stats["echo"]["runs"] == 2 and stats["echo"]["succeeded"] == 1
    assert stats["echo"]["last_status"] == "failed"
    assert stats["echo"]["cost_usd"] == 0.03

    by_provider = await store.costs_summary(group_by="provider")
    assert by_provider == [
        {"provider": "apollo", "cost_usd": 0.02},
        {"provider": "openai", "cost_usd": 0.01},
    ]
    by_day = await store.costs_summary(group_by="day")
    assert len(by_day) == 1 and by_day[0]["cost_usd"] == 0.03

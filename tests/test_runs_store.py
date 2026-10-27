# tests/test_runs_store.py
from __future__ import annotations

from app.runs import MemoryRunStore


async def test_replay_of_round_trip():
    store = MemoryRunStore()
    original = await store.create("echo", {"text": "hola"})
    replay = await store.create("echo", {"text": "hola"}, replay_of=original)
    assert (await store.get(replay))["replay_of"] == original
    assert (await store.get(original))["replay_of"] is None

import httpx
import pytest

from app.flows.fireflies_summaries import FirefliesSummariesInput, run, spec
from app.flows.registry import discover
from app.runs import bind_run
from tests.world import World

FF = "https://api.fireflies.ai/graphql"


async def go(clients, store, **kw):
    run_id = await store.create("fireflies-summaries", kw)
    with bind_run(store, run_id):
        result = await run(clients, FirefliesSummariesInput(**kw))
    return result, await store.get(run_id)


def test_spec_registrado():
    assert discover()["fireflies-summaries"] is spec
    assert (spec.mode, spec.kind) == ("sync", "node")


async def test_happy_path_dedupes_and_orders(clients, mock, store):
    World(mock)
    transcripts = {
        "a@x.com": [
            {"id": "t2", "date": 20, "summary": {"short_summary": " Second. "}},
            {"id": "t1", "date": 10, "summary": {"short_summary": "First."}},
        ],
        "b@x.com": [
            {"id": "t1", "date": 10, "summary": {"short_summary": "First."}},
            {"id": "t3", "date": 30, "summary": None},
        ],
    }

    def handler(request):
        import json

        email = json.loads(request.content)["variables"]["participants"][0]
        return httpx.Response(200, json={"data": {"transcripts": transcripts[email]}})

    mock.post(FF).mock(side_effect=handler)
    result, run_ = await go(clients, store, emails=["a@x.com", " b@x.com ", ""])
    assert result.meeting_summaries == ["First.", "Second."]
    assert result.meeting_count == 2
    fireflies = next(s for s in run_["steps"] if s["name"] == "fireflies")
    assert fireflies["status"] == "succeeded"


async def test_no_emails_skips_provider(clients, mock, store):
    World(mock)
    result, run_ = await go(clients, store, emails=[])
    assert result.meeting_summaries == [] and result.meeting_count == 0
    assert not run_["steps"]


@pytest.mark.parametrize(
    "response",
    [httpx.Response(500, json={}), httpx.Response(200, json={"errors": [{"message": "boom"}]})],
)
async def test_provider_error_degrades_to_empty_list(clients, mock, store, response):
    World(mock)
    mock.post(FF).mock(return_value=response)
    result, run_ = await go(clients, store, emails=["a@x.com"])
    assert result.meeting_summaries == []
    fireflies = next(s for s in run_["steps"] if s["name"] == "fireflies")
    assert fireflies["status"] == "degraded"
    assert [s["status"] for s in run_["steps"] if s["name"] == "fireflies"] == ["degraded"]

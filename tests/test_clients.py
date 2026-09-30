import httpx
import pytest

from app.clients.http import ProviderError, request_json
from app.runs import bind_run
from tests.conftest import ALMA, prompts_route


async def test_request_json_retries_on_5xx_then_succeeds(mock):
    route = mock.get("https://x.test/a").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json={"ok": 1})]
    )
    async with httpx.AsyncClient() as http:
        assert await request_json(http, "GET", "https://x.test/a", provider="x", base_delay=0) == {
            "ok": 1
        }
    assert route.call_count == 2


async def test_request_json_does_not_retry_4xx(mock):
    route = mock.get("https://x.test/a").mock(
        return_value=httpx.Response(404, json={"message": "nope"})
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(ProviderError, match="HTTP 404: nope"):
            await request_json(http, "GET", "https://x.test/a", provider="x", base_delay=0)
    assert route.call_count == 1


async def test_alma_prompt_and_google_accounts(clients, mock):
    prompts_route(mock)
    mock.get(f"{ALMA}/rest/v1/accounts").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": 1, "unipilePlatform": "Google", "unipileAccountId": "acc1"},
                {"id": 2, "unipilePlatform": "Microsoft", "unipileAccountId": "acc2"},
                {"id": 3, "unipilePlatform": "Google", "unipileAccountId": None},
            ],
        )
    )
    assert await clients.alma.get_prompt("Company Analysis (Discovery Prep)") == (
        "BASE[Company Analysis (Discovery Prep)]"
    )
    accounts = await clients.alma.get_google_accounts()
    assert [a["unipileAccountId"] for a in accounts] == ["acc1"]


async def test_apollo_records_a_credit_only_on_match(clients, mock, store):
    mock.post("https://api.apollo.io/api/v1/people/match").mock(
        side_effect=[
            httpx.Response(200, json={"person": {"id": "p1", "name": "Ana"}}),
            httpx.Response(200, json={"person": None}),
        ]
    )
    run_id = await store.create("t", {})
    with bind_run(store, run_id):
        assert (await clients.apollo.match_person("a@x.com"))["id"] == "p1"
        assert await clients.apollo.match_person("b@x.com") is None
    run = await store.get(run_id)
    assert [c["operation"] for c in run["costs"]] == ["people_match"]
    assert run["total_cost_usd"] == 0.02


async def test_hubspot_skips_blank_properties(clients, mock):
    route = mock.post("https://api.hubapi.com/crm/v3/objects/companies").mock(
        return_value=httpx.Response(201, json={"id": "c1"})
    )
    await clients.hubspot.create_company({"name": "Nido", "city": "", "phone": None, "zip": "C14"})
    import json

    assert json.loads(route.calls[0].request.content) == {
        "properties": {"name": "Nido", "zip": "C14"}
    }


async def test_slack_raises_when_not_ok(clients, mock):
    from app.clients.http import ProviderError

    mock.post("https://slack.com/api/chat.postMessage").mock(
        return_value=httpx.Response(200, json={"ok": False, "error": "channel_not_found"})
    )
    with pytest.raises(ProviderError, match="channel_not_found"):
        await clients.slack.post_message("hi")

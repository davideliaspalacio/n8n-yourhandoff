from types import SimpleNamespace

import httpx
import pytest

from app.clients.http import ProviderError, request_json
from app.clients.openai_llm import LLMClient, ensure_json_mention
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


class _FakeCompletions:
    """Guarda el mensaje enviado, para comprobar qué texto llega de verdad a OpenAI."""

    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def create(self, *, model, messages, response_format):
        self.messages = messages
        usage = SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=5,
            prompt_tokens_details=SimpleNamespace(cached_tokens=0),
        )
        return SimpleNamespace(
            id="chatcmpl-test",
            model=model,
            usage=usage,
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"summary": "ok"}'))],
        )


def _fake_openai(completions: _FakeCompletions) -> SimpleNamespace:
    return SimpleNamespace(chat=SimpleNamespace(completions=completions))


def test_ensure_json_mention_leaves_a_prompt_that_already_says_json():
    for prompt in ("Return a JSON object.", "responde en json"):
        assert ensure_json_mention(prompt) == prompt


def test_ensure_json_mention_adds_the_instruction_when_missing():
    salida = ensure_json_mention("Summarize these emails.")
    assert salida.startswith("Summarize these emails.")
    assert "json" in salida.lower()


async def test_complete_json_always_sends_the_word_json(settings, store):
    """Los prompts vienen de `handoff_prompt_library` y ninguno de los tres que
    usan los flows menciona json. Sin esta garantía OpenAI responde 400
    ("'messages' must contain the word 'json'") y el paso degrada sin resumen."""
    completions = _FakeCompletions()
    client = LLMClient(settings, client=_fake_openai(completions))
    run_id = await store.create("t", {})
    with bind_run(store, run_id):
        result = await client.complete_json("Summarize these emails.", stage="team_communications")
    enviado = completions.messages[0]["content"]
    assert "json" in enviado.lower()
    assert result.prompt == enviado
    run = await store.get(run_id)
    assert [c["operation"] for c in run["costs"]] == ["team_communications"]

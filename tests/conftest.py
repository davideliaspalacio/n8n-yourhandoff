from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest
import respx

from app.clients import Clients
from app.clients.openai_llm import LLMResult
from app.config import Settings
from app.domain.summaries import extract_summary
from app.main import create_app
from app.runs import MemoryRunStore, record_cost

ALMA = "https://alma.supabase.test"
UNIPILE = "https://api.unipile.test:13460"
EVENT_FN = "https://portal.supabase.test/functions/v1/handle-discovery-call-booked"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        api_key="test-key",
        http_retry_base_delay=0,
        openai_api_key="sk-test",
        alma_supabase_url=ALMA,
        alma_supabase_key="alma-key",
        event_function_url=EVENT_FN,
        event_function_auth_value="Bearer portal",
        unipile_base_url=UNIPILE,
        unipile_api_key="unipile-key",
        fireflies_api_key="ff-key",
        apollo_api_key="apollo-key",
        price_apollo_per_credit=0.02,
        serpapi_api_key="serp-key",
        price_serpapi_per_search=0.01,
        hubspot_token="hs-token",
        hubspot_portal_id="999000111",
        slack_bot_token="xoxb-test",
        slack_channel_id="C_TEST",
    )


class FakeLLM:
    """Responde según una palabra clave del prompt y registra coste como el real."""

    def __init__(self) -> None:
        self.prompts: list[tuple[str, str]] = []
        self.fail_stages: set[str] = set()

    async def complete_json(self, prompt: str, *, stage: str) -> LLMResult:
        self.prompts.append((stage, prompt))
        if stage in self.fail_stages:
            raise RuntimeError(f"llm down for {stage}")
        content = json.dumps({"summary": f"{stage} summary"})
        if "Contact: " in prompt:
            name = prompt.split("Contact: ", 1)[1].split("\n", 1)[0]
            content = json.dumps({"summary": f"Bio of {name}"})
        await record_cost("openai", stage, 150, Decimal("0.001"), {})
        return LLMResult(
            content=content,
            summary=extract_summary(content),
            response_id="chatcmpl-1",
            model="gpt-4.1",
            input_tokens=100,
            cached_tokens=0,
            output_tokens=50,
            cost_usd=Decimal("0.001"),
            latency_ms=5,
            prompt=prompt,
        )


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
async def clients(settings, fake_llm):
    c = Clients.build(settings, httpx.AsyncClient())
    c.llm = fake_llm  # type: ignore[assignment]
    yield c
    await c.aclose()


@pytest.fixture
def store() -> MemoryRunStore:
    return MemoryRunStore()


@pytest.fixture
def mock():
    with respx.mock(assert_all_called=False) as router:
        yield router


def prompts_route(mock):
    def handler(request: httpx.Request) -> httpx.Response:
        title = request.url.params["title"].removeprefix("eq.")
        return httpx.Response(200, json=[{"prompt": f"BASE[{title}]"}])

    return mock.get(f"{ALMA}/rest/v1/handoff_prompt_library").mock(side_effect=handler)


@pytest.fixture
async def api(settings, clients, store):
    app = create_app(settings, clients=clients, store=store)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            yield client

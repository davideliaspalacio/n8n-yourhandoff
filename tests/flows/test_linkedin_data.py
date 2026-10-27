import httpx
import pytest
from pydantic import ValidationError

from app.clients.http import ProviderError
from app.flows.linkedin_data import LinkedinDataInput, run, spec
from app.flows.registry import discover
from app.runs import bind_run
from tests.world import World

APOLLO_ORG = "https://api.apollo.io/api/v1/organizations/enrich"


async def go(clients, store, **kw):
    run_id = await store.create("linkedin-data", kw)
    with bind_run(store, run_id):
        result = await run(clients, LinkedinDataInput(**kw))
    return result, await store.get(run_id)


def test_spec_registrado():
    assert discover()["linkedin-data"] is spec
    assert (spec.mode, spec.kind) == ("sync", "node")


def test_company_url_obligatorio():
    with pytest.raises(ValidationError):
        LinkedinDataInput(companyUrl="  ")


async def test_happy_path_shape_and_cost(clients, mock, store):
    World(mock)
    result, run_ = await go(clients, store, companyUrl="https://www.staynido.com")
    assert result.foundLinkedIn is True
    assert result.linkedIn["name"] == "Nido" and result.linkedIn["employeeCount"] == 150
    assert result.crm["companyLinkedinUid"] == "1389763"
    assert result.crm["companyGrowthMetrics"][0] == {"monthRange": 6, "growthPercentage": 12.8049}
    assert "aiSummary" not in result.crm
    assert [c["provider"] for c in run_["costs"]] == ["apollo"]
    assert [s["name"] for s in run_["steps"]] == ["apollo"]


async def test_unknown_company_returns_empty_blocks(clients, mock, store):
    World(mock, org=None)
    result, _ = await go(clients, store, companyUrl="ghost.io")
    assert result.foundLinkedIn is False
    assert result.linkedIn["name"] == "" and result.crm["companyWebsiteUrl"] == "ghost.io"


async def test_apollo_error_fails_like_enrich_company(clients, mock, store):
    World(mock)
    mock.get(APOLLO_ORG).mock(return_value=httpx.Response(401, json={"error": "bad key"}))
    with pytest.raises(ProviderError):
        await go(clients, store, companyUrl="staynido.com")

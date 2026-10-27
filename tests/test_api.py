import asyncio

import httpx
import pytest

from app.main import create_app
from tests.test_flows import BOOKING
from tests.world import World

H = {"X-API-Key": "test-key"}


async def test_health_and_docs_are_public(api):
    assert (await api.get("/health")).json() == {"status": "ok"}
    assert (await api.get("/docs")).status_code == 200
    spec = (await api.get("/openapi.json")).json()
    assert "/v1/flows/discovery-call-booked" in spec["paths"]
    assert "/v1/companies/enrich" in spec["paths"]


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}])
async def test_v1_requires_api_key(api, headers):
    r = await api.post("/v1/contacts/enrich", json={"emails": []}, headers=headers)
    assert r.status_code == 401


async def test_empty_api_key_setting_rejects_everything(settings, clients, store):
    settings.api_key = ""
    app = create_app(settings, clients=clients, store=store)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.post(
                "/v1/contacts/enrich", json={"emails": []}, headers={"X-API-Key": ""}
            )
    assert r.status_code == 401


async def test_discovery_call_runs_in_background(api, mock):
    World(mock)
    r = await api.post("/v1/flows/discovery-call-booked", json=BOOKING, headers=H)
    assert r.status_code == 202
    status_url = r.json()["status_url"]
    for _ in range(50):
        run = (await api.get(status_url, headers=H)).json()
        if run["status"] != "running":
            break
        await asyncio.sleep(0.02)
    assert run["status"] == "succeeded", run["error"]
    assert run["output"]["hubspot"]["companyId"] == "c-new"
    assert run["total_cost_usd"] > 0


async def test_unknown_run_is_404(api):
    assert (await api.get("/v1/runs/nope", headers=H)).status_code == 404


async def test_sync_endpoint_returns_run_id_and_records_cost(api, mock):
    World(mock)
    r = await api.post("/v1/companies/enrich", json={"companyUrl": "staynido.com"}, headers=H)
    assert r.status_code == 200 and r.json()["crm"]["companyName"] == "Nido"
    run = (await api.get(f"/v1/runs/{r.headers['X-Run-Id']}", headers=H)).json()
    assert run["status"] == "succeeded" and len(run["costs"]) == 4


async def test_generic_route_matches_alias(api, mock):
    World(mock)
    alias = await api.post("/v1/companies/enrich", json={"companyUrl": "staynido.com"}, headers=H)
    World(mock)
    generic = await api.post(
        "/v1/flows/enrich-company", json={"companyUrl": "staynido.com"}, headers=H
    )
    assert generic.status_code == 200
    alias_body, generic_body = alias.json(), generic.json()
    # generatedAt es un timestamp volátil generado en cada llamada; se excluye de la comparación.
    alias_body["Company"].pop("generatedAt", None)
    generic_body["Company"].pop("generatedAt", None)
    assert generic_body == alias_body
    run = (await api.get(f"/v1/runs/{generic.headers['X-Run-Id']}", headers=H)).json()
    assert run["flow"] == "enrich-company" and run["status"] == "succeeded"
    # El alias debe registrar el run con el slug, igual que la ruta genérica.
    alias_run = (await api.get(f"/v1/runs/{alias.headers['X-Run-Id']}", headers=H)).json()
    assert alias_run["flow"] == "enrich-company"


async def test_missing_credential_is_503(api, settings, mock):
    settings.apollo_api_key = ""
    World(mock)
    r = await api.post("/v1/companies/enrich", json={"companyUrl": "staynido.com"}, headers=H)
    assert r.status_code == 503 and "APOLLO_API_KEY" in r.json()["detail"]
    run = (await api.get(f"/v1/runs/{r.headers['X-Run-Id']}", headers=H)).json()
    assert run["status"] == "failed"


async def test_provider_error_is_502(api, mock):
    mock.post("https://api.hubapi.com/crm/v3/objects/notes").mock(
        return_value=httpx.Response(400, json={"message": "bad"})
    )
    r = await api.post(
        "/v1/crm/notes",
        headers=H,
        json={"content": "x", "associated_hubspot_id": "1", "contact_company_deal": "company"},
    )
    assert r.status_code == 502 and r.json()["detail"] == "hubspot: HTTP 400: bad"

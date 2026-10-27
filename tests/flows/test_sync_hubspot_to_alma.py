import httpx
import pytest

from app.clients.http import ProviderError
from app.flows.registry import discover
from app.flows.sync_hubspot_to_alma import SyncHubspotToAlmaInput, run, since_to_epoch_ms, spec
from app.runs import bind_run
from tests.conftest import ALMA

HS = "https://api.hubapi.com"


def contact(cid, email, company_id=None):
    props = {"email": email, "firstname": "A", "lastname": "B", "jobtitle": "CEO", "phone": "1"}
    if company_id:
        props["associatedcompanyid"] = company_id
    return {"id": cid, "properties": props}


class Env:
    """HubSpot y Alma simulados con respx; registra las escrituras a Alma."""

    def __init__(self, mock, pages, companies=None, link_status=201):
        self.search = mock.post(f"{HS}/crm/v3/objects/contacts/search").mock(side_effect=pages)
        companies = companies or {"77": {"name": "Nido", "domain": "staynido.com"}}
        for cid, props in companies.items():
            mock.get(f"{HS}/crm/v3/objects/companies/{cid}").mock(
                return_value=httpx.Response(200, json={"id": cid, "properties": props})
            )
        self.companies = mock.post(f"{ALMA}/rest/v1/companies").mock(
            return_value=httpx.Response(201, json=[{"id": "co-1"}])
        )
        self.contacts = mock.post(f"{ALMA}/rest/v1/contacts").mock(
            return_value=httpx.Response(201, json=[{"id": "ct-1"}])
        )
        self.links = mock.post(f"{ALMA}/rest/v1/contact_company").mock(
            return_value=httpx.Response(link_status, json=[{}] if link_status < 400 else {})
        )
        self.lookup = mock.get(f"{ALMA}/rest/v1/companies").mock(
            return_value=httpx.Response(200, json=[{"id": "co-1"}])
        )

    @property
    def writes(self):
        return self.companies.call_count + self.contacts.call_count + self.links.call_count


def page(results, after=None):
    body = {"results": results}
    if after:
        body["paging"] = {"next": {"after": after}}
    return httpx.Response(200, json=body)


async def go(clients, store, **kw):
    run_id = await store.create("sync-hubspot-to-alma", kw)
    with bind_run(store, run_id):
        result = await run(clients, SyncHubspotToAlmaInput(**kw))
    return result, await store.get(run_id)


def test_registrado_como_async():
    assert discover()["sync-hubspot-to-alma"] is spec
    assert spec.mode == "async"


def test_since_to_epoch_ms():
    assert since_to_epoch_ms("1792728000000") == 1792728000000
    assert since_to_epoch_ms("2026-01-01T00:00:00Z") == 1767225600000
    assert since_to_epoch_ms("2026-01-01T00:00:00") == 1767225600000
    assert since_to_epoch_ms(None, now_ms=7_200_000) == 3_600_000


async def test_happy_path(clients, mock, store):
    env = Env(
        mock,
        [
            page(
                [
                    contact("1", "fred@fireflies.ai", "77"),
                    contact("2", "nocompany@x.com"),
                    contact("3", "ok@staynido.com", "77"),
                ]
            )
        ],
    )
    result, run_ = await go(clients, store, since="1000")
    assert result.contacts_found == 3
    assert (result.skipped_test_accounts, result.skipped_no_company) == (1, 1)
    assert (result.companies_upserted, result.contacts_upserted, result.links_upserted) == (1, 1, 1)
    body = env.search.calls[0].request.content.decode()
    assert '"value":"1000"' in body.replace(" ", "")
    assert env.contacts.calls[0].request.url.params["on_conflict"] == "hubspot_id"
    assert "merge-duplicates" in env.contacts.calls[0].request.headers["Prefer"]
    names = [s["name"] for s in run_["steps"]]
    assert names == ["hubspot_search", "filter", "companies_upsert", "contacts_upsert"]


async def test_pagination_consumes_all_pages(clients, mock, store):
    env = Env(
        mock,
        [page([contact("1", "a@x.com", "77")], after="1"), page([contact("2", "b@x.com", "77")])],
    )
    result, _ = await go(clients, store, since="1000")
    assert result.contacts_found == 2 and env.search.call_count == 2
    assert b'"after":"1"' in env.search.calls[1].request.content.replace(b" ", b"")


async def test_dry_run_writes_nothing(clients, mock, store):
    env = Env(mock, [page([contact("3", "ok@staynido.com", "77")])])
    result, run_ = await go(clients, store, since="1000", dry_run=True)
    assert env.writes == 0
    assert result.would_upsert_companies == ["Nido"]
    assert result.would_upsert_contacts == ["ok@staynido.com"]
    assert run_["steps"][-1]["status"] == "skipped"


async def test_duplicate_link_is_skipped(clients, mock, store):
    Env(mock, [page([contact("3", "ok@staynido.com", "77")])], link_status=409)
    result, run_ = await go(clients, store, since="1000")
    assert result.links_skipped == 1 and result.links_upserted == 0
    assert result.contacts_upserted == 1
    assert run_["status"] != "failed"


async def test_company_without_name_is_skipped(clients, mock, store):
    env = Env(
        mock,
        [page([contact("3", "ok@x.com", "77")])],
        companies={"77": {"name": " ", "domain": "x.com"}},
    )
    result, _ = await go(clients, store, since="1000")
    assert result.skipped_no_company_name == 1 and env.writes == 0


async def test_company_fetch_error_degrades_and_continues(clients, mock, store):
    env = Env(
        mock,
        [page([contact("3", "a@x.com", "88"), contact("4", "b@x.com", "77")])],
    )
    mock.get(f"{HS}/crm/v3/objects/companies/88").mock(return_value=httpx.Response(400, json={}))
    result, run_ = await go(clients, store, since="1000")
    assert result.contacts_upserted == 1 and result.skipped_errors == 1
    assert env.contacts.call_count == 1
    assert any(s["status"] == "degraded" for s in run_["steps"])


async def test_hubspot_search_failure_fails_the_run(clients, mock, store):
    mock.post(f"{HS}/crm/v3/objects/contacts/search").mock(return_value=httpx.Response(401))
    with pytest.raises(ProviderError):
        await go(clients, store, since="1000")

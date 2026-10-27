import httpx

from app.flows.news_jobs_search import NewsJobsSearchInput, run, spec
from app.flows.registry import discover
from app.runs import bind_run
from tests.world import World

SERP = "https://serpapi.com/search.json"


async def go(clients, store, **kw):
    run_id = await store.create("news-jobs-search", kw)
    with bind_run(store, run_id):
        result = await run(clients, NewsJobsSearchInput(**kw))
    return result, await store.get(run_id)


def test_spec_registrado():
    assert discover()["news-jobs-search"] is spec
    assert (spec.mode, spec.kind) == ("sync", "node")


async def test_happy_path_shape_and_costs(clients, mock, store):
    world = World(mock)
    serp = mock.get(SERP).mock(side_effect=world._serp)  # espía las llamadas
    result, run_ = await go(clients, store, companyName="Nido")
    assert result.google_news == [{"title": "Nido expands to Monterrey"}]
    assert result.google_jobs == [{"title": "Operations Manager"}]
    queries = {c.request.url.params["engine"]: c.request.url.params["q"] for c in serp.calls}
    assert queries == {"google_news": '"Nido"', "google_jobs": "Nido"}
    assert [c["provider"] for c in run_["costs"]] == ["serpapi", "serpapi"]
    assert {s["name"] for s in run_["steps"]} == {"serpapi_google_news", "serpapi_google_jobs"}


async def test_name_falls_back_to_url(clients, mock, store):
    world = World(mock)
    serp = mock.get(SERP).mock(side_effect=world._serp)
    await go(clients, store, companyName=" ", companyUrl="https://www.staynido.com")
    assert serp.calls[0].request.url.params["q"].strip('"') != ""


async def test_provider_error_degrades_to_empty_list(clients, mock, store):
    World(mock)
    mock.get(SERP).mock(
        side_effect=lambda r: httpx.Response(500, json={})
        if r.url.params["engine"] == "google_news"
        else httpx.Response(200, json={"jobs_results": [{"title": "Operations Manager"}]})
    )
    result, run_ = await go(clients, store, companyName="Nido")
    assert result.google_news == []
    assert result.google_jobs == [{"title": "Operations Manager"}]
    news = next(s for s in run_["steps"] if s["name"] == "serpapi_google_news")
    assert news["status"] == "degraded"

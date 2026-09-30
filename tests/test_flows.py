import json

import httpx

from app.flows.attendees import enrich_contacts
from app.flows.company import enrich_company
from app.flows.crm import CompanyCrmInput, upsert_company
from app.flows.discovery_call import DiscoveryCallInput, company_package, run_discovery_call
from app.flows.team_communications import search_team_communications
from app.runs import bind_run
from tests.world import World

BOOKING = {
    "companyName": "N/A",
    "primaryContactName": "Emanuel Ituarte",
    "primaryContactEmail": "Emanuel@staynido.com",
    "companyUrl": "staynido.com",
    "guestEmails": [
        {"email": "emanuel@staynido.com", "firstName": "Emanuel", "lastName": "Ituarte"},
        {"email": "carlos@staynido.com", "name": "Carlos Llewellyn"},
    ],
    "eventTitle": "Nido + Handoff | Discovery Call",
    "organizerEmail": "david@yourhandoff.com",
}


# --- Search Team Communications ----------------------------------------------


async def test_communications_summarizes_and_skips_internal(clients, mock, fake_llm):
    world = World(mock)
    result = await search_team_communications(
        clients, ["emanuel@staynido.com", "david@yourhandoff.com"]
    )
    assert result.contacts == ["emanuel@staynido.com"]
    # El mismo correo en dos buzones cuenta una vez.
    assert result.email_count == 1 and result.meeting_count == 1
    assert result.summary == "team_communications summary"
    assert world.unipile.calls[0].request.url.params["any_email"] == "emanuel@staynido.com"
    stage, prompt = fake_llm.prompts[0]
    assert prompt.startswith("BASE[Communications Analysis (Email)]\nEMAIL CONTEXT:")
    assert "Staffing for Mexico City" in prompt and "Meeting 1: Intro call." in prompt


async def test_communications_without_history_skips_the_model(clients, mock, fake_llm):
    World(mock, emails={"items": []})
    mock.post("https://api.fireflies.ai/graphql").mock(
        return_value=httpx.Response(200, json={"data": {"transcripts": []}})
    )
    result = await search_team_communications(clients, ["emanuel@staynido.com"])
    assert result.summary == "No communications found" and not fake_llm.prompts


# --- Enrich External Attendees -------------------------------------------------


async def test_each_bio_stays_with_its_own_contact(clients, mock):
    """Regresión del bug de n8n: con el primero sin coincidencia en Apollo, la bio
    del segundo acababa asignada al primero."""
    World(mock)
    contacts = await enrich_contacts(clients, ["unknown@staynido.com", "emanuel@staynido.com"])
    by_email = {c.email: c for c in contacts}
    assert not by_email["unknown@staynido.com"].enriched
    assert by_email["unknown@staynido.com"].ai_summary == ""
    assert by_email["emanuel@staynido.com"].ai_summary == "Bio of Emanuel Ituarte"
    assert (
        by_email["emanuel@staynido.com"].linkedin_url
        == "http://www.linkedin.com/in/emanuel-ituarte"
    )


async def test_domain_mismatch_is_flagged_in_prompt(clients, mock, fake_llm):
    World(mock)
    [carlos] = await enrich_contacts(clients, ["carlos@staynido.com"])
    assert carlos.company_domain_mismatch
    assert "DATA VERIFICATION NOTE" in fake_llm.prompts[0][1]


async def test_one_failing_summary_does_not_sink_the_rest(clients, mock, fake_llm):
    World(mock)
    fake_llm.fail_stages.add("contact_summary")
    contacts = await enrich_contacts(clients, ["emanuel@staynido.com"])
    assert contacts[0].error.startswith("openai:") and contacts[0].first_name == "Emanuel"


# --- Enrich Company --------------------------------------------------------------


async def test_company_keeps_the_n8n_webhook_shape(clients, mock, store):
    World(mock)
    run_id = await store.create("t", {})
    with bind_run(store, run_id):
        result = await enrich_company(clients, "https://www.staynido.com")
    assert result.crm["companyName"] == "Nido"
    assert result.crm["companyGrowthMetrics"][0] == {"monthRange": 6, "growthPercentage": 12.8049}
    assert result.crm["aiSummary"] == {"summary": "company_summary summary"}
    assert result.industryHubspot == "HOSPITALITY"
    assert result.Company["departmentalHeadCount"] == {"operations": 40}
    assert result.Company["googleJobs"] == [{"title": "Operations Manager"}]
    providers = sorted(c["provider"] for c in (await store.get(run_id))["costs"])
    assert providers == ["apollo", "openai", "serpapi", "serpapi"]


async def test_company_package_survives_missing_growth():
    """Regresión: n8n reventaba con growthMetrics vacío."""
    data = DiscoveryCallInput(
        primaryContactEmail="a@x.com", companyName="N/A", companyUrl="acme.io"
    )
    package = company_package(None, data)
    assert package.companyName == "Acme.io"
    assert package.headcountGrowth6Months is None


# --- CRM ---------------------------------------------------------------------------


async def test_upsert_company_updates_when_domain_exists(clients, mock):
    world = World(mock, existing_company="c-42")
    result = await upsert_company(
        clients,
        CompanyCrmInput(
            companyName="Nido",
            websiteUrl="http://www.staynido.com",
            industry="Hospitality",
            aiSummary="Nido summary",
        ),
    )
    assert result.hubspotId == "c-42" and not result.created and result.aiNoteSuccess
    search = json.loads(world.company_search.calls[0].request.content)
    assert search["filterGroups"][0]["filters"][0]["value"] == "staynido.com"
    sent = json.loads(world.company_update.calls[0].request.content)["properties"]
    assert sent["industry"] == "HOSPITALITY" and sent["ai_summary"] == "Nido summary"
    assert not world.company_create.called


# --- Orquestador ---------------------------------------------------------------------


async def test_discovery_call_end_to_end(clients, mock, store):
    world = World(mock)
    run_id = await store.create("discovery_call_booked", BOOKING)
    with bind_run(store, run_id):
        result = await run_discovery_call(clients, DiscoveryCallInput(**BOOKING))

    assert result.attendee_emails == ["emanuel@staynido.com", "carlos@staynido.com"]
    assert result.hubspot == {
        "companyId": "c-new",
        "contactIds": {"emanuel@staynido.com": "hs-emanuel", "carlos@staynido.com": "hs-carlos"},
    }
    initial, reply = world.slack_texts()
    assert initial["channel"] == "C_TEST" and "thread_ts" not in initial
    assert reply["thread_ts"] == "ts0"
    assert initial["text"].startswith(
        "*--- Discovery Call Booked ---*\n🏢 Nido\n📅 Thursday, October 1, 2026 at 10:00 AM EDT"
    )
    assert "👤 Emanuel Ituarte, Director of Operations" in initial["text"]
    assert "https://app.hubspot.com/contacts/999000111/record/0-2/c-new" in reply["text"]
    assert "🤖 Bio: Bio of Carlos Llewellyn" in reply["text"]
    # Regresión del bug 1: el resumen de comunicaciones llega a Slack.
    assert reply["text"].endswith("🤖 team_communications summary")
    # Los contactos quedan asociados a la empresa y con su nota.
    assert world.assoc.call_count == 2 and world.notes.call_count == 3
    run = await store.get(run_id)
    assert [s["name"] for s in run["steps"]][0] == "event_function"
    assert {s["name"] for s in run["steps"]} >= {
        "team_communications",
        "enrich_contacts",
        "enrich_company",
        "hubspot_company",
        "slack",
    }


async def test_discovery_call_falls_back_to_booking_names(clients, mock):
    """Sin coincidencia en Apollo, el contacto se crea con el nombre del booking."""
    world = World(mock, people={})
    await run_discovery_call(clients, DiscoveryCallInput(**BOOKING))
    sent = {
        json.loads(c.request.content)["inputs"][0]["id"]: json.loads(c.request.content)["inputs"][
            0
        ]["properties"]
        for c in world.contacts.calls
    }
    assert sent["carlos@staynido.com"]["firstname"] == "Carlos"
    assert sent["carlos@staynido.com"]["lastname"] == "Llewellyn"
    assert sent["emanuel@staynido.com"]["lastname"] == "Ituarte"


async def test_discovery_call_degrades_when_research_fails(clients, mock, fake_llm):
    world = World(mock)
    mock.get("https://api.apollo.io/api/v1/organizations/enrich").mock(
        return_value=httpx.Response(500)
    )
    fake_llm.fail_stages.add("team_communications")
    import app.flows.discovery_call as dc

    dc.COMPANY_RETRY_SECONDS = 0
    result = await run_discovery_call(clients, DiscoveryCallInput(**BOOKING))
    assert result.company == {}
    _, reply = world.slack_texts()
    assert reply["text"].startswith("🏢 *Staynido.com*")
    assert reply["text"].endswith("🤖 No communications found")


async def test_dry_run_writes_nothing(clients, mock):
    world = World(mock)
    result = await run_discovery_call(
        clients,
        DiscoveryCallInput(
            **BOOKING,
            dry_run=True,
            eventStartDateTime="2026-10-01T14:00:00Z",
            eventTimeZone="America/New_York",
        ),
    )
    assert not world.event.called and not world.slack.called
    assert not world.contacts.called and not world.company_create.called
    assert "Thursday, October 1, 2026" in result.slack["initial"]
    assert "HubSpot" not in result.slack["reply"]


async def test_company_is_not_retried_without_credentials(clients, settings, mock):
    settings.apollo_api_key = ""
    World(mock)
    import time

    started = time.perf_counter()
    result = await run_discovery_call(clients, DiscoveryCallInput(**BOOKING, dry_run=True))
    assert result.company == {} and time.perf_counter() - started < 1

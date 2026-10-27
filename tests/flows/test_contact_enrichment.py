import json

import pytest
from pydantic import ValidationError

from app.flows.contact_enrichment import ContactEnrichmentInput, run, spec
from app.flows.registry import discover
from app.runs import bind_run
from tests.world import World

EMAIL = "emanuel@staynido.com"


async def go(clients, store, **kw):
    run_id = await store.create("contact-enrichment", kw)
    with bind_run(store, run_id):
        result = await run(clients, ContactEnrichmentInput(**kw))
    return result, await store.get(run_id)


def test_spec_registrado():
    assert discover()["contact-enrichment"] is spec
    assert (spec.mode, spec.kind) == ("sync", "workflow")


@pytest.mark.parametrize("email", ["", "   "])
def test_email_obligatorio(email):
    with pytest.raises(ValidationError):
        ContactEnrichmentInput(email=email)


async def test_happy_path_upserts_mapped_fields(clients, mock, store):
    world = World(mock)
    result, run_ = await go(clients, store, email=f"  {EMAIL} ", companyHubspotId="77")
    assert result.enriched and not result.dry_run and result.would_upsert is None
    assert result.contact.hubspotId == "hs-emanuel" and result.contact.associatedCompany
    sent = json.loads(world.contacts.calls[0].request.content)["inputs"][0]
    assert sent["id"] == EMAIL
    props = sent["properties"]
    assert props["firstname"] == "Emanuel" and props["lastname"] == "Ituarte"
    assert props["jobtitle"] == "Director of Operations" and props["country"] == "Argentina"
    assert props["ai_contact_summary"] == "Bio of Emanuel Ituarte"
    assert "linkedin_uid" not in props  # vacío: se descarta
    assert "/contacts/hs-emanuel/associations/default/companies/77" in str(
        world.assoc.calls[0].request.url
    )
    steps = [s["name"] for s in run_["steps"]]
    assert "enrich" in steps and "crm_upsert" in steps
    enrich = next(s for s in run_["steps"] if s["name"] == "enrich")
    assert enrich["detail"]["enriched"] is True


async def test_not_found_still_upserts_empty_fields(clients, mock, store):
    world = World(mock, people={})
    result, run_ = await go(clients, store, email="ghost@nowhere.com")
    assert result.enriched is False and result.contact is not None
    assert world.contacts.call_count == 1
    sent = json.loads(world.contacts.calls[0].request.content)["inputs"][0]
    assert sent["id"] == "ghost@nowhere.com"
    # El client descarta propiedades vacías: solo viaja el email.
    assert sent["properties"] == {"email": "ghost@nowhere.com"}
    assert not world.notes.called
    assert not world.assoc.called
    enrich = next(s for s in run_["steps"] if s["name"] == "enrich")
    assert enrich["detail"]["enriched"] is False


async def test_dry_run_writes_nothing(clients, mock, store):
    world = World(mock)
    result, run_ = await go(clients, store, email=EMAIL, companyHubspotId="77", dry_run=True)
    assert result.dry_run and result.contact is None and result.enriched
    assert not world.contacts.called and not world.assoc.called and not world.notes.called
    w = result.would_upsert
    assert (w.email, w.firstName, w.companyHubspotId) == (EMAIL, "Emanuel", "77")
    assert w.aiContactSummary == "Bio of Emanuel Ituarte" and w.linkedinUid == ""
    crm = next(s for s in run_["steps"] if s["name"] == "crm_upsert")
    assert crm["status"] == "skipped"

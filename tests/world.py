"""Un mundo simulado con todos los proveedores, para probar flujos enteros."""

from __future__ import annotations

import json

import httpx

from tests.conftest import ALMA, EVENT_FN, UNIPILE, prompts_route

PEOPLE = {
    "emanuel@staynido.com": {
        "id": "p1",
        "name": "Emanuel Ituarte",
        "first_name": "Emanuel",
        "last_name": "Ituarte",
        "title": "Director of Operations",
        "email": "emanuel@staynido.com",
        "linkedin_url": "http://www.linkedin.com/in/emanuel-ituarte ",
        "country": "Argentina",
        "organization": {"name": "Nido"},
        "employment_history": [
            {"title": "Director of Operations", "organization_name": "Nido", "current": True}
        ],
    },
    "carlos@staynido.com": {
        "id": "p2",
        "name": "Carlos Llewellyn",
        "first_name": "Carlos",
        "last_name": "Llewellyn",
        "title": "Regional General Manager",
        "city": "Mexico City",
        "state": "Mexico City",
        "organization": {"name": "Oasis"},
    },
}

ORG = {
    "name": "Nido",
    "website_url": "http://www.staynido.com",
    "linkedin_url": "http://www.linkedin.com/company/staynido",
    "linkedin_uid": "1389763",
    "facebook_url": "https://www.facebook.com/StayNido",
    "primary_phone": {"number": "+54 9 11 4030-9431", "sanitized_number": "+5491140309431"},
    "industry": "hospitality",
    "street_address": "4450 Juncal",
    "city": "Buenos Aires",
    "state": "Autonomous City of Buenos Aires",
    "country": "Argentina",
    "postal_code": "C1425BAB",
    "short_description": "Nido is a premium short-term rental provider.",
    "estimated_num_employees": 150,
    "annual_revenue": 8438000,
    "annual_revenue_printed": "8.4M",
    "founded_year": 2006,
    "organization_headcount_six_month_growth": 0.12804878,
    "organization_headcount_twelve_month_growth": 0.3719512,
    "organization_headcount_twenty_four_month_growth": 0.6890243,
    "departmental_head_count": {"operations": 40, "legal": 0},
}

UNIPILE_EMAILS = {
    "items": [
        {
            "object": "Email",
            "message_id": "m1",
            "date": "2026-09-01T10:00:00Z",
            "subject": "Staffing for Mexico City",
            "body_plain": "Hi, we need 3 ops people.",
            "from_attendee": {"identifier": "emanuel@staynido.com"},
            "to_attendees": [{"identifier": "david@yourhandoff.com"}],
        },
    ]
}


class World:
    def __init__(self, mock, *, people=None, org=ORG, emails=UNIPILE_EMAILS, existing_company=None):
        self.mock = mock
        self.people = PEOPLE if people is None else people
        prompts_route(mock)
        mock.get(f"{ALMA}/rest/v1/accounts").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"id": 1, "unipilePlatform": "Google", "unipileAccountId": "acc1"},
                    {"id": 2, "unipilePlatform": "Google", "unipileAccountId": "acc2"},
                ],
            )
        )
        self.event = mock.post(EVENT_FN).mock(
            return_value=httpx.Response(
                200,
                json={
                    "Event": {
                        "startDateTime": "2026-10-01T14:00:00Z",
                        "timeZone": "America/New_York",
                        "ExternalAttendees": [
                            {"email": "Carlos@staynido.com"},
                            {"email": "david@yourhandoff.com"},
                        ],
                    }
                },
            )
        )
        self.unipile = mock.get(f"{UNIPILE}/api/v1/emails").mock(
            return_value=httpx.Response(200, json=emails)
        )
        mock.post("https://api.fireflies.ai/graphql").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "transcripts": [
                            {"id": "t1", "date": 1, "summary": {"short_summary": "Intro call."}}
                        ]
                    }
                },
            )
        )
        mock.post("https://api.apollo.io/api/v1/people/match").mock(side_effect=self._person)
        mock.get("https://api.apollo.io/api/v1/organizations/enrich").mock(
            return_value=httpx.Response(200, json={"organization": org})
        )
        mock.get("https://serpapi.com/search.json").mock(side_effect=self._serp)
        hs = "https://api.hubapi.com"
        self.company_search = mock.post(f"{hs}/crm/v3/objects/companies/search").mock(
            return_value=httpx.Response(
                200, json={"results": [{"id": existing_company}] if existing_company else []}
            )
        )
        self.company_create = mock.post(f"{hs}/crm/v3/objects/companies").mock(
            return_value=httpx.Response(201, json={"id": "c-new"})
        )
        self.company_update = mock.patch(url__regex=rf"{hs}/crm/v3/objects/companies/\w+").mock(
            return_value=httpx.Response(200, json={"id": existing_company or "c-new"})
        )
        self.contacts = mock.post(f"{hs}/crm/v3/objects/contacts/batch/upsert").mock(
            side_effect=self._contact
        )
        self.assoc = mock.put(url__regex=rf"{hs}/crm/v4/objects/contacts/.*").mock(
            return_value=httpx.Response(200, json={})
        )
        self.notes = mock.post(f"{hs}/crm/v3/objects/notes").mock(
            return_value=httpx.Response(201, json={"id": "n1"})
        )
        self.slack = mock.post("https://slack.com/api/chat.postMessage").mock(
            side_effect=lambda r: httpx.Response(
                200, json={"ok": True, "ts": f"ts{len(self.slack.calls)}"}
            )
        )

    def _person(self, request):
        email = json.loads(request.content)["email"]
        return httpx.Response(200, json={"person": self.people.get(email)})

    def _serp(self, request):
        if request.url.params["engine"] == "google_news":
            return httpx.Response(
                200, json={"news_results": [{"title": "Nido expands to Monterrey"}]}
            )
        return httpx.Response(200, json={"jobs_results": [{"title": "Operations Manager"}]})

    def _contact(self, request):
        email = json.loads(request.content)["inputs"][0]["id"]
        return httpx.Response(200, json={"results": [{"id": f"hs-{email.split('@')[0]}"}]})

    def slack_texts(self) -> list[dict]:
        return [json.loads(c.request.content) for c in self.slack.calls]

from app.domain.emails import (
    attendee_emails,
    clean_body,
    clean_messages,
    clean_text,
    email_context,
    external_emails,
    split_email_string,
)
from app.domain.industry import to_hubspot_industry
from app.domain.naming import company_name_from_url, domain_of, split_full_name
from app.domain.slack_messages import (
    SlackCompany,
    SlackContact,
    build_messages,
    format_event_date,
    hubspot_record_url,
)
from app.domain.summaries import extract_summary

# --- emails -----------------------------------------------------------------


def test_external_emails_dedupes_validates_and_drops_internal():
    raw = [
        " Ana.Gazarian@ems-ir.com",
        "ana.gazarian@ems-ir.com",
        "nope",
        3,
        "david@yourhandoff.com",
    ]
    assert external_emails(raw, "yourhandoff.com") == ["ana.gazarian@ems-ir.com"]


def test_split_email_string_matches_n8n_pin_data():
    value = "ana.gazarian@ems-ir.com, patricia.raventos@ems-ir.com"
    assert split_email_string(value) == ["ana.gazarian@ems-ir.com", "patricia.raventos@ems-ir.com"]


def test_attendee_emails_puts_primary_first():
    attendees = [{"email": "B@x.com"}, {"email": "luuuva@proton.me"}, {"name": "sin email"}]
    assert attendee_emails("Luuuva@proton.me", attendees) == ["luuuva@proton.me", "b@x.com"]


def test_clean_text_strips_html_and_entities():
    assert clean_text("<p>Hi&nbsp;there &amp; bye</p>\r\n\r\n\r\n\r\nok") == "Hi there & bye\n\nok"


def test_clean_body_cuts_quoted_reply_chain():
    body = "Thanks, see you Tuesday.\n[image: logo.png]\n> old\nOn Mon, Sep 1, 2026 at 9:00 AM\nAna <a@x.com> wrote:\n> quoted"
    assert clean_body(body) == "Thanks, see you Tuesday."


def _msg(**kw):
    base = {
        "object": "Email",
        "message_id": "m1",
        "date": "2026-09-01T10:00:00Z",
        "subject": "Hello",
        "body_plain": "Body",
        "from_attendee": {"identifier": "a@x.com"},
        "to_attendees": [{"identifier": "b@y.com"}],
        "cc_attendees": [],
    }
    return base | kw


def test_clean_messages_filters_dedupes_and_sorts():
    msgs = [
        _msg(message_id="m2", date="2026-09-03T10:00:00Z", body_plain="Later"),
        _msg(message_id="m1", date="2026-09-01T10:00:00Z"),
        _msg(message_id="m1", date="2026-09-02T10:00:00Z", body_plain="Newer copy"),
        _msg(message_id="m3", subject="Accepted: Discovery Call"),
        _msg(
            message_id="m4",
            body_plain="Event Name: Discovery Call\nIn this 30-minute conversation...",
        ),
        {"object": "Folder"},
    ]
    out = clean_messages(msgs)
    assert [(e.message_id, e.body) for e in out] == [("m1", "Newer copy"), ("m2", "Later")]
    assert out[0].sender == "a@x.com" and out[0].cc_recipients == "None"


def test_email_context_format():
    ctx = email_context(clean_messages([_msg()]))
    assert "From: a@x.com" in ctx and "Subject: Hello" in ctx and ctx.endswith("---")


# --- industria ----------------------------------------------------------------


def test_industry_mapping():
    assert to_hubspot_industry("hospitality") == "HOSPITALITY"
    assert (
        to_hubspot_industry("IT Services and IT Consulting")
        == "INFORMATION_TECHNOLOGY_AND_SERVICES"
    )
    assert to_hubspot_industry("Health, Wellness & Fitness") == "HEALTH_WELLNESS_AND_FITNESS"
    assert to_hubspot_industry("Staffing & Recruiting") == "STAFFING_AND_RECRUITING"
    assert to_hubspot_industry("Banking Services") == "BANKING"
    assert to_hubspot_industry("Underwater basket weaving") is None
    assert to_hubspot_industry("") is None


# --- resúmenes ----------------------------------------------------------------


def test_extract_summary_variants():
    assert extract_summary('{"summary": "A"}').text == "A"
    assert not extract_summary('{"summary": "A"}').flagged
    assert extract_summary({"briefing": "B"}).text == "B"
    assert extract_summary('{"result": "C"}').flagged
    # El bug de las comillas: un string JSON se desenvuelve.
    assert (
        extract_summary('"Nido is a hospitality company"').text == "Nido is a hospitality company"
    )
    assert extract_summary("plain text").text == "plain text"
    assert extract_summary(None).text == ""


# --- nombres ------------------------------------------------------------------


def test_naming():
    assert domain_of("http://www.staynido.com/about?x=1") == "staynido.com"
    assert company_name_from_url("proton.me") == "Proton.me"
    assert split_full_name("Ana Cristina Ibarra Villezcas") == ("Ana", "Cristina Ibarra Villezcas")
    assert split_full_name(None) == ("", "")


# --- Slack --------------------------------------------------------------------


def test_format_event_date():
    assert format_event_date("2026-10-01T14:00:00Z", "America/New_York") == (
        "Thursday, October 1, 2026 at 10:00 AM EDT"
    )
    assert format_event_date("2026-10-01T14:00:00Z", "America/Bogota").endswith("9:00 AM GMT-5")
    assert format_event_date(None, None) == "Date not available"


def test_hubspot_record_url():
    assert hubspot_record_url("999000111", "0-2", "322") == (
        "https://app.hubspot.com/contacts/999000111/record/0-2/322"
    )
    assert hubspot_record_url("999000111", "0-1", None) is None


def test_build_messages():
    contacts = [
        SlackContact(
            email="carlos@staynido.com",
            first_name="Carlos",
            last_name="Llewellyn",
            title="Regional GM",
            bio="Carlos bio",
        ),
        SlackContact(
            email="emanuel@staynido.com",
            first_name="Emanuel",
            last_name="Ituarte",
            title="Director of Operations",
            bio="Emanuel bio",
            hubspot_url="https://hs/1",
        ),
    ]
    msgs = build_messages(
        company=SlackCompany(
            name="Nido",
            about="About",
            ai_summary="Summary",
            linkedin_url="http://li",
            linkedin_uid="1389763",
            hubspot_url="https://hs/c",
        ),
        event_date="Thursday, October 1, 2026 at 10:00 AM EDT",
        primary_email="emanuel@staynido.com",
        contacts=contacts,
        team_communications="",
    )
    assert msgs.initial.startswith("*--- Discovery Call Booked ---*\n🏢 Nido\n📅 Thursday")
    assert "👤 Emanuel Ituarte, Director of Operations\n✉️ emanuel@staynido.com" in msgs.initial
    assert "*Additional Attendees:*\n👤 Carlos Llewellyn, Regional GM" in msgs.initial
    assert "💼 *LinkedIn Jobs:* https://www.linkedin.com/company/1389763/jobs" in msgs.reply
    assert "🤖 Bio: Emanuel bio\n📇 HubSpot: https://hs/1" in msgs.reply
    assert msgs.reply.endswith("🤖 No prior communications found.")

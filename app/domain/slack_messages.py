"""Los dos mensajes de Slack del flujo: el anuncio y el hilo con el detalle.

Porta "Prepare Slack Messages" con el mismo texto. Recibe datos ya resueltos
(nada de leer nodos por nombre), así se prueba sin red.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel

NO_TITLE = "N/A"
NO_BIO = "No professional summary available."


class SlackContact(BaseModel):
    email: str
    first_name: str = ""
    last_name: str = ""
    title: str = ""
    bio: str = ""
    hubspot_url: str | None = None

    @property
    def name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


class SlackCompany(BaseModel):
    name: str
    about: str = ""
    ai_summary: str = ""
    linkedin_url: str | None = None
    linkedin_uid: str | None = None
    hubspot_url: str | None = None


class SlackMessages(BaseModel):
    initial: str
    reply: str


def hubspot_record_url(portal_id: str, object_type: str, record_id: str | None) -> str | None:
    """object_type: "0-1" contacto, "0-2" empresa."""
    if not portal_id or not record_id:
        return None
    return f"https://app.hubspot.com/contacts/{portal_id}/record/{object_type}/{record_id}"


def format_event_date(start: str | None, time_zone: str | None) -> str:
    """Como `toLocaleString('en-US', {... timeZoneName: 'short'})` de n8n:
    "Wednesday, October 1, 2026 at 10:00 AM EDT"."""
    if not start:
        return "Date not available"
    try:
        moment = datetime.fromisoformat(start.replace("Z", "+00:00"))
    except ValueError:
        return start
    try:
        zone = ZoneInfo(time_zone) if time_zone else None
    except (ZoneInfoNotFoundError, ValueError):
        zone = None
    if zone is not None:
        moment = moment.astimezone(zone)
    tz_name = moment.tzname() or "UTC"
    if tz_name[:1] in "+-":
        # tzdata usa "-05" donde el navegador dice "GMT-5".
        hours = int(tz_name[:3])
        minutes = tz_name[3:]
        tz_name = f"GMT{hours:+d}" + (f":{minutes}" if minutes and minutes != "00" else "")
    hour12 = moment.hour % 12 or 12
    ampm = "AM" if moment.hour < 12 else "PM"
    return (
        f"{moment:%A}, {moment:%B} {moment.day}, {moment.year} "
        f"at {hour12}:{moment.minute:02d} {ampm} {tz_name}"
    )


def build_messages(
    *,
    company: SlackCompany,
    event_date: str,
    primary_email: str,
    contacts: list[SlackContact],
    team_communications: str,
) -> SlackMessages:
    primary = next((c for c in contacts if c.email == primary_email), None)
    if primary is None:
        primary = contacts[0] if contacts else SlackContact(email=primary_email)
    others = [c for c in contacts if c.email and c.email != primary.email]

    initial = (
        "*--- Discovery Call Booked ---*\n"
        f"🏢 {company.name}\n"
        f"📅 {event_date}\n"
        "*Primary Contact:*\n"
        f"👤 {primary.first_name} {primary.last_name}, {primary.title or NO_TITLE}\n"
        f"✉️ {primary_email}"
    )
    if others:
        initial += "\n\n*Additional Attendees:*"
        for c in others:
            initial += f"\n👤 {c.name}, {c.title or NO_TITLE}\n✉️ {c.email}"

    reply = f"🏢 *{company.name}*"
    if company.linkedin_url:
        reply += f"\n🔗 *LinkedIn:* {company.linkedin_url}"
    if company.linkedin_uid:
        reply += (
            f"\n💼 *LinkedIn Jobs:* https://www.linkedin.com/company/{company.linkedin_uid}/jobs"
        )
    if company.hubspot_url:
        reply += f"\n📇 *HubSpot:* {company.hubspot_url}"
    reply += (
        f"\n🤖 {company.ai_summary or 'No summary available.'}"
        f"\n📝 *About:* {company.about or 'No description available.'}"
        f"\n\n👤 *{primary.first_name} {primary.last_name}, {primary.title or NO_TITLE}*"
        f"\n🤖 Bio: {primary.bio or NO_BIO}"
    )
    if primary.hubspot_url:
        reply += f"\n📇 HubSpot: {primary.hubspot_url}"
    for c in others:
        reply += f"\n\n👤 *{c.name}, {c.title or NO_TITLE}*\n🤖 Bio: {c.bio or NO_BIO}"
        if c.hubspot_url:
            reply += f"\n📇 HubSpot: {c.hubspot_url}"
    reply += (
        "\n\n📧 *Team Communications (Gmail History):*"
        f"\n🤖 {team_communications or 'No prior communications found.'}"
    )
    return SlackMessages(initial=initial, reply=reply)

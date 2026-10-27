from __future__ import annotations

import time
from datetime import UTC, datetime

from pydantic import BaseModel, Field, field_validator

DEFAULT_WINDOW_MS = 60 * 60 * 1000  # el cron corre cada hora


def since_to_epoch_ms(since: str | None, *, now_ms: int | None = None) -> int:
    """`since` (ISO-8601 o epoch ms) a epoch ms; por defecto, ahora menos 1 hora."""
    if since is None or not since.strip():
        return (now_ms if now_ms is not None else int(time.time() * 1000)) - DEFAULT_WINDOW_MS
    value = since.strip()
    if value.isdigit():
        return int(value)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp() * 1000)


class SyncHubspotToAlmaInput(BaseModel):
    since: str | None = Field(
        default=None,
        description="Only contacts modified after this moment: ISO-8601 or epoch milliseconds. "
        "Defaults to one hour ago.",
    )
    dry_run: bool = Field(
        default=False, description="Read everything but write nothing to Alma; return a preview."
    )

    @field_validator("since")
    @classmethod
    def _valid_since(cls, value: str | None) -> str | None:
        # Un `since` mal formado debe dar 422, no un run fallido opaco.
        try:
            since_to_epoch_ms(value)
        except ValueError as exc:
            raise ValueError("since must be ISO-8601 or epoch milliseconds") from exc
        return value


class SyncHubspotToAlmaResult(BaseModel):
    contacts_found: int = 0
    skipped_test_accounts: int = 0
    skipped_no_company: int = 0
    skipped_no_company_name: int = 0
    skipped_errors: int = 0
    companies_upserted: int = 0
    contacts_upserted: int = 0
    links_upserted: int = 0
    links_skipped: int = 0
    dry_run: bool = False
    would_upsert_companies: list[str] | None = None
    would_upsert_contacts: list[str] | None = None

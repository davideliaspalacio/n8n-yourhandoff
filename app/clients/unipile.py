from __future__ import annotations

from ..config import require
from .base import BaseClient


class UnipileClient(BaseClient):
    provider = "unipile"

    async def list_emails(self, account_id: str, any_email: list[str], limit: int) -> list[dict]:
        base = require(self.settings.unipile_base_url, "UNIPILE_BASE_URL").rstrip("/")
        data = await self._request(
            "GET",
            f"{base}/api/v1/emails",
            headers={
                "X-API-KEY": require(self.settings.unipile_api_key, "UNIPILE_API_KEY"),
                "accept": "application/json",
            },
            params={"account_id": account_id, "any_email": ",".join(any_email), "limit": limit},
        )
        return (data or {}).get("items") or []

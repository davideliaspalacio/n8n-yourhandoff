from __future__ import annotations

from ..config import require
from .base import BaseClient
from .http import ProviderError

FIREFLIES_URL = "https://api.fireflies.ai/graphql"

_QUERY = """
query Transcripts($participants: [String!], $limit: Int) {
  transcripts(participants: $participants, limit: $limit) {
    id
    title
    date
    summary { short_summary }
  }
}
"""


class FirefliesClient(BaseClient):
    provider = "fireflies"

    async def short_summaries(self, emails: list[str], per_email: int) -> list[str]:
        """Resúmenes cortos de las reuniones más recientes con cada email, sin
        repetir la misma reunión aunque haya varios participantes en ella."""
        token = require(self.settings.fireflies_api_key, "FIREFLIES_API_KEY")
        seen: set[str] = set()
        summaries: list[tuple[float, str]] = []
        for email in emails:
            data = await self._request(
                "POST",
                FIREFLIES_URL,
                headers={"Authorization": f"Bearer {token}"},
                json={"query": _QUERY, "variables": {"participants": [email], "limit": per_email}},
            )
            if data and data.get("errors"):
                raise ProviderError(self.provider, str(data["errors"][0].get("message"))[:200])
            for t in (data or {}).get("data", {}).get("transcripts") or []:
                short = ((t or {}).get("summary") or {}).get("short_summary")
                if t.get("id") in seen or not short:
                    continue
                seen.add(t["id"])
                summaries.append((float(t.get("date") or 0), short.strip()))
        return [s for _, s in sorted(summaries)]

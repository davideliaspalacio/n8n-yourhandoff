from __future__ import annotations

from decimal import Decimal

from ..config import require
from ..runs import record_cost
from .base import BaseClient

SERPAPI_URL = "https://serpapi.com/search.json"
_RESULT_KEYS = {"google_news": "news_results", "google_jobs": "jobs_results"}


class SerpApiClient(BaseClient):
    provider = "serpapi"

    async def search(self, engine: str, query: str) -> list[dict]:
        data = await self._request(
            "GET",
            SERPAPI_URL,
            params={
                "engine": engine,
                "q": query,
                "api_key": require(self.settings.serpapi_api_key, "SERPAPI_API_KEY"),
            },
        )
        await record_cost(
            "serpapi",
            engine,
            1,
            Decimal(str(self.settings.price_serpapi_per_search)),
            {"query": query},
        )
        return (data or {}).get(_RESULT_KEYS[engine]) or []

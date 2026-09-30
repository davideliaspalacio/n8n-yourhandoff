from __future__ import annotations

from decimal import Decimal

from ..config import require
from ..runs import record_cost
from .base import BaseClient

APOLLO_URL = "https://api.apollo.io/api/v1"


class ApolloClient(BaseClient):
    provider = "apollo"

    def _headers(self) -> dict:
        return {
            "x-api-key": require(self.settings.apollo_api_key, "APOLLO_API_KEY"),
            "Cache-Control": "no-cache",
        }

    async def _charge(self, operation: str, found: bool, meta: dict) -> None:
        # Apollo cobra un crédito por coincidencia encontrada.
        if found:
            price = Decimal(str(self.settings.price_apollo_per_credit))
            await record_cost("apollo", operation, 1, price, meta)

    async def match_person(self, email: str) -> dict | None:
        data = await self._request(
            "POST", f"{APOLLO_URL}/people/match", headers=self._headers(), json={"email": email}
        )
        person = (data or {}).get("person")
        await self._charge("people_match", bool(person), {"email": email})
        return person or None

    async def enrich_organization(self, domain: str) -> dict | None:
        data = await self._request(
            "GET",
            f"{APOLLO_URL}/organizations/enrich",
            headers=self._headers(),
            params={"domain": domain},
        )
        org = (data or {}).get("organization")
        await self._charge("organizations_enrich", bool(org), {"domain": domain})
        return org or None

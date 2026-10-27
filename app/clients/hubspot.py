"""HubSpot CRM v3: empresas, contactos y notas.

Los valores vacíos no se envían: en un update, mandar "" borraría en HubSpot
un dato que alguien metió a mano. n8n sí los mandaba.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from ..config import require
from .base import BaseClient
from .http import ProviderError

HUBSPOT_URL = "https://api.hubapi.com"

# Tipos de asociación definidos por HubSpot.
NOTE_TO_COMPANY = 190
NOTE_TO_CONTACT = 202
CONTACT_TO_COMPANY_PRIMARY = 1


def clean_properties(props: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in props.items():
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        out[key] = str(value)
    return out


class HubSpotClient(BaseClient):
    provider = "hubspot"

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {require(self.settings.hubspot_token, 'HUBSPOT_TOKEN')}"}

    async def find_company_by_domain(self, domain: str) -> str | None:
        if not domain:
            return None
        data = await self._request(
            "POST",
            f"{HUBSPOT_URL}/crm/v3/objects/companies/search",
            headers=self._headers(),
            json={
                "filterGroups": [
                    {"filters": [{"propertyName": "domain", "operator": "EQ", "value": domain}]}
                ],
                "properties": ["domain", "name"],
                "limit": 1,
            },
        )
        results = (data or {}).get("results") or []
        return results[0]["id"] if results else None

    async def create_company(self, properties: dict[str, Any]) -> str:
        data = await self._request(
            "POST",
            f"{HUBSPOT_URL}/crm/v3/objects/companies",
            headers=self._headers(),
            json={"properties": clean_properties(properties)},
        )
        return data["id"]

    async def update_company(self, company_id: str, properties: dict[str, Any]) -> str:
        data = await self._request(
            "PATCH",
            f"{HUBSPOT_URL}/crm/v3/objects/companies/{company_id}",
            headers=self._headers(),
            json={"properties": clean_properties(properties)},
        )
        return data["id"]

    async def upsert_contact(self, email: str, properties: dict[str, Any]) -> str:
        """Crea o actualiza por email en una sola llamada."""
        data = await self._request(
            "POST",
            f"{HUBSPOT_URL}/crm/v3/objects/contacts/batch/upsert",
            headers=self._headers(),
            json={
                "inputs": [
                    {
                        "id": email,
                        "idProperty": "email",
                        "properties": clean_properties({**properties, "email": email}),
                    }
                ]
            },
        )
        return data["results"][0]["id"]

    async def associate_contact_company(self, contact_id: str, company_id: str) -> None:
        await self._request(
            "PUT",
            f"{HUBSPOT_URL}/crm/v4/objects/contacts/{contact_id}/associations/default/companies/{company_id}",
            headers=self._headers(),
        )

    async def create_note(
        self, body: str, object_type: Literal["company", "contact"], object_id: str
    ) -> str:
        type_id = NOTE_TO_COMPANY if object_type == "company" else NOTE_TO_CONTACT
        data = await self._request(
            "POST",
            f"{HUBSPOT_URL}/crm/v3/objects/notes",
            headers=self._headers(),
            json={
                "properties": {
                    "hs_note_body": body,
                    "hs_timestamp": datetime.now(UTC).isoformat(),
                },
                "associations": [
                    {
                        "to": {"id": object_id},
                        "types": [
                            {"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": type_id}
                        ],
                    }
                ],
            },
        )
        return data["id"]

    async def search_contacts_modified_since(
        self, since_ms: int, *, properties: list[str], page_limit: int = 100
    ) -> list[dict]:
        """Contactos con `lastmodifieddate` posterior a `since_ms` (epoch ms).
        Sigue `paging.next.after` hasta agotar las páginas."""
        out: list[dict] = []
        after: str | None = None
        while True:
            body: dict[str, Any] = {
                "filterGroups": [
                    {
                        "filters": [
                            {
                                "propertyName": "lastmodifieddate",
                                "operator": "GT",
                                "value": str(since_ms),
                            }
                        ]
                    }
                ],
                "properties": properties,
                "limit": page_limit,
            }
            if after:
                body["after"] = after
            data = await self._request(
                "POST",
                f"{HUBSPOT_URL}/crm/v3/objects/contacts/search",
                headers=self._headers(),
                json=body,
            )
            out.extend((data or {}).get("results") or [])
            after = (((data or {}).get("paging") or {}).get("next") or {}).get("after")
            if not after:
                return out

    async def get_company(self, company_id: str, *, properties: list[str]) -> dict | None:
        """Empresa por id; `None` si no existe (404)."""
        try:
            return await self._request(
                "GET",
                f"{HUBSPOT_URL}/crm/v3/objects/companies/{company_id}",
                headers=self._headers(),
                params={"properties": ",".join(properties)},
            )
        except ProviderError as exc:
            if exc.status == 404:
                return None
            raise

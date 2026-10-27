"""Supabase de Alma por PostgREST: prompts del equipo y cuentas de Unipile."""

from __future__ import annotations

from typing import Any

from ..config import require
from .base import BaseClient
from .http import ProviderError


class AlmaClient(BaseClient):
    provider = "alma_supabase"

    def _url(self, table: str) -> str:
        base = require(self.settings.alma_supabase_url, "ALMA_SUPABASE_URL").rstrip("/")
        return f"{base}/rest/v1/{table}"

    def _headers(self) -> dict:
        key = require(self.settings.alma_supabase_key, "ALMA_SUPABASE_KEY")
        return {"apikey": key, "Authorization": f"Bearer {key}"}

    async def get_prompt(self, title: str) -> str:
        """Prompt base de `handoff_prompt_library` por título. Se lee en cada
        ejecución para que un cambio en la tabla aplique sin redeploy."""
        rows = await self._request(
            "GET",
            self._url("handoff_prompt_library"),
            headers=self._headers(),
            params={"title": f"eq.{title}", "select": "prompt", "limit": "1"},
        )
        if not rows or not isinstance(rows[0].get("prompt"), str):
            raise ProviderError(self.provider, f"prompt not found: {title!r}")
        return rows[0]["prompt"]

    async def get_google_accounts(self) -> list[dict]:
        """Cuentas del equipo conectadas a Unipile con Google."""
        teams = ",".join(f'"{t}"' for t in self.settings.team_list)
        rows = await self._request(
            "GET",
            self._url("accounts"),
            headers=self._headers(),
            params={"team": f"in.({teams})", "select": "*"},
        )
        return [
            r
            for r in rows or []
            if r.get("unipilePlatform") == "Google" and r.get("unipileAccountId")
        ]

    async def find(self, table: str, **eq_filters: Any) -> list[dict]:
        """Ids de las filas de `table` que cumplen todos los `columna=valor` (igualdad)."""
        rows = await self._request(
            "GET",
            self._url(table),
            headers=self._headers(),
            params={**{k: f"eq.{v}" for k, v in eq_filters.items()}, "select": "id"},
        )
        return rows or []

    async def upsert(self, table: str, row: dict, *, on_conflict: str) -> dict:
        """Inserta o actualiza por `on_conflict` y devuelve la fila resultante."""
        rows = await self._request(
            "POST",
            self._url(table),
            headers={
                **self._headers(),
                "Prefer": "resolution=merge-duplicates,return=representation",
            },
            params={"on_conflict": on_conflict},
            json=row,
        )
        if not rows:
            raise ProviderError(self.provider, f"upsert into {table} returned no row")
        return rows[0]

    async def insert(self, table: str, row: dict) -> dict | None:
        """Inserta una fila; `None` si ya existía (409, clave duplicada)."""
        try:
            rows = await self._request(
                "POST",
                self._url(table),
                headers={**self._headers(), "Prefer": "return=representation"},
                json=row,
            )
        except ProviderError as exc:
            if exc.status == 409:
                return None
            raise
        return rows[0] if rows else {}

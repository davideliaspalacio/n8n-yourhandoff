"""Supabase de Alma por PostgREST: prompts del equipo y cuentas de Unipile."""

from __future__ import annotations

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

from __future__ import annotations

from typing import Any

import httpx

from ..config import Settings
from .http import request_json


class BaseClient:
    provider = "provider"

    def __init__(self, http: httpx.AsyncClient, settings: Settings) -> None:
        self.http = http
        self.settings = settings

    async def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        return await request_json(
            self.http,
            method,
            url,
            provider=self.provider,
            max_attempts=self.settings.http_max_attempts,
            base_delay=self.settings.http_retry_base_delay,
            **kwargs,
        )

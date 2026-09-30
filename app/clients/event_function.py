"""Edge function `handle-discovery-call-booked` del portal de Handoff.

Agrega al equipo al evento de calendario y devuelve sus datos (fecha, zona
horaria y asistentes externos). No es de n8n: se sigue llamando igual.
"""

from __future__ import annotations

from ..config import require
from .base import BaseClient
from .http import ProviderError


class EventFunctionClient(BaseClient):
    provider = "event_function"

    async def handle_booking(self, payload: dict) -> dict:
        headers = {}
        if self.settings.event_function_auth_value:
            headers[self.settings.event_function_auth_header] = (
                self.settings.event_function_auth_value
            )
        data = await self._request(
            "POST",
            require(self.settings.event_function_url, "EVENT_FUNCTION_URL"),
            headers=headers,
            json=payload,
        )
        event = (data or {}).get("Event")
        if not isinstance(event, dict):
            raise ProviderError(self.provider, "response has no Event object")
        return event

from __future__ import annotations

from ..config import require
from .base import BaseClient
from .http import ProviderError


class SlackClient(BaseClient):
    provider = "slack"

    async def post_message(self, text: str, *, thread_ts: str | None = None) -> str:
        """Publica en el canal de Handoff y devuelve el `ts` del mensaje."""
        body = {
            "channel": require(self.settings.slack_channel_id, "SLACK_CHANNEL_ID"),
            "text": text,
            "unfurl_links": False,
        }
        if thread_ts:
            body["thread_ts"] = thread_ts
        token = require(self.settings.slack_bot_token, "SLACK_BOT_TOKEN")
        data = await self._request(
            "POST",
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {token}"},
            json=body,
        )
        if not data or not data.get("ok"):
            raise ProviderError(self.provider, (data or {}).get("error", "unknown error"))
        return data["ts"]

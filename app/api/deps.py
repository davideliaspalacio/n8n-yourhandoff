from __future__ import annotations

import hmac

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader

from ..config import Settings
from ..runs import RunStore

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def require_api_key(
    key: str | None = Depends(_api_key_header),
    settings: Settings = Depends(settings_dep),
) -> None:
    # Sin API_KEY configurada se rechaza todo: fallar cerrado.
    if not settings.api_key or not key or not hmac.compare_digest(key, settings.api_key):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing API key")


def run_store(request: Request) -> RunStore:
    return request.app.state.run_store

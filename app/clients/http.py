"""Una sola forma de hablar HTTP con los proveedores: reintentos y errores limpios.

Los errores nunca llevan la URL completa ni el cuerpo de la petición: varias
URLs llevan la clave en la query (SerpApi) o son privadas (edge function).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}
MAX_RETRY_AFTER_SECONDS = 30.0


class ProviderError(RuntimeError):
    def __init__(self, provider: str, message: str, status: int | None = None) -> None:
        super().__init__(f"{provider}: {message}")
        self.provider = provider
        self.status = status


async def request_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    provider: str,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    **kwargs: Any,
) -> Any:
    """Petición con reintento en 429/5xx y fallos de red. Devuelve el JSON (o
    `None` si la respuesta viene vacía)."""
    last_error: ProviderError | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = await client.request(method, url, **kwargs)
        except httpx.TransportError as exc:
            last_error = ProviderError(provider, f"network error: {type(exc).__name__}")
            delay = base_delay * 2 ** (attempt - 1)
        else:
            if response.status_code < 400:
                if not response.content:
                    return None
                try:
                    return response.json()
                except ValueError as exc:
                    raise ProviderError(
                        provider, "invalid JSON response", response.status_code
                    ) from exc
            last_error = ProviderError(
                provider, f"HTTP {response.status_code}: {_short(response)}", response.status_code
            )
            if response.status_code not in RETRYABLE_STATUS:
                raise last_error
            delay = _retry_after(response) or base_delay * 2 ** (attempt - 1)
        if attempt < max_attempts:
            logger.warning(
                "%s attempt %d/%d failed (%s); retrying in %.1fs",
                provider,
                attempt,
                max_attempts,
                last_error,
                delay,
            )
            await asyncio.sleep(delay)
    assert last_error is not None
    raise last_error


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    try:
        return min(float(value), MAX_RETRY_AFTER_SECONDS) if value else None
    except ValueError:
        return None


def _short(response: httpx.Response) -> str:
    """Mensaje de error del proveedor, recortado, para logs y runs."""
    try:
        data = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(data, dict):
        for key in ("message", "error", "detail", "error_message"):
            if isinstance(data.get(key), str):
                return data[key][:200]
    return str(data)[:200]

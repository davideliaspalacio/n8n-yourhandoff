"""Pequeñas utilidades de nombres y dominios."""

from __future__ import annotations

import re


def domain_of(url: str | None) -> str:
    """`https://www.staynido.com/about` → `staynido.com`."""
    if not url:
        return ""
    host = re.sub(r"^https?://", "", url.strip(), flags=re.IGNORECASE)
    host = re.sub(r"^www\.", "", host, flags=re.IGNORECASE)
    return host.split("/")[0].split("?")[0].split("#")[0].lower()


def company_name_from_url(url: str | None) -> str:
    """Último recurso para el nombre de la empresa: el dominio capitalizado."""
    domain = domain_of(url)
    return domain[:1].upper() + domain[1:] if domain else ""


def split_full_name(full_name: str | None) -> tuple[str, str]:
    first, _, rest = (full_name or "").strip().partition(" ")
    return first, rest.strip()


def is_blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())

"""El catálogo vivo se genera por introspección: cubre el registro y no filtra valores."""

from __future__ import annotations

from app.config import Settings
from app.flows.registry import discover
from scripts.catalog import build


def test_catalog_lists_every_slug_and_known_method():
    out = build()
    for slug in discover():
        assert slug in out
    assert "create_note" in out
    assert "## Workflows" in out and "## Nodes" in out


def test_catalog_settings_only_names(monkeypatch):
    monkeypatch.setenv("HUBSPOT_TOKEN", "super-secret-token-123")
    monkeypatch.setenv("API_KEY", "another-secret-456")
    out = build()
    assert "hubspot_token" in out
    assert "super-secret-token-123" not in out
    assert "another-secret-456" not in out
    line = next(
        ln for ln in out.splitlines() if ln.startswith("api_key") or "hubspot_token" in ln
    )
    assert "=" not in line
    assert all(name in out for name in Settings.model_fields)

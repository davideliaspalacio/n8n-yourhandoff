# tests/test_registry.py
from __future__ import annotations

import pytest
from pydantic import BaseModel

from app.flows.registry import FlowSpec, add_specs, discover, validate


class EchoInput(BaseModel):
    text: str


async def _echo(clients, data):
    return {"echo": data.text}


def _spec(**overrides) -> FlowSpec:
    base = dict(slug="echo", title="Echo", input_model=EchoInput, run=_echo)
    return FlowSpec(**{**base, **overrides})


def test_valid_spec_passes():
    validate(_spec(), origin="test")


@pytest.mark.parametrize("slug", ["Echo", "e cho", "echo_", "-echo", ""])
def test_bad_slug_is_rejected(slug):
    with pytest.raises(RuntimeError, match="slug"):
        validate(_spec(slug=slug), origin="test")


def test_bad_mode_is_rejected():
    with pytest.raises(RuntimeError, match="mode"):
        validate(_spec(mode="batch"), origin="test")


def test_input_model_must_be_pydantic():
    with pytest.raises(RuntimeError, match="input_model"):
        validate(_spec(input_model=dict), origin="test")


def test_duplicate_slug_is_rejected():
    registry: dict[str, FlowSpec] = {}
    add_specs(registry, [_spec()], origin="a")
    with pytest.raises(RuntimeError, match="duplicado"):
        add_specs(registry, [_spec()], origin="b")


def test_valid_graph_passes():
    graph = (
        {"id": "a", "label": "A", "to": ("b", "c")},
        {"id": "b", "label": "B", "to": ("d",)},
        {"id": "c", "label": "C", "to": ["d"]},
        {"id": "d", "label": "D", "to": ()},
    )
    validate(_spec(graph=graph), origin="test")


def test_graph_unknown_target_is_rejected():
    graph = ({"id": "a", "label": "A", "to": ("zzz",)},)
    with pytest.raises(RuntimeError, match="graph"):
        validate(_spec(graph=graph), origin="test")


def test_graph_duplicate_id_is_rejected():
    graph = ({"id": "a", "label": "A", "to": ()}, {"id": "a", "label": "A2", "to": ()})
    with pytest.raises(RuntimeError, match="graph"):
        validate(_spec(graph=graph), origin="test")


def test_graph_empty_id_is_rejected():
    with pytest.raises(RuntimeError, match="graph"):
        validate(_spec(graph=({"id": "", "label": "A", "to": ()},)), origin="test")


def test_graph_without_root_is_rejected():
    graph = ({"id": "a", "label": "A", "to": ("b",)}, {"id": "b", "label": "B", "to": ("a",)})
    with pytest.raises(RuntimeError, match="graph"):
        validate(_spec(graph=graph), origin="test")


def test_every_real_flow_declares_a_graph():
    for spec in discover().values():
        assert spec.graph, spec.slug


def test_discover_returns_registry():
    registry = discover()
    assert isinstance(registry, dict)
    for slug, spec in registry.items():
        assert slug == spec.slug


def test_research_flows_are_registered():
    registry = discover()
    assert {"enrich-company", "enrich-contacts", "team-communications"} <= set(registry)
    assert registry["enrich-company"].mode == "sync"


def test_crm_and_orchestrator_flows_are_registered():
    registry = discover()
    assert {"crm-company", "crm-contact", "crm-note", "discovery-call-booked"} <= set(registry)
    assert registry["discovery-call-booked"].mode == "async"


def test_sync_hubspot_to_alma_is_registered_and_async():
    assert discover()["sync-hubspot-to-alma"].mode == "async"

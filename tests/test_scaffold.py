from __future__ import annotations

import pytest

from scripts.new_flow import create_flow


def test_scaffold_creates_flow_package(tmp_path):
    created = create_flow("lead-scoring", replaces="Lead Scoring v2", root=tmp_path)
    pkg = tmp_path / "app" / "flows" / "lead_scoring"
    assert {pkg / "flow.py", pkg / "schemas.py", pkg / "__init__.py", pkg / "README.md",
            tmp_path / "tests" / "flows" / "test_lead_scoring.py"} == set(created)
    flow = (pkg / "flow.py").read_text()
    assert 'slug="lead-scoring"' in flow and "Lead Scoring v2" in flow
    assert "LeadScoringInput" in (pkg / "schemas.py").read_text()


def test_scaffold_rejects_bad_slug_and_duplicates(tmp_path):
    with pytest.raises(ValueError, match="slug"):
        create_flow("Bad Slug", root=tmp_path)
    create_flow("dup", root=tmp_path)
    with pytest.raises(FileExistsError):
        create_flow("dup", root=tmp_path)


def test_scaffold_rejects_leading_digit_and_unsafe_text(tmp_path):
    with pytest.raises(ValueError, match="slug"):
        create_flow("1-lead", root=tmp_path)
    with pytest.raises(ValueError, match="title"):
        create_flow("ok-flow", title='Di "hola"', root=tmp_path)
    with pytest.raises(ValueError, match="replaces"):
        create_flow("ok-flow", replaces="a\\b", root=tmp_path)


def test_scaffold_kind_option(tmp_path):
    create_flow("a-node", kind="node", root=tmp_path)
    assert 'kind="node"' in (tmp_path / "app/flows/a_node/flow.py").read_text()
    create_flow("a-wf", root=tmp_path)
    assert 'kind="workflow"' in (tmp_path / "app/flows/a_wf/flow.py").read_text()
    with pytest.raises(ValueError, match="kind"):
        create_flow("bad-kind", kind="x", root=tmp_path)


def test_scaffold_includes_description_todo(tmp_path):
    create_flow("with-desc", root=tmp_path)
    assert "description=" in (tmp_path / "app/flows/with_desc/flow.py").read_text()

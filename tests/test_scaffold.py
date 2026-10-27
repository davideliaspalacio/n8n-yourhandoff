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

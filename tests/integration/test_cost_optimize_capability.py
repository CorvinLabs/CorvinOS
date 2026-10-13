"""S1 of PLAN-0955 / ADR-2259: the ``cost.optimize`` capability.

The Free ceiling of the automatic-saving cap lives in exactly ONE place,
``CAPABILITIES["cost.optimize"]["free"]``. These tests pin the shape and the
licensing doc table, and prove the capability is resolved through the real
``require_capability`` entry point (not by importing the dict).
"""
from __future__ import annotations

import math
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def caps():
    from corvin_operator.license.limits import CAPABILITIES

    return CAPABILITIES


def test_capability_exists_and_is_class_l(caps):
    assert "cost.optimize" in caps
    assert caps["cost.optimize"]["class"] == "L"


def test_free_allowance_is_ratio_with_floor_and_no_quantity(caps):
    free = caps["cost.optimize"]["free"]
    # A ratio, not a count: 0 quantity keeps require_capability well-defined
    # (Free has no unlimited allowance), the ratio is read by savings_cap.
    assert free["limit"] == 0
    assert free["ratio"] == 0.10
    assert free["floor_usd"] == 5.0
    assert not isinstance(free["ratio"], bool)
    assert math.isfinite(free["ratio"]) and 0.0 <= free["ratio"] <= 1.0
    assert math.isfinite(free["floor_usd"]) and free["floor_usd"] >= 0.0


def test_member_is_unlimited(caps):
    assert caps["cost.optimize"]["member"]["limit"] is None


def test_the_matrix_is_frozen(caps):
    with pytest.raises(TypeError):
        caps["cost.optimize"]["free"]["ratio"] = 1.0  # type: ignore[index]


def test_ratio_literal_exists_nowhere_else_in_the_selector_path():
    """One source of the number: no second 0.10 cap literal next to the selector."""
    for rel in ("corvin_operator/bridges/shared/model_selector.py",
                "corvin_operator/bridges/shared/savings_cap.py"):
        p = REPO / rel
        if not p.exists():
            continue  # savings_cap.py arrives in S3
        text = p.read_text(encoding="utf-8")
        assert not re.search(r"\b(ratio|cap)\w*\s*=\s*0?\.10\b", text), rel


def test_real_entry_point_denies_free_quantity_and_allows_member(monkeypatch, tmp_path):
    """Through require_capability (the licensing chokepoint), not the dict."""
    import corvin_operator.license.capability_api as api

    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    monkeypatch.setattr(api, "_audit_capability_decision", lambda **_k: None)

    monkeypatch.setattr(api, "active_tier", lambda **_k: "free")
    with pytest.raises(api.LicenseDenied):
        api.require_capability("cost.optimize", 1, tenant_id="_default", entry_point="test:s1")

    monkeypatch.setattr(api, "active_tier", lambda **_k: "member")
    decision = api.require_capability("cost.optimize", 1, tenant_id="_default", entry_point="test:s1")
    assert decision.allowed is None  # unlimited


def test_licensing_doc_table_lists_the_capability():
    doc = (REPO / "docs/claude-ref/licensing.md").read_text(encoding="utf-8")
    assert "`cost.optimize`" in doc
    row = next(l for l in doc.splitlines() if l.startswith("| `cost.optimize`"))
    assert "| L |" in row

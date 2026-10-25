"""Regression tests for the 2026-09-07 learning-subsystem hardening (F-L5..F-L12).

Each test names the finding it pins. They drive the real modules against an
isolated ``CORVIN_HOME`` / core chain (``tests/conftest.py`` + the learning
``conftest.py``).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from core.learning.event_store import EventStore
from core.learning.learning_events import EventType, LearningEvent
from core.paths.tenant import corvin_home, tenant_home


def _outcome(tenant: str, success: bool) -> LearningEvent:
    return LearningEvent.create(
        event_type=EventType.OUTCOME, skill_id="os.delegation_router", tenant_id=tenant,
        signal={"task_id": "t", "success": success}, lom="tests",
    )


# ── F-L5: the live collector was retired by ADR-2282 (maturity_live measures on demand) ──


# ── F-L6: no free text in learning records ───────────────────────────────────


def test_rating_event_carries_has_text_not_the_text():
    from core.learning.operator_feedback import RATING_KIND_TOOL, build_rating_event

    ev = build_rating_event(kind=RATING_KIND_TOOL, entity_id="t", entity_name="T", rating=5,
                            tenant_id="_default", feedback_text="  do not store me  ")
    assert "feedback_text" not in ev.signal
    assert ev.signal["has_text"] is True and ev.signal["text_length"] == len("do not store me")
    assert "store me" not in json.dumps(ev.to_dict())
    ev2 = build_rating_event(kind=RATING_KIND_TOOL, entity_id="t", entity_name="T", rating=3, tenant_id="_default")
    assert ev2.signal["has_text"] is False and ev2.signal["text_length"] == 0


def test_grade_pattern_goes_through_the_chained_store(tmp_path: Path):
    from core.learning.integration import LearningIntegration

    th = tenant_home("_default")
    integration = LearningIntegration(th / "learning", tenant_id="_default")
    integration.register_pattern("pattern_a", "A", when=[])
    event_id = integration.grade_pattern("pattern_a", 0.4, reason="private operator remark")
    assert integration.get_pattern_confidence("pattern_a") > 0.5

    disk = [e for e in EventStore(th).query_events("_default", event_type=EventType.FEEDBACK) if e.event_id == event_id]
    assert len(disk) == 1 and disk[0].audit_ref
    assert disk[0].signal == {
        "kind": "pattern_grade", "pattern_id": "pattern_a", "grade": 0.4,
        "has_reason": True, "reason_length": len("private operator remark"), "source": "operator",
        "trust_weight": 1.0,
    }
    chain = Path(os.environ["VOICE_AUDIT_PATH"]).read_text()
    assert disk[0].audit_ref in chain and "operator remark" not in chain
    # the unchained TreeOfThoughts JSONL is no longer written for grades
    assert not list((th / "learning" / "events").glob("*.jsonl")) or all(
        "confidence_delta" not in line
        for f in (th / "learning" / "events").glob("*.jsonl") for line in f.read_text().splitlines()
    )


# ── F-L7: negative feedback is negative ─────────────────────────────────────




# ── F-L8: user_id cannot escape the profiles directory ──────────────────────






# ── F-L10: unified loss — validated weights, no prod mock, no div-by-zero ───






# ── F-L11: no asserts, plugin gradients recorded ────────────────────────────






# ── F-L12: everything under CORVIN_HOME ─────────────────────────────────────


def test_no_module_in_core_learning_hardcodes_home_corvin():
    root = Path(__file__).resolve().parents[2] / "core" / "learning"
    offenders = []
    for py in root.glob("*.py"):
        for i, line in enumerate(py.read_text().splitlines(), 1):
            if 'Path.home() / ".corvin"' in line or 'Path.home()/".corvin"' in line:
                offenders.append(f"{py.name}:{i}")
    assert offenders == []


# ── EventStore tenant binding ───────────────────────────────────────────────


def test_bound_event_store_rejects_foreign_tenant_events(tmp_path: Path):
    store = EventStore(tenant_home("_default"), tenant_id="_default")
    with pytest.raises(ValueError):
        store.write_event(_outcome("acme-corp", True))
    assert store.count_events("_default") == 0
    with pytest.raises(ValueError):
        EventStore(tmp_path, tenant_id="../evil")

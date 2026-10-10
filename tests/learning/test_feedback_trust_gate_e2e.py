"""ADR-0534 feedback trust gate — E2E through the real console router.

Every case POSTs to a live console route with a real session (``console_client``)
and then asserts on what the audit-first ``EventStore`` and the core hash chain
actually contain. The gate itself sits in ``EventStore.write_event``; these
tests prove the routes reach it, that each layer fires, and that every decision
leaves its chain record.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from tests.learning.console_client import console_client


def _seed_execution(sb, skill_id: str, output: dict | None = None):
    from core.learning.event_store import EventStore
    from core.learning.learning_events import EventType, LearningEvent
    ev = LearningEvent.create(
        event_type=EventType.SKILL_EXECUTED, skill_id=skill_id, tenant_id=sb.tenant_id,
        signal={"status": "success", "output": output or {"decision": "allow"}},
        lom="core/skills/skill_registry_phase1.py:execute")
    EventStore(sb.tenant_home, tenant_id=sb.tenant_id).write_event(ev)
    return ev


def _chain(sb, name: str) -> list[dict]:
    return [c for c in sb.chain_records() if c.get("event_type") == name]


def _feedback_on_disk(sb) -> list[dict]:
    return [e for e in sb.events_on_disk() if e["event_type"] == "feedback"]


def _rate(sb, skill: str, rating: int = 4):
    return sb.client.post(f"/v1/console/skills/{skill}/rating", json={"rating": rating},
                          headers=sb.csrf_headers)


def test_rating_without_a_real_execution_is_refused_and_audited(tmp_path: Path):
    with console_client(tmp_path) as sb:
        r = _rate(sb, "os.flow_guard")
        assert r.status_code == 422, r.text
        detail = r.json()["detail"]
        assert detail["reason"] == "no_corresponding_execution"
        assert _feedback_on_disk(sb) == []
        rec = _chain(sb, "learning.feedback_rejected")
        assert len(rec) == 1
        assert rec[0]["details"]["audit_ref"] == detail["audit_ref"]
        assert rec[0]["details"]["skill_id"] == "skill:os.flow_guard"
        assert _chain(sb, "learning.feedback") == []  # nothing became learnable


def test_rating_after_a_real_execution_is_accepted_with_trust_weight(tmp_path: Path):
    with console_client(tmp_path) as sb:
        _seed_execution(sb, "os.flow_guard")
        r = _rate(sb, "os.flow_guard", 5)
        assert r.status_code == 200, r.text
        stored = _feedback_on_disk(sb)
        assert len(stored) == 1
        assert stored[0]["signal"]["trust_weight"] == 1.0
        acc = _chain(sb, "learning.feedback_accepted")
        assert len(acc) == 1
        assert acc[0]["details"]["feedback_event_id"] == stored[0]["event_id"]
        assert acc[0]["details"]["executions_in_window"] == 1
        # the accepted record precedes the learnable one (gate before store)
        names = [c["event_type"] for c in sb.chain_records()]
        assert names.index("learning.feedback_accepted") < names.index("learning.feedback")


def test_execution_older_than_the_window_does_not_verify(tmp_path: Path):
    with console_client(tmp_path) as sb:
        from core.learning import reality_check
        ev = _seed_execution(sb, "os.flow_guard")
        from core.learning.learning_events import EventType, LearningEvent
        from core.learning.event_store import EventStore
        store = EventStore(sb.tenant_home, tenant_id=sb.tenant_id)
        fb = LearningEvent.create(EventType.FEEDBACK, "skill:os.flow_guard", sb.tenant_id, signal={})
        assert reality_check.validate(fb, store).is_valid
        # the same feedback 25 h later no longer has an execution in the window
        late = fb.__class__(**{**fb.__dict__, "timestamp": "2099-01-01T00:00:00Z"})
        assert reality_check.validate(late, store).reason == "no_corresponding_execution"
        assert ev  # seeded


def test_burst_is_throttled_and_counts_against_trust(tmp_path: Path):
    with console_client(tmp_path) as sb:
        _seed_execution(sb, "os.flow_guard")
        codes = [_rate(sb, "os.flow_guard").status_code for _ in range(6)]
        assert codes[:5] == [200] * 5, codes
        assert codes[5] == 422
        thr = _chain(sb, "learning.feedback_throttled")
        assert len(thr) == 1 and thr[0]["details"]["signals_in_window"] == 5
        assert len(_feedback_on_disk(sb)) == 5


def test_low_trust_source_is_down_weighted_and_flagged(tmp_path: Path):
    """Rejections lower the subject's trust; the next accepted signal carries
    the lower weight and the weighted stats use it."""
    from core.learning import feedback_gate
    from core.learning.event_store import EventStore
    from core.learning.learning_events import EventType, LearningEvent
    with console_client(tmp_path) as sb:
        _seed_execution(sb, "os.flow_guard", output={"decision": "allow"})
        store = EventStore(sb.tenant_home, tenant_id=sb.tenant_id)
        t0 = time.time() - 3600
        # 8 signals claiming an output the skill never produced → 8 mismatches,
        # spaced out so the throttle stays out of it
        for i in range(8):
            bad = LearningEvent.create(EventType.FEEDBACK, "skill:os.flow_guard", sb.tenant_id,
                                       signal={"kind": "operator_rated_skill", "rating": 1,
                                               "skill_id": "os.flow_guard",
                                               "claimed_output_hash": "sha256:" + "0" * 64})
            with pytest.raises(feedback_gate.FeedbackRejected) as exc:
                feedback_gate.admit(bad, store, now=t0 + i * 120)
            assert exc.value.reason == "output_hash_mismatch"
        assert len(_chain(sb, "learning.feedback_reality_mismatch")) == 8

        r = _rate(sb, "os.flow_guard", 5)
        assert r.status_code == 200, r.text
        stored = _feedback_on_disk(sb)
        assert len(stored) == 1
        assert stored[0]["signal"]["trust_weight"] == pytest.approx(0.1)
        flagged = _chain(sb, "learning.adversarial_feedback_detected")
        assert flagged and flagged[-1]["details"]["rejection_count"] == 8


def test_matching_claimed_output_hash_is_accepted(tmp_path: Path):
    from core.learning import reality_check
    from core.learning.event_store import EventStore
    from core.learning.learning_events import EventType, LearningEvent
    with console_client(tmp_path) as sb:
        ex = _seed_execution(sb, "os.flow_guard", output={"decision": "deny"})
        claim = reality_check.execution_output_hash(ex.signal)
        ok = LearningEvent.create(EventType.FEEDBACK, "os.flow_guard", sb.tenant_id,
                                  signal={"claimed_output_hash": claim})
        EventStore(sb.tenant_home, tenant_id=sb.tenant_id).write_event(ok)
        assert [e["event_id"] for e in _feedback_on_disk(sb)] == [ok.event_id]


def test_feedback_submit_records_instead_of_pretending(tmp_path: Path):
    """``/learning/feedback/submit`` answered "queued" and stored nothing."""
    with console_client(tmp_path) as sb:
        body = {"skill_id": "os.flow_guard", "signal_type": "outcome", "value": 0.9,
                "comment": "private words"}
        r = sb.client.post("/v1/console/learning/feedback/submit", json=body, headers=sb.csrf_headers)
        assert r.status_code == 422, r.text  # no execution yet → refused, not "queued"
        _seed_execution(sb, "os.flow_guard")
        r = sb.client.post("/v1/console/learning/feedback/submit", json=body, headers=sb.csrf_headers)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "recorded"
        stored = _feedback_on_disk(sb)
        assert len(stored) == 1 and stored[0]["signal"]["value"] == 0.9
        assert "private words" not in sb.chain.read_text()
        assert stored[0]["signal"]["text_length"] == len("private words")


def test_gate_is_tenant_scoped(tmp_path: Path):
    """An execution in tenant B does not verify feedback in tenant A."""
    import json
    from core.learning.learning_events import EventType, LearningEvent
    with console_client(tmp_path) as sb:
        # tenant acme's store holds an execution (written as its own store
        # would; the sandbox audit writer is bound to _default)
        ev = LearningEvent.create(EventType.SKILL_EXECUTED, "os.flow_guard", "acme",
                                  signal={"output": {}})
        f = sb.home / "tenants" / "acme" / "learning" / "events" / (ev.timestamp[:10] + ".jsonl")
        f.parent.mkdir(parents=True)
        f.write_text(json.dumps(ev.to_dict()) + "\n")
        r = _rate(sb, "os.flow_guard")
        assert r.status_code == 422
        assert r.json()["detail"]["reason"] == "no_corresponding_execution"

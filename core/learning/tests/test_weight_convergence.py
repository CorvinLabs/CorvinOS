"""WeightConvergence against the REAL shape of an outcome record.

Replaces the two test modules bc7c8a85 added
(test_event_store_live.py / test_event_store_critical.py). Those drove the
95-line EventStore reimplementation and its OutcomeSink class, both of which
were an unintended regression over the hardened store; with that reverted the
classes they imported no longer exist, and the surviving coverage for the
store itself lives in test_event_store_audit_first.py + test_outcome_sink.py.

What was NOT covered before and is here: ``signal`` is the dict
emit_task_outcome writes, not a float.
"""
from __future__ import annotations

from core.learning.learning_events import EventType
from core.learning.weight_convergence import (
    MIN_SAMPLES,
    WeightConvergence,
    _success_series,
)

TENANT = "_default"
SKILL = "os.delegation_router"


class _Event:
    def __init__(self, signal):
        self.signal = signal
        self.event_type = EventType.OUTCOME
        self.skill_id = SKILL


class _Store:
    """Stands in for EventStore.query_events — SYNC, enum-filtered."""

    def __init__(self, events):
        self._events = events
        self.calls = []

    def query_events(self, tenant_id, event_type=None, skill_id=None,
                     limit=10000, newest_first=False, **kw):
        self.calls.append({"tenant_id": tenant_id, "event_type": event_type,
                           "skill_id": skill_id, "newest_first": newest_first})
        return self._events[:limit]


def _outcomes(n, success=True):
    return [_Event({"task_id": str(i), "status": "completed" if success else "failed",
                    "success": success, "exit_code": 0 if success else 1})
            for i in range(n)]


def test_signal_is_a_dict_not_a_float():
    """The real on-disk shape. A float series here would mean the producer changed."""
    assert _success_series(_outcomes(3, success=True)) == [1.0, 1.0, 1.0]
    assert _success_series(_outcomes(2, success=False)) == [0.0, 0.0]


def test_unreadable_signal_is_skipped_not_counted_as_success():
    series = _success_series([_Event(None), _Event("completed"), _Event({})])
    assert series == []


def test_status_only_record_still_scores():
    assert _success_series([_Event({"status": "completed"}),
                            _Event({"status": "failed"})]) == [1.0, 0.0]


def test_below_min_samples_withholds_the_verdict():
    wc = WeightConvergence(_Store(_outcomes(MIN_SAMPLES - 1)))
    assert wc.detect_convergence(SKILL, TENANT) is False
    stats = wc.get_convergence_stats(SKILL, TENANT)
    assert stats["is_converged"] is False
    assert stats["mean_confidence"] is None
    assert "insufficient samples" in stats["reason"]


def test_converges_on_a_real_sample():
    wc = WeightConvergence(_Store(_outcomes(MIN_SAMPLES + 5)))
    assert wc.detect_convergence(SKILL, TENANT) is True
    stats = wc.get_convergence_stats(SKILL, TENANT)
    assert stats["is_converged"] is True
    assert stats["mean_confidence"] == 1.0
    assert stats["reason"] == ""


def test_failures_pull_the_rate_below_threshold():
    events = _outcomes(6, success=True) + _outcomes(6, success=False)
    wc = WeightConvergence(_Store(events))
    assert wc.detect_convergence(SKILL, TENANT) is False


def test_query_is_tenant_and_enum_scoped():
    store = _Store(_outcomes(MIN_SAMPLES))
    WeightConvergence(store).detect_convergence(SKILL, TENANT)
    call = store.calls[0]
    assert call["tenant_id"] == TENANT
    assert call["event_type"] is EventType.OUTCOME
    assert call["skill_id"] == SKILL
    assert call["newest_first"] is True

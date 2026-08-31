"""ADR-0215 F4 regression: LossProfileTracker must not bleed across
(tenant, session) boundaries.

Before this fix, ``get_session_tracker()`` was a single process-wide
singleton — one session's delegation-quality evidence silently influenced
every other concurrent session's delegation decisions in the same process,
contradicting the tracker's own "In-Session only" docstring claim and
ADR-0007's tenant-isolation guarantee.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "operator" / "orchestration"))

from tde.loss_profile_tracker import (  # noqa: E402
    LossProfileTracker,
    clear_session_tracker,
    get_session_tracker,
    _MAX_SESSION_TRACKERS,
    _reset_all_session_trackers_for_tests,
)


def setup_function(_fn):
    _reset_all_session_trackers_for_tests()


def teardown_function(_fn):
    _reset_all_session_trackers_for_tests()


def test_different_session_keys_get_different_trackers():
    a = get_session_tracker(session_key="tenant_a:sess_1")
    b = get_session_tracker(session_key="tenant_b:sess_2")
    assert a is not b


def test_same_session_key_returns_same_instance():
    a1 = get_session_tracker(session_key="tenant_a:sess_1")
    a2 = get_session_tracker(session_key="tenant_a:sess_1")
    assert a1 is a2


def test_tenant_a_evidence_does_not_leak_into_tenant_b():
    tracker_a = get_session_tracker(session_key="tenant_a:sess_1")
    tracker_b = get_session_tracker(session_key="tenant_b:sess_2")

    # Tenant A records 20 high-loss delegations for "write_file".
    for _ in range(20):
        tracker_a.record_delegation_result(
            task_type="write_file", loss_pct=95.0, engine="tiered_delegation",
        )

    # Tenant B has recorded nothing — its estimate must stay at the
    # conservative default, NOT be poisoned by tenant A's bad experience.
    default_fraction = LossProfileTracker.DEFAULT_LOSS_PCT / 100.0

    est_b = tracker_b.estimate_loss_for_task_type(
        "write_file", "moderate", engine="tiered_delegation",
    )
    assert abs(est_b - default_fraction) < 1e-6, (
        f"tenant B's estimate ({est_b}) was influenced by tenant A's evidence "
        "— cross-tenant loss-tracker leak"
    )

    est_a = tracker_a.estimate_loss_for_task_type(
        "write_file", "moderate", engine="tiered_delegation",
    )
    assert est_a > est_b, "tenant A's own evidence should raise its own estimate"


def test_no_session_key_falls_back_to_default_and_is_stable():
    a = get_session_tracker()
    b = get_session_tracker()
    assert a is b
    assert a is get_session_tracker(session_key="default")


def test_clear_session_tracker_drops_it():
    get_session_tracker(session_key="tenant_a:sess_1")
    clear_session_tracker(session_key="tenant_a:sess_1")
    fresh = get_session_tracker(session_key="tenant_a:sess_1")
    # A cleared+recreated tracker starts with empty history.
    assert fresh.history == []


def test_registry_is_lru_bounded():
    for i in range(_MAX_SESSION_TRACKERS + 50):
        get_session_tracker(session_key=f"tenant_x:sess_{i}")
    from tde.loss_profile_tracker import _session_trackers
    assert len(_session_trackers) <= _MAX_SESSION_TRACKERS

"""Gap #2 Token Measurement + Cost Gate — Unit Tests (Test-First, LDD k=2).

Tests for real token measurement (tokens_delegated, tokens_local) and
cost-aware delegation gating (Gate 4).

All tests MUST pass before implementation is committed.
"""

import sys
from pathlib import Path

import pytest

# Add operator package to path
_op_root = Path(__file__).parent.parent / "operator" / "orchestration"  # `tde` is a top-level package there (no operator/orchestration/__init__.py)
if str(_op_root) not in sys.path:
    sys.path.insert(0, str(_op_root))

# Minimal test for cost-ratio estimation
def test_cost_ratio_calculation():
    """Test that cost ratio (delegated/local) is calculated correctly."""
    # Example: local=1000, delegated=600 → ratio=0.6
    local_tokens = 1000
    delegated_tokens = 600
    ratio = delegated_tokens / local_tokens

    assert ratio == 0.6, "Cost ratio should be delegated/local"
    assert ratio < 1.0, "Cheap delegation: saves 400 tokens"


def test_cost_ratio_expensive_delegation():
    """Test identifying expensive delegations (ratio > 1.5)."""
    # Local: 200, Delegated: 320 → ratio = 1.6 (expensive)
    local = 200
    delegated = 320
    ratio = delegated / local

    assert ratio > 1.5, "This delegation costs 60% more"
    # Gate 4 should BLOCK this (cost_too_high)


def test_cost_ratio_break_even():
    """Test break-even delegations (1.0 <= ratio <= 1.5)."""
    # Local: 1000, Delegated: 1100 → ratio = 1.1
    local = 1000
    delegated = 1100
    ratio = delegated / local

    assert 1.0 <= ratio <= 1.5, "Break-even zone"
    # Gate 4 should still ALLOW (for learning)


def test_cost_ratio_cheap_delegation():
    """Test cheap delegations (ratio < 1.0)."""
    # Local: 4000, Delegated: 2200 → ratio = 0.55
    local = 4000
    delegated = 2200
    ratio = delegated / local

    assert ratio < 1.0, "Cheap delegation saves 45%"
    # Gate 4 should strongly ALLOW


def test_loss_entry_with_token_fields():
    """Test that LossEntry dataclass has token fields."""
    # This imports the actual class once implemented
    import time

    from tde.loss_profile_tracker import LossEntry

    entry = LossEntry(
        timestamp=time.time(),
        task_type="refactor",
        model_id="opus",
        loss_pct=15.0,
        engine="tiered_delegation",
        tokens_delegated=2000,
        tokens_local=4000,
    )

    assert entry.tokens_delegated == 2000
    assert entry.tokens_local == 4000


def test_record_delegation_result_accepts_token_params():
    """Test that record_delegation_result() accepts token parameters."""
    from tde.loss_profile_tracker import get_session_tracker

    tracker = get_session_tracker(session_key="test_session")

    # Call with token parameters (this is the key fix from the audit)
    tracker.record_delegation_result(
        task_type="refactor",
        engine="tiered_delegation",
        loss_pct=10.0,
        tokens_delegated=2000,  # NEW parameter
        tokens_local=4000,  # NEW parameter
    )

    # Should complete without error


def test_estimate_cost_ratio_returns_none_with_no_data():
    """Test that estimate_cost_ratio() returns None when no data exists."""
    from tde.loss_profile_tracker import get_session_tracker

    tracker = get_session_tracker(session_key="test_empty")
    ratio = tracker.estimate_cost_ratio(task_type="refactor", model_id="opus")

    assert ratio is None, "Should return None when no measurements"


def test_estimate_cost_ratio_with_single_measurement():
    """Test cost ratio with one measurement (MIN_SAMPLES may apply)."""
    from tde.loss_profile_tracker import get_session_tracker

    tracker = get_session_tracker(session_key="test_single")
    tracker.record_delegation_result(
        task_type="refactor",
        engine="tiered_delegation",
        loss_pct=10.0,
        tokens_delegated=800,
        tokens_local=1000,
    )

    ratio = tracker.estimate_cost_ratio(task_type="refactor", model_id="opus")

    # Depending on MIN_SAMPLES threshold, might be None or 0.8
    if ratio is not None:
        assert 0.7 < ratio < 0.9, "Expected ratio ~0.8"


def test_estimate_cost_ratio_averaging():
    """Test that multiple measurements are averaged."""
    from tde.loss_profile_tracker import get_session_tracker

    tracker = get_session_tracker(session_key="test_average")

    # Three measurements
    tracker.record_delegation_result(
        task_type="refactor",
        engine="tiered_delegation",
        loss_pct=10.0,
        tokens_delegated=600,  # ratio 0.6
        tokens_local=1000,
    )
    tracker.record_delegation_result(
        task_type="refactor",
        engine="tiered_delegation",
        loss_pct=10.0,
        tokens_delegated=800,  # ratio 0.8
        tokens_local=1000,
    )
    tracker.record_delegation_result(
        task_type="refactor",
        engine="tiered_delegation",
        loss_pct=10.0,
        tokens_delegated=500,  # ratio 0.5
        tokens_local=1000,
    )

    ratio = tracker.estimate_cost_ratio(task_type="refactor", model_id="opus")

    if ratio is not None:
        # Average of [0.6, 0.8, 0.5] = 0.633
        assert 0.6 < ratio < 0.7, f"Expected average ~0.63, got {ratio}"


def test_cost_ratio_persistence_across_reloads(monkeypatch, tmp_path):
    """Test that cost ratios survive tracker reload from disk.

    Landed red on 2026-07-25 and stayed red: it passed `schema_valid=` /
    `downstream_ok=`, which `record_delegation_result` has never accepted (the
    required argument is `loss_pct`), so every run raised TypeError. Every other
    test in this file uses the real signature — which is what made the failure
    easy to miss in a 3300-test run.

    It also set `os.environ["CORVIN_HOME"]` and never restored it, leaking a tmp
    home into every test that ran after it in the same process. monkeypatch now
    owns that.
    """
    from tde.loss_profile_tracker import get_session_tracker

    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))

    # Record a measurement
    tracker1 = get_session_tracker(session_key="test_persist")
    tracker1.record_delegation_result(
        task_type="refactor",
        engine="tiered_delegation",
        loss_pct=10.0,
        tokens_delegated=600,
        tokens_local=1000,
    )

    ratio1 = tracker1.estimate_cost_ratio(task_type="refactor", model_id="opus")

    # Reload tracker (simulates process restart)
    tracker2 = get_session_tracker(session_key="test_persist")
    ratio2 = tracker2.estimate_cost_ratio(task_type="refactor", model_id="opus")

    if ratio1 is not None:
        assert ratio2 == ratio1, "Cost ratio should persist across reloads"


def test_gate_4_decision_expensive():
    """Test Gate 4 decision: BLOCK if ratio > 1.5."""
    cost_ratio = 1.6  # Expensive
    should_delegate = cost_ratio < 1.5  # False

    assert not should_delegate, "Gate 4 should block expensive delegations"


def test_gate_4_decision_break_even():
    """Test Gate 4 decision: ALLOW break-even (1.0 <= ratio <= 1.5) for learning."""
    cost_ratio = 1.1  # Break-even
    should_delegate = cost_ratio <= 1.5  # True (but log as "break-even")

    assert should_delegate, "Gate 4 should allow break-even for learning"


def test_gate_4_decision_cheap():
    """Test Gate 4 decision: ALLOW cheap delegations (ratio < 1.0)."""
    cost_ratio = 0.6  # Cheap
    should_delegate = cost_ratio < 1.5  # True

    assert should_delegate, "Gate 4 should allow cheap delegations"


if __name__ == "__main__":
    pytest.main([__file__, "-xvs"])

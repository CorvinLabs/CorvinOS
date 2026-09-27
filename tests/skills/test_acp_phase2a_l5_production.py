"""Phase 2a L5 Production Wiring Tests — Dual-Write + Correctness + Rollback.

Tests that the os.delegation_router Skill is used for real routing (not shadow),
dual-write monitoring tracks correctness, and auto-rollback triggers on degradation.

ADR-0532 Phase 2a: L5 Production Wiring
"""
from __future__ import annotations

import pytest
import time
import uuid
from unittest.mock import MagicMock, patch

from core.skills.os_skills.monitoring.correctness_tracker import (
    CorrectnessTracker,
    RoutingDecision,
    RoutingOutcome,
)
from core.skills.os_skills.monitoring.rollback_detector import RollbackDetector
from core.skills.os_skills.monitoring.dual_write import (
    resolve_worker_engine_dual_write,
    record_routing_outcome,
    get_monitoring_dashboard,
)


class TestCorrectnessTrackerBasic:
    """Basic correctness tracking functionality."""

    def test_record_outcome_basic(self):
        """Record a single routing outcome."""
        tracker = CorrectnessTracker(window_size=100, bootstrap_samples=10)

        real_decision = RoutingDecision(
            request_id="req_001",
            timestamp=time.time(),
            engine="sonnet",
            decision_source="skill",
            confidence=0.9,
            task_type="chat",
            tenant_id="_default",
        )

        shadow_decision = RoutingDecision(
            request_id="req_001",
            timestamp=time.time(),
            engine="opus",
            decision_source="bundled",
            confidence=1.0,
            task_type="chat",
            tenant_id="_default",
        )

        outcome = RoutingOutcome(
            request_id="req_001",
            real_decision=real_decision,
            shadow_decision=shadow_decision,
            success=True,
            ground_truth="sonnet",
            latency_ms=42.5,
        )

        tracker.record_outcome(outcome)

        metrics = tracker.current_metrics()
        assert metrics.total_count == 1
        assert metrics.correct_count == 1
        assert metrics.correctness == 1.0

    def test_rolling_window_correctness(self):
        """Correctness computed over rolling window."""
        tracker = CorrectnessTracker(window_size=100, bootstrap_samples=10)

        # Record 20 outcomes: 18 correct, 2 wrong
        for i in range(20):
            outcome = RoutingOutcome(
                request_id=f"req_{i:03d}",
                real_decision=RoutingDecision(
                    request_id=f"req_{i:03d}",
                    timestamp=time.time(),
                    engine="sonnet",
                    decision_source="skill",
                    confidence=0.9,
                    task_type="chat",
                    tenant_id="_default",
                ),
                shadow_decision=RoutingDecision(
                    request_id=f"req_{i:03d}",
                    timestamp=time.time(),
                    engine="opus",
                    decision_source="bundled",
                    confidence=1.0,
                    task_type="chat",
                    tenant_id="_default",
                ),
                success=(i % 10) != 0,  # Fail on i=0, 10
                ground_truth="sonnet",
                latency_ms=42.5,
            )
            tracker.record_outcome(outcome)

        metrics = tracker.current_metrics()
        assert metrics.total_count == 20
        assert metrics.correct_count == 18
        assert abs(metrics.correctness - 0.9) < 0.01  # 90% correctness


class TestRollbackDetector:
    """Rollback detection and triggering."""

    def test_no_rollback_when_correctness_stable(self):
        """No rollback when correctness stays above baseline."""
        tracker = CorrectnessTracker(window_size=200, bootstrap_samples=50)
        detector = RollbackDetector(correctness_tracker=tracker)

        # Record 100 outcomes with 95% correctness
        for i in range(100):
            outcome = RoutingOutcome(
                request_id=f"req_{i:03d}",
                real_decision=RoutingDecision(
                    request_id=f"req_{i:03d}",
                    timestamp=time.time(),
                    engine="sonnet",
                    decision_source="skill",
                    confidence=0.9,
                    task_type="chat",
                    tenant_id="_default",
                ),
                shadow_decision=RoutingDecision(
                    request_id=f"req_{i:03d}",
                    timestamp=time.time(),
                    engine="opus",
                    decision_source="bundled",
                    confidence=1.0,
                    task_type="chat",
                    tenant_id="_default",
                ),
                success=(i % 20) != 0,  # 95% success rate
                ground_truth="sonnet",
                latency_ms=42.5,
            )
            tracker.record_outcome(outcome)

        # Check for rollback every 100 requests
        for _ in range(2):
            assert not detector.update()  # No rollback

        assert not detector.is_rolled_back

    def test_rollback_triggered_on_2_percent_drop(self):
        """Rollback triggered when correctness drops > 2%."""
        tracker = CorrectnessTracker(window_size=200, bootstrap_samples=50)
        detector = RollbackDetector(correctness_tracker=tracker)

        # First 50 outcomes: establish 96% baseline
        for i in range(50):
            outcome = RoutingOutcome(
                request_id=f"req_{i:03d}",
                real_decision=RoutingDecision(
                    request_id=f"req_{i:03d}",
                    timestamp=time.time(),
                    engine="sonnet",
                    decision_source="skill",
                    confidence=0.9,
                    task_type="chat",
                    tenant_id="_default",
                ),
                shadow_decision=RoutingDecision(
                    request_id=f"req_{i:03d}",
                    timestamp=time.time(),
                    engine="opus",
                    decision_source="bundled",
                    confidence=1.0,
                    task_type="chat",
                    tenant_id="_default",
                ),
                success=(i % 25) != 0,  # 96% success rate
                ground_truth="sonnet",
                latency_ms=42.5,
            )
            tracker.record_outcome(outcome)

        # Next 100 outcomes: 93% success (3% drop)
        for i in range(50, 150):
            outcome = RoutingOutcome(
                request_id=f"req_{i:03d}",
                real_decision=RoutingDecision(
                    request_id=f"req_{i:03d}",
                    timestamp=time.time(),
                    engine="sonnet",
                    decision_source="skill",
                    confidence=0.9,
                    task_type="chat",
                    tenant_id="_default",
                ),
                shadow_decision=RoutingDecision(
                    request_id=f"req_{i:03d}",
                    timestamp=time.time(),
                    engine="opus",
                    decision_source="bundled",
                    confidence=1.0,
                    task_type="chat",
                    tenant_id="_default",
                ),
                success=(i % 14) != 0,  # ~93% success rate
                ground_truth="sonnet",
                latency_ms=42.5,
            )
            tracker.record_outcome(outcome)

        # Trigger rollback check (should detect drop > 2%)
        assert detector.update()  # Rollback triggered
        assert detector.is_rolled_back


class TestBaselineIsNotOneSample:
    """2026-09-27: the baseline used to be the FIRST sample (0.0 or 1.0)."""

    @staticmethod
    def _outcome(i: int, ok: bool) -> RoutingOutcome:
        d = RoutingDecision(request_id=f"r{i}", timestamp=time.time(), engine="acs",
                            decision_source="skill", confidence=0.9, task_type="chat",
                            tenant_id="_default")
        return RoutingOutcome(request_id=f"r{i}", real_decision=d, shadow_decision=d,
                              success=ok, ground_truth="acs", latency_ms=1.0)

    def test_first_sample_failure_does_not_disable_the_gate(self):
        tracker = CorrectnessTracker(window_size=100, bootstrap_samples=10)
        tracker.record_outcome(self._outcome(0, False))  # old code: baseline 0.0
        for i in range(1, 10):
            tracker.record_outcome(self._outcome(i, True))  # baseline 0.9
        for i in range(10, 20):
            tracker.record_outcome(self._outcome(i, False))
        assert tracker.should_rollback()

    def test_single_miss_does_not_trip(self):
        """Old code: a perfect first sample made the baseline 1.0 and the first
        100-sample check trip on 2 misses in 100 (from a baseline of ONE)."""
        tracker = CorrectnessTracker(window_size=200, bootstrap_samples=50)
        for i in range(50):
            tracker.record_outcome(self._outcome(i, True))
        for i in range(50, 100):
            tracker.record_outcome(self._outcome(i, i != 70))  # 98% == exactly 2% drop
        assert not tracker.should_rollback()

    def test_no_check_before_bootstrap(self):
        tracker = CorrectnessTracker(window_size=100, bootstrap_samples=50)
        for i in range(60):
            tracker.record_outcome(self._outcome(i, i < 50))  # 10 post-baseline misses
        assert not tracker.should_rollback()  # only 10 of 50 post-baseline samples


class TestDualWriteIntegration:
    """Dual-write routing integration (module level; not reachable from routing)."""

    @pytest.fixture(autouse=True)
    def _isolated(self, tmp_path):
        from core.skills.os_skills.monitoring import dual_write

        dual_write.initialize_dual_write(storage_dir=tmp_path / "metrics")
        self.dw = dual_write
        self.tmp = tmp_path

    def test_resolve_worker_engine_dual_write_uses_skill_decision(self):
        """A confident Skill answer (key ``decision``) is the served engine."""
        with patch.object(self.dw, "_audit"):
            result = resolve_worker_engine_dual_write(
                request_id="req_test_001",
                bundled_engine="native",
                bundled_confidence=1.0,
                skill_decision={"decision": "acs", "confidence": 0.92},
                task_type="chat",
                tenant_id="_default",
            )
        assert result == "acs"

    def test_resolve_worker_engine_dual_write_falls_back_on_rollback(self):
        """Falls back to bundled engine when rollback is active."""
        detector = self.dw.get_detector()
        detector._state = detector._state.__class__.ROLLBACK_TRIGGERED
        with patch.object(self.dw, "_audit") as audit:
            result = resolve_worker_engine_dual_write(
                request_id="req_rb",
                bundled_engine="native",
                skill_decision={"decision": "acs", "confidence": 0.99},
                task_type="chat",
                tenant_id="_default",
            )
        assert result == "native"
        assert audit.call_args_list[0].args[0] == "l5_routing_rollback_active"

    def test_record_routing_outcome_logs_correctness(self):
        """Record outcome updates the tracker AND persists (used to raise
        AttributeError on ``outcome.timestamp`` and persist nothing)."""
        record_routing_outcome(
            request_id="req_outcome_001",
            real_engine="acs",
            shadow_engine="native",
            success=True,
            ground_truth="acs",
            latency_ms=50.0,
        )
        m = self.dw.get_tracker().current_metrics()
        assert (m.total_count, m.correct_count) == (1, 1)
        lines = (self.tmp / "metrics" / "correctness.jsonl").read_text().splitlines()
        assert len(lines) == 1 and '"req_outcome_001"' in lines[0]

    def test_reload_from_disk_does_not_duplicate(self):
        from core.skills.os_skills.monitoring.correctness_tracker import load_tracker_from_disk

        for i in range(3):
            record_routing_outcome(request_id=f"r{i}", real_engine="acs", shadow_engine="native",
                                   success=True, ground_truth="acs", latency_ms=1.0)
        path = self.tmp / "metrics" / "correctness.jsonl"
        t = load_tracker_from_disk(path)
        assert t.current_metrics().total_count == 3
        assert len(path.read_text().splitlines()) == 3

    def test_rollback_is_chained(self, tmp_path, monkeypatch):
        """The rollback event reaches the tenant audit chain (used to import a
        non-existent module and vanish)."""
        home = tmp_path / "ch"
        chain = home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
        chain.parent.mkdir(parents=True)
        monkeypatch.setenv("CORVIN_HOME", str(home))
        monkeypatch.setenv("VOICE_AUDIT_PATH", str(chain))
        tracker = CorrectnessTracker(window_size=50, bootstrap_samples=5)
        detector = RollbackDetector(correctness_tracker=tracker)
        for i in range(10):
            tracker.record_outcome(TestBaselineIsNotOneSample._outcome(i, i < 5))
        assert detector.update()
        import json as _json
        recs = [_json.loads(x) for x in chain.read_text().splitlines()]
        rb = [r for r in recs if r.get("event_type") == "l5_rollback_triggered"]
        assert rb, [r.get("event_type") for r in recs]
        d = rb[0].get("details") or rb[0]
        assert d["reason_code"] == "correctness_drop"
        assert d["correctness"] == 0.0 and d["baseline_correctness"] == 1.0


class TestMonitoringDashboard:
    """Monitoring dashboard metrics."""

    def test_get_monitoring_dashboard_structure(self, tmp_path):
        """Dashboard returns expected structure."""
        # Initialize dual-write system
        from core.skills.os_skills.monitoring.dual_write import initialize_dual_write

        initialize_dual_write(storage_dir=tmp_path / "metrics")

        dashboard = get_monitoring_dashboard()

        # Check structure
        assert "is_rolled_back" in dashboard
        assert "rollback_events" in dashboard
        assert "correctness_metrics" in dashboard

        metrics = dashboard["correctness_metrics"]
        assert "correctness" in metrics
        assert "shadow_correctness" in metrics
        assert "correctness_drop_percent" in metrics


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

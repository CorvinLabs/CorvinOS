"""E2E tests for loss signal detection and learning loop tracking.

Simulates real-world loss scenarios and verifies:
  1. Latency regression detection → escalate/rollback alert
  2. Confidence decline detection → retraining flag
  3. Negative feedback detection → sentiment investigation
  4. A/B test regression detection → halt + revert

All tests verify audit trail integration (signals logged to core chain).

References: ADR-0722 (loss signals), ADR-0232 (audit).
"""

from __future__ import annotations

import unittest
import tempfile
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Any

import sys
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[1]
_VIBE = _REPO / "core" / "vibe"
_LEARN = _REPO / "core" / "learning"

for _p in [str(_VIBE), str(_LEARN)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


class MockAuditBackend:
    """Mock audit backend for testing (records events to dict)."""

    def __init__(self):
        self.events: List[Dict[str, Any]] = []
        self.chain_hashes: List[str] = []

    def write_event(self, event: Dict[str, Any]) -> str:
        """Record event and return hash."""
        self.events.append(event)
        hash_val = f"hash_{len(self.events):06d}"
        self.chain_hashes.append(hash_val)
        return hash_val

    def last_hash(self) -> str:
        """Return last hash."""
        return self.chain_hashes[-1] if self.chain_hashes else ""

    def verify_chain(self) -> bool:
        """Verify all events were recorded."""
        return len(self.events) > 0


class TestLatencyRegressionDetection(unittest.TestCase):
    """Test latency regression signal detection."""

    def setUp(self):
        from loss_signal_emitter import LossSignalEmitter
        self.audit = MockAuditBackend()
        self.emitter = LossSignalEmitter(self.audit)

    def test_detect_latency_spike_20pct(self):
        """Verify 20% p99 spike is detected."""
        tenant_id = "_default"
        p99_baseline = 100.0  # 100 ms baseline
        p99_current = 120.0   # 20% increase

        signal = self.emitter.emit_latency_signal(
            tenant_id=tenant_id,
            p99_baseline=p99_baseline,
            p99_current=p99_current,
            threshold_pct=20.0,
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal.signal_type, "latency_regression")
        self.assertEqual(signal.severity.value, "warning")
        self.assertAlmostEqual(signal.deviation_pct, 20.0, places=1)

    def test_detect_latency_spike_50pct_critical(self):
        """Verify 50% spike is marked CRITICAL."""
        tenant_id = "_default"
        signal = self.emitter.emit_latency_signal(
            tenant_id=tenant_id,
            p99_baseline=100.0,
            p99_current=150.0,
            threshold_pct=20.0,
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal.severity.value, "critical")

    def test_no_signal_below_threshold(self):
        """Verify no signal if spike is below threshold."""
        signal = self.emitter.emit_latency_signal(
            tenant_id="_default",
            p99_baseline=100.0,
            p99_current=105.0,  # Only 5% increase
            threshold_pct=20.0,
        )

        self.assertIsNone(signal)

    def test_latency_signal_audit_chain(self):
        """Verify latency signal is recorded in audit chain."""
        signal = self.emitter.emit_latency_signal(
            tenant_id="_default",
            p99_baseline=100.0,
            p99_current=125.0,
            threshold_pct=20.0,
        )

        # Verify audit event was recorded
        self.assertEqual(len(self.audit.events), 1)
        audit_event = self.audit.events[0]
        self.assertEqual(audit_event["event_type"], "loss_signal_latency_regression")
        self.assertEqual(audit_event["tenant_id"], "_default")
        self.assertAlmostEqual(audit_event["current_value"], 125.0, places=1)

        # Verify signal has hash chain links
        self.assertIsNotNone(signal.hash)

    def test_escalate_recommendation(self):
        """Verify recommendation is 'escalate_or_rollback'."""
        signal = self.emitter.emit_latency_signal(
            tenant_id="_default",
            p99_baseline=100.0,
            p99_current=130.0,
        )

        self.assertEqual(signal.recommendation, "escalate_or_rollback")


class TestConfidenceDeclineDetection(unittest.TestCase):
    """Test confidence decline signal detection."""

    def setUp(self):
        from loss_signal_emitter import LossSignalEmitter
        self.audit = MockAuditBackend()
        self.emitter = LossSignalEmitter(self.audit)

    def test_detect_confidence_decline(self):
        """Verify declining confidence is detected."""
        # Simulated 7-day confidence timeseries (declining steeply)
        timeseries = [0.95, 0.92, 0.88, 0.84, 0.80, 0.76, 0.72]

        signal = self.emitter.emit_confidence_signal(
            tenant_id="_default",
            confidence_timeseries=timeseries,
            threshold_slope=-0.05,
            target_confidence=0.90,
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal.signal_type, "confidence_decline")
        self.assertLess(signal.slope_7d, -0.05)

    def test_no_signal_if_improving(self):
        """Verify no signal if confidence is improving."""
        # Improving timeseries
        timeseries = [0.82, 0.84, 0.86, 0.88, 0.89, 0.90, 0.91]

        signal = self.emitter.emit_confidence_signal(
            tenant_id="_default",
            confidence_timeseries=timeseries,
            threshold_slope=-0.05,
        )

        self.assertIsNone(signal)

    def test_no_signal_if_above_target(self):
        """Verify no signal if confidence already >= target."""
        # All above target
        timeseries = [0.92, 0.91, 0.90, 0.91, 0.92, 0.93, 0.94]

        signal = self.emitter.emit_confidence_signal(
            tenant_id="_default",
            confidence_timeseries=timeseries,
            threshold_slope=-0.05,
            target_confidence=0.90,
        )

        self.assertIsNone(signal)

    def test_insufficient_samples(self):
        """Verify no signal with < 7 samples."""
        timeseries = [0.85, 0.84, 0.83, 0.82, 0.81]  # Only 5 samples

        signal = self.emitter.emit_confidence_signal(
            tenant_id="_default",
            confidence_timeseries=timeseries,
        )

        self.assertIsNone(signal)

    def test_retraining_recommendation(self):
        """Verify recommendation is 'flag_retraining'."""
        timeseries = [0.95, 0.92, 0.88, 0.84, 0.80, 0.76, 0.72]

        signal = self.emitter.emit_confidence_signal(
            tenant_id="_default",
            confidence_timeseries=timeseries,
            threshold_slope=-0.05,
            target_confidence=0.90,
        )

        self.assertEqual(signal.recommendation, "flag_retraining")


class TestFeedbackNegativeDetection(unittest.TestCase):
    """Test negative feedback signal detection."""

    def setUp(self):
        from loss_signal_emitter import LossSignalEmitter
        self.audit = MockAuditBackend()
        self.emitter = LossSignalEmitter(self.audit)

    def test_detect_low_thumbs_up_pct(self):
        """Verify <70% thumbs up is detected."""
        signal = self.emitter.emit_feedback_signal(
            tenant_id="_default",
            thumbs_up=50,    # 50%
            thumbs_down=40,  # 40%
            thumbs_neutral=10,  # 10%
            threshold_pct=70.0,
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal.signal_type, "feedback_negative")
        self.assertAlmostEqual(signal.thumbs_up_pct, 50.0, places=1)

    def test_no_signal_above_threshold(self):
        """Verify no signal if thumbs up >= threshold."""
        signal = self.emitter.emit_feedback_signal(
            tenant_id="_default",
            thumbs_up=75,   # 75%
            thumbs_down=20,
            thumbs_neutral=5,
            threshold_pct=70.0,
        )

        self.assertIsNone(signal)

    def test_sentiment_trend_declining(self):
        """Verify sentiment trend is marked 'declining' if high negative %."""
        signal = self.emitter.emit_feedback_signal(
            tenant_id="_default",
            thumbs_up=30,   # 30%
            thumbs_down=65, # 65% (high negative)
            thumbs_neutral=5,
            threshold_pct=70.0,
        )

        self.assertEqual(signal.sentiment_trend, "declining")

    def test_sentiment_trend_stable(self):
        """Verify sentiment trend is 'stable' if moderate negative."""
        signal = self.emitter.emit_feedback_signal(
            tenant_id="_default",
            thumbs_up=60,   # 60%
            thumbs_down=15, # 15%
            thumbs_neutral=25,
            threshold_pct=70.0,
        )

        self.assertEqual(signal.sentiment_trend, "stable")

    def test_investigate_sentiment_recommendation(self):
        """Verify recommendation is 'investigate_sentiment'."""
        signal = self.emitter.emit_feedback_signal(
            tenant_id="_default",
            thumbs_up=50,
            thumbs_down=40,
            threshold_pct=70.0,
        )

        self.assertEqual(signal.recommendation, "investigate_sentiment")


class TestABTestRegressionDetection(unittest.TestCase):
    """Test A/B test regression signal detection."""

    def setUp(self):
        from loss_signal_emitter import LossSignalEmitter
        self.audit = MockAuditBackend()
        self.emitter = LossSignalEmitter(self.audit)

    def test_detect_ci_crosses_zero(self):
        """Verify CI crossing zero is detected."""
        signal = self.emitter.emit_ab_test_signal(
            tenant_id="_default",
            control_mean=100.0,
            variant_mean=102.0,
            ci_lower=-1.5,   # CI crosses zero
            ci_upper=5.5,
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal.signal_type, "ab_test_regression")
        self.assertTrue(signal.ci_crosses_zero)

    def test_no_signal_ci_positive(self):
        """Verify no signal if CI is entirely positive."""
        signal = self.emitter.emit_ab_test_signal(
            tenant_id="_default",
            control_mean=100.0,
            variant_mean=110.0,
            ci_lower=5.0,    # CI entirely positive
            ci_upper=15.0,
        )

        self.assertIsNone(signal)

    def test_no_signal_ci_negative(self):
        """Verify no signal if CI is entirely negative."""
        signal = self.emitter.emit_ab_test_signal(
            tenant_id="_default",
            control_mean=100.0,
            variant_mean=90.0,
            ci_lower=-15.0,  # CI entirely negative
            ci_upper=-5.0,
        )

        self.assertIsNone(signal)

    def test_halt_and_revert_recommendation(self):
        """Verify recommendation is 'halt_variant_halt_revert'."""
        signal = self.emitter.emit_ab_test_signal(
            tenant_id="_default",
            control_mean=100.0,
            variant_mean=102.0,
            ci_lower=-1.0,
            ci_upper=5.0,
        )

        self.assertEqual(signal.recommendation, "halt_variant_halt_revert")

    def test_critical_severity(self):
        """Verify A/B regression is marked CRITICAL."""
        signal = self.emitter.emit_ab_test_signal(
            tenant_id="_default",
            control_mean=100.0,
            variant_mean=99.0,
            ci_lower=-2.0,
            ci_upper=0.5,
        )

        self.assertEqual(signal.severity.value, "critical")


class TestLearningLoopTracking(unittest.TestCase):
    """Test learning loop convergence tracking."""

    def setUp(self):
        from learning_loop_tracker import LearningLoopTracker
        self.audit = MockAuditBackend()
        self.tracker = LearningLoopTracker(self.audit)

    def test_track_convergence_point(self):
        """Verify convergence point is recorded."""
        point = self.tracker.track_confidence_point(
            tenant_id="_default",
            confidence=0.85,
            sample_count=100,
            slope_7d=0.02,
        )

        self.assertIsNotNone(point)
        self.assertEqual(point.confidence, 0.85)
        self.assertEqual(point.sample_count, 100)

    def test_detect_converged_status(self):
        """Verify CONVERGED status when 3+ points >= 0.90."""
        self.tracker.track_confidence_point(
            tenant_id="_default",
            confidence=0.90,
            sample_count=100,
            slope_7d=0.02,
        )
        self.tracker.track_confidence_point(
            tenant_id="_default",
            confidence=0.91,
            sample_count=105,
            slope_7d=0.03,
        )
        self.tracker.track_confidence_point(
            tenant_id="_default",
            confidence=0.92,
            sample_count=110,
            slope_7d=0.02,
        )

        status = self.tracker.get_convergence_status("_default")
        self.assertEqual(status.value, "converged")

    def test_detect_diverging_status(self):
        """Verify DIVERGING status when slope < -0.05."""
        self.tracker.track_confidence_point(
            tenant_id="_default",
            confidence=0.85,
            sample_count=100,
            slope_7d=0.02,
        )
        self.tracker.track_confidence_point(
            tenant_id="_default",
            confidence=0.80,
            sample_count=105,
            slope_7d=-0.10,  # Large negative slope
        )

        status = self.tracker.get_convergence_status("_default")
        self.assertEqual(status.value, "diverging")

    def test_forecast_convergence_will_reach_target(self):
        """Verify forecast predicts reaching target confidence."""
        for i in range(7):
            self.tracker.track_confidence_point(
                tenant_id="_default",
                confidence=0.80 + (i * 0.02),  # Rising: 0.80 -> 0.92
                sample_count=100 + (i * 5),
                slope_7d=0.02,
            )

        forecast = self.tracker.forecast_convergence(
            tenant_id="_default",
            target_confidence=0.90,
            forecast_days=30,
        )

        self.assertIsNotNone(forecast)
        self.assertTrue(forecast.will_converge)
        self.assertGreaterEqual(forecast.estimated_days_to_target, 0)

    def test_record_feedback_cycle(self):
        """Verify learning loop cycle is recorded."""
        now = datetime.utcnow()
        feedback_time = now.isoformat() + "Z"
        opt_time = (now + timedelta(minutes=2)).isoformat() + "Z"
        applied_time = (now + timedelta(minutes=5)).isoformat() + "Z"

        metrics = self.tracker.record_feedback_cycle(
            tenant_id="_default",
            cycle_id="cycle_001",
            feedback_received_at=feedback_time,
            optimization_triggered_at=opt_time,
            config_applied_at=applied_time,
            feedback_count=15,
            confidence_delta=0.05,
        )

        self.assertIsNotNone(metrics)
        self.assertAlmostEqual(metrics.cycle_time_minutes, 5.0, places=1)
        self.assertEqual(metrics.feedback_count, 15)

    def test_compute_loop_velocity(self):
        """Verify learning loop velocity metrics."""
        now = datetime.utcnow()

        for i in range(3):
            feedback_time = (now + timedelta(hours=i)).isoformat() + "Z"
            applied_time = (now + timedelta(hours=i, minutes=10)).isoformat() + "Z"

            self.tracker.record_feedback_cycle(
                tenant_id="_default",
                cycle_id=f"cycle_{i:03d}",
                feedback_received_at=feedback_time,
                optimization_triggered_at=(now + timedelta(hours=i, minutes=5)).isoformat() + "Z",
                config_applied_at=applied_time,
                feedback_count=10 + i,
                confidence_delta=0.02,
            )

        velocity = self.tracker.get_loop_velocity("_default", window_hours=24)

        self.assertEqual(velocity["cycle_count"], 3)
        self.assertGreaterEqual(velocity["mean_cycle_time_minutes"], 10.0)
        self.assertGreater(velocity["mean_feedback_per_cycle"], 0)


class TestSignalAuditIntegration(unittest.TestCase):
    """Test audit chain integration for all signals."""

    def setUp(self):
        from loss_signal_emitter import LossSignalEmitter
        self.audit = MockAuditBackend()
        self.emitter = LossSignalEmitter(self.audit)

    def test_all_signals_audited(self):
        """Verify all emitted signals create audit events."""
        initial_count = len(self.audit.events)

        # Emit signals that will be detected
        self.emitter.emit_latency_signal("_default", 100.0, 150.0)  # 50% increase
        self.emitter.emit_confidence_signal("_default", [0.95, 0.92, 0.88, 0.84, 0.80, 0.76, 0.72])
        self.emitter.emit_feedback_signal("_default", 50, 40, 10)
        self.emitter.emit_ab_test_signal("_default", 100.0, 102.0, -1.0, 5.0)

        # Verify audit events were recorded (4 signals)
        self.assertEqual(len(self.audit.events), initial_count + 4)

    def test_recent_signals_retrieval(self):
        """Verify recent signals can be retrieved."""
        self.emitter.emit_latency_signal("_default", 100.0, 150.0)
        self.emitter.emit_confidence_signal("_default", [0.95, 0.92, 0.88, 0.84, 0.80, 0.76, 0.72])

        recent = self.emitter.get_recent_signals(minutes=60)
        self.assertEqual(len(recent), 2)

    def test_signals_by_type(self):
        """Verify signals can be retrieved by type."""
        self.emitter.emit_latency_signal("_default", 100.0, 150.0)
        self.emitter.emit_latency_signal("_default", 100.0, 140.0)
        self.emitter.emit_confidence_signal("_default", [0.95, 0.92, 0.88, 0.84, 0.80, 0.76, 0.72])

        latency_signals = self.emitter.get_signals_by_type("latency")
        self.assertEqual(len(latency_signals), 2)

        confidence_signals = self.emitter.get_signals_by_type("confidence")
        self.assertEqual(len(confidence_signals), 1)

    def test_critical_signals_retrieval(self):
        """Verify critical signals are retrievable."""
        self.emitter.emit_ab_test_signal("_default", 100.0, 102.0, -1.0, 5.0)  # CRITICAL
        self.emitter.emit_confidence_signal("_default", [0.95, 0.92, 0.88, 0.84, 0.80, 0.76, 0.72])  # WARNING

        critical = self.emitter.get_critical_signals()
        self.assertEqual(len(critical), 1)
        self.assertEqual(critical[0].signal_type, "ab_test_regression")


if __name__ == "__main__":
    unittest.main()

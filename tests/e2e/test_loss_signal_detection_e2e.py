"""E2E tests for loss signal detection and learning loop tracking."""

from __future__ import annotations

import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
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
    """Mock audit backend for testing."""
    
    def __init__(self):
        self.events: List[Dict[str, Any]] = []
        self.chain_hashes: List[str] = []
    
    def write_event(self, event: Dict[str, Any]) -> str:
        self.events.append(event)
        hash_val = f"hash_{len(self.events):06d}"
        self.chain_hashes.append(hash_val)
        return hash_val
    
    def last_hash(self) -> str:
        return self.chain_hashes[-1] if self.chain_hashes else ""


class TestLatencyRegressionDetection(unittest.TestCase):
    
    def setUp(self):
        from loss_signal_emitter import LossSignalEmitter
        self.audit = MockAuditBackend()
        self.emitter = LossSignalEmitter(self.audit)
    
    def test_detect_latency_spike_20pct(self):
        signal = self.emitter.emit_latency_signal(
            tenant_id="_default",
            p99_baseline=100.0,
            p99_current=120.0,
            threshold_pct=20.0,
        )
        self.assertIsNotNone(signal)
        self.assertEqual(signal.severity.value, "warning")
    
    def test_detect_latency_spike_50pct_critical(self):
        signal = self.emitter.emit_latency_signal(
            tenant_id="_default",
            p99_baseline=100.0,
            p99_current=150.0,
            threshold_pct=20.0,
        )
        self.assertIsNotNone(signal)
        self.assertEqual(signal.severity.value, "critical")
    
    def test_no_signal_below_threshold(self):
        signal = self.emitter.emit_latency_signal(
            tenant_id="_default",
            p99_baseline=100.0,
            p99_current=105.0,
            threshold_pct=20.0,
        )
        self.assertIsNone(signal)


class TestConfidenceDeclineDetection(unittest.TestCase):
    
    def setUp(self):
        from loss_signal_emitter import LossSignalEmitter
        self.audit = MockAuditBackend()
        self.emitter = LossSignalEmitter(self.audit)
    
    def test_detect_confidence_decline(self):
        timeseries = [1.00, 0.95, 0.90, 0.85, 0.80, 0.75, 0.65]
        signal = self.emitter.emit_confidence_signal(
            tenant_id="_default",
            confidence_timeseries=timeseries,
            threshold_slope=-0.05,
            target_confidence=0.90,
        )
        self.assertIsNotNone(signal)
        self.assertEqual(signal.signal_type, "confidence_decline")
    
    def test_no_signal_if_improving(self):
        timeseries = [0.82, 0.84, 0.86, 0.88, 0.89, 0.90, 0.91]
        signal = self.emitter.emit_confidence_signal(
            tenant_id="_default",
            confidence_timeseries=timeseries,
            threshold_slope=-0.05,
        )
        self.assertIsNone(signal)


class TestFeedbackNegativeDetection(unittest.TestCase):
    
    def setUp(self):
        from loss_signal_emitter import LossSignalEmitter
        self.audit = MockAuditBackend()
        self.emitter = LossSignalEmitter(self.audit)
    
    def test_detect_low_thumbs_up_pct(self):
        signal = self.emitter.emit_feedback_signal(
            tenant_id="_default",
            thumbs_up=50,
            thumbs_down=40,
            thumbs_neutral=10,
            threshold_pct=70.0,
        )
        self.assertIsNotNone(signal)
        self.assertEqual(signal.signal_type, "feedback_negative")
    
    def test_no_signal_above_threshold(self):
        signal = self.emitter.emit_feedback_signal(
            tenant_id="_default",
            thumbs_up=75,
            thumbs_down=20,
            thumbs_neutral=5,
            threshold_pct=70.0,
        )
        self.assertIsNone(signal)


class TestABTestRegressionDetection(unittest.TestCase):
    
    def setUp(self):
        from loss_signal_emitter import LossSignalEmitter
        self.audit = MockAuditBackend()
        self.emitter = LossSignalEmitter(self.audit)
    
    def test_detect_ci_crosses_zero(self):
        signal = self.emitter.emit_ab_test_signal(
            tenant_id="_default",
            control_mean=100.0,
            variant_mean=102.0,
            ci_lower=-1.5,
            ci_upper=5.5,
        )
        self.assertIsNotNone(signal)
        self.assertEqual(signal.severity.value, "critical")
    
    def test_no_signal_ci_positive(self):
        signal = self.emitter.emit_ab_test_signal(
            tenant_id="_default",
            control_mean=100.0,
            variant_mean=110.0,
            ci_lower=5.0,
            ci_upper=15.0,
        )
        self.assertIsNone(signal)


class TestLearningLoopTracking(unittest.TestCase):
    
    def setUp(self):
        from learning_loop_tracker import LearningLoopTracker
        self.audit = MockAuditBackend()
        self.tracker = LearningLoopTracker(self.audit)
    
    def test_track_convergence_point(self):
        point = self.tracker.track_confidence_point(
            tenant_id="_default",
            confidence=0.85,
            sample_count=100,
            slope_7d=0.02,
        )
        self.assertIsNotNone(point)
    
    def test_detect_converged_status(self):
        self.tracker.track_confidence_point("_default", 0.90, 100, 0.02)
        self.tracker.track_confidence_point("_default", 0.91, 105, 0.03)
        self.tracker.track_confidence_point("_default", 0.92, 110, 0.02)
        status = self.tracker.get_convergence_status("_default")
        self.assertEqual(status.value, "converged")
    
    def test_detect_diverging_status(self):
        self.tracker.track_confidence_point("_default", 0.85, 100, 0.02)
        self.tracker.track_confidence_point("_default", 0.80, 105, -0.10)
        status = self.tracker.get_convergence_status("_default")
        self.assertEqual(status.value, "diverging")
    
    def test_record_feedback_cycle(self):
        now = datetime.now(timezone.utc)
        feedback_time = now.isoformat()
        opt_time = (now + timedelta(minutes=2)).isoformat()
        applied_time = (now + timedelta(minutes=5)).isoformat()
        
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


class TestSignalAuditIntegration(unittest.TestCase):
    
    def setUp(self):
        from loss_signal_emitter import LossSignalEmitter
        self.audit = MockAuditBackend()
        self.emitter = LossSignalEmitter(self.audit)
    
    def test_latency_audited(self):
        self.emitter.emit_latency_signal("_default", 100.0, 150.0)
        self.assertEqual(len(self.audit.events), 1)
    
    def test_critical_signals_retrieval(self):
        self.emitter.emit_ab_test_signal("_default", 100.0, 102.0, -1.0, 5.0)
        critical = self.emitter.get_critical_signals()
        self.assertEqual(len(critical), 1)


if __name__ == "__main__":
    unittest.main()

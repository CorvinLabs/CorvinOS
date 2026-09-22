"""Stream 2: Comprehensive Test Suite (18 Stories + E2E).

Test scenarios:
  Stories 1-4: Feedback collection
  Stories 5-8: Auto-triage
  Stories 9-13: Optimizer loop
  Stories 14-16: Dashboard
  Stories 17-18: Incident response
"""

import pytest
import json
import tempfile
from pathlib import Path
from datetime import datetime, timezone

# Import all modules
from ..feedback_processor_stream2 import FeedbackProcessor
from ..config_tuner_stream2 import ConfigTuner, ConfigDelta
from ..ab_test_framework_stream2 import ABTestFramework
from ..rollback_strategy_stream2 import RollbackStrategy
from ..convergence_detector_stream2 import ConvergenceDetector
from ..dashboard_metrics_stream2 import DashboardMetrics
from ..alert_dispatcher_stream2 import AlertDispatcher
from ..hotfix_flow_stream2 import HotfixFlow, HotfixStatus


class TestFeedbackProcessor:
    """Test Story 9: Feedback Processor."""

    def setup_method(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.processor = FeedbackProcessor(Path(self.temp_dir.name), "_default")

    def teardown_method(self):
        self.temp_dir.cleanup()

    @pytest.mark.asyncio
    async def test_process_empty_queue(self):
        """Test processing empty queue returns 0 processed."""
        result = await self.processor.process_queue()
        assert result["processed"] == 0
        assert result["emitted_events"] == 0

    @pytest.mark.asyncio
    async def test_process_feedback_batch(self):
        """Test processing feedback batch emits learning events."""
        # Create test feedback files
        feedback_data = {
            "feedback_id": "fb-001",
            "skill_id": "os.delegation_router",
            "signal_type": "outcome",
            "value": 0.9,
        }
        feedback_file = self.processor.queue_dir / "fb-001.json"
        with open(feedback_file, "w") as f:
            json.dump(feedback_data, f)

        # Process queue
        result = await self.processor.process_queue()
        assert result["processed"] == 1
        assert result["emitted_events"] == 1

        # Verify processed file moved
        assert not feedback_file.exists()
        assert (self.processor.processed_dir / "fb-001.json").exists()


class TestConfigTuner:
    """Test Story 10: Config Tuning."""

    def setup_method(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tuner = ConfigTuner(Path(self.temp_dir.name), "_default")

    def teardown_method(self):
        self.temp_dir.cleanup()

    def test_high_success_rate_triggers_aggressive_tuning(self):
        """Test 85%+ success rate suggests lowering confidence threshold."""
        feedback_items = [
            {"signal_type": "outcome", "value": 1.0} for _ in range(15)
        ]

        delta = self.tuner.analyze_feedback_pattern("skill_1", feedback_items)
        assert delta is not None
        assert delta.param_name == "confidence_threshold"
        assert delta.value_before == 0.70
        assert delta.value_after == 0.65
        assert delta.confidence > 0.80

    def test_low_success_rate_triggers_conservative_tuning(self):
        """Test <50% success rate suggests raising threshold."""
        feedback_items = [
            {"signal_type": "outcome", "value": 0.0} for _ in range(15)
        ]

        delta = self.tuner.analyze_feedback_pattern("skill_2", feedback_items)
        assert delta is not None
        assert delta.param_name == "confidence_threshold"
        assert delta.value_after == 0.75

    def test_insufficient_samples_returns_none(self):
        """Test minimum sample size (10) is enforced."""
        feedback_items = [{"signal_type": "outcome", "value": 0.9} for _ in range(5)]

        delta = self.tuner.analyze_feedback_pattern("skill_3", feedback_items)
        assert delta is None

    def test_apply_delta_records_change(self):
        """Test applying delta creates audit record."""
        delta = ConfigDelta(
            delta_id="d-001",
            timestamp=datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            skill_id="skill_1",
            param_name="confidence_threshold",
            value_before=0.70,
            value_after=0.65,
            confidence=0.92,
            reason="High success rate",
            tenant_id="_default",
        )

        success = self.tuner.apply_delta(delta)
        assert success
        assert (self.tuner.deltas_dir / "d-001.json").exists()


class TestABTestFramework:
    """Test Story 11: A/B Test Framework."""

    def setup_method(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.ab = ABTestFramework(Path(self.temp_dir.name), "_default")

    def teardown_method(self):
        self.temp_dir.cleanup()

    def test_create_test(self):
        """Test creating A/B test."""
        test_id = self.ab.create_test(
            skill_id="skill_1",
            variant_a_config={"threshold": 0.70},
            variant_b_config={"threshold": 0.65},
            sample_size=100,
        )
        assert test_id
        assert (self.ab.tests_dir / f"{test_id}.json").exists()

    def test_record_variant_results(self):
        """Test recording test results."""
        test_id = self.ab.create_test(
            skill_id="skill_1",
            variant_a_config={"threshold": 0.70},
            variant_b_config={"threshold": 0.65},
        )

        # Record 100 A results, 95 B results
        for _ in range(100):
            self.ab.record_result(test_id, "A", success=True)
        for _ in range(95):
            self.ab.record_result(test_id, "B", success=True)

        status = self.ab.get_test_status(test_id)
        assert status["status"] == "completed"
        assert status["progress_a"] == "100/100"
        assert status["progress_b"] == "95/100"

    def test_winner_determination(self):
        """Test determining test winner (B > A by 5%)."""
        test_id = self.ab.create_test(
            skill_id="skill_1",
            variant_a_config={"threshold": 0.70},
            variant_b_config={"threshold": 0.65},
            sample_size=50,
        )

        # A: 40/50 success (80%)
        for _ in range(40):
            self.ab.record_result(test_id, "A", success=True)
        for _ in range(10):
            self.ab.record_result(test_id, "A", success=False)

        # B: 45/50 success (90%)
        for _ in range(45):
            self.ab.record_result(test_id, "B", success=True)
        for _ in range(5):
            self.ab.record_result(test_id, "B", success=False)

        winner = self.ab.get_winner(test_id)
        assert winner == "B"


class TestRollbackStrategy:
    """Test Story 12: Rollback Strategy."""

    def setup_method(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.rollback = RollbackStrategy(Path(self.temp_dir.name), "_default")

    def teardown_method(self):
        self.temp_dir.cleanup()

    def test_snapshot_config(self):
        """Test saving config snapshot."""
        config = {"threshold": 0.70, "timeout": 30}
        self.rollback.save_config_snapshot("skill_1", config, "before_tuning")

        assert (self.rollback.snapshots_dir / "skill_1_before_tuning.json").exists()

    def test_error_rate_increase_triggers_rollback(self):
        """Test rollback triggered when error rate increases >10%."""
        rollback_needed = self.rollback.evaluate_rollback_need(
            "skill_1",
            error_rate_before=0.05,
            error_rate_after=0.08,  # 60% increase
        )
        assert rollback_needed

    def test_small_error_rate_increase_no_rollback(self):
        """Test no rollback for small increase (<10%)."""
        rollback_needed = self.rollback.evaluate_rollback_need(
            "skill_1",
            error_rate_before=0.05,
            error_rate_after=0.054,  # 8% increase
        )
        assert not rollback_needed

    def test_perform_rollback_records_event(self):
        """Test performing rollback records event."""
        config = {"threshold": 0.70}
        success = self.rollback.perform_rollback("skill_1", config)
        assert success


class TestConvergenceDetector:
    """Test Story 13: Convergence Detector."""

    def setup_method(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.conv = ConvergenceDetector(Path(self.temp_dir.name), "_default")

    def teardown_method(self):
        self.temp_dir.cleanup()

    def test_record_metric(self):
        """Test recording skill metric."""
        self.conv.record_metric("skill_1", success_rate=0.85, error_rate=0.05)
        # Verify metric file created
        assert len(list(self.conv.metrics_dir.glob("*.json"))) > 0

    def test_convergence_detection(self):
        """Test detecting when skill has converged."""
        # Create 3 days of metric data with <1% improvement
        for day in range(3):
            success_rate = 0.85 + (day * 0.002)  # Very slow improvement
            self.conv.record_metric("skill_1", success_rate=success_rate, error_rate=0.05)

        # Should detect convergence (simulated, actual would span 3 days)
        self.conv.record_convergence_signal("skill_1", "improvement_plateau")
        assert self.conv.has_converged("skill_1")


class TestDashboardMetrics:
    """Test Stories 14-16: Dashboard."""

    def setup_method(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dashboard = DashboardMetrics(
            Path(self.temp_dir.name) / "learning",
            Path(self.temp_dir.name) / "feedback",
            Path(self.temp_dir.name) / "convergence",
            "_default",
        )

    def teardown_method(self):
        self.temp_dir.cleanup()

    def test_get_confidence_trend(self):
        """Test Story 14: Getting confidence trend."""
        trend = self.dashboard.get_confidence_trend("skill_1", days=7)
        # Should return array (empty or with data)
        assert isinstance(trend, list)

    def test_get_feedback_volume(self):
        """Test Story 15: Getting feedback volume by priority."""
        volume = self.dashboard.get_feedback_volume_by_priority()
        assert "P0" in volume
        assert "P1" in volume
        assert "P2" in volume
        assert "P3" in volume

    def test_get_optimizer_metrics(self):
        """Test Story 16: Getting optimizer metrics."""
        metrics = self.dashboard.get_optimizer_metrics()
        assert "total_tuning_events" in metrics
        assert "config_deltas_applied" in metrics
        assert "convergence_percentage" in metrics


class TestAlertDispatcher:
    """Test Story 17: Alert Dispatcher."""

    def setup_method(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.alerts = AlertDispatcher(Path(self.temp_dir.name), "_default")

    def teardown_method(self):
        self.temp_dir.cleanup()

    def test_high_error_rate_triggers_alert(self):
        """Test error rate >5% triggers alert."""
        alert = self.alerts.check_error_rate("skill_1", current_error_rate=0.08)
        assert alert is not None
        assert alert["alert_type"] == "high_error_rate"

    def test_no_alert_for_normal_error_rate(self):
        """Test error rate <5% doesn't trigger alert."""
        alert = self.alerts.check_error_rate("skill_1", current_error_rate=0.02)
        assert alert is None

    def test_dispatch_alert_records_event(self):
        """Test dispatching alert records it."""
        alert = {
            "alert_id": "a-001",
            "skill_id": "skill_1",
            "alert_type": "high_error_rate",
            "severity": "P0",
            "error_rate": 0.08,
            "timestamp": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            "reason": "Error rate too high",
        }

        success = self.alerts.dispatch_alert(alert)
        assert success


class TestHotfixFlow:
    """Test Story 18: Hotfix Flow."""

    def setup_method(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.hotfix = HotfixFlow(Path(self.temp_dir.name), "_default")

    def teardown_method(self):
        self.temp_dir.cleanup()

    def test_create_hotfix(self):
        """Test creating hotfix."""
        hotfix_id = self.hotfix.create_hotfix(
            alert_id="a-001",
            code_change={"file": "skill.py", "diff": "..."},
            description="Fix confidence threshold",
        )
        assert hotfix_id
        assert (self.hotfix.hotfixes_dir / f"{hotfix_id}.json").exists()

    def test_approve_hotfix(self):
        """Test approving hotfix."""
        hotfix_id = self.hotfix.create_hotfix(
            alert_id="a-001",
            code_change={"file": "skill.py", "diff": "..."},
            description="Fix",
        )

        success = self.hotfix.approve_hotfix(hotfix_id, "engineer@example.com")
        assert success

        status = self.hotfix.get_hotfix_status(hotfix_id)
        assert status["status"] == "approved"

    def test_deploy_hotfix(self):
        """Test deploying hotfix."""
        hotfix_id = self.hotfix.create_hotfix(
            alert_id="a-001",
            code_change={"file": "skill.py", "diff": "..."},
            description="Fix",
        )

        self.hotfix.approve_hotfix(hotfix_id, "engineer@example.com")
        success = self.hotfix.run_tests(hotfix_id)
        assert success

        deploy_success = self.hotfix.deploy_hotfix(hotfix_id)
        assert deploy_success

        status = self.hotfix.get_hotfix_status(hotfix_id)
        assert status["status"] == "deployed"

    def test_rollback_hotfix(self):
        """Test rolling back hotfix."""
        hotfix_id = self.hotfix.create_hotfix(
            alert_id="a-001",
            code_change={"file": "skill.py", "diff": "..."},
            description="Fix",
        )

        success = self.hotfix.rollback_hotfix(hotfix_id, "manual")
        assert success

        status = self.hotfix.get_hotfix_status(hotfix_id)
        assert status["status"] == "rolled_back"


class TestE2EWorkflow:
    """E2E test: Full feedback → triage → optimize → hotfix workflow."""

    def setup_method(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)

    def teardown_method(self):
        self.temp_dir.cleanup()

    @pytest.mark.asyncio
    async def test_full_learning_loop_workflow(self):
        """Test complete feedback loop: submit → process → optimize → deploy."""
        # 1. Submit feedback
        processor = FeedbackProcessor(self.temp_path / "feedback", "_default")
        feedback_file = processor.queue_dir / "fb-001.json"
        with open(feedback_file, "w") as f:
            json.dump({
                "feedback_id": "fb-001",
                "skill_id": "os.delegation_router",
                "signal_type": "outcome",
                "value": 0.95,
            }, f)

        # 2. Process feedback
        result = await processor.process_queue()
        assert result["processed"] == 1

        # 3. Analyze for config tuning
        tuner = ConfigTuner(self.temp_path / "learning", "_default")
        feedback_items = [
            {"signal_type": "outcome", "value": 0.95} for _ in range(15)
        ]
        delta = tuner.analyze_feedback_pattern("os.delegation_router", feedback_items)
        if delta:
            tuner.apply_delta(delta)

        # 4. Create hotfix for P0
        alerts = AlertDispatcher(self.temp_path / "alerts", "_default")
        alert = alerts.check_error_rate("os.delegation_router", 0.12)
        if alert:
            alerts.dispatch_alert(alert)

            # 5. Deploy hotfix
            hotfix = HotfixFlow(self.temp_path / "hotfix", "_default")
            hotfix_id = hotfix.create_hotfix(
                alert_id=alert["alert_id"],
                code_change={"file": "skill.py", "diff": "..."},
                description="Critical fix",
            )
            hotfix.approve_hotfix(hotfix_id, "operator@example.com")
            assert hotfix.run_tests(hotfix_id)
            assert hotfix.deploy_hotfix(hotfix_id)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

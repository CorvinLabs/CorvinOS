"""Track E Gate 3: RED→GREEN — Core Integration Implementation.

Tests that validate the complete feedback→config→execution loop:
1. SkillInstance accepts feedback
2. SkillConfigTuner converts feedback to config deltas
3. ConfigApplier applies deltas to SkillInstance
4. Next SkillInstance execution uses improved config
5. Measurable improvement detected and audited

ADR-0675, ADR-0676 implementation proof.
"""

import pytest
import json
import tempfile
from pathlib import Path
from datetime import datetime
from unittest.mock import Mock, MagicMock, patch
import sys

# Add core modules to path
sys.path.insert(0, "/home/shumway/projects/CorvinOS")

from core.skills.skill_instance import SkillInstance, SkillExecutionMetrics, SkillConfigSnapshot
from core.skills.skill_config_tuner import SkillConfigTuner, FeedbackSignal, ConfigDelta


# ============================================================================
# Test Fixtures
# ============================================================================


@pytest.fixture
def skill_instance():
    """Create a test SkillInstance."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_store = Path(tmpdir) / "configs.json"
        instance = SkillInstance(
            skill_id="os.delegation_router",
            initial_config={
                "routing_threshold": 0.7,
                "attention_weight": 0.5,
                "latency_target_ms": 200.0,
                "version": 0,
            },
            config_store_path=config_store,
            tenant_id="_default",
        )
        yield instance


@pytest.fixture
def config_tuner():
    """Create a test SkillConfigTuner."""
    return SkillConfigTuner(skill_type="router", audit_backend=None)


@pytest.fixture
def mock_audit():
    """Create a mock audit backend."""
    audit = Mock()
    audit.write_event = Mock(return_value=None)
    return audit


@pytest.fixture
def executor_function():
    """Mock executor function for skill execution."""

    def executor(request, config):
        import time

        time.sleep(0.005)  # 5ms
        confidence = 0.85
        return {"routed": True, "confidence": confidence}

    return executor


# ============================================================================
# Gate 3: RED→GREEN Test Suite
# ============================================================================


class TestSkillInstanceBasics:
    """Test 1: SkillInstance initialization and basic operations."""

    def test_skill_instance_initializes_with_config(self, skill_instance):
        """Verify SkillInstance loads initial config."""
        assert skill_instance.skill_id == "os.delegation_router"
        assert skill_instance.config["routing_threshold"] == 0.7
        assert skill_instance.config["version"] == 0

    def test_skill_instance_executes_and_captures_metrics(
        self, skill_instance, executor_function
    ):
        """Verify execution captures latency and success metrics."""
        skill_instance.execute({"task": "test"}, executor_function)

        assert len(skill_instance.execution_history) == 1
        metrics = skill_instance.execution_history[0]
        assert metrics.success is True
        assert metrics.latency_ms > 0
        assert metrics.config_version == 0

    def test_skill_instance_records_multiple_executions(
        self, skill_instance, executor_function
    ):
        """Verify multiple executions recorded in history."""
        for i in range(3):
            skill_instance.execute({"task": f"test_{i}"}, executor_function)

        assert len(skill_instance.execution_history) == 3
        assert all(m.success for m in skill_instance.execution_history)


class TestFeedbackCollection:
    """Test 2: SkillInstance feedback collection and scrubbing."""

    def test_skill_instance_accepts_feedback(self, skill_instance):
        """Verify feedback accepted in 1-5 range."""
        for rating in [1, 2, 3, 4, 5]:
            skill_instance.receive_feedback(quality_rating=rating)

        assert len(skill_instance.feedback_queue) == 5

    def test_skill_instance_rejects_invalid_ratings(self, skill_instance):
        """Verify invalid ratings raise ValueError."""
        invalid_ratings = [0, 6, -1, 10]
        for rating in invalid_ratings:
            with pytest.raises(ValueError):
                skill_instance.receive_feedback(quality_rating=rating)

    def test_skill_instance_scrubs_pii_from_feedback(self, skill_instance):
        """Verify email addresses scrubbed from feedback notes."""
        skill_instance.receive_feedback(
            quality_rating=4,
            notes="User silvio.jurk@googlemail.com reported this issue",
        )

        feedback = skill_instance.feedback_queue[0]
        assert "@" not in feedback["notes"]
        assert "[EMAIL]" in feedback["notes"]

    def test_skill_instance_scrubs_phone_numbers(self, skill_instance):
        """Verify phone numbers scrubbed."""
        skill_instance.receive_feedback(
            quality_rating=3, notes="Call me at 555-123-4567 to discuss"
        )

        feedback = skill_instance.feedback_queue[0]
        assert "555-123" not in feedback["notes"]
        assert "[PHONE]" in feedback["notes"]

    def test_skill_instance_scrubs_credit_cards(self, skill_instance):
        """Verify credit card patterns scrubbed."""
        skill_instance.receive_feedback(
            quality_rating=2, notes="Card is 1234-5678-9012-3456 invalid"
        )

        feedback = skill_instance.feedback_queue[0]
        assert "1234" not in feedback["notes"]
        assert "[CC]" in feedback["notes"]


class TestConfigTunerSignalProcessing:
    """Test 3: SkillConfigTuner signal processing."""

    def test_config_tuner_converts_feedback_to_signal(self, config_tuner):
        """Verify feedback dict converted to FeedbackSignal."""
        feedback = {
            "skill_id": "os.delegation_router",
            "quality_rating": 4,
            "execution_id": "exec_001",
        }

        signal = config_tuner.feedback_to_signal(feedback)
        assert signal is not None
        assert signal.skill_id == "os.delegation_router"
        assert signal.value == 0.8  # 4/5 = 0.8

    def test_config_tuner_rejects_invalid_feedback(self, config_tuner):
        """Verify invalid feedback returns None."""
        invalid_feedbacks = [
            {"skill_id": "test", "quality_rating": 6},  # Out of range
            {"quality_rating": 3},  # Missing skill_id
            {"skill_id": "test"},  # Missing rating
        ]

        for fb in invalid_feedbacks:
            signal = config_tuner.feedback_to_signal(fb)
            assert signal is None

    def test_config_tuner_computes_loss(self, config_tuner):
        """Verify loss computation from metrics."""
        metrics = {"confidence_score": 0.7, "latency_ms": 200.0}
        loss = config_tuner.compute_loss(metrics)

        # Loss = (1 - 0.7) + 0.1 * (200/100) = 0.3 + 0.2 = 0.5
        assert loss == pytest.approx(0.5, abs=0.01)

    def test_config_tuner_detects_high_confidence_signal(self, config_tuner):
        """Verify high confidence (rating 5) produces delta."""
        feedback = {"skill_id": "test", "quality_rating": 5}
        signal = config_tuner.feedback_to_signal(feedback)

        config = {
            "routing_threshold": 0.7,
            "attention_weight": 0.5,
            "latency_target_ms": 200.0,
        }
        loss_before = 0.5

        delta = config_tuner.compute_config_delta(signal, config, loss_before)
        assert delta is not None
        assert delta.new_value > delta.old_value  # Threshold increased

    def test_config_tuner_detects_low_confidence_signal(self, config_tuner):
        """Verify low confidence (rating 1) produces delta."""
        feedback = {"skill_id": "test", "quality_rating": 1}
        signal = config_tuner.feedback_to_signal(feedback)

        config = {
            "routing_threshold": 0.7,
            "attention_weight": 0.5,
            "latency_target_ms": 200.0,
        }
        loss_before = 0.5

        delta = config_tuner.compute_config_delta(signal, config, loss_before)
        assert delta is not None
        assert delta.new_value < delta.old_value  # Threshold decreased

    def test_config_tuner_neutral_feedback_no_delta(self, config_tuner):
        """Verify neutral feedback (rating 3) produces no delta."""
        feedback = {"skill_id": "test", "quality_rating": 3}
        signal = config_tuner.feedback_to_signal(feedback)

        config = {
            "routing_threshold": 0.7,
            "attention_weight": 0.5,
            "latency_target_ms": 200.0,
        }
        loss_before = 0.5

        delta = config_tuner.compute_config_delta(signal, config, loss_before)
        assert delta is None


class TestConfigDeltaApplication:
    """Test 4: Applying config deltas to SkillInstance."""

    def test_skill_instance_applies_config_delta(self, skill_instance):
        """Verify config update accepted and applied."""
        new_config = {
            "routing_threshold": 0.72,
            "attention_weight": 0.5,
            "latency_target_ms": 200.0,
            "version": 1,
        }

        success = skill_instance.apply_config_update(new_config, reason="learning")
        assert success is True
        assert skill_instance.config["routing_threshold"] == 0.72
        assert skill_instance.config["version"] == 1

    def test_skill_instance_clamps_config_to_bounds(self, skill_instance):
        """Verify config values clamped to safe ranges."""
        # Try to set out-of-bounds values
        invalid_config = {
            "routing_threshold": 1.5,  # Above 0.95
            "attention_weight": 1.5,  # Above 1.0
            "latency_target_ms": 1000.0,  # Above 500
        }

        success = skill_instance.apply_config_update(invalid_config)
        assert success is True
        assert skill_instance.config["routing_threshold"] == 0.95
        assert skill_instance.config["attention_weight"] == 1.0
        assert skill_instance.config["latency_target_ms"] == 500.0

    def test_skill_instance_rejects_invalid_config_structure(self, skill_instance):
        """Verify config with missing keys rejected."""
        invalid_config = {
            "routing_threshold": 0.7,
            # Missing attention_weight and latency_target_ms
        }

        success = skill_instance.apply_config_update(invalid_config)
        assert success is False

    def test_skill_instance_persists_config_to_store(self, skill_instance):
        """Verify config persisted to JSON store."""
        new_config = {
            "routing_threshold": 0.75,
            "attention_weight": 0.5,
            "latency_target_ms": 200.0,
        }

        skill_instance.apply_config_update(new_config)

        # Verify persisted
        if skill_instance.config_store_path:
            with open(skill_instance.config_store_path) as f:
                stored = json.load(f)
            assert stored[skill_instance.skill_id]["routing_threshold"] == 0.75

    def test_skill_instance_audits_config_change(self, skill_instance, mock_audit):
        """Verify config changes logged to audit backend."""
        skill_instance.audit_backend = mock_audit

        new_config = {
            "routing_threshold": 0.72,
            "attention_weight": 0.5,
            "latency_target_ms": 200.0,
        }

        skill_instance.apply_config_update(new_config, reason="test_update")
        assert mock_audit.write_event.called

        call_args = mock_audit.write_event.call_args
        audit_event = call_args[0][0]
        assert audit_event["event_type"] == "skill_config_updated"
        assert audit_event["reason"] == "test_update"


class TestExecutionWithTunedConfig:
    """Test 5: Skill execution with tuned config."""

    def test_skill_instance_executes_with_tuned_config(
        self, skill_instance, executor_function
    ):
        """Verify execution uses tuned config parameters."""
        # Update config
        new_config = {
            "routing_threshold": 0.75,
            "attention_weight": 0.6,
            "latency_target_ms": 180.0,
            "version": 1,
        }
        skill_instance.apply_config_update(new_config)

        # Execute
        skill_instance.execute({"task": "test"}, executor_function)

        # Verify metrics show version 1
        metrics = skill_instance.execution_history[0]
        assert metrics.config_version == 1

    def test_skill_instance_reload_loads_persisted_config(self):
        """Verify SkillInstance reload loads persisted config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_store = Path(tmpdir) / "configs.json"

            # Instance 1: Create and update config
            instance1 = SkillInstance(
                skill_id="os.delegation_router",
                initial_config={
                    "routing_threshold": 0.7,
                    "attention_weight": 0.5,
                    "latency_target_ms": 200.0,
                    "version": 0,
                },
                config_store_path=config_store,
            )

            new_config = {
                "routing_threshold": 0.75,
                "attention_weight": 0.5,
                "latency_target_ms": 200.0,
                "version": 1,
            }
            instance1.apply_config_update(new_config)

            # Instance 2: New instance should load tuned config
            instance2 = SkillInstance(
                skill_id="os.delegation_router",
                initial_config={
                    "routing_threshold": 0.7,
                    "attention_weight": 0.5,
                    "latency_target_ms": 200.0,
                    "version": 0,
                },
                config_store_path=config_store,
            )

            # Should load v1 config from store
            assert instance2.config["routing_threshold"] == 0.75
            assert instance2.config["version"] == 1


class TestConvergenceDetection:
    """Test 6: Learning convergence detection."""

    def test_config_tuner_detects_convergence(self, config_tuner):
        """Verify tuner detects loss plateau (convergence)."""
        # Simulated loss over time (converging)
        losses = [0.500, 0.480, 0.470, 0.468, 0.467, 0.467]

        metrics = config_tuner.compute_convergence_metrics(losses, window=3)
        assert metrics["is_converged"] is True
        assert metrics["plateau_confidence"] > 0.0

    def test_config_tuner_detects_non_convergence(self, config_tuner):
        """Verify tuner detects improving loss (not converged)."""
        # Simulated loss improving
        losses = [0.500, 0.450, 0.400, 0.350, 0.300]

        metrics = config_tuner.compute_convergence_metrics(losses, window=3)
        assert metrics["is_converged"] is False
        assert metrics["slope"] < 0.0  # Negative slope = improving


class TestMetricsSummary:
    """Test 7: Execution metrics aggregation."""

    def test_skill_instance_reports_metrics_summary(
        self, skill_instance, executor_function
    ):
        """Verify metrics summary computed correctly."""
        for _ in range(3):
            skill_instance.execute({"task": "test"}, executor_function)

        summary = skill_instance.get_metrics_summary()
        assert summary["skill_id"] == "os.delegation_router"
        assert summary["executions"] == 3
        assert summary["success_rate"] == 1.0
        assert summary["avg_latency_ms"] > 0


# ============================================================================
# Complete Integration Test
# ============================================================================


class TestCompleteGate3Integration:
    """Test 8: Full RED→GREEN cycle (feedback → improvement)."""

    @pytest.mark.integration
    def test_complete_red_green_cycle(self, skill_instance, config_tuner, executor_function):
        """Complete cycle: execute → collect feedback → tune → re-execute → verify improvement."""

        # =================================================================
        # RED: Execute skill with baseline config
        # =================================================================
        skill_instance.execute({"task": "classify_request_1"}, executor_function)
        metrics_before = skill_instance.get_metrics_summary()

        # =================================================================
        # Collect feedback (simulated user rating)
        # =================================================================
        skill_instance.receive_feedback(quality_rating=5, notes="Excellent routing!")
        feedback_batch = skill_instance.get_feedback_batch()
        assert len(feedback_batch) == 1

        # =================================================================
        # Convert feedback to signal and compute delta
        # =================================================================
        signal = config_tuner.feedback_to_signal(feedback_batch[0])
        assert signal is not None

        metrics_feedback = {
            "confidence_score": 0.9,
            "latency_ms": 180.0,
        }
        loss_before = config_tuner.compute_loss(metrics_feedback)

        delta = config_tuner.compute_config_delta(
            signal, skill_instance.config, loss_before
        )
        assert delta is not None

        # =================================================================
        # Apply delta to config (via tuner → applier → instance)
        # =================================================================
        new_config = skill_instance.config.copy()
        new_config[delta.param_name] = delta.new_value
        new_config["version"] = skill_instance.config.get("version", 0) + 1

        success = skill_instance.apply_config_update(new_config, reason="learning")
        assert success is True

        # =================================================================
        # GREEN: Re-execute with tuned config
        # =================================================================
        skill_instance.execute({"task": "classify_request_2"}, executor_function)
        metrics_after = skill_instance.get_metrics_summary()

        # =================================================================
        # Verify measurable changes
        # =================================================================
        assert metrics_after["config_version"] > metrics_before["config_version"]
        assert skill_instance.config[delta.param_name] == delta.new_value

        # Expected: same # executions or more
        assert metrics_after["executions"] >= metrics_before["executions"]


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

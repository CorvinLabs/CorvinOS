"""Phase 2 Tests: Learning Loop + Weight Optimizer + Feedback Collection."""

import pytest
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core" / "skills" / "os_skills"))

from definition_of_done_verifier.loss_signal import DoD_LossSignal
from definition_of_done_verifier.feedback_event import DoD_FeedbackEvent
from definition_of_done_verifier.weight_optimizer import DoD_WeightOptimizer, OptimizationResult
from definition_of_done_verifier.weight_persistence import WeightPersistence
from definition_of_done_verifier.api_handlers import DoD_OutcomeSink, DoD_FeedbackCollector


# ============================================================================
# UNIT TESTS: Loss Signal + Feedback Event (6 tests)
# ============================================================================

class TestDoD_LossSignal:
    """Loss signal creation and immutability."""

    def test_create_signal_with_scores(self):
        signal = DoD_LossSignal(
            task_id="t1",
            dod_score=0.85,
            task_type="api_endpoint",
        )
        assert signal.dod_score == 0.85
        assert signal.feedback_received == False

    def test_signal_with_feedback(self):
        signal = DoD_LossSignal(
            task_id="t1",
            dod_score=0.75,
            operator_score=0.85,
            feedback_received=True,
            feedback_reason="too_harsh",
        )
        assert signal.operator_score == 0.85
        assert signal.feedback_received == True


class TestDoD_FeedbackEvent:
    """Feedback event creation and delta computation."""

    def test_feedback_delta_positive(self):
        feedback = DoD_FeedbackEvent(
            task_id="t1",
            dod_score_automatic=0.65,
            dod_score_operator=0.85,
        )
        assert feedback.delta == 0.20

    def test_feedback_delta_negative(self):
        feedback = DoD_FeedbackEvent(
            task_id="t1",
            dod_score_automatic=0.85,
            dod_score_operator=0.60,
        )
        assert feedback.delta == -0.25


# ============================================================================
# UNIT TESTS: Weight Optimizer (15 tests)
# ============================================================================

class TestDoD_WeightOptimizer:
    """Weight learning and convergence."""

    def test_default_weights_initialized(self):
        opt = DoD_WeightOptimizer()
        assert "api_endpoint" in opt.weights
        assert sum(opt.weights["api_endpoint"].values()) > 0.99

    def test_learn_harsh_feedback_single_check(self):
        """Single harsh feedback reduces check weight."""
        opt = DoD_WeightOptimizer()
        old_w = opt.weights["api_endpoint"]["w_audit"]

        opt.observe_feedback("api_endpoint", delta=0.20, affected_checks=["audit_trail"])

        new_w = opt.weights["api_endpoint"]["w_audit"]
        assert new_w < old_w

    def test_learn_lenient_feedback_single_check(self):
        """Single lenient feedback increases check weight."""
        opt = DoD_WeightOptimizer()
        old_w = opt.weights["api_endpoint"]["w_reach"]

        opt.observe_feedback("api_endpoint", delta=-0.15, affected_checks=["reachability"])

        new_w = opt.weights["api_endpoint"]["w_reach"]
        assert new_w > old_w

    def test_learn_multiple_checks(self):
        """Multiple affected checks all get adjusted."""
        opt = DoD_WeightOptimizer()
        old_weights = {
            "w_audit": opt.weights["api_endpoint"]["w_audit"],
            "w_test": opt.weights["api_endpoint"]["w_test"],
        }

        opt.observe_feedback(
            "api_endpoint",
            delta=0.15,
            affected_checks=["audit_trail", "test_evidence"]
        )

        new_weights = {
            "w_audit": opt.weights["api_endpoint"]["w_audit"],
            "w_test": opt.weights["api_endpoint"]["w_test"],
        }

        assert new_weights["w_audit"] < old_weights["w_audit"]
        assert new_weights["w_test"] < old_weights["w_test"]

    def test_weights_normalize_after_update(self):
        """Weights always sum to 1.0."""
        opt = DoD_WeightOptimizer()

        for _ in range(5):
            opt.observe_feedback(
                "api_endpoint",
                delta=0.10,
                affected_checks=["audit_trail"]
            )

        total = sum(opt.weights["api_endpoint"].values())
        assert abs(total - 1.0) < 0.01

    def test_confidence_increases_with_feedback(self):
        """Confidence builds with each feedback."""
        opt = DoD_WeightOptimizer()
        initial_conf = opt.confidence["api_endpoint"]["w_audit"]

        opt.observe_feedback(
            "api_endpoint",
            delta=0.15,
            affected_checks=["audit_trail"]
        )

        new_conf = opt.confidence["api_endpoint"]["w_audit"]
        assert new_conf > initial_conf

    def test_convergence_at_threshold(self):
        """Convergence detected when confidence >= 0.8."""
        opt = DoD_WeightOptimizer()
        assert not opt.is_converged("api_endpoint")

        for _ in range(8):
            opt.observe_feedback(
                "api_endpoint",
                delta=0.10,
                affected_checks=["audit_trail"]
            )

        assert opt.is_converged("api_endpoint", threshold=0.8)

    def test_recommend_uses_defaults_low_confidence(self):
        """Low confidence → use defaults."""
        opt = DoD_WeightOptimizer()
        weights = opt.recommend_next_weights("api_endpoint")

        # Should be defaults (no feedback given yet)
        defaults = opt.DEFAULT_WEIGHTS
        for w_name in weights:
            assert abs(weights[w_name] - defaults.get(w_name, 0)) < 0.001

    def test_recommend_uses_learned_high_confidence(self):
        """High confidence → use learned."""
        opt = DoD_WeightOptimizer()

        # Build confidence
        for _ in range(8):
            opt.observe_feedback(
                "api_endpoint",
                delta=0.15,
                affected_checks=["audit_trail"]
            )

        weights = opt.recommend_next_weights("api_endpoint")
        defaults = opt.DEFAULT_WEIGHTS

        # Should differ from defaults (learned from feedback)
        differs = False
        for w_name in weights:
            if abs(weights[w_name] - defaults.get(w_name, 0)) > 0.001:
                differs = True
                break
        assert differs

    def test_per_task_type_learning(self):
        """Different task types learn independently."""
        opt = DoD_WeightOptimizer()

        # Feedback for API
        opt.observe_feedback(
            "api_endpoint",
            delta=0.20,
            affected_checks=["audit_trail"]
        )

        # Feedback for CLI (opposite direction)
        opt.observe_feedback(
            "cli_command",
            delta=-0.20,
            affected_checks=["audit_trail"]
        )

        api_w = opt.weights["api_endpoint"]["w_audit"]
        cli_w = opt.weights["cli_command"]["w_audit"]

        # Should differ due to opposite feedback
        assert abs(api_w - cli_w) > 0.001

    def test_sample_count_increments(self):
        """Sample count tracks feedback per task type."""
        opt = DoD_WeightOptimizer()
        assert opt.sample_count["api_endpoint"] == 0

        opt.observe_feedback("api_endpoint", delta=0.10, affected_checks=["audit_trail"])
        assert opt.sample_count["api_endpoint"] == 1

        opt.observe_feedback("api_endpoint", delta=0.10, affected_checks=["audit_trail"])
        assert opt.sample_count["api_endpoint"] == 2

    def test_convergence_per_task_type(self):
        """Convergence is independent per task type."""
        opt = DoD_WeightOptimizer()

        # Build confidence only for API
        for _ in range(8):
            opt.observe_feedback(
                "api_endpoint",
                delta=0.10,
                affected_checks=["audit_trail"]
            )

        # API converged, CLI not
        assert opt.is_converged("api_endpoint")
        assert not opt.is_converged("cli_command")


# ============================================================================
# UNIT TESTS: Persistence (6 tests)
# ============================================================================

class TestWeightPersistence:
    """Weight persistence to disk."""

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            persist = WeightPersistence(storage_path=Path(tmpdir) / "weights.json")

            weights = {
                "api_endpoint": {
                    "w_audit": 0.30,
                    "w_reach": 0.18,
                }
            }
            confidence = {
                "api_endpoint": {
                    "w_audit": 0.8,
                    "w_reach": 0.5,
                }
            }

            persist.save_weights(weights, confidence)
            loaded = persist.load_weights()

            assert loaded["api_endpoint"]["w_audit"] == 0.30

    def test_load_missing_file_returns_empty(self):
        persist = WeightPersistence(storage_path=Path("/tmp/nonexistent_xyz_abc.json"))
        loaded = persist.load_weights()
        assert loaded == {}


# ============================================================================
# UNIT TESTS: Feedback Collector (8 tests)
# ============================================================================

class TestDoD_FeedbackCollector:
    """Feedback collection and weight optimization."""

    def test_submit_feedback_harsh(self):
        collector = DoD_FeedbackCollector()

        result = collector.submit_feedback(
            task_id="t1",
            dod_score_automatic=0.65,
            dod_score_operator=0.85,
            reason="too_harsh",
            affected_checks=["audit_trail"],
            task_type="api_endpoint",
        )

        assert result["success"] == True
        assert result["delta"] == 0.20

    def test_submit_feedback_lenient(self):
        collector = DoD_FeedbackCollector()

        result = collector.submit_feedback(
            task_id="t1",
            dod_score_automatic=0.85,
            dod_score_operator=0.60,
            reason="too_lenient",
            affected_checks=["reachability"],
            task_type="cli_command",
        )

        assert result["success"] == True
        assert result["delta"] == -0.25

    def test_get_convergence_status(self):
        collector = DoD_FeedbackCollector()

        status = collector.get_convergence_status("api_endpoint")
        assert status["converged"] == False

    def test_get_weights_for_task_type(self):
        collector = DoD_FeedbackCollector()

        weights = collector.get_weights_for_task_type("api_endpoint")
        assert isinstance(weights, dict)
        assert "w_audit" in weights
        assert sum(weights.values()) > 0.99


# ============================================================================
# PHASE 2 GATE TESTS
# ============================================================================

class TestPhase2Gate:
    """Gate: all Phase 2 components must be present and functional."""

    @pytest.mark.gate
    def test_loss_signal_immutable(self):
        """LossSignal is frozen dataclass."""
        signal = DoD_LossSignal(task_id="t1", dod_score=0.5)
        try:
            signal.dod_score = 0.9
            assert False, "Should be immutable"
        except AttributeError:
            pass

    @pytest.mark.gate
    def test_weight_optimizer_exists(self):
        """WeightOptimizer implements learning."""
        opt = DoD_WeightOptimizer()
        assert hasattr(opt, 'observe_feedback')
        assert hasattr(opt, 'recommend_next_weights')
        assert hasattr(opt, 'is_converged')

    @pytest.mark.gate
    def test_feedback_collector_exists(self):
        """FeedbackCollector handles feedback."""
        collector = DoD_FeedbackCollector()
        assert hasattr(collector, 'submit_feedback')
        assert hasattr(collector, 'get_weights_for_task_type')
        assert hasattr(collector, 'get_convergence_status')

    @pytest.mark.gate
    def test_weight_persistence_exists(self):
        """WeightPersistence handles disk I/O."""
        persist = WeightPersistence()
        assert hasattr(persist, 'load_weights')
        assert hasattr(persist, 'save_weights')


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-m", "not gate"])

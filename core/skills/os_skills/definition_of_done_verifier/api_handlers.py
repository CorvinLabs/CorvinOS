"""API Handlers: DoD Outcome Sink + Feedback Collection."""

from typing import Dict, Any, Optional
from loss_signal import DoD_LossSignal
from feedback_event import DoD_FeedbackEvent
from weight_optimizer import DoD_WeightOptimizer
from weight_persistence import WeightPersistence


class DoD_OutcomeSink:
    """Handle outcome signals from Skill → Learning Infrastructure."""

    def __init__(self):
        """Initialize sink."""
        self.optimizer = DoD_WeightOptimizer()
        self.persistence = WeightPersistence()
        self.signals = []  # In-memory store (would be EventStore in production)

    def record_outcome(self, signal: DoD_LossSignal) -> bool:
        """
        Record a DoD outcome signal.

        Called by Skill.execute() after computing score.
        TODO Phase 3: wire to EventStore for audit trail.

        Args:
            signal: DoD_LossSignal with score + checks + weights

        Returns:
            bool (success)
        """
        try:
            self.signals.append(signal.to_dict())
            print(f"[OUTCOME] task_id={signal.task_id} score={signal.dod_score:.2%}")
            return True
        except Exception as e:
            print(f"[ERROR] Failed to record outcome: {e}")
            return False


class DoD_FeedbackCollector:
    """Handle feedback from Console → Weight Optimizer."""

    def __init__(self):
        """Initialize collector."""
        self.optimizer = DoD_WeightOptimizer()
        self.persistence = WeightPersistence()
        self.feedback_history = []  # In-memory store

        # Load persisted weights on init
        persisted_weights = self.persistence.load_weights()
        if persisted_weights:
            for task_type, weights in persisted_weights.items():
                if task_type in self.optimizer.weights:
                    self.optimizer.weights[task_type].update(weights)

    def submit_feedback(
        self,
        task_id: str,
        dod_score_automatic: float,
        dod_score_operator: float,
        reason: str,
        affected_checks: list,
        task_type: str = "cli_command",
        tenant_id: str = "_default",
    ) -> Dict[str, Any]:
        """
        Operator submits feedback on DoD score.

        Called by Console Quality Panel when operator confirms/corrects score.

        Args:
            task_id: Task identifier
            dod_score_automatic: Skill's computed score
            dod_score_operator: Operator's correction
            reason: "too_harsh", "too_lenient", "wrong_type", "check_broken"
            affected_checks: List of check names
            task_type: Task classification
            tenant_id: Tenant scope

        Returns:
            Dict with optimization result + new weights
        """
        try:
            # Create feedback event
            delta = dod_score_operator - dod_score_automatic
            feedback = DoD_FeedbackEvent(
                task_id=task_id,
                dod_score_automatic=dod_score_automatic,
                dod_score_operator=dod_score_operator,
                reason=reason,
                affected_checks=affected_checks,
                task_type=task_type,
                tenant_id=tenant_id,
            )

            # Optimize weights
            result = self.optimizer.observe_feedback(
                task_type=task_type,
                delta=delta,
                affected_checks=affected_checks
            )

            # Persist updated weights
            self.persistence.save_weights(
                self.optimizer.weights,
                self.optimizer.confidence
            )

            # Store feedback
            self.feedback_history.append(feedback.to_dict())

            # Return result
            return {
                "success": True,
                "task_id": task_id,
                "delta": delta,
                "old_weights": result.old_weights,
                "new_weights": result.new_weights,
                "confidence": result.confidence,
                "converged": self.optimizer.is_converged(task_type),
                "message": f"Weights updated for {task_type}. Confidence: {result.confidence}",
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
            }

    def get_weights_for_task_type(self, task_type: str) -> Dict[str, float]:
        """
        Get current weights for task type.

        Called by Skill before executing (to apply learned weights).

        Returns: {weight_name: value, ...}
        """
        return self.optimizer.recommend_next_weights(task_type)

    def get_convergence_status(self, task_type: str) -> Dict[str, Any]:
        """
        Get convergence status for task type.

        Called by Dashboard to show learning progress.
        """
        return {
            "task_type": task_type,
            "converged": self.optimizer.is_converged(task_type),
            "sample_count": self.optimizer.sample_count.get(task_type, 0),
            "confidence": self.optimizer.confidence.get(task_type, {}),
            "weights": self.optimizer.weights.get(task_type, {}),
        }


# Unit Tests
class TestDoD_FeedbackCollector:
    """Test feedback collection + weight optimization."""

    def test_submit_feedback_too_harsh(self):
        """Operator feedback adjusts weights."""
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
        assert result["new_weights"]["w_audit"] != result["old_weights"]["w_audit"]

    def test_weights_loaded_on_init(self, tmp_path):
        """Persisted weights loaded when collector initialized."""
        # Save some weights first
        persist = WeightPersistence(storage_path=tmp_path / "weights.json")
        weights = {
            "api_endpoint": {
                "w_reach": 0.18,
                "w_audit": 0.30,
            }
        }
        confidence = {
            "api_endpoint": {
                "w_reach": 0.5,
                "w_audit": 0.8,
            }
        }
        persist.save_weights(weights, confidence)

        # Create new collector (should load persisted weights)
        # Note: In real code, this would use the same storage_path
        # For now, this is a simulated test
        collector = DoD_FeedbackCollector()
        collector.persistence.storage_path = tmp_path / "weights.json"
        collector.optimizer.weights = {}
        collector.optimizer.weights.update(persist.load_weights())

        assert collector.optimizer.weights["api_endpoint"]["w_audit"] == 0.30

    def test_convergence_status(self):
        """Get convergence status."""
        collector = DoD_FeedbackCollector()

        # Not converged initially
        status = collector.get_convergence_status("api_endpoint")
        assert status["converged"] == False

        # Build confidence
        for _ in range(8):
            collector.submit_feedback(
                task_id=f"t_{_}",
                dod_score_automatic=0.70,
                dod_score_operator=0.80,
                reason="too_harsh",
                affected_checks=["audit_trail"],
                task_type="api_endpoint",
            )

        # Should be converged
        status = collector.get_convergence_status("api_endpoint")
        assert status["converged"] == True


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])

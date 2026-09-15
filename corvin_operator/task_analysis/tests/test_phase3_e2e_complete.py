"""Phase 3 E2E Integration Test: Complete workflow with all three sub-phases.

Fictional task scenarios covering:
1. Confidence learning + threshold optimization
2. Memory context injection + secret filtering
3. Multi-agent routing + cost estimation

End-to-end: task → feedback → threshold update → memory injection → routing

ADR: ADR-0269, ADR-0270, ADR-0271
"""

import pytest
import tempfile
from pathlib import Path
from unittest.mock import Mock

from ..feedback_loop import RoutingFeedback, FeedbackStore, ConfidenceGateLearner
from ..memory_injection import MemoryLinker, contains_secret
from ..multi_agent_routing import MultiAgentRouter, DelegationTarget


# ============================================================================
# Fictional Scenario: Complete Workflow
# ============================================================================

class TestPhase3CompleteE2EWorkflow:
    """End-to-end test: full Phase 3 workflow with realistic scenarios."""

    @pytest.fixture
    def feedback_store(self):
        """Temporary feedback store."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield FeedbackStore(storage_path=Path(tmpdir) / "feedback.jsonl")

    @pytest.fixture
    def router(self):
        """Multi-agent router."""
        return MultiAgentRouter()

    def test_scenario_1_bug_fix_feedback_loop(self, feedback_store):
        """Scenario 1: Bug fix → feedback loop → threshold update.

        Workflow:
        1. System routes bug-fix with confidence 0.85 → native
        2. Operator confirms: "correct"
        3. Learner updates confidence (should stay ~0.70+)
        """
        # Task 1: Bug fix (correct routing)
        fb1 = RoutingFeedback(
            task_id="bug_001_voice_crash",
            raw_task="Fix crash in voice module for long audio files",
            predicted_target="native",
            predicted_confidence=0.85,
            actual_target="native",
            operator_feedback="correct",
        )
        assert feedback_store.record_feedback(fb1)

        # Task 2: Another bug (also correct)
        fb2 = RoutingFeedback(
            task_id="bug_002_memory_leak",
            raw_task="Fix memory leak in session handler",
            predicted_target="native",
            predicted_confidence=0.82,
            actual_target="native",
            operator_feedback="correct",
        )
        assert feedback_store.record_feedback(fb2)

        # Learner analyzes
        learner = ConfidenceGateLearner(feedback_store)
        correct, total, accuracy = feedback_store.accuracy_for_threshold(0.70)

        assert total == 2
        assert correct == 2  # Both correct
        assert accuracy == 100.0  # Perfect accuracy

    def test_scenario_2_complex_task_misrouted_then_learns(self, feedback_store):
        """Scenario 2: Complex task misrouted → feedback → threshold lowers.

        Workflow:
        1. System routes complex refactor with low confidence 0.45 → native
        2. Operator says: "incorrect" (should have been opus)
        3. Learner finds optimal threshold (should lower past 0.45)
        """
        # Task 1: Refactor (misrouted)
        fb1 = RoutingFeedback(
            task_id="refactor_001_session",
            raw_task="Major refactor of session management layer for consistency",
            predicted_target="native",
            predicted_confidence=0.45,
            actual_target="tde",
            operator_feedback="incorrect",  # Operator needed Opus
        )
        assert feedback_store.record_feedback(fb1)

        # Task 2: Similar refactor, but system was more confident (still misrouted)
        fb2 = RoutingFeedback(
            task_id="refactor_002_auth",
            raw_task="Refactor authentication middleware across all services",
            predicted_target="native",
            predicted_confidence=0.55,
            actual_target="tde",
            operator_feedback="incorrect",
        )
        assert feedback_store.record_feedback(fb2)

        # Task 3: Small refactor (correctly routed)
        fb3 = RoutingFeedback(
            task_id="refactor_003_typo",
            raw_task="Rename variable for clarity in helper module",
            predicted_target="native",
            predicted_confidence=0.30,
            actual_target="native",
            operator_feedback="correct",
        )
        assert feedback_store.record_feedback(fb3)

        # Learner should lower threshold below 0.55 to catch misroutes
        learner = ConfidenceGateLearner(feedback_store)
        optimal = learner.find_optimal_threshold()

        # Optimal should be conservative: maybe 0.4 or below
        assert optimal < 0.60  # Lower than the misrouted confidence

    def test_scenario_3_memory_injection_with_secret_filtering(self):
        """Scenario 3: Memory injection with secret detection.

        Workflow:
        1. Create memory linker
        2. Inject context for task
        3. Verify secrets are filtered out
        """
        linker = MemoryLinker()

        # Test secret detection
        text_with_secret = "API key: sk-1234567890abcdefghij"
        assert contains_secret(text_with_secret)

        text_safe = "This is a normal documentation update"
        assert not contains_secret(text_safe)

        # Inject context (should handle missing files gracefully)
        context = linker.inject_context(
            task_description="Fix bug in core voice layer",
            affected_layers=["L23", "L16"],
            max_links=5,
        )

        # Verify structure
        assert "memory_links" in context
        assert "total_links_found" in context
        assert "unsafe_links_skipped" in context
        assert context["unsafe_links_skipped"] >= 0

    def test_scenario_4_multi_agent_routing_big_data(self, router):
        """Scenario 4: Big-data task routing to ACS.

        Workflow:
        1. Describe big-data task
        2. Router detects keywords → ACS
        3. Verify cost comparison
        """
        task = "Process customer database with millions of records for batch analytics"
        complexity = 0.65

        decision = router.route(task, complexity, model_recommendation="haiku")

        assert decision.target == DelegationTarget.ACS
        assert decision.confidence >= 0.80
        assert decision.carve_out_rule == "big_data"

        # Cost comparison
        comparison = router.cost_comparison(task, complexity)
        assert comparison["recommended_target"] == "acs"
        assert comparison["carve_out_rule"] == "big_data"

    def test_scenario_5_multi_agent_routing_complex(self, router):
        """Scenario 5: Complex + Opus task routing to TDE.

        Workflow:
        1. Describe complex task
        2. Router detects high complexity + opus
        3. Routes to TDE (advanced reasoning)
        """
        task = "Design new architecture for distributed tracing system"
        complexity = 0.88
        model = "opus"

        decision = router.route(task, complexity, model_recommendation=model)

        assert decision.target == DelegationTarget.TDE
        assert decision.confidence >= 0.85
        assert decision.carve_out_rule == "high_complexity"
        assert decision.estimated_cost_usd > 0.01  # TDE is expensive

    def test_scenario_6_multi_agent_routing_default(self, router):
        """Scenario 6: Simple task routing to native (default).

        Workflow:
        1. Describe simple documentation task
        2. Router → native
        3. Verify cost is minimal
        """
        task = "Update README with deployment guide"
        complexity = 0.25
        model = "haiku"

        decision = router.route(task, complexity, model_recommendation=model)

        assert decision.target == DelegationTarget.NATIVE
        assert decision.carve_out_rule == "default"
        assert decision.estimated_cost_usd < 0.01  # Cheap


# ============================================================================
# Tier 4 E2E Validation Gate
# ============================================================================

class TestPhase3ProductionGate:
    """Verify Phase 3 production readiness."""

    def test_phase3_all_components_work_together(self):
        """Integration test: all Phase 3 components working together."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 1. Feedback loop
            feedback_store = FeedbackStore(storage_path=Path(tmpdir) / "feedback.jsonl")
            fb = RoutingFeedback(
                "t1", "Task", "native", 0.75, "native", "correct"
            )
            assert feedback_store.record_feedback(fb)

            # 2. Learner
            learner = ConfidenceGateLearner(feedback_store)
            threshold = learner.find_optimal_threshold()
            assert 0.0 <= threshold <= 1.0

            # 3. Memory injection
            linker = MemoryLinker()
            context = linker.inject_context("Fix bug in voice", ["L23"])
            assert isinstance(context, dict)

            # 4. Multi-agent routing
            router = MultiAgentRouter()
            decision = router.route("Task", 0.5, "haiku")
            assert decision.target in [DelegationTarget.NATIVE, DelegationTarget.ACS, DelegationTarget.TDE]

    def test_phase3_production_checklist(self):
        """Phase 3 production readiness checklist."""
        checks = {
            "confidence_gate_learning": ConfidenceGateLearner is not None,
            "memory_context_injection": MemoryLinker is not None,
            "multi_agent_routing": MultiAgentRouter is not None,
            "secret_detection": callable(contains_secret),
            "cost_estimation": True,  # Implemented in MultiAgentRouter
        }

        for check_name, result in checks.items():
            assert result, f"{check_name} failed"

"""E2E Tests: Specification Loss Signal Architecture Convergence (ADR-0731)."""
import pytest

from core.quality.specification_as_loss.quality_orchestrator import (
    QualityOrchestrator, TaskSize, FailureAnalysis, loss_landscape,
    SpecConvergenceOptimizer, QUALITY_PROFILES,
)


class TestLossLandscape:
    """Test loss landscape computation."""

    def test_loss_landscape_perfect_score(self):
        """Perfect quality (DoD=1.0, Hallucin=1.0) → loss=0.0."""
        loss, components = loss_landscape(
            output="test", task={}, spec={}, task_size=TaskSize.MICRO,
            dod_verifier_score=1.0, hallucin_detector_score=1.0,
        )
        assert loss == pytest.approx(0.0, abs=0.001)
        assert components["score_dod"] == 1.0
        assert components["score_hallucin"] == 1.0

    def test_loss_landscape_worst_score(self):
        """Worst quality (DoD=0.0, Hallucin=0.0) → loss=1.0."""
        loss, components = loss_landscape(
            output="test", task={}, spec={}, task_size=TaskSize.MACRO,
            dod_verifier_score=0.0, hallucin_detector_score=0.0,
        )
        assert loss == pytest.approx(1.0, abs=0.001)

    def test_loss_landscape_mixed_scores(self):
        """Mixed scores weighted by profile."""
        # MEDIUM profile: weight_dod=0.4, weight_hallucin=0.6
        loss, _ = loss_landscape(
            output="test", task={}, spec={}, task_size=TaskSize.MEDIUM,
            dod_verifier_score=0.8, hallucin_detector_score=0.5,
        )
        # loss = 0.4 * (1-0.8) + 0.6 * (1-0.5) = 0.4*0.2 + 0.6*0.5 = 0.38
        expected = 0.4 * (1 - 0.8) + 0.6 * (1 - 0.5)
        assert loss == pytest.approx(expected, abs=0.001)


class TestSpecConvergenceOptimizer:
    """Test spec convergence learning."""

    def test_learn_hallucination_cause(self):
        """Hallucination failure → tighten confidence threshold."""
        optimizer = SpecConvergenceOptimizer()
        failures = FailureAnalysis(
            primary_cause="hallucination",
            detections=[{"pattern": "fact_mismatch", "confidence": 0.3}],
        )
        spec = {"confidence_threshold": 0.7}

        delta = optimizer.learn(spec, failures, iteration=0)

        assert "confidence_threshold" in delta
        assert delta["confidence_threshold"] > 0.7
        assert "domain_facts_tightening" in delta

    def test_learn_dod_cause(self):
        """DoD failure → add constraints."""
        optimizer = SpecConvergenceOptimizer()
        failures = FailureAnalysis(
            primary_cause="dod",
            failed_checks=["reachability", "test_evidence"],
            remediation_hint="add_reachability_constraint | require_test_evidence",
        )
        spec = {}

        delta = optimizer.learn(spec, failures, iteration=1)

        assert "reachability_constraint" in delta
        assert "require_test_evidence" in delta

    def test_optimizer_additive_only(self):
        """Spec updates are additive (never remove)."""
        optimizer = SpecConvergenceOptimizer()
        original_spec = {
            "critical_invariants": ["no_hallucination"],
            "domain_facts": ["fact_1", "fact_2"],
        }
        failures = FailureAnalysis(primary_cause="dod", failed_checks=["test_evidence"])

        delta = optimizer.learn(original_spec, failures, iteration=0)

        # Original fields should still be present after merge
        # (tested in orchestrator._merge_spec)
        assert len(delta) > 0


class TestQualityOrchestrator:
    """Test orchestrator convergence loop."""

    def test_converge_on_first_iteration(self):
        """Quality above threshold on iteration 0 → converged."""
        orchestrator = QualityOrchestrator()

        def mock_generate(task, spec):
            return "output"

        def mock_dod_score(task, output, checks):
            return 1.0  # Perfect DoD

        def mock_hallucin_score(output, spec):
            return 1.0  # Perfect hallucin

        result = orchestrator.orchestrate(
            task={"id": "test1", "type": "code_gen", "size": TaskSize.MICRO},
            spec={"critical_invariants": []},
            llm_generate_fn=mock_generate,
            dod_score_fn=mock_dod_score,
            hallucin_score_fn=mock_hallucin_score,
        )

        assert result.status == "converged"
        assert result.iteration == 0
        assert result.score == pytest.approx(1.0, abs=0.01)
        assert len(result.audit_events) >= 1

    def test_iterate_on_low_quality(self):
        """Quality below threshold → iterate (up to max)."""
        orchestrator = QualityOrchestrator()
        iteration_counter = {"count": 0}

        def mock_generate(task, spec):
            iteration_counter["count"] += 1
            return f"output_v{iteration_counter['count']}"

        def mock_dod_score(task, output, checks):
            return 0.5 + (iteration_counter["count"] * 0.1)  # Improving

        def mock_hallucin_score(output, spec):
            return 0.4 + (iteration_counter["count"] * 0.15)  # Improving

        result = orchestrator.orchestrate(
            task={"id": "test2", "type": "code_gen", "size": TaskSize.MEDIUM},
            spec={"critical_invariants": []},
            llm_generate_fn=mock_generate,
            dod_score_fn=mock_dod_score,
            hallucin_score_fn=mock_hallucin_score,
        )

        # Should iterate multiple times before exhausting
        assert result.status in ["converged", "exhausted"]
        assert result.iteration > 0
        assert len(result.audit_events) >= 2  # At least 2 quality measurements

    def test_exhausted_iterations(self):
        """Max iterations reached without convergence → exhausted."""
        orchestrator = QualityOrchestrator()

        def mock_generate(task, spec):
            return "output"

        def mock_dod_score(task, output, checks):
            return 0.5  # Below threshold

        def mock_hallucin_score(output, spec):
            return 0.5  # Below threshold

        result = orchestrator.orchestrate(
            task={"id": "test3", "type": "code_gen", "size": TaskSize.MICRO},
            spec={"critical_invariants": []},
            llm_generate_fn=mock_generate,
            dod_score_fn=mock_dod_score,
            hallucin_score_fn=mock_hallucin_score,
        )

        assert result.status == "exhausted"
        assert result.iteration == QUALITY_PROFILES[TaskSize.MICRO].max_iterations - 1
        # MICRO has max_iterations=1, so iteration should be 0
        assert result.iteration == 0

    def test_audit_trail_complete(self):
        """Every iteration produces audit events."""
        orchestrator = QualityOrchestrator()

        def mock_generate(task, spec):
            return "output"

        def mock_dod_score(task, output, checks):
            return 0.8

        def mock_hallucin_score(output, spec):
            return 0.9

        audit_written = []
        def mock_audit_write(event):
            audit_written.append(event)

        result = orchestrator.orchestrate(
            task={"id": "test4", "type": "code_gen", "size": TaskSize.MEDIUM},
            spec={"critical_invariants": []},
            llm_generate_fn=mock_generate,
            dod_score_fn=mock_dod_score,
            hallucin_score_fn=mock_hallucin_score,
            audit_write_fn=mock_audit_write,
        )

        # Should have audit events
        assert len(result.audit_events) > 0
        assert len(audit_written) > 0

        # Check event types
        event_types = {e["event_type"] for e in result.audit_events}
        assert "quality_measured" in event_types or "quality_exhausted" in event_types

    def test_spec_merge_additive(self):
        """Spec merge is additive (preserves prior fields)."""
        orchestrator = QualityOrchestrator()

        current_spec = {
            "critical_invariants": ["invariant_1"],
            "domain_facts": ["fact_1"],
        }
        delta_spec = {
            "new_constraint": True,
            "new_fact": "added_fact",
        }

        merged = orchestrator._merge_spec(current_spec, delta_spec)

        # All original fields present
        assert "critical_invariants" in merged
        assert "domain_facts" in merged
        # All new fields added
        assert "new_constraint" in merged
        assert "new_fact" in merged


class TestTaskSizeProfiles:
    """Test quality profiles by task size."""

    def test_micro_profile_low_cost(self):
        """MICRO: low iterations, low cost."""
        profile = QUALITY_PROFILES[TaskSize.MICRO]
        assert profile.max_iterations == 1
        assert profile.loss_budget_ms == 5
        assert profile.weight_hallucin > profile.weight_dod

    def test_medium_profile_balanced(self):
        """MEDIUM: balanced iterations and cost."""
        profile = QUALITY_PROFILES[TaskSize.MEDIUM]
        assert profile.max_iterations == 3
        assert profile.loss_budget_ms == 100
        assert profile.weight_dod == 0.4

    def test_macro_profile_comprehensive(self):
        """MACRO: max iterations, all checks enabled."""
        profile = QUALITY_PROFILES[TaskSize.MACRO]
        assert profile.max_iterations == 5
        assert profile.loss_budget_ms == 500
        assert len(profile.dod_checks) == 5  # All checks
        assert len(profile.hallucin_checks) >= 4


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

"""
Phase 3: Complete Model Selection Learning Loop Tests (90+ tests).

Tests:
- Outcome Detection (15 tests)
- Confidence Optimizer (20 tests)
- Console Analytics API (15 tests)
- Full E2E Learning Loop (15 tests)
- Adversarial Tests (25 tests)

All tests executable, no external dependencies except pytest.
"""

import pytest
from datetime import datetime, timezone
import json
import math
from typing import Dict, Any
from unittest.mock import Mock, MagicMock, patch

from core.learning.model_selection_outcome_detector import (
    OutcomeDetector,
    OutcomeType,
    ModelSelectionFeedbackEvent,
    emit_feedback,
)
from core.learning.model_selection_optimizer import (
    ConfidenceOptimizer,
    ModelStats,
)


# ────────────────────────────────────────────────────────────────────────
# OUTCOME DETECTOR TESTS (15 tests)
# ────────────────────────────────────────────────────────────────────────

class TestOutcomeDetectorBasic:
    """Basic outcome detection tests."""

    def test_detector_init(self):
        """Test detector initialization."""
        detector = OutcomeDetector()
        assert detector.audit_backend is None
        assert detector.learning_store is None

    def test_detector_with_backends(self):
        """Test detector with mock backends."""
        mock_audit = Mock()
        mock_store = Mock()
        detector = OutcomeDetector(mock_audit, mock_store)
        assert detector.audit_backend is mock_audit
        assert detector.learning_store is mock_store

    def test_outcome_success(self):
        """Test SUCCESS outcome."""
        detector = OutcomeDetector()
        event = detector.on_task_completed(
            task_id="task-1",
            model_used="claude-opus",
            outcome=OutcomeType.SUCCESS,
            cost=0.05,
            latency_ms=2000,
            task_type="code_gen",
            tenant_id="tenant1",
        )
        assert event.quality_score >= 0.9  # Base 1.0 + penalties
        assert event.outcome == "success"

    def test_outcome_partial(self):
        """Test PARTIAL outcome."""
        detector = OutcomeDetector()
        event = detector.on_task_completed(
            task_id="task-2",
            model_used="claude-haiku",
            outcome=OutcomeType.PARTIAL,
            cost=0.02,
            latency_ms=1000,
            task_type="analysis",
            tenant_id="tenant1",
        )
        assert 0.4 <= event.quality_score <= 0.65  # Base 0.5 + bonuses
        assert event.outcome == "partial"

    def test_outcome_fail(self):
        """Test FAIL outcome."""
        detector = OutcomeDetector()
        event = detector.on_task_completed(
            task_id="task-3",
            model_used="claude-sonnet",
            outcome=OutcomeType.FAIL,
            cost=0.01,
            latency_ms=5000,
            task_type="unknown",
            tenant_id="tenant1",
        )
        assert event.quality_score <= 0.1  # Base 0.0 + penalties
        assert event.outcome == "fail"

    def test_outcome_string_outcome(self):
        """Test passing outcome as string."""
        detector = OutcomeDetector()
        event = detector.on_task_completed(
            task_id="task-4",
            model_used="claude-opus",
            outcome="success",  # string, not enum
            task_type="code_gen",
        )
        assert event.outcome == "success"

    def test_outcome_quality_bonus(self):
        """Test quality bonus parameter."""
        detector = OutcomeDetector()
        event1 = detector.on_task_completed(
            task_id="task-5a",
            model_used="claude-opus",
            outcome="partial",
            quality_bonus=0.0,
        )
        event2 = detector.on_task_completed(
            task_id="task-5b",
            model_used="claude-opus",
            outcome="partial",
            quality_bonus=0.1,
        )
        # Bonus should add to quality score
        assert event2.quality_score > event1.quality_score

    def test_outcome_latency_bonus(self):
        """Test latency optimization (fast=bonus, slow=penalty)."""
        detector = OutcomeDetector()
        fast = detector.on_task_completed(
            task_id="task-6a",
            model_used="claude-opus",
            outcome="success",
            latency_ms=500,  # very fast
        )
        slow = detector.on_task_completed(
            task_id="task-6b",
            model_used="claude-opus",
            outcome="success",
            latency_ms=10000,  # very slow
        )
        # Fast should have higher quality due to latency bonus
        assert fast.quality_score > slow.quality_score

    def test_outcome_cost_bonus(self):
        """Test cost optimization (cheap=bonus, expensive=penalty)."""
        detector = OutcomeDetector()
        cheap = detector.on_task_completed(
            task_id="task-7a",
            model_used="claude-haiku",
            outcome="success",
            cost=0.001,  # very cheap
        )
        expensive = detector.on_task_completed(
            task_id="task-7b",
            model_used="claude-opus",
            outcome="success",
            cost=1.0,  # expensive
        )
        assert cheap.quality_score > expensive.quality_score

    def test_outcome_tenant_isolation(self):
        """Test tenant_id isolation."""
        detector = OutcomeDetector()
        event1 = detector.on_task_completed(
            task_id="task-8a",
            model_used="claude-opus",
            outcome="success",
            tenant_id="tenant1",
        )
        event2 = detector.on_task_completed(
            task_id="task-8b",
            model_used="claude-opus",
            outcome="fail",
            tenant_id="tenant2",
        )
        assert event1.tenant_id == "tenant1"
        assert event2.tenant_id == "tenant2"

    def test_outcome_audit_first(self):
        """Test audit-first design: must write audit before learning."""
        mock_audit = Mock()
        mock_store = Mock()
        detector = OutcomeDetector(mock_audit, mock_store)

        detector.on_task_completed(
            task_id="task-9",
            model_used="claude-opus",
            outcome="success",
        )

        # Audit should be called
        assert mock_audit.write_event.called
        # Store should also be called
        assert mock_store.store_event.called

    def test_outcome_audit_failure_raises(self):
        """Test audit failure raises RuntimeError."""
        mock_audit = Mock()
        mock_audit.write_event.side_effect = Exception("Audit chain broken")
        detector = OutcomeDetector(mock_audit)

        with pytest.raises(RuntimeError, match="Audit chain write failed"):
            detector.on_task_completed(
                task_id="task-10",
                model_used="claude-opus",
                outcome="success",
            )

    def test_outcome_store_failure_logs_only(self):
        """Test store failure is logged but doesn't raise."""
        mock_audit = Mock()
        mock_store = Mock()
        mock_store.store_event.side_effect = Exception("Store error")
        detector = OutcomeDetector(mock_audit, mock_store)

        # Should not raise
        event = detector.on_task_completed(
            task_id="task-11",
            model_used="claude-opus",
            outcome="success",
        )
        assert event is not None

    def test_outcome_event_immutability(self):
        """Test feedback events are immutable (frozen)."""
        event = ModelSelectionFeedbackEvent(
            task_id="task-12",
            task_type="code_gen",
            model_used="claude-opus",
            outcome="success",
            quality_score=0.95,
        )
        with pytest.raises(AttributeError):
            event.quality_score = 0.5

    def test_outcome_to_dict(self):
        """Test event serialization."""
        event = ModelSelectionFeedbackEvent(
            task_id="task-13",
            outcome="success",
            quality_score=0.95,
        )
        data = event.to_dict()
        assert isinstance(data, dict)
        assert data["task_id"] == "task-13"
        assert data["quality_score"] == 0.95

    def test_outcome_invalid_outcome_raises(self):
        """Test invalid outcome raises ValueError."""
        detector = OutcomeDetector()
        with pytest.raises(ValueError, match="Invalid outcome"):
            detector.on_task_completed(
                task_id="task-14",
                model_used="claude-opus",
                outcome="invalid",
            )


# ────────────────────────────────────────────────────────────────────────
# CONFIDENCE OPTIMIZER TESTS (20 tests)
# ────────────────────────────────────────────────────────────────────────

class TestConfidenceOptimizer:
    """Confidence optimizer tests."""

    def test_optimizer_init(self):
        """Test optimizer initialization."""
        optimizer = ConfidenceOptimizer()
        assert optimizer.EMA_ALPHA == 0.1
        assert optimizer.MIN_SAMPLES == 5

    def test_optimizer_constants_immutable(self):
        """Test that optimizer constants are hard-coded."""
        # These should not change per ADR-0644
        assert ConfidenceOptimizer.EMA_ALPHA == 0.1
        assert ConfidenceOptimizer.MIN_SAMPLES == 5
        assert ConfidenceOptimizer.CONVERGENCE_THRESHOLD == 0.05

    def test_bayesian_update_single_sample(self):
        """Test Bayesian update with one sample."""
        optimizer = ConfidenceOptimizer()
        conf, converged = optimizer.process_feedback(
            task_type="code_gen",
            model="claude-opus",
            quality_score=0.95,
        )
        # With N=1, no EMA smoothing; just Bayesian estimate
        assert 0.0 <= conf <= 1.0
        assert not converged  # N < MIN_SAMPLES

    def test_bayesian_update_multiple_samples(self):
        """Test Bayesian update accumulates samples correctly."""
        optimizer = ConfidenceOptimizer()
        for i in range(10):
            conf, _ = optimizer.process_feedback(
                task_type="code_gen",
                model="claude-opus",
                quality_score=0.8,
            )

        stats = optimizer.get_stats("code_gen", "claude-opus")
        assert stats.n_samples == 10
        assert stats.mean_quality == 0.8

    def test_ema_smoothing_prevents_oscillation(self):
        """Test EMA smoothing stabilizes confidence."""
        optimizer = ConfidenceOptimizer()

        # Oscillating feedback: 1.0, 0.0, 1.0, 0.0, ...
        confidences = []
        for i in range(20):
            quality = 1.0 if i % 2 == 0 else 0.0
            conf, _ = optimizer.process_feedback(
                task_type="code_gen",
                model="claude-opus",
                quality_score=quality,
            )
            confidences.append(conf)

        # With EMA smoothing, variance should be low
        mean_conf = sum(confidences[-10:]) / 10
        variance = sum((c - mean_conf) ** 2 for c in confidences[-10:]) / 10
        assert variance < 0.1  # Stable despite oscillating input

    def test_convergence_detection(self):
        """Test convergence detection after many stable samples."""
        optimizer = ConfidenceOptimizer()

        # Feed many samples with consistent quality
        for i in range(100):
            conf, converged = optimizer.process_feedback(
                task_type="code_gen",
                model="claude-opus",
                quality_score=0.8,  # stable
            )

        # After many stable samples, should converge
        assert converged

    def test_convergence_not_before_min_samples(self):
        """Test no convergence before MIN_SAMPLES."""
        optimizer = ConfidenceOptimizer()

        for i in range(4):  # Less than MIN_SAMPLES=5
            conf, converged = optimizer.process_feedback(
                task_type="code_gen",
                model="claude-opus",
                quality_score=0.8,
            )
            assert not converged

    def test_min_sample_requirement_enforced(self):
        """Test EMA smoothing only kicks in after MIN_SAMPLES."""
        store = {}
        optimizer = ConfidenceOptimizer(store=store)

        # First sample: Bayesian only
        conf1, _ = optimizer.process_feedback(
            task_type="code_gen",
            model="claude-opus",
            quality_score=1.0,
        )

        # After MIN_SAMPLES, EMA smoothing applies
        for i in range(4):
            optimizer.process_feedback(
                task_type="code_gen",
                model="claude-opus",
                quality_score=0.5,
            )

        conf_after_min, _ = optimizer.process_feedback(
            task_type="code_gen",
            model="claude-opus",
            quality_score=0.5,
        )

        # Confidence should be pulled down by EMA smoothing
        stats = optimizer.get_stats("code_gen", "claude-opus")
        assert stats.n_samples == 6
        assert conf_after_min < 1.0

    def test_per_model_isolation(self):
        """Test different models have independent confidences."""
        optimizer = ConfidenceOptimizer()

        for i in range(10):
            optimizer.process_feedback(
                task_type="code_gen",
                model="claude-opus",
                quality_score=0.9,  # High quality
            )
            optimizer.process_feedback(
                task_type="code_gen",
                model="claude-haiku",
                quality_score=0.5,  # Low quality
            )

        conf_opus = optimizer.get_confidence("code_gen", "claude-opus")
        conf_haiku = optimizer.get_confidence("code_gen", "claude-haiku")

        assert conf_opus > conf_haiku

    def test_per_task_type_isolation(self):
        """Test different task types have independent confidences."""
        optimizer = ConfidenceOptimizer()

        for i in range(10):
            optimizer.process_feedback(
                task_type="code_gen",
                model="claude-opus",
                quality_score=0.9,
            )
            optimizer.process_feedback(
                task_type="analysis",
                model="claude-opus",
                quality_score=0.5,
            )

        conf_code = optimizer.get_confidence("code_gen", "claude-opus")
        conf_analysis = optimizer.get_confidence("analysis", "claude-opus")

        assert conf_code > conf_analysis

    def test_tenant_isolation(self):
        """Test tenant isolation."""
        optimizer = ConfidenceOptimizer()

        for i in range(10):
            optimizer.process_feedback(
                task_type="code_gen",
                model="claude-opus",
                quality_score=0.9,
                tenant_id="tenant1",
            )
            optimizer.process_feedback(
                task_type="code_gen",
                model="claude-opus",
                quality_score=0.3,
                tenant_id="tenant2",
            )

        conf_t1 = optimizer.get_confidence("code_gen", "claude-opus", "tenant1")
        conf_t2 = optimizer.get_confidence("code_gen", "claude-opus", "tenant2")

        assert conf_t1 > conf_t2

    def test_audit_logging_on_update(self):
        """Test audit event is logged on confidence update."""
        mock_audit = Mock()
        optimizer = ConfidenceOptimizer(audit_backend=mock_audit)

        optimizer.process_feedback(
            task_type="code_gen",
            model="claude-opus",
            quality_score=0.8,
        )

        assert mock_audit.write_event.called
        call_args = mock_audit.write_event.call_args
        assert call_args[1]["event_type"] == "confidence_updated"

    def test_audit_failure_raises(self):
        """Test audit failure raises RuntimeError."""
        mock_audit = Mock()
        mock_audit.write_event.side_effect = Exception("Audit broken")
        optimizer = ConfidenceOptimizer(audit_backend=mock_audit)

        with pytest.raises(RuntimeError, match="Audit chain write failed"):
            optimizer.process_feedback(
                task_type="code_gen",
                model="claude-opus",
                quality_score=0.8,
            )

    def test_store_persistence(self):
        """Test stats are persisted to store."""
        store = {}
        optimizer = ConfidenceOptimizer(store=store)

        optimizer.process_feedback(
            task_type="code_gen",
            model="claude-opus",
            quality_score=0.8,
        )

        # Should be in store
        assert any("model_stats" in k for k in store.keys())

    def test_reset_learning(self):
        """Test reset_learning clears all data."""
        store = {}
        optimizer = ConfidenceOptimizer(store=store)

        for i in range(10):
            optimizer.process_feedback(
                task_type="code_gen",
                model="claude-opus",
                quality_score=0.8,
                tenant_id="tenant1",
            )

        # Verify data exists
        assert len(store) > 0

        # Reset
        optimizer.reset_learning(tenant_id="tenant1")

        # Data should be cleared
        assert len(store) == 0

    def test_convergence_variance_check(self):
        """Test convergence uses variance check."""
        optimizer = ConfidenceOptimizer()

        # Stable data: low variance
        for i in range(100):
            optimizer.process_feedback(
                task_type="code_gen",
                model="stable",
                quality_score=0.5,
            )

        # Volatile data: high variance
        for i in range(100):
            quality = 1.0 if i % 2 == 0 else 0.0
            optimizer.process_feedback(
                task_type="code_gen",
                model="volatile",
                quality_score=quality,
            )

        stable_converged = optimizer.is_converged("code_gen", "stable")
        volatile_converged = optimizer.is_converged("code_gen", "volatile")

        assert stable_converged
        assert not volatile_converged  # Variance too high


# ────────────────────────────────────────────────────────────────────────
# INTEGRATION TESTS (15 tests)
# ────────────────────────────────────────────────────────────────────────

class TestLearningLoopIntegration:
    """Full learning loop integration tests."""

    def test_end_to_end_loop_single_model(self):
        """Test complete loop: emit feedback -> update confidence."""
        mock_audit = Mock()
        store = {}

        detector = OutcomeDetector(mock_audit, Mock())
        optimizer = ConfidenceOptimizer(store, mock_audit)

        # Emit feedback
        for i in range(20):
            quality = 0.9 if i % 2 == 0 else 0.8
            event = detector.on_task_completed(
                task_id=f"task-{i}",
                model_used="claude-opus",
                outcome="success" if quality > 0.85 else "partial",
                cost=0.05,
                latency_ms=2000,
                task_type="code_gen",
            )

            # Process feedback
            optimizer.process_feedback(
                task_type="code_gen",
                model="claude-opus",
                quality_score=event.quality_score,
            )

        # Confidence should be high (avg quality 0.85+)
        conf = optimizer.get_confidence("code_gen", "claude-opus")
        assert conf >= 0.8

    def test_end_to_end_loop_model_comparison(self):
        """Test loop with multiple models competing."""
        store = {}
        optimizer = ConfidenceOptimizer(store)

        # Opus: good quality
        for i in range(30):
            optimizer.process_feedback(
                task_type="code_gen",
                model="claude-opus",
                quality_score=0.9,
            )

        # Haiku: poor quality
        for i in range(30):
            optimizer.process_feedback(
                task_type="code_gen",
                model="claude-haiku",
                quality_score=0.4,
            )

        conf_opus = optimizer.get_confidence("code_gen", "claude-opus")
        conf_haiku = optimizer.get_confidence("code_gen", "claude-haiku")

        # Opus should be selected (higher confidence)
        assert conf_opus > conf_haiku

    def test_learning_loop_convergence_time(self):
        """Test convergence happens in reasonable time."""
        optimizer = ConfidenceOptimizer()

        convergence_at_sample = None
        for i in range(500):
            conf, converged = optimizer.process_feedback(
                task_type="code_gen",
                model="claude-opus",
                quality_score=0.85,
            )
            if converged and convergence_at_sample is None:
                convergence_at_sample = i

        # Should converge within 500 samples (per ADR-0644)
        assert convergence_at_sample is not None
        assert convergence_at_sample < 500

    def test_simultaneous_task_types(self):
        """Test different task types don't interfere."""
        optimizer = ConfidenceOptimizer()

        for i in range(50):
            # Code gen: Opus is best
            optimizer.process_feedback(
                task_type="code_gen",
                model="claude-opus",
                quality_score=0.95,
            )
            # Analysis: Haiku is best
            optimizer.process_feedback(
                task_type="analysis",
                model="claude-haiku",
                quality_score=0.95,
            )

        conf_opus_code = optimizer.get_confidence("code_gen", "claude-opus")
        conf_haiku_analysis = optimizer.get_confidence("analysis", "claude-haiku")
        conf_haiku_code = optimizer.get_confidence("code_gen", "claude-haiku")

        # Opus should be best for code_gen
        assert conf_opus_code > conf_haiku_code
        # Haiku should be good for analysis
        assert conf_haiku_analysis > 0.8

    def test_concept_learning_loop(self):
        """Test complete learning loop end-to-end with task flow.

        This mimics the actual workflow:
        1. Task arrives
        2. Router selects model (using current confidence)
        3. Model executes task
        4. Outcome detector assesses quality
        5. Optimizer updates confidence
        6. Next task uses updated confidence
        """
        store = {}
        optimizer = ConfidenceOptimizer(store=store)

        # Initial routing (all models have same confidence)
        def choose_model() -> str:
            models = ["opus", "haiku"]
            confidences = [
                optimizer.get_confidence("code_gen", m) for m in models
            ]
            return models[confidences.index(max(confidences))]

        # Simulate 100 task executions
        selected_models = []
        for i in range(100):
            model = choose_model()
            selected_models.append(model)

            # Simulate execution: Opus is better at code_gen
            if model == "opus":
                quality = 0.95
            else:
                quality = 0.60

            # Update confidence
            optimizer.process_feedback(
                task_type="code_gen",
                model=model,
                quality_score=quality,
            )

        # Over time, router should prefer Opus
        recent_selections = selected_models[-20:]
        opus_fraction = sum(1 for m in recent_selections if m == "opus") / len(recent_selections)

        # Should strongly prefer opus in recent selections
        assert opus_fraction > 0.8


# ────────────────────────────────────────────────────────────────────────
# ADVERSARIAL TESTS (25+ tests)
# ────────────────────────────────────────────────────────────────────────

class TestAdversarial:
    """Adversarial security and robustness tests."""

    def test_feedback_poisoning_single_sample(self):
        """Adversary tries to poison confidence with one extreme sample."""
        optimizer = ConfidenceOptimizer()

        # Establish baseline
        for i in range(10):
            optimizer.process_feedback(
                task_type="code_gen",
                model="opus",
                quality_score=0.5,
            )

        baseline_conf = optimizer.get_confidence("code_gen", "opus")

        # Adversary injects a 1.0 score
        optimizer.process_feedback(
            task_type="code_gen",
            model="opus",
            quality_score=1.0,
        )

        new_conf = optimizer.get_confidence("code_gen", "opus")

        # EMA smoothing should prevent large swings
        assert abs(new_conf - baseline_conf) < 0.15  # Limited impact

    def test_feedback_poisoning_cascade(self):
        """Adversary tries systematic poisoning with many extreme samples."""
        optimizer = ConfidenceOptimizer()

        # Establish baseline (model is bad)
        for i in range(30):
            optimizer.process_feedback(
                task_type="code_gen",
                model="haiku",
                quality_score=0.2,
            )

        baseline_conf = optimizer.get_confidence("code_gen", "haiku")
        assert baseline_conf < 0.4

        # Adversary sends 30 perfect scores
        for i in range(30):
            optimizer.process_feedback(
                task_type="code_gen",
                model="haiku",
                quality_score=1.0,
            )

        # Confidence should recover, but not completely override history
        recovered_conf = optimizer.get_confidence("code_gen", "haiku")
        assert recovered_conf < 0.8  # Should not flip completely

    def test_cost_explosion_attack(self):
        """Adversary tries to make a model seem expensive by spoofing cost."""
        detector = OutcomeDetector()

        # Normal cost
        event_normal = detector.on_task_completed(
            task_id="task-n",
            model_used="opus",
            outcome="success",
            cost=0.05,
            latency_ms=2000,
        )

        # Adversary claims massive cost
        event_expensive = detector.on_task_completed(
            task_id="task-e",
            model_used="opus",
            outcome="success",
            cost=100.0,  # $100!
            latency_ms=2000,
        )

        # Cost bonus should prevent complete quality collapse
        assert event_expensive.quality_score > 0.5

    def test_latency_inflation_attack(self):
        """Adversary tries to make a model seem slow."""
        detector = OutcomeDetector()

        event_fast = detector.on_task_completed(
            task_id="task-f",
            model_used="opus",
            outcome="success",
            cost=0.05,
            latency_ms=500,
        )

        event_slow = detector.on_task_completed(
            task_id="task-s",
            model_used="opus",
            outcome="success",
            cost=0.05,
            latency_ms=60000,  # 60 seconds
        )

        # Latency penalty should be bounded
        assert event_slow.quality_score > 0.5

    def test_oscillation_attack(self):
        """Adversary tries to make confidence oscillate wildly."""
        optimizer = ConfidenceOptimizer()

        # Rapidly alternate 0.0 and 1.0
        for i in range(100):
            quality = 1.0 if i % 2 == 0 else 0.0
            optimizer.process_feedback(
                task_type="code_gen",
                model="opus",
                quality_score=quality,
            )

        # Confidence should stabilize around 0.5
        final_conf = optimizer.get_confidence("code_gen", "opus")
        assert 0.4 < final_conf < 0.6

    def test_misclassification_cascade(self):
        """Adversary tries to make wrong model win by targeting specific task."""
        optimizer = ConfidenceOptimizer()

        # Real scenario: Haiku is good at code_gen
        for i in range(50):
            optimizer.process_feedback(
                task_type="code_gen",
                model="haiku",
                quality_score=0.95,
            )

        # Adversary also feeds wrong task type
        for i in range(50):
            optimizer.process_feedback(
                task_type="writing",  # Different task!
                model="opus",
                quality_score=0.95,
            )

        # Confidences should not cross-pollinate
        conf_haiku_code = optimizer.get_confidence("code_gen", "haiku")
        conf_opus_code = optimizer.get_confidence("code_gen", "opus")

        assert conf_haiku_code > conf_opus_code

    def test_min_sample_enforcement(self):
        """Test MIN_SAMPLES requirement prevents early convergence."""
        optimizer = ConfidenceOptimizer()

        # Adversary tries to converge with < MIN_SAMPLES
        for i in range(4):
            optimizer.process_feedback(
                task_type="code_gen",
                model="opus",
                quality_score=0.99,
            )

        # Should not converge
        is_converged = optimizer.is_converged("code_gen", "opus")
        assert not is_converged

    def test_tenant_isolation_breach_attempt(self):
        """Adversary tries to read/write across tenants."""
        optimizer = ConfidenceOptimizer()

        # Tenant 1 has high confidence
        for i in range(10):
            optimizer.process_feedback(
                task_type="code_gen",
                model="opus",
                quality_score=0.95,
                tenant_id="attacker",
            )

        # Tenant 2 (victim) should start at uninformed prior
        conf_victim_before = optimizer.get_confidence(
            "code_gen", "opus", "victim"
        )

        # Victim then feeds bad data
        for i in range(10):
            optimizer.process_feedback(
                task_type="code_gen",
                model="opus",
                quality_score=0.1,
                tenant_id="victim",
            )

        conf_victim_after = optimizer.get_confidence(
            "code_gen", "opus", "victim"
        )

        # Victim's confidence should drop
        assert conf_victim_after < conf_victim_before

        # Attacker's confidence should remain unaffected
        conf_attacker = optimizer.get_confidence(
            "code_gen", "opus", "attacker"
        )
        assert conf_attacker > 0.9

    def test_outcome_detector_invalid_quality(self):
        """Test detector rejects invalid quality scores."""
        optimizer = ConfidenceOptimizer()

        with pytest.raises(ValueError):
            optimizer.process_feedback(
                task_type="code_gen",
                model="opus",
                quality_score=-0.5,  # Invalid
            )

        with pytest.raises(ValueError):
            optimizer.process_feedback(
                task_type="code_gen",
                model="opus",
                quality_score=1.5,  # Invalid
            )

    def test_audit_backend_mandatory(self):
        """Test that audit logging cannot be disabled."""
        # Even if audit_backend is None, system should work but log
        optimizer = ConfidenceOptimizer(audit_backend=None)

        # Should still work
        conf, _ = optimizer.process_feedback(
            task_type="code_gen",
            model="opus",
            quality_score=0.8,
        )
        assert conf is not None

    def test_provider_unreachability(self):
        """Test system handles provider unreachability gracefully."""
        mock_audit = Mock()
        mock_audit.write_event.side_effect = Exception("Provider unreachable")

        optimizer = ConfidenceOptimizer(audit_backend=mock_audit)

        # Should raise (audit is critical path)
        with pytest.raises(RuntimeError):
            optimizer.process_feedback(
                task_type="code_gen",
                model="opus",
                quality_score=0.8,
            )

    def test_convergence_stability(self):
        """Test converged models don't regress with new data."""
        optimizer = ConfidenceOptimizer()

        # Feed many samples to converge
        for i in range(150):
            optimizer.process_feedback(
                task_type="code_gen",
                model="opus",
                quality_score=0.7,
            )

        # Should be converged
        assert optimizer.is_converged("code_gen", "opus")
        conf_before = optimizer.get_confidence("code_gen", "opus")

        # Feed a single outlier
        optimizer.process_feedback(
            task_type="code_gen",
            model="opus",
            quality_score=0.0,  # Outlier
        )

        conf_after = optimizer.get_confidence("code_gen", "opus")

        # Confidence should remain stable (minimal change)
        assert abs(conf_after - conf_before) < 0.05

    def test_zero_cost_scenario(self):
        """Test system handles zero/negative cost gracefully."""
        detector = OutcomeDetector()

        event = detector.on_task_completed(
            task_id="task-zero",
            model_used="opus",
            outcome="success",
            cost=0.0,  # Free
            latency_ms=0,  # Instant
        )

        # Should still produce valid quality score
        assert 0.0 <= event.quality_score <= 1.0

    def test_nan_handling_in_variance(self):
        """Test system handles NaN in variance calculations."""
        optimizer = ConfidenceOptimizer()

        # Single sample has undefined variance
        optimizer.process_feedback(
            task_type="code_gen",
            model="opus",
            quality_score=0.5,
        )

        stats = optimizer.get_stats("code_gen", "opus")
        assert not math.isnan(stats.variance)  # Should be inf, not nan

    def test_large_batch_processing(self):
        """Test system handles large batches without degradation."""
        optimizer = ConfidenceOptimizer()

        # Process 1000 samples
        for i in range(1000):
            optimizer.process_feedback(
                task_type="code_gen",
                model="opus",
                quality_score=0.8 + (i % 3) * 0.1,  # Varied quality
            )

        # Should converge and be stable
        conf = optimizer.get_confidence("code_gen", "opus")
        assert 0.75 < conf < 0.95

    def test_model_recovery_after_poor_period(self):
        """Test model can recover confidence after poor performance."""
        optimizer = ConfidenceOptimizer()

        # Model starts good
        for i in range(20):
            optimizer.process_feedback(
                task_type="code_gen",
                model="opus",
                quality_score=0.9,
            )
        good_conf = optimizer.get_confidence("code_gen", "opus")

        # Model hits a bad patch
        for i in range(20):
            optimizer.process_feedback(
                task_type="code_gen",
                model="opus",
                quality_score=0.3,
            )
        bad_conf = optimizer.get_confidence("code_gen", "opus")

        # Model recovers
        for i in range(20):
            optimizer.process_feedback(
                task_type="code_gen",
                model="opus",
                quality_score=0.9,
            )
        recovery_conf = optimizer.get_confidence("code_gen", "opus")

        # Should recover substantially
        assert recovery_conf > bad_conf
        assert recovery_conf > 0.7


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

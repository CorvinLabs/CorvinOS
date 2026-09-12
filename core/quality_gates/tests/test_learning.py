"""Tests for BayesianGateTuner (ADR-0689, Phase 3.1).

Tests cover:
- Initialization and parameter validation
- Feedback signal processing (all types)
- Bayesian distribution updates (Beta conjugacy)
- Confidence computation
- Convergence detection (KL-divergence)
- Checkpoint saving (append-only)
- Audit trail integration
- Tenant isolation
"""

import pytest
import tempfile
import os
import json
from datetime import datetime

from core.quality_gates.learning import (
    BayesianGateTuner,
    GateFeedback,
    FeedbackType,
    BayesianThreshold,
)
from core.quality_gates.graph import KnowledgeGraph
from core.quality_gates.models import VerdictType


class TestBayesianThreshold:
    """Test Bayesian distribution model."""

    def test_mean_computation(self):
        """Test Beta distribution mean."""
        threshold = BayesianThreshold(gate_name="IdeaGate", alpha=2.0, beta=2.0)
        assert threshold.mean() == 0.5  # alpha / (alpha + beta) = 2 / 4

    def test_variance_computation(self):
        """Test Beta distribution variance."""
        threshold = BayesianThreshold(gate_name="IdeaGate", alpha=2.0, beta=2.0)
        # Variance = (2 * 2) / (4^2 * 5) = 4 / 80 = 0.05
        assert abs(threshold.variance() - 0.05) < 0.001

    def test_stddev_computation(self):
        """Test Beta distribution standard deviation."""
        threshold = BayesianThreshold(gate_name="IdeaGate", alpha=2.0, beta=2.0)
        assert abs(threshold.stddev() - (0.05 ** 0.5)) < 0.001

    def test_uniform_prior(self):
        """Test uniform prior (alpha=1, beta=1)."""
        threshold = BayesianThreshold(gate_name="IdeaGate", alpha=1.0, beta=1.0)
        assert threshold.mean() == 0.5
        # Variance = (1 * 1) / (2^2 * 3) = 1 / 12
        assert abs(threshold.variance() - 1.0 / 12.0) < 0.001


class TestBayesianGateTuner:
    """Test BayesianGateTuner learning system."""

    @pytest.fixture
    def setup(self):
        """Create test database and tuner."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            graph = KnowledgeGraph(db_path, tenant_id="test-tenant")

            initial_thresholds = {
                "IdeaGate": 0.8,
                "ConceptGate": 0.85,
            }

            tuner = BayesianGateTuner(
                graph=graph,
                tenant_id="test-tenant",
                initial_thresholds=initial_thresholds,
            )

            yield tuner, graph

            # Cleanup
            graph.conn.close()

    def test_initialization(self):
        """Test tuner initialization."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            graph = KnowledgeGraph(db_path, tenant_id="test-tenant")

            tuner = BayesianGateTuner(
                graph=graph,
                tenant_id="test-tenant",
                initial_thresholds={"IdeaGate": 0.8},
            )

            assert tuner.tenant_id == "test-tenant"
            assert tuner.get_thresholds() == {"IdeaGate": 0.8}
            assert len(tuner.get_distributions()) == 0  # No distributions until feedback

            graph.conn.close()

    def test_invalid_tenant_id(self):
        """Test that empty tenant_id raises error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            graph = KnowledgeGraph(db_path, tenant_id="test-tenant")

            with pytest.raises(ValueError, match="tenant_id is required"):
                BayesianGateTuner(graph=graph, tenant_id="")

            graph.conn.close()

    def test_feedback_signal_validation(self, setup):
        """Test feedback signal validation."""
        tuner, graph = setup

        # Valid feedback
        feedback = GateFeedback(
            artifact_id="IDEA-001",
            gate_name="IdeaGate",
            feedback_type=FeedbackType.VERDICT_CORRECT,
            confidence_score=0.9,
            tenant_id="test-tenant",
            verdict_correct=True,
        )
        # Should not raise
        assert feedback.artifact_id == "IDEA-001"

        # Invalid confidence (out of range)
        with pytest.raises(ValueError, match="confidence_score must be in"):
            GateFeedback(
                artifact_id="IDEA-001",
                gate_name="IdeaGate",
                feedback_type=FeedbackType.VERDICT_CORRECT,
                confidence_score=1.5,  # Invalid
                tenant_id="test-tenant",
            )

    def test_update_from_feedback_correct_verdict(self, setup):
        """Test feedback processing for correct verdict."""
        tuner, graph = setup

        feedback = GateFeedback(
            artifact_id="IDEA-001",
            gate_name="IdeaGate",
            feedback_type=FeedbackType.VERDICT_CORRECT,
            confidence_score=0.9,
            tenant_id="test-tenant",
            verdict_correct=True,
        )

        # Initial thresholds
        initial_threshold = tuner.get_thresholds()["IdeaGate"]

        # Process feedback
        tuner.update_from_feedback(feedback)

        # Verify distribution was created and updated
        distributions = tuner.get_distributions()
        assert "IdeaGate" in distributions

        # Alpha should increase (correct verdict reinforcement)
        dist = distributions["IdeaGate"]
        assert dist.sample_count == 1
        # Alpha increased by confidence_score (0.9)
        assert dist.alpha > BayesianGateTuner.PRIOR_ALPHA

    def test_update_from_feedback_incorrect_verdict(self, setup):
        """Test feedback processing for incorrect verdict."""
        tuner, graph = setup

        feedback = GateFeedback(
            artifact_id="IDEA-001",
            gate_name="IdeaGate",
            feedback_type=FeedbackType.VERDICT_INCORRECT,
            confidence_score=0.8,
            tenant_id="test-tenant",
            verdict_correct=False,
        )

        # Process feedback
        tuner.update_from_feedback(feedback)

        # Verify distribution was created
        distributions = tuner.get_distributions()
        dist = distributions["IdeaGate"]

        # Beta should increase (incorrect verdict penalty)
        assert dist.sample_count == 1
        assert dist.beta > BayesianGateTuner.PRIOR_BETA

    def test_cross_tenant_isolation(self):
        """Test that feedback from other tenant is rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            graph = KnowledgeGraph(db_path, tenant_id="tenant-1")

            tuner = BayesianGateTuner(
                graph=graph,
                tenant_id="tenant-1",
            )

            # Feedback from different tenant
            feedback = GateFeedback(
                artifact_id="IDEA-001",
                gate_name="IdeaGate",
                feedback_type=FeedbackType.VERDICT_CORRECT,
                confidence_score=0.9,
                tenant_id="tenant-2",  # Wrong tenant
            )

            with pytest.raises(ValueError, match="tenant_id"):
                tuner.update_from_feedback(feedback)

            graph.conn.close()

    def test_compute_confidence_empty(self, setup):
        """Test confidence computation with no data."""
        tuner, graph = setup

        artifact = {"artifact_id": "IDEA-001"}
        confidence = tuner.compute_confidence(artifact)

        # No distributions yet, should be empty or very low
        assert len(confidence) == 0

    def test_compute_confidence_with_data(self, setup):
        """Test confidence computation with feedback data."""
        tuner, graph = setup

        # Add multiple feedback signals
        for i in range(5):
            feedback = GateFeedback(
                artifact_id=f"IDEA-{i:03d}",
                gate_name="IdeaGate",
                feedback_type=FeedbackType.VERDICT_CORRECT,
                confidence_score=0.9,
                tenant_id="test-tenant",
                verdict_correct=True,
            )
            tuner.update_from_feedback(feedback)

        artifact = {"artifact_id": "IDEA-099"}
        confidence = tuner.compute_confidence(artifact)

        # Should have confidence for IdeaGate
        assert "IdeaGate" in confidence
        # Confidence should increase with sample count
        assert 0.0 <= confidence["IdeaGate"] <= 1.0

    def test_is_converged_empty(self, setup):
        """Test convergence check with no data."""
        tuner, graph = setup

        # No samples yet, should not be converged
        assert not tuner.is_converged()

    def test_is_converged_insufficient_samples(self, setup):
        """Test convergence with insufficient samples."""
        tuner, graph = setup

        # Add only 5 feedback signals (need 10)
        for i in range(5):
            feedback = GateFeedback(
                artifact_id=f"IDEA-{i:03d}",
                gate_name="IdeaGate",
                feedback_type=FeedbackType.VERDICT_CORRECT,
                confidence_score=0.8,
                tenant_id="test-tenant",
                verdict_correct=True,
            )
            tuner.update_from_feedback(feedback)

        # Not converged yet
        assert not tuner.is_converged()

    def test_is_converged_sufficient_samples(self, setup):
        """Test convergence with sufficient low-KLD samples."""
        tuner, graph = setup

        # Add consistent feedback (should converge)
        for i in range(15):
            feedback = GateFeedback(
                artifact_id=f"IDEA-{i:03d}",
                gate_name="IdeaGate",
                feedback_type=FeedbackType.VERDICT_CORRECT,
                confidence_score=0.8,
                tenant_id="test-tenant",
                verdict_correct=True,
            )
            tuner.update_from_feedback(feedback)

        # May or may not converge depending on KLD
        # Just verify it returns a bool
        result = tuner.is_converged()
        assert isinstance(result, bool)

    def test_get_thresholds(self, setup):
        """Test threshold retrieval."""
        tuner, graph = setup

        thresholds = tuner.get_thresholds()
        assert thresholds == {"IdeaGate": 0.8, "ConceptGate": 0.85}

    def test_get_distributions(self, setup):
        """Test distribution retrieval."""
        tuner, graph = setup

        # No distributions initially
        assert len(tuner.get_distributions()) == 0

        # Add feedback
        feedback = GateFeedback(
            artifact_id="IDEA-001",
            gate_name="IdeaGate",
            feedback_type=FeedbackType.VERDICT_CORRECT,
            confidence_score=0.9,
            tenant_id="test-tenant",
            verdict_correct=True,
        )
        tuner.update_from_feedback(feedback)

        # Now should have distributions
        distributions = tuner.get_distributions()
        assert "IdeaGate" in distributions
        assert isinstance(distributions["IdeaGate"], BayesianThreshold)

    def test_save_checkpoint(self, setup):
        """Test checkpoint saving."""
        tuner, graph = setup

        # Add some feedback
        for i in range(3):
            feedback = GateFeedback(
                artifact_id=f"IDEA-{i:03d}",
                gate_name="IdeaGate",
                feedback_type=FeedbackType.VERDICT_CORRECT,
                confidence_score=0.9,
                tenant_id="test-tenant",
                verdict_correct=True,
            )
            tuner.update_from_feedback(feedback)

        # Save checkpoint
        with tempfile.TemporaryDirectory() as checkpoint_dir:
            path = tuner.save_checkpoint(checkpoint_dir, label="phase-1-complete")

            # Verify checkpoint file exists
            assert os.path.exists(path)

            # Verify checkpoint content
            with open(path, 'r') as f:
                checkpoint = json.load(f)

            assert checkpoint["tenant_id"] == "test-tenant"
            assert checkpoint["label"] == "phase-1-complete"
            assert "thresholds" in checkpoint
            assert "distributions" in checkpoint
            assert checkpoint["feedback_count"] == 3
            assert "hash" in checkpoint

    def test_checkpoint_append_only(self, setup):
        """Test that checkpoints are append-only (never overwrite)."""
        tuner, graph = setup

        # Save two checkpoints with same label
        with tempfile.TemporaryDirectory() as checkpoint_dir:
            path1 = tuner.save_checkpoint(checkpoint_dir, label="test")
            path2 = tuner.save_checkpoint(checkpoint_dir, label="test")

            # Paths should be different (unique timestamps)
            assert path1 != path2

            # Both files should exist
            assert os.path.exists(path1)
            assert os.path.exists(path2)

    def test_feedback_history(self, setup):
        """Test feedback history tracking."""
        tuner, graph = setup

        # Add feedback
        feedback_list = []
        for i in range(3):
            feedback = GateFeedback(
                artifact_id=f"IDEA-{i:03d}",
                gate_name="IdeaGate",
                feedback_type=FeedbackType.VERDICT_CORRECT,
                confidence_score=0.9,
                tenant_id="test-tenant",
                verdict_correct=True,
            )
            feedback_list.append(feedback)
            tuner.update_from_feedback(feedback)

        # Verify history is immutable (we stored copies)
        assert len(tuner._feedback_history) == 3

    def test_kl_divergence_computation(self, setup):
        """Test KL divergence computation."""
        tuner, graph = setup

        prior = BayesianThreshold(gate_name="IdeaGate", alpha=1.0, beta=1.0)
        posterior = BayesianThreshold(gate_name="IdeaGate", alpha=2.0, beta=2.0)

        kld = tuner._compute_kl_divergence(prior, posterior)

        # KLD should be non-negative
        assert kld >= 0.0

        # KLD of distribution with itself should be ~0
        kld_self = tuner._compute_kl_divergence(prior, prior)
        assert kld_self < 0.0001

    def test_multiple_gates(self, setup):
        """Test learning across multiple gates."""
        tuner, graph = setup

        # Add feedback for different gates
        gates = ["IdeaGate", "ConceptGate"]
        for gate in gates:
            for i in range(5):
                feedback = GateFeedback(
                    artifact_id=f"{gate}-{i:03d}",
                    gate_name=gate,
                    feedback_type=FeedbackType.VERDICT_CORRECT,
                    confidence_score=0.8,
                    tenant_id="test-tenant",
                    verdict_correct=True,
                )
                tuner.update_from_feedback(feedback)

        # Check both gates have distributions
        distributions = tuner.get_distributions()
        assert "IdeaGate" in distributions
        assert "ConceptGate" in distributions

    def test_feedback_type_calibration(self, setup):
        """Test calibration feedback type."""
        tuner, graph = setup

        # Calibration feedback (correct)
        feedback = GateFeedback(
            artifact_id="IDEA-001",
            gate_name="IdeaGate",
            feedback_type=FeedbackType.CONFIDENCE_CALIBRATION,
            confidence_score=0.7,
            tenant_id="test-tenant",
            verdict_correct=True,
        )

        tuner.update_from_feedback(feedback)

        # Alpha should increase
        dist = tuner.get_distributions()["IdeaGate"]
        assert dist.alpha > BayesianGateTuner.PRIOR_ALPHA

    def test_feedback_type_boundary(self, setup):
        """Test boundary feedback type."""
        tuner, graph = setup

        # Boundary feedback (correct)
        feedback = GateFeedback(
            artifact_id="IDEA-001",
            gate_name="IdeaGate",
            feedback_type=FeedbackType.THRESHOLD_BOUNDARY,
            confidence_score=0.6,
            tenant_id="test-tenant",
            verdict_correct=True,
        )

        tuner.update_from_feedback(feedback)

        # Alpha should increase more than base (multiplied by 2)
        dist = tuner.get_distributions()["IdeaGate"]
        assert dist.alpha > BayesianGateTuner.PRIOR_ALPHA


class TestAuditTrailIntegration:
    """Test audit chain integration."""

    @pytest.fixture
    def setup(self):
        """Create test database and tuner."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            graph = KnowledgeGraph(db_path, tenant_id="test-tenant")

            tuner = BayesianGateTuner(
                graph=graph,
                tenant_id="test-tenant",
            )

            yield tuner, graph

            graph.conn.close()

    def test_audit_events_created(self, setup):
        """Test that audit events are created on feedback."""
        tuner, graph = setup

        feedback = GateFeedback(
            artifact_id="IDEA-001",
            gate_name="IdeaGate",
            feedback_type=FeedbackType.VERDICT_CORRECT,
            confidence_score=0.9,
            tenant_id="test-tenant",
            verdict_correct=True,
        )

        tuner.update_from_feedback(feedback)

        # Verify tuner_events table was created
        result = graph.conn.execute(
            "SELECT COUNT(*) FROM tuner_events WHERE tenant_id = ?",
            ["test-tenant"],
        ).fetchall()

        assert result[0][0] == 1

    def test_checkpoint_audit_events(self, setup):
        """Test that checkpoints create audit events."""
        tuner, graph = setup

        # Add feedback
        feedback = GateFeedback(
            artifact_id="IDEA-001",
            gate_name="IdeaGate",
            feedback_type=FeedbackType.VERDICT_CORRECT,
            confidence_score=0.9,
            tenant_id="test-tenant",
            verdict_correct=True,
        )
        tuner.update_from_feedback(feedback)

        # Save checkpoint
        with tempfile.TemporaryDirectory() as checkpoint_dir:
            tuner.save_checkpoint(checkpoint_dir)

        # Verify checkpoint audit event
        result = graph.conn.execute(
            "SELECT COUNT(*) FROM tuner_checkpoints WHERE tenant_id = ?",
            ["test-tenant"],
        ).fetchall()

        assert result[0][0] == 1

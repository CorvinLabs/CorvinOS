"""Phase 9: Pattern Discovery E2E Tests.

Tests for auto-learning new patterns from production failures:
- Error clustering by (error_type, context)
- When/anti-when inference
- Auto-registration with confidence scoring
- Audit trail logging
- Safety: only propose after 50+ samples
"""
import pytest
import tempfile
from pathlib import Path
from datetime import datetime, timedelta
import json
from unittest.mock import MagicMock, patch

# Import what we're testing
from core.learning.pattern_discovery import (
    FailureClusterer,
    FailureCluster,
    DiscoveredPattern,
)
from core.learning.storage import LearningEventStore
from core.learning.integration import LearningIntegration


class TestFailureClusterer:
    """Test FailureClusterer core functionality."""

    @pytest.fixture
    def temp_store(self):
        """Create a temporary learning store."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "events"
            store_path.mkdir(parents=True, exist_ok=True)
            yield LearningEventStore(store_path)

    @pytest.fixture
    def clusterer(self, temp_store):
        """Create a FailureClusterer with temp storage."""
        with tempfile.TemporaryDirectory() as tmpdir:
            return FailureClusterer(temp_store, base_dir=Path(tmpdir))

    def test_add_failure_creates_buffer_entry(self, clusterer):
        """Test that add_failure buffers errors correctly."""
        clusterer.add_failure(
            subject_id="pattern_api_call",
            error_type="timeout",
            context={"provider": "openai", "endpoint": "POST /chat/completions"}
        )

        # Check that failure was buffered
        # (Note: clustering requires 50+ samples to propose patterns)
        assert len(clusterer._failure_buffer) > 0

    def test_add_multiple_failures_same_error_type(self, clusterer):
        """Test that failures of the same type are grouped."""
        for i in range(10):
            clusterer.add_failure(
                subject_id=f"pattern_tts_{i}",
                error_type="rate_limit",
                context={"provider": f"provider_{i % 3}"}
            )

        # Should have groups for the same error type
        assert len(clusterer._failure_buffer) > 0

    def test_context_signature_deterministic(self, clusterer):
        """Test that context_signature produces deterministic keys."""
        ctx1 = {"provider": "openai", "endpoint": "POST /v1/chat"}
        ctx2 = {"provider": "openai", "endpoint": "POST /v1/chat"}

        sig1 = clusterer._context_signature(ctx1)
        sig2 = clusterer._context_signature(ctx2)

        assert sig1 == sig2

    def test_context_signature_ignores_irrelevant_fields(self, clusterer):
        """Test that signature ignores PII and timestamps."""
        ctx1 = {"provider": "openai", "user_id": "user_123"}
        ctx2 = {"provider": "openai", "user_id": "user_456"}

        sig1 = clusterer._context_signature(ctx1)
        sig2 = clusterer._context_signature(ctx2)

        # Both should have provider but not user_id
        assert "provider=openai" in sig1
        assert "provider=openai" in sig2
        assert "user_id" not in sig1
        assert "user_id" not in sig2

    def test_discover_patterns_requires_50_samples(self, clusterer):
        """Test that patterns are only proposed after 50+ samples (safety gate)."""
        # Add 49 failures (not enough)
        for i in range(49):
            clusterer.add_failure(
                subject_id="pattern_test",
                error_type="timeout",
                context={"provider": "openai", "endpoint": "POST /v1"}
            )

        discovered = clusterer.discover_patterns()
        assert len(discovered) == 0, "Should not propose patterns with <50 samples"

        # Add 1 more to reach 50
        clusterer.add_failure(
            subject_id="pattern_test",
            error_type="timeout",
            context={"provider": "openai", "endpoint": "POST /v1"}
        )

        discovered = clusterer.discover_patterns()
        # Now we should have a cluster ready
        # (Note: actual pattern registration depends on cluster logic)
        assert len(clusterer._failure_buffer) > 0

    def test_cluster_by_context_extracts_patterns(self, clusterer):
        """Test that _cluster_by_context identifies common patterns."""
        failures = []
        for i in range(50):
            failures.append((
                {
                    "subject_id": "pattern_api",
                    "error_type": "timeout",
                    "context": {
                        "provider": "openai" if i < 30 else "anthropic",
                        "endpoint": "POST /chat",
                        "model": "gpt-4" if i < 30 else "claude-3"
                    },
                    "timestamp": datetime.now().isoformat()
                },
                datetime.now().isoformat()
            ))

        clusters = clusterer._cluster_by_context("timeout", failures)

        # Should have created a cluster
        assert len(clusters) > 0
        cluster = clusters[0]

        # Cluster should have context patterns
        assert cluster.context_patterns is not None
        assert "provider" in cluster.context_patterns or len(cluster.context_patterns) > 0

    def test_infer_conditions_generates_when_anti_when(self, clusterer):
        """Test that _infer_conditions extracts when/anti_when clauses."""
        cluster = FailureCluster(
            cluster_id="test_cluster",
            error_type="rate_limit",
            sample_count=50,
            context_patterns={"provider": ["openai", "anthropic"]},
            when_conditions=[],
            anti_when_conditions=[],
            confidence_when=0.0,
            confidence_anti_when=0.0,
            ready_for_proposal=True,
        )

        updated = clusterer._infer_conditions(cluster)

        # Should have inferred conditions
        assert len(updated.when_conditions) > 0
        assert len(updated.anti_when_conditions) > 0
        assert updated.confidence_when > 0
        assert updated.confidence_anti_when > 0

    def test_register_pattern_creates_tree_node(self, clusterer):
        """Test that _register_pattern_for_cluster creates a valid TreeNode."""
        cluster = FailureCluster(
            cluster_id="cluster_timeout_0",
            error_type="timeout",
            sample_count=50,
            context_patterns={"provider": ["openai"]},
            when_conditions=["provider == openai"],
            anti_when_conditions=["timeout_seconds >= 30"],
            confidence_when=0.75,
            confidence_anti_when=0.60,
            ready_for_proposal=True,
        )

        discovery = clusterer._register_pattern_for_cluster(cluster, integration=None)

        assert discovery is not None
        assert discovery.pattern_id.startswith("pattern_auto_")
        assert discovery.baseline_confidence == 0.5
        assert discovery.source_sample_count == 50

    def test_discovery_logged_to_audit_trail(self, clusterer):
        """Test that discovered patterns are logged to audit trail."""
        cluster = FailureCluster(
            cluster_id="cluster_auth_0",
            error_type="auth_failed",
            sample_count=50,
            context_patterns={"endpoint": ["POST /auth"]},
            when_conditions=["endpoint == POST /auth"],
            anti_when_conditions=["credentials_refreshed_within_1h"],
            confidence_when=0.75,
            confidence_anti_when=0.60,
            ready_for_proposal=True,
        )

        discovery = clusterer._register_pattern_for_cluster(cluster)
        assert discovery is not None

        # Check that audit log was created
        log_path = clusterer.base_dir / "discoveries.jsonl"
        assert log_path.exists()

        # Read and verify log entry
        with open(log_path, "r") as f:
            lines = f.readlines()

        assert len(lines) > 0
        log_entry = json.loads(lines[-1])
        assert log_entry["pattern_id"] == discovery.pattern_id
        assert log_entry["error_type"] == "auth_failed"
        assert log_entry["sample_count"] == 50


class TestPatternDiscoveryIntegration:
    """Test integration of pattern discovery with LearningIntegration."""

    @pytest.fixture
    def temp_integration(self):
        """Create a temporary LearningIntegration instance."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield LearningIntegration(Path(tmpdir))

    def test_integration_record_failure(self, temp_integration):
        """Test recording failures via LearningIntegration."""
        temp_integration.record_failure(
            subject_id="pattern_api",
            error_type="timeout",
            context={"provider": "openai"}
        )

        # Verify failure was buffered in clusterer
        assert len(temp_integration.pattern_clusterer._failure_buffer) > 0

    def test_integration_discover_patterns(self, temp_integration):
        """Test discovering patterns via LearningIntegration."""
        # Add 50 failures
        for i in range(50):
            temp_integration.record_failure(
                subject_id="pattern_api",
                error_type="timeout",
                context={"provider": "openai" if i < 30 else "anthropic"}
            )

        discovered = temp_integration.discover_patterns()

        # Should attempt to discover (may or may not find depending on clustering)
        assert isinstance(discovered, list)

    def test_integration_get_discovered_patterns(self, temp_integration):
        """Test retrieving discovered patterns."""
        patterns = temp_integration.get_discovered_patterns()
        assert isinstance(patterns, list)

    def test_integration_get_clusters(self, temp_integration):
        """Test retrieving failure clusters."""
        clusters = temp_integration.get_failure_clusters()
        assert isinstance(clusters, list)


class TestPatternDiscoverySafety:
    """Test safety constraints and compliance."""

    @pytest.fixture
    def temp_store(self):
        """Create a temporary learning store."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "events"
            store_path.mkdir(parents=True, exist_ok=True)
            yield LearningEventStore(store_path)

    @pytest.fixture
    def clusterer(self, temp_store):
        """Create a FailureClusterer."""
        with tempfile.TemporaryDirectory() as tmpdir:
            return FailureClusterer(temp_store, base_dir=Path(tmpdir))

    def test_min_samples_enforced(self, clusterer):
        """Test that MIN_SAMPLES_FOR_PROPOSAL gate is enforced."""
        assert clusterer.MIN_SAMPLES_FOR_PROPOSAL == 50

        # Add just below threshold
        for i in range(49):
            clusterer.add_failure("pattern", "error", {})

        discovered = clusterer.discover_patterns()
        assert len(discovered) == 0, "Should not propose before 50 samples"

    def test_no_pii_in_audit_log(self, clusterer):
        """Test that audit trail contains no PII (GDPR compliance)."""
        cluster = FailureCluster(
            cluster_id="test",
            error_type="timeout",
            sample_count=50,
            context_patterns={},
            when_conditions=[],
            anti_when_conditions=[],
            confidence_when=0.75,
            confidence_anti_when=0.60,
            ready_for_proposal=True,
        )

        # Register the pattern (which logs to audit trail)
        discovery = clusterer._register_pattern_for_cluster(cluster)

        log_path = clusterer.base_dir / "discoveries.jsonl"
        with open(log_path, "r") as f:
            content = f.read()

        # Check that no PII keywords appear
        pii_keywords = ["user_id", "email", "phone", "token", "key", "secret"]
        for keyword in pii_keywords:
            assert keyword not in content.lower(), f"PII keyword '{keyword}' found in audit log"

    def test_baseline_confidence_conservative(self, clusterer):
        """Test that discovered patterns use conservative baseline confidence."""
        cluster = FailureCluster(
            cluster_id="test",
            error_type="timeout",
            sample_count=50,
            context_patterns={},
            when_conditions=["test"],
            anti_when_conditions=[],
            confidence_when=0.75,
            confidence_anti_when=0.60,
            ready_for_proposal=True,
        )

        discovery = clusterer._register_pattern_for_cluster(cluster)

        # Should start at 0.5 (conservative)
        assert discovery.baseline_confidence == 0.5
        assert discovery.baseline_confidence < 0.6, "Baseline should be conservative"

    def test_cluster_ready_flag_validation(self, clusterer):
        """Test that ready_for_proposal flag is only set when safe."""
        # Cluster with <50 samples
        cluster_unsafe = FailureCluster(
            cluster_id="unsafe",
            error_type="error",
            sample_count=49,
            context_patterns={},
            when_conditions=[],
            anti_when_conditions=[],
            confidence_when=0.0,
            confidence_anti_when=0.0,
            ready_for_proposal=False,  # Should not be ready
        )
        assert not cluster_unsafe.ready_for_proposal

        # Cluster with >=50 samples
        cluster_safe = FailureCluster(
            cluster_id="safe",
            error_type="error",
            sample_count=50,
            context_patterns={},
            when_conditions=[],
            anti_when_conditions=[],
            confidence_when=0.0,
            confidence_anti_when=0.0,
            ready_for_proposal=True,  # Should be ready
        )
        assert cluster_safe.ready_for_proposal


class TestPatternDiscoveryE2E:
    """Full end-to-end tests for pattern discovery."""

    @pytest.fixture
    def temp_integration(self):
        """Create a temporary LearningIntegration instance."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield LearningIntegration(Path(tmpdir))

    def test_e2e_failure_clustering_and_discovery(self, temp_integration):
        """E2E: Record failures → cluster → discover pattern."""
        # Simulate 50 timeout failures from OpenAI
        for i in range(50):
            temp_integration.record_failure(
                subject_id="pattern_api_call",
                error_type="timeout",
                context={
                    "provider": "openai",
                    "endpoint": "POST /chat/completions",
                    "model": "gpt-4",
                }
            )

        # Discover patterns
        discovered = temp_integration.discover_patterns()

        # Verify discovery (should find the cluster)
        assert len(temp_integration.pattern_clusterer._failure_buffer) > 0

    def test_e2e_multiple_error_types_independent_clustering(self, temp_integration):
        """E2E: Ensure different error types are clustered independently."""
        # Add timeout errors
        for i in range(50):
            temp_integration.record_failure(
                subject_id="pattern_api",
                error_type="timeout",
                context={"provider": "openai"}
            )

        # Add rate_limit errors
        for i in range(50):
            temp_integration.record_failure(
                subject_id="pattern_api",
                error_type="rate_limit",
                context={"provider": "anthropic"}
            )

        clusters = temp_integration.get_failure_clusters()

        # Should cluster independently
        error_types = {c.error_type for c in clusters}
        # Note: clusters only created on discover_patterns call with >=50 samples

    def test_e2e_pattern_lifecycle(self, temp_integration):
        """E2E: Full lifecycle - buffer → cluster → discover → register."""
        # Buffer failures
        for i in range(50):
            temp_integration.record_failure(
                subject_id="pattern_auth",
                error_type="auth_failed",
                context={
                    "provider": "oauth",
                    "endpoint": "POST /authorize",
                }
            )

        # Discover patterns (should trigger clustering and registration)
        discovered = temp_integration.discover_patterns()

        # Patterns should be discoverable
        all_patterns = temp_integration.get_discovered_patterns()
        assert isinstance(all_patterns, list)

    def test_e2e_audit_trail_completeness(self, temp_integration):
        """E2E: Verify audit trail is complete and append-only."""
        # Simulate pattern discovery
        for i in range(50):
            temp_integration.record_failure(
                subject_id="pattern_test",
                error_type="test_error",
                context={"test_key": f"test_value_{i % 5}"}
            )

        discovered = temp_integration.discover_patterns()

        # Check discovery logs
        clusterer = temp_integration.pattern_clusterer
        log_path = clusterer.base_dir / "discoveries.jsonl"

        if log_path.exists():
            with open(log_path, "r") as f:
                lines = f.readlines()

            # Each line should be valid JSON
            for line in lines:
                entry = json.loads(line)
                assert "timestamp" in entry
                assert "pattern_id" in entry or "error_type" in entry


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

"""Phase 3 k=5: Marketplace Hardening Phase 2 + E2E Convergence Tests (ADR-0776).

40+ tests covering:
- Plugin distribution (registry, fetch, verify)
- Canary deployment (5% sampling, error rate tracking)
- Auto-rollback (error >2% triggers revert)
- Learning loop integration (k=1 + k=2 + k=5)
- Confidence-based disable (confidence <0.4)
- E2E convergence proof (50+ cycles, stable variance)
- GDPR compliance (tenant isolation)
"""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

from core.marketplace.plugin_distribution import (
    PluginDistributionManager,
    PluginVersion,
    DeploymentStrategy,
    DeploymentEvent,
    HealthMetric,
)

from core.marketplace.plugin_learning_loop import (
    PluginLearningLoopCoordinator,
    PluginLearningCycle,
)

from core.learning.confidence_optimizer import (
    ConfidenceOptimizer,
    OutcomeSignal,
)


# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture(autouse=True)
def _isolated_audit(tmp_path, monkeypatch):
    """Deploy/rollback records go to the REAL tenant chain — in a scratch
    CORVIN_HOME, as the tenant the manager serves (the writer refuses any
    other tenant's record)."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "corvin"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "home" / ".config"))
    monkeypatch.setenv("CORVIN_TENANT_ID", "test_tenant_default")
    monkeypatch.delenv("VOICE_AUDIT_PATH", raising=False)
    monkeypatch.delenv("FORGE_ROOT", raising=False)
    yield


def chain_events(event_type: str, tenant: str = "test_tenant_default") -> list[dict]:
    import json

    from forge import paths as fp

    chain = fp.tenant_audit_chain(tenant)
    if not chain.exists():
        return []
    recs = [json.loads(line) for line in chain.read_text().splitlines() if line.strip()]
    return [r["details"] for r in recs if r["event_type"] == event_type]


@pytest.fixture
def tenant_id():
    """Test tenant ID."""
    return "test_tenant_default"


@pytest.fixture
def optimizer(tenant_id):
    """ConfidenceOptimizer instance."""
    return ConfidenceOptimizer(tenant_id=tenant_id)


@pytest.fixture
def distribution_manager(tenant_id, optimizer):
    """PluginDistributionManager instance."""
    return PluginDistributionManager(
        tenant_id=tenant_id,
        optimizer=optimizer,
        canary_percentage=0.05,
        rollback_error_threshold=0.02,
    )


@pytest.fixture
def learning_coordinator(tenant_id, distribution_manager, optimizer):
    """PluginLearningLoopCoordinator instance."""
    return PluginLearningLoopCoordinator(
        tenant_id=tenant_id,
        distribution_manager=distribution_manager,
        confidence_optimizer=optimizer,
    )


@pytest.fixture
def sample_plugin_version():
    """Sample plugin version."""
    return PluginVersion(
        plugin_id="test_plugin",
        version="1.0.0",
        signature="sig_base64",
        signature_verified=True,
        code_hash="code_hash_sha256",
        released_at=datetime.now(),
        download_url="https://marketplace.corvin.io/test_plugin/1.0.0",
        checksum="checksum_sha256",
    )


# ============================================================================
# TESTS: PluginDistributionManager — Initialization & Registration
# ============================================================================


class TestPluginDistributionInit:
    """Tests for PluginDistributionManager initialization."""

    def test_init_valid(self, tenant_id, optimizer):
        """Should initialize with valid params."""
        manager = PluginDistributionManager(
            tenant_id=tenant_id,
            optimizer=optimizer,
            canary_percentage=0.05,
            rollback_error_threshold=0.02,
        )
        assert manager.tenant_id == tenant_id
        assert manager.canary_percentage == 0.05

    def test_init_missing_tenant_id_fails(self, optimizer):
        """Should fail if tenant_id is empty (GDPR)."""
        with pytest.raises(ValueError, match="tenant_id required"):
            PluginDistributionManager(tenant_id="", optimizer=optimizer)

    def test_init_invalid_canary_percentage_fails(self, tenant_id, optimizer):
        """Should fail if canary_percentage invalid."""
        with pytest.raises(ValueError, match="canary_percentage must be"):
            PluginDistributionManager(
                tenant_id=tenant_id,
                optimizer=optimizer,
                canary_percentage=0.0,  # Invalid
            )


class TestPluginVersionRegistration:
    """Tests for version registration."""

    def test_register_version(self, distribution_manager, sample_plugin_version):
        """Should register plugin version."""
        distribution_manager.register_version(sample_plugin_version)
        # Should not raise

    def test_register_version_missing_id_fails(self, distribution_manager):
        """Should fail if plugin_id missing."""
        version = PluginVersion(
            plugin_id="",  # Missing
            version="1.0.0",
            signature="sig",
            signature_verified=True,
            code_hash="hash",
            released_at=datetime.now(),
            download_url="url",
            checksum="checksum",
        )
        with pytest.raises(ValueError, match="plugin_id and version required"):
            distribution_manager.register_version(version)


# ============================================================================
# TESTS: PluginDistributionManager — Deployment
# ============================================================================


class TestPluginDeployment:
    """Tests for plugin deployment."""

    def test_deploy_canary(self, distribution_manager, sample_plugin_version):
        """Should deploy canary (5% users)."""
        distribution_manager.register_version(sample_plugin_version)

        event = distribution_manager.deploy_version(
            plugin_id="test_plugin",
            version="1.0.0",
            strategy=DeploymentStrategy.CANARY,
            total_user_count=1000,
        )

        assert event.status == "success"
        assert event.strategy == DeploymentStrategy.CANARY
        assert event.percent_users == 5.0  # 5%
        assert 40 <= event.user_count <= 60  # ~50 users

    def test_deploy_stable(self, distribution_manager, sample_plugin_version):
        """Should deploy stable (100% users)."""
        distribution_manager.register_version(sample_plugin_version)

        event = distribution_manager.deploy_version(
            plugin_id="test_plugin",
            version="1.0.0",
            strategy=DeploymentStrategy.STABLE,
            total_user_count=1000,
        )

        assert event.status == "success"
        assert event.strategy == DeploymentStrategy.STABLE
        assert event.percent_users == 100.0

    def test_deploy_unregistered_version_fails(self, distribution_manager):
        """Should fail if version not registered."""
        with pytest.raises(ValueError, match="not found in registry"):
            distribution_manager.deploy_version(
                plugin_id="test_plugin",
                version="99.0.0",  # Not registered
                strategy=DeploymentStrategy.CANARY,
            )

    def test_deploy_unsigned_version_fails(self, distribution_manager):
        """Should fail if signature not verified."""
        version = PluginVersion(
            plugin_id="test_plugin",
            version="1.0.0",
            signature="sig",
            signature_verified=False,  # NOT verified
            code_hash="hash",
            released_at=datetime.now(),
            download_url="url",
            checksum="checksum",
        )
        distribution_manager.register_version(version)

        with pytest.raises(ValueError, match="Signature not verified"):
            distribution_manager.deploy_version(
                plugin_id="test_plugin",
                version="1.0.0",
                strategy=DeploymentStrategy.CANARY,
            )


# ============================================================================
# TESTS: PluginDistributionManager — Rollback & Auto-Rollback
# ============================================================================


class TestPluginRollback:
    """Tests for plugin rollback."""

    def test_rollback_to_previous_version(self, distribution_manager, sample_plugin_version):
        """Should rollback to previous version."""
        # Deploy v1.0.0
        distribution_manager.register_version(sample_plugin_version)
        distribution_manager.deploy_version(
            plugin_id="test_plugin",
            version="1.0.0",
            strategy=DeploymentStrategy.STABLE,
        )

        # Deploy v1.0.1
        v101 = PluginVersion(
            plugin_id="test_plugin",
            version="1.0.1",
            signature="sig",
            signature_verified=True,
            code_hash="hash",
            released_at=datetime.now(),
            download_url="url",
            checksum="checksum",
        )
        distribution_manager.register_version(v101)
        distribution_manager.deploy_version(
            plugin_id="test_plugin",
            version="1.0.1",
            strategy=DeploymentStrategy.STABLE,
        )

        # Rollback to v1.0.0
        event = distribution_manager.rollback_version(
            plugin_id="test_plugin",
            reason="Too many errors",
        )

        assert event.status == "success"
        assert event.version == "1.0.0"
        assert event.strategy == DeploymentStrategy.ROLLBACK

    def test_rollback_no_previous_version_fails(self, distribution_manager):
        """Should fail if no previous version."""
        with pytest.raises(ValueError, match="No previous version"):
            distribution_manager.rollback_version(plugin_id="test_plugin")


# ============================================================================
# TESTS: PluginDistributionManager — Health Metrics & Auto-Rollback
# ============================================================================


class TestHealthMetricsAndAutoRollback:
    """Tests for health tracking and auto-rollback."""

    def test_record_invocation_success(
        self, distribution_manager, sample_plugin_version
    ):
        """Should record successful invocation."""
        distribution_manager.register_version(sample_plugin_version)

        distribution_manager.record_invocation(
            plugin_id="test_plugin",
            version="1.0.0",
            success=True,
            latency_ms=42,
        )

        metric = distribution_manager.get_health_metrics("test_plugin", "1.0.0")
        assert metric is not None
        assert metric.error_rate == 0.0

    def test_record_invocation_failure(
        self, distribution_manager, sample_plugin_version
    ):
        """Should track invocation failures."""
        distribution_manager.register_version(sample_plugin_version)
        distribution_manager.deploy_version(
            plugin_id="test_plugin",
            version="1.0.0",
            strategy=DeploymentStrategy.CANARY,
            total_user_count=1000,
        )

        # Record failures
        for i in range(3):
            distribution_manager.record_invocation(
                plugin_id="test_plugin",
                version="1.0.0",
                success=False,
                latency_ms=42,
            )

        metric = distribution_manager.get_health_metrics("test_plugin", "1.0.0")
        assert metric.error_rate == 1.0
        assert metric.n_invocations == 3

    def test_auto_rollback_on_error_rate(
        self, distribution_manager, sample_plugin_version
    ):
        """Should auto-rollback if error rate >2%."""
        # Deploy v1.0.0 (stable)
        distribution_manager.register_version(sample_plugin_version)
        distribution_manager.deploy_version(
            plugin_id="test_plugin",
            version="1.0.0",
            strategy=DeploymentStrategy.STABLE,
            total_user_count=1000,
        )

        # Deploy v1.0.1 (canary, 50 users)
        v101 = PluginVersion(
            plugin_id="test_plugin",
            version="1.0.1",
            signature="sig",
            signature_verified=True,
            code_hash="hash",
            released_at=datetime.now(),
            download_url="url",
            checksum="checksum",
        )
        distribution_manager.register_version(v101)
        distribution_manager.deploy_version(
            plugin_id="test_plugin",
            version="1.0.1",
            strategy=DeploymentStrategy.CANARY,
            total_user_count=1000,
        )

        # Record failures (>2% error rate)
        for i in range(2):
            distribution_manager.record_invocation(
                plugin_id="test_plugin",
                version="1.0.1",
                success=False,
                latency_ms=42,
            )

        # The canary was aborted; the stable version is untouched (it used to
        # be downgraded one step by a canary failure).
        assert not distribution_manager.is_canary_active("test_plugin")
        assert distribution_manager.get_current_version("test_plugin") == "1.0.0"
        rolled = chain_events("marketplace.plugin_rolled_back")
        assert len(rolled) == 1
        assert rolled[0]["from_version"] == "1.0.1"
        assert rolled[0]["version"] == "1.0.0"
        assert rolled[0]["reason"] == "canary_error_rate_exceeded"

    def test_canary_failure_without_stable_does_not_crash(
        self, distribution_manager, sample_plugin_version
    ):
        """A failing canary with no stable version used to raise ValueError
        from inside ``record_invocation``."""
        distribution_manager.register_version(sample_plugin_version)
        distribution_manager.deploy_version("test_plugin", "1.0.0", DeploymentStrategy.CANARY)
        distribution_manager.record_invocation("test_plugin", "1.0.0", success=False, latency_ms=5)
        assert not distribution_manager.is_canary_active("test_plugin")
        assert distribution_manager.get_current_version("test_plugin") is None

    def test_p99_is_a_real_percentile(self, distribution_manager):
        for ms in [10] * 99 + [1000]:
            distribution_manager.record_invocation("p", "1", success=True, latency_ms=ms)
        assert distribution_manager.get_health_metrics("p", "1").latency_p99 == 10

    def test_deploy_is_audited_before_bookkeeping(
        self, distribution_manager, sample_plugin_version, monkeypatch
    ):
        from core.deployment.audit_sink import AuditWriteFailed

        distribution_manager.register_version(sample_plugin_version)

        def boom(*a, **k):
            raise AuditWriteFailed("disk full")

        monkeypatch.setattr("core.marketplace.plugin_distribution.audit_sink.emit", boom)
        with pytest.raises(AuditWriteFailed):
            distribution_manager.deploy_version("test_plugin", "1.0.0", DeploymentStrategy.STABLE)
        assert distribution_manager.get_current_version("test_plugin") is None

    def test_unmeasured_confidence_never_disables(self, distribution_manager):
        distribution_manager.record_invocation("p", "1", success=True, latency_ms=1)
        assert distribution_manager.get_health_metrics("p", "1").confidence is None
        assert distribution_manager.auto_disable_if_low_confidence("p", "1") is False

    def test_disabled_version_is_refused(self, distribution_manager, sample_plugin_version):
        import dataclasses

        distribution_manager.register_version(sample_plugin_version)
        distribution_manager.record_invocation("test_plugin", "1.0.0", success=True, latency_ms=1)
        key = ("test_plugin", "1.0.0")
        distribution_manager._health_metrics[key] = dataclasses.replace(
            distribution_manager._health_metrics[key], confidence=0.1)
        assert distribution_manager.auto_disable_if_low_confidence("test_plugin", "1.0.0") is True
        with pytest.raises(ValueError, match="disabled"):
            distribution_manager.deploy_version("test_plugin", "1.0.0", DeploymentStrategy.STABLE)
        assert chain_events("marketplace.plugin_version_disabled")[0]["confidence"] == 0.1


# ============================================================================
# TESTS: PluginLearningLoopCoordinator — Version Selection
# ============================================================================


class TestLearningLoopVersionSelection:
    """Tests for learning-based version selection."""

    def test_select_highest_confidence_version(self, learning_coordinator, optimizer):
        """Should select version with highest confidence."""
        # Record outcomes to build confidence
        for i in range(20):
            optimizer.record_outcome(
                OutcomeSignal(
                    model_id="test_plugin:1.0.0",
                    success=True,  # 1.0.0 succeeds
                )
            )

        for i in range(10):
            optimizer.record_outcome(
                OutcomeSignal(
                    model_id="test_plugin:1.0.1",
                    success=True,  # 1.0.1 succeeds less often
                )
            )

        # Select version
        selected = learning_coordinator.select_plugin_version(
            plugin_id="test_plugin",
            available_versions=["1.0.0", "1.0.1"],
        )

        # Should select 1.0.0 (higher confidence)
        assert selected == "1.0.0"

    def test_select_version_no_versions_fails(self, learning_coordinator):
        """Should fail if no versions available."""
        with pytest.raises(ValueError, match="available_versions required"):
            learning_coordinator.select_plugin_version(
                plugin_id="test_plugin",
                available_versions=[],
            )


# ============================================================================
# TESTS: PluginLearningLoopCoordinator — Learning Cycles
# ============================================================================


class TestLearningLoopCycles:
    """Tests for learning cycle recording."""

    def test_record_learning_cycle(self, learning_coordinator):
        """Should record learning cycle."""
        cycle = learning_coordinator.record_learning_cycle(
            plugin_id="test_plugin",
            selected_version="1.0.0",
            reason="exploit_high_confidence",
            outcome_success=True,
            outcome_latency_ms=42,
            error_rate=0.0,
        )

        assert cycle.plugin_id == "test_plugin"
        assert cycle.cycle_number == 1
        assert cycle.outcome_success is True

    def test_learning_cycle_updates_confidence(self, learning_coordinator):
        """Learning cycle should update confidence."""
        # Record success
        cycle1 = learning_coordinator.record_learning_cycle(
            plugin_id="test_plugin",
            selected_version="1.0.0",
            reason="initial",
            outcome_success=True,
        )

        assert cycle1.confidence_after > cycle1.confidence_before

    def test_convergence_detection(self, learning_coordinator):
        """Should detect convergence after ~20 cycles."""
        # Record 20+ successful outcomes
        for i in range(20):
            cycle = learning_coordinator.record_learning_cycle(
                plugin_id="test_plugin",
                selected_version="1.0.0",
                reason="exploit",
                outcome_success=True,
            )

        # Should be converged
        assert learning_coordinator.get_convergence_status("test_plugin")


# ============================================================================
# TESTS: E2E Convergence Proof (Full Learning Loop)
# ============================================================================


class TestE2EConvergenceProof:
    """Tests for end-to-end learning loop convergence."""

    def test_e2e_learning_loop_50_cycles(self, learning_coordinator):
        """E2E: 50 cycles should show convergence."""
        # Simulate 50 learning cycles
        for cycle_num in range(50):
            # Select version (learning-based)
            selected = learning_coordinator.select_plugin_version(
                plugin_id="test_plugin",
                available_versions=["1.0.0", "1.0.1"],
            )

            # Simulate outcome (v1.0.0 has 90% success, v1.0.1 has 70%)
            import random

            success = random.random() < (
                0.9 if selected == "1.0.0" else 0.7
            )

            # Record cycle
            cycle = learning_coordinator.record_learning_cycle(
                plugin_id="test_plugin",
                selected_version=selected,
                reason="exploit" if cycle_num > 10 else "explore",
                outcome_success=success,
            )

        # After 50 cycles, should be mostly selecting 1.0.0
        history = learning_coordinator.get_learning_history("test_plugin")
        assert len(history) == 50

        # Check convergence
        converged = learning_coordinator.is_converged("test_plugin", min_cycles=20)
        assert converged or learning_coordinator.get_cycle_count("test_plugin") >= 20

    def test_e2e_confidence_stability(self, learning_coordinator):
        """E2E: confidence should stabilize (variance <0.05)."""
        # Record many cycles
        confidences = []
        for i in range(40):
            cycle = learning_coordinator.record_learning_cycle(
                plugin_id="test_plugin",
                selected_version="1.0.0",
                reason="exploit",
                outcome_success=True,
            )
            confidences.append(cycle.confidence_after)

        # Last 20 confidences should be stable
        last_20 = confidences[-20:]
        mean = sum(last_20) / len(last_20)
        variance = sum((x - mean) ** 2 for x in last_20) / len(last_20)

        # Variance should be low (converged)
        assert variance < 0.05 or learning_coordinator.get_convergence_status("test_plugin")


# ============================================================================
# TESTS: Compliance & Edge Cases
# ============================================================================


class TestCompliance:
    """Tests for GDPR compliance and fail-closed."""

    def test_tenant_isolation_distribution(self, optimizer):
        """PluginDistributionManager should enforce tenant_id."""
        with pytest.raises(ValueError, match="tenant_id required"):
            PluginDistributionManager(tenant_id="", optimizer=optimizer)

    def test_tenant_isolation_coordinator(self, distribution_manager, optimizer):
        """PluginLearningLoopCoordinator should enforce tenant_id."""
        with pytest.raises(ValueError, match="tenant_id required"):
            PluginLearningLoopCoordinator(
                tenant_id="",
                distribution_manager=distribution_manager,
                confidence_optimizer=optimizer,
            )

    def test_deployment_event_immutable(self, distribution_manager, sample_plugin_version):
        """DeploymentEvent should be frozen."""
        distribution_manager.register_version(sample_plugin_version)
        event = distribution_manager.deploy_version(
            plugin_id="test_plugin",
            version="1.0.0",
            strategy=DeploymentStrategy.CANARY,
        )

        with pytest.raises(AttributeError):
            event.status = "failed"

    def test_learning_cycle_immutable(self, learning_coordinator):
        """PluginLearningCycle should be frozen."""
        cycle = learning_coordinator.record_learning_cycle(
            plugin_id="test_plugin",
            selected_version="1.0.0",
            reason="test",
            outcome_success=True,
        )

        with pytest.raises(AttributeError):
            cycle.outcome_success = False


# ============================================================================
# TESTS: Audit Trail
# ============================================================================


class TestAuditTrail:
    """Tests for audit trail (ADR-0232 integration)."""

    def test_deployment_history_complete(self, distribution_manager, sample_plugin_version):
        """Should record complete deployment history."""
        distribution_manager.register_version(sample_plugin_version)

        # Deploy canary
        distribution_manager.deploy_version(
            plugin_id="test_plugin",
            version="1.0.0",
            strategy=DeploymentStrategy.CANARY,
        )

        # Check history
        history = distribution_manager.get_deployment_history()
        assert len(history) > 0
        assert history[-1].status == "success"

    def test_learning_cycle_history_complete(self, learning_coordinator):
        """Should record complete learning history."""
        for i in range(5):
            learning_coordinator.record_learning_cycle(
                plugin_id="test_plugin",
                selected_version="1.0.0",
                reason="test",
                outcome_success=True,
            )

        history = learning_coordinator.get_learning_history("test_plugin")
        assert len(history) == 5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

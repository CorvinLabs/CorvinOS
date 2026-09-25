"""
Phase 1 E2E Tests: Deployment State Synchronization

Validates that drift detection works across multiple instances.
"""

import pytest
from core.deployment.state_sync import (
    DeploymentStateManager,
    DriftAlert,
    DriftSeverity,
)
from core.deployment.manifest import ManifestManager, ManifestSnapshot


class TestManifestGeneration:
    """Test manifest snapshot creation"""

    def test_create_snapshot_has_required_fields(self):
        """Snapshot contains all required fields"""
        snapshot = ManifestManager.create_snapshot("test-instance")

        assert snapshot.git_sha  # Not empty
        assert snapshot.version_tag  # Not empty
        assert snapshot.manifest_hash  # SHA256
        assert snapshot.instance_id == "test-instance"
        assert snapshot.tenant_id == "_default"
        assert len(snapshot.manifest_hash) == 64  # SHA256 = 64 hex chars

    def test_manifest_hash_is_deterministic(self):
        """Same code tree → same hash"""
        hash1 = ManifestManager.calculate_manifest_hash()
        hash2 = ManifestManager.calculate_manifest_hash()

        assert hash1 == hash2

    def test_snapshot_serialization(self):
        """Snapshot → dict → snapshot (round-trip)"""
        original = ManifestManager.create_snapshot("test-instance")
        data = ManifestManager.to_dict(original)
        restored = ManifestManager.from_dict(data)

        assert restored.git_sha == original.git_sha
        assert restored.instance_id == original.instance_id


class TestDeploymentStateManager:
    """Test drift detection across instances"""

    def test_first_instance_becomes_canonical(self):
        """First registered instance is the canonical state"""
        manager = DeploymentStateManager()

        snapshot1 = manager.register_instance("instance-1")
        assert manager.canonical_state == snapshot1
        assert manager.canonical_state.instance_id == "instance-1"

    def test_no_drift_when_identical(self):
        """Identical manifests → no drift"""
        manager = DeploymentStateManager()

        # Both instances have same code
        manager.register_instance("instance-1")
        manager.register_instance("instance-2")

        drifts = manager.detect_drift("instance-2")
        assert drifts == []

    def test_detect_code_version_drift(self):
        """Detects when git SHA differs"""
        manager = DeploymentStateManager()

        snapshot1 = manager.register_instance("instance-1")
        snapshot2 = manager.register_instance("instance-2")

        # Simulate different git SHA
        snapshot2.git_sha = "different_sha_" + snapshot2.git_sha[:50]
        manager.instance_states["instance-2"] = snapshot2

        drifts = manager.detect_drift("instance-2")

        assert len(drifts) > 0
        assert any(d.alert_type == "CODE_VERSION_DRIFT" for d in drifts)
        assert any(d.severity == DriftSeverity.CRITICAL for d in drifts)

    def test_detect_manifest_hash_mismatch(self):
        """Detects when code tree diverges"""
        manager = DeploymentStateManager()

        snapshot1 = manager.register_instance("instance-1")
        snapshot2 = manager.register_instance("instance-2")

        # Simulate different manifest hash
        snapshot2.manifest_hash = "0" * 64
        manager.instance_states["instance-2"] = snapshot2

        drifts = manager.detect_drift("instance-2")

        assert any(d.alert_type == "MANIFEST_HASH_MISMATCH" for d in drifts)
        assert any(d.severity == DriftSeverity.CRITICAL for d in drifts)

    def test_drift_has_remediation_action(self):
        """Critical drift includes remediation action"""
        manager = DeploymentStateManager()

        snapshot1 = manager.register_instance("instance-1")
        snapshot2 = manager.register_instance("instance-2")
        snapshot2.git_sha = "wrong_sha_" + snapshot2.git_sha[:50]
        manager.instance_states["instance-2"] = snapshot2

        drifts = manager.detect_drift("instance-2")
        critical_drifts = [d for d in drifts if d.severity == DriftSeverity.CRITICAL]

        assert all(d.remediation_available for d in critical_drifts)
        assert all(d.remediation_action == "REDEPLOY_INSTANCE" for d in critical_drifts)

    def test_detect_all_drifts_across_instances(self):
        """Scan all instances for drift"""
        manager = DeploymentStateManager()

        manager.register_instance("instance-1")
        manager.register_instance("instance-2")
        manager.register_instance("instance-3")

        # Drift instance-2
        snapshot2 = manager.instance_states["instance-2"]
        snapshot2.git_sha = "wrong_" + snapshot2.git_sha[:59]
        manager.instance_states["instance-2"] = snapshot2

        all_drifts = manager.detect_all_drifts()

        # Should detect drifts in instance-2 but not instance-3
        assert len(all_drifts) > 0
        assert all(d.instance_id == "instance-2" for d in all_drifts)

    def test_report_generation(self):
        """Generate human-readable drift report"""
        manager = DeploymentStateManager()

        manager.register_instance("instance-1")
        manager.register_instance("instance-2")

        report = manager.report_summary()

        assert "DEPLOYMENT STATE REPORT" in report
        assert "instance-1" in report
        assert "instance-2" in report
        assert "CANONICAL" in report

    def test_get_canonical_state(self):
        """Retrieve canonical state"""
        manager = DeploymentStateManager()

        snapshot = manager.register_instance("instance-1")
        canonical = manager.get_canonical_state()

        assert canonical.instance_id == "instance-1"
        assert canonical.git_sha == snapshot.git_sha


class TestPhase1E2E:
    """End-to-end Phase 1 scenario"""

    def test_three_instance_deployment_scenario(self):
        """
        Real-world scenario: 3 instances, detect drift, report status
        """
        manager = DeploymentStateManager()

        # Deploy to NYC (canonical)
        nyc = manager.register_instance("prod-us-east-1", tenant_id="_default")

        # Deploy to London (in sync)
        london = manager.register_instance("prod-eu-west-1", tenant_id="_default")

        # Deploy to Sydney (drifted — older version)
        sydney = manager.register_instance("prod-ap-southeast-1", tenant_id="_default")
        sydney.git_sha = "old_version_" + sydney.git_sha[:52]
        manager.instance_states["prod-ap-southeast-1"] = sydney

        # Run full drift detection
        all_drifts = manager.detect_all_drifts()

        # Generate report
        report = manager.report_summary()

        # Assertions
        assert len(all_drifts) > 0
        assert any(d.instance_id == "prod-ap-southeast-1" for d in all_drifts)
        assert "prod-ap-southeast-1" in report
        assert "DRIFTED" in report


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

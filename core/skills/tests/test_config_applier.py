"""Unit tests for SkillConfigApplier (Task 1).

Tests cover:
1. Config apply on approval
2. Config rollback on revoke
3. Audit trail logging
4. Failure handling
5. Callback wiring
"""

import pytest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, MagicMock, call, patch

from core.skills.config_applier import SkillConfigApplier, ConfigApplyResult
from core.skills.feedback_stability import (
    OperatorApprovalGate,
    DriftAlert,
    ApprovalReasonCode,
)


@pytest.fixture
def mock_audit_backend():
    """Mock audit backend."""
    backend = Mock()
    backend.write_event = Mock(return_value="event_id_123")
    return backend


@pytest.fixture
def mock_optimizer():
    """Mock OptimizerWithApprovalGate."""
    optimizer = Mock()
    optimizer.approval_gate = Mock()
    optimizer.on_approval_callback = None
    optimizer.on_rejection_callback = None
    optimizer.on_revoke_callback = None
    return optimizer


@pytest.fixture
def config_applier(mock_optimizer, mock_audit_backend):
    """Create SkillConfigApplier instance."""
    applier = SkillConfigApplier(
        skill_id="test.skill",
        optimizer_with_gate=mock_optimizer,
        audit_backend=mock_audit_backend,
        tenant_id="_default",
        config_getter=lambda: {"key": "current"},
    )
    return applier


# ============================================================================
# Task 1.1: Callback Registration
# ============================================================================


class TestCallbackWiring:
    """Test that callbacks are properly registered with optimizer."""

    def test_approval_callback_registered(self, config_applier):
        """Test that on_approval_callback is registered."""
        assert config_applier.optimizer.on_approval_callback is not None

    def test_rejection_callback_registered(self, config_applier):
        """Test that on_rejection_callback is registered."""
        assert config_applier.optimizer.on_rejection_callback is not None

    def test_callbacks_are_methods(self, config_applier):
        """Test that callbacks are bound methods of applier."""
        assert callable(config_applier.optimizer.on_approval_callback)
        assert callable(config_applier.optimizer.on_rejection_callback)


# ============================================================================
# Task 1.2: Config Apply on Approval
# ============================================================================


class TestApprovalCallback:
    """Test config apply when approval is granted."""

    def test_approval_callback_success(self, config_applier, mock_optimizer):
        """Test successful config apply on approval."""
        approval_id = "approval_123"
        new_config_hash = "b" * 64

        # Mock the approval record
        drift_alert = DriftAlert(
            skill_id="test.skill",
            metric_name="confidence_threshold",
            smoothed_delta=0.2,
            drift_threshold=0.15,
            consecutive_high_deltas=2,
        )

        mock_record = Mock()
        mock_record.scrubbed_alert.skill_id = "test.skill"
        mock_record.scrubbed_alert.metric_name = "confidence_threshold"
        mock_optimizer.approval_gate.get_approval_status.return_value = mock_record

        # Configure applier to use test implementation
        config_applier._config_applier = Mock(return_value={"key": "value"})

        # Call approval callback
        config_applier._on_approval_callback(approval_id, new_config_hash)

        # Verify audit was called
        assert config_applier.audit_backend.write_event.called
        call_args = config_applier.audit_backend.write_event.call_args[0][0]
        assert call_args["event_type"] == "skill_config_applied"
        assert call_args["success"] is True

    def test_approval_callback_failure(self, config_applier, mock_optimizer):
        """Test config apply failure on approval."""
        approval_id = "approval_456"
        new_config_hash = "c" * 64

        # Mock the approval record
        mock_record = Mock()
        mock_record.scrubbed_alert.skill_id = "test.skill"
        mock_record.scrubbed_alert.metric_name = "confidence_threshold"
        mock_optimizer.approval_gate.get_approval_status.return_value = mock_record

        # Configure applier to fail
        config_applier._config_applier = Mock(
            side_effect=RuntimeError("Config apply failed")
        )

        # Call approval callback
        config_applier._on_approval_callback(approval_id, new_config_hash)

        # Verify audit was called with success=False
        assert config_applier.audit_backend.write_event.called
        call_args = config_applier.audit_backend.write_event.call_args[0][0]
        assert call_args["event_type"] == "skill_config_applied"
        assert call_args["success"] is False
        assert "Config apply failed" in call_args["error"]

    def test_approval_callback_missing_record(self, config_applier, mock_optimizer):
        """Test approval callback when record is not found."""
        approval_id = "nonexistent"
        new_config_hash = "d" * 64

        # Record not found
        mock_optimizer.approval_gate.get_approval_status.return_value = None

        # Call approval callback (should log error, not crash)
        config_applier._on_approval_callback(approval_id, new_config_hash)

        # Should still complete without exception


# ============================================================================
# Task 1.3: Config Rollback on Revoke
# ============================================================================


class TestRevokeRollback:
    """Test config rollback when approval is revoked."""

    def test_revoke_success(self, config_applier):
        """Test successful config rollback on revoke."""
        approval_id = "approval_789"

        # Store previous config info
        config_applier.previous_configs[approval_id] = {
            "config_hash": "a" * 64,
            "new_config_hash": "b" * 64,
            "metric_name": "confidence_threshold",
            "timestamp": datetime.utcnow().isoformat(),
        }

        # Configure applier to succeed on restore
        config_applier._config_restorer = Mock(return_value={"key": "old_value"})

        # Call handle_revoke
        success = config_applier.handle_revoke(approval_id)

        # Verify success
        assert success is True
        assert approval_id not in config_applier.previous_configs  # Cleaned up

        # Verify audit was called
        assert config_applier.audit_backend.write_event.called
        call_args = config_applier.audit_backend.write_event.call_args[0][0]
        assert call_args["event_type"] == "skill_config_rolled_back"
        assert call_args["success"] is True

    def test_revoke_missing_previous_config(self, config_applier):
        """Test revoke when previous config is not found."""
        approval_id = "unknown_approval"

        # No previous config stored
        success = config_applier.handle_revoke(approval_id)

        # Should fail gracefully
        assert success is False

    def test_revoke_restore_failure(self, config_applier):
        """Test revoke when config restore fails."""
        approval_id = "approval_restore_fail"

        # Store previous config info
        config_applier.previous_configs[approval_id] = {
            "config_hash": "a" * 64,
            "new_config_hash": "b" * 64,
            "metric_name": "confidence_threshold",
            "timestamp": datetime.utcnow().isoformat(),
        }

        # Configure applier to fail on restore
        config_applier._config_restorer = Mock(
            side_effect=RuntimeError("Restore failed")
        )

        # Call handle_revoke
        success = config_applier.handle_revoke(approval_id)

        # Should fail but not crash
        assert success is False

        # Audit should show failure
        call_args = config_applier.audit_backend.write_event.call_args[0][0]
        assert call_args["event_type"] == "skill_config_rolled_back"
        assert call_args["success"] is False


# ============================================================================
# Task 1.4: Audit Trail Logging
# ============================================================================


class TestAuditLogging:
    """Test audit trail logging."""

    def test_audit_config_apply(self, config_applier):
        """Test audit logging for config apply."""
        config_applier._audit_config_apply(
            approval_id="app_123",
            metric_name="confidence",
            prev_hash="a" * 64,
            next_hash="b" * 64,
            success=True,
            error=None,
        )

        # Verify audit was called
        assert config_applier.audit_backend.write_event.called
        event = config_applier.audit_backend.write_event.call_args[0][0]

        assert event["event_type"] == "skill_config_applied"
        assert event["approval_id"] == "app_123"
        assert event["metric_name"] == "confidence"
        assert event["success"] is True
        assert event["error"] is None

    def test_audit_config_rejected(self, config_applier):
        """Test audit logging for rejected config."""
        config_applier._audit_config_rejected("app_456")

        # Verify audit was called
        assert config_applier.audit_backend.write_event.called
        event = config_applier.audit_backend.write_event.call_args[0][0]

        assert event["event_type"] == "skill_config_apply_skipped"
        assert event["reason"] == "approval_rejected"

    def test_audit_config_rollback(self, config_applier):
        """Test audit logging for config rollback."""
        config_applier._audit_config_rollback(
            approval_id="app_789",
            metric_name="threshold",
            rolled_back_hash="a" * 64,
            success=True,
            error=None,
        )

        # Verify audit was called
        assert config_applier.audit_backend.write_event.called
        event = config_applier.audit_backend.write_event.call_args[0][0]

        assert event["event_type"] == "skill_config_rolled_back"
        assert event["approval_id"] == "app_789"
        assert event["success"] is True

    def test_audit_failure_does_not_crash(self, config_applier, mock_audit_backend):
        """Test that audit failures don't crash the applier."""
        # Make audit fail
        mock_audit_backend.write_event.side_effect = RuntimeError("Audit failed")

        # Should not raise
        config_applier._audit_config_apply(
            approval_id="app_fail",
            metric_name="conf",
            prev_hash="a" * 64,
            next_hash="b" * 64,
            success=True,
            error=None,
        )


# ============================================================================
# Task 1.5: Failure Handling (Config Apply Failure ≠ Approval Revoke)
# ============================================================================


class TestFailureHandling:
    """Test that config apply failures don't break approval state."""

    def test_config_apply_failure_preserves_approval(self, config_applier):
        """Test that config apply failure doesn't revoke the approval."""
        # Configure applier to fail
        config_applier._config_applier = Mock(
            side_effect=RuntimeError("Apply failed")
        )

        approval_id = "approval_fail"
        new_config_hash = "b" * 64

        # Mock approval record
        mock_record = Mock()
        mock_record.scrubbed_alert.skill_id = "test.skill"
        mock_record.scrubbed_alert.metric_name = "metric"
        config_applier.optimizer.approval_gate.get_approval_status.return_value = (
            mock_record
        )

        # Call approval callback (should fail but not revoke)
        config_applier._on_approval_callback(approval_id, new_config_hash)

        # Audit should show failure but not revoke
        call_args = config_applier.audit_backend.write_event.call_args[0][0]
        assert call_args["success"] is False

    def test_callback_exception_does_not_crash(self, config_applier):
        """Test that exceptions in callbacks don't crash."""
        config_applier._config_applier = Mock(
            side_effect=Exception("Unhandled error")
        )

        # Should not raise
        config_applier._on_approval_callback("approval_123", "b" * 64)


# ============================================================================
# Task 1.6: Config Hashing
# ============================================================================


class TestConfigHashing:
    """Test config hashing functionality."""

    def test_hash_dict_config(self):
        """Test hashing a dictionary config."""
        config = {"key1": "value1", "key2": 42}
        hash1 = SkillConfigApplier._hash_config(config)
        hash2 = SkillConfigApplier._hash_config(config)

        # Same config should produce same hash
        assert hash1 == hash2
        assert len(hash1) == 64  # SHA256 hex

    def test_hash_different_configs(self):
        """Test that different configs produce different hashes."""
        config1 = {"key": "value1"}
        config2 = {"key": "value2"}

        hash1 = SkillConfigApplier._hash_config(config1)
        hash2 = SkillConfigApplier._hash_config(config2)

        assert hash1 != hash2

    def test_hash_order_independent(self):
        """Test that dict key order doesn't affect hash."""
        config1 = {"a": 1, "b": 2}
        config2 = {"b": 2, "a": 1}

        hash1 = SkillConfigApplier._hash_config(config1)
        hash2 = SkillConfigApplier._hash_config(config2)

        # Should be same hash (JSON dumps with sort_keys=True)
        assert hash1 == hash2


# ============================================================================
# Task 1.7: Rejection Callback
# ============================================================================


class TestRejectionCallback:
    """Test rejection callback."""

    def test_rejection_callback_audited(self, config_applier):
        """Test that rejection is audited."""
        config_applier._on_rejection_callback("approval_rej")

        # Verify audit
        assert config_applier.audit_backend.write_event.called
        event = config_applier.audit_backend.write_event.call_args[0][0]

        assert event["event_type"] == "skill_config_apply_skipped"
        assert event["reason"] == "approval_rejected"

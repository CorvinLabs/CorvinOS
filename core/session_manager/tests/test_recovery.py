"""Tests for RecoveryEngine (k=4).

10 unit + integration tests covering:
- 4 recovery patterns (Replay, Adapt, Backtrack, Pause)
- Error type to recovery pattern mapping
- Recovery success tracking
- Attempt counting
- Audit logging
"""

import pytest
from datetime import datetime

from core.session_manager.recovery import (
    RecoveryEngine,
    RecoveryAction,
    RecoveryPattern,
    RecoveryErrorType,
)


class MockHub:
    """Mock SubsystemHub for testing."""

    def __init__(self):
        self.published_events = []

    def publish_event(self, event_name, event_data):
        self.published_events.append((event_name, event_data))


class TestRecoveryAction:
    """Test RecoveryAction dataclass."""

    def test_recovery_action_creation(self):
        """Test creating a recovery action."""
        action = RecoveryAction(
            session_id="s1",
            task_id="t1",
            tenant_id="default",
            error_type=RecoveryErrorType.TIMEOUT,
            recovery_pattern=RecoveryPattern.REPLAY,
            reason="Timeout after 5 minutes",
        )

        assert action.session_id == "s1"
        assert action.recovery_pattern == RecoveryPattern.REPLAY
        assert action.success is False

    def test_recovery_action_requires_session_id(self):
        """Test that recovery action requires session_id."""
        with pytest.raises(ValueError, match="session_id"):
            RecoveryAction(
                session_id="",
                task_id="t1",
                tenant_id="default",
            )

    def test_recovery_action_audit_event(self):
        """Test recovery action audit event generation."""
        action = RecoveryAction(
            session_id="s1",
            task_id="t1",
            tenant_id="default",
            error_type=RecoveryErrorType.TIMEOUT,
            recovery_pattern=RecoveryPattern.REPLAY,
        )

        audit_event = action.to_audit_event()

        assert audit_event["event_type"] == "session.recovery_action.replay"
        assert audit_event["session_id"] == "s1"
        assert audit_event["error_type"] == "timeout"


class TestRecoveryEngine:
    """Test RecoveryEngine."""

    def setup_method(self):
        """Setup test fixtures."""
        self.hub = MockHub()
        self.engine = RecoveryEngine(hub=self.hub)
        self.engine.startup(self.hub)

    def teardown_method(self):
        """Cleanup."""
        self.engine.shutdown()

    # ========================================================================
    # Test 1: Error to Pattern Mapping
    # ========================================================================

    def test_timeout_maps_to_replay(self):
        """Test that TIMEOUT error maps to REPLAY pattern."""
        action = self.engine.initiate_recovery(
            session_id="s1",
            task_id="t1",
            tenant_id="default",
            error_type=RecoveryErrorType.TIMEOUT,
        )

        assert action.recovery_pattern == RecoveryPattern.REPLAY

    def test_strategy_failure_maps_to_adapt(self):
        """Test that STRATEGY_FAILED maps to ADAPT pattern."""
        action = self.engine.initiate_recovery(
            session_id="s1",
            task_id="t1",
            tenant_id="default",
            error_type=RecoveryErrorType.STRATEGY_FAILED,
        )

        assert action.recovery_pattern == RecoveryPattern.ADAPT

    def test_validation_error_maps_to_backtrack(self):
        """Test that VALIDATION_ERROR maps to BACKTRACK pattern."""
        action = self.engine.initiate_recovery(
            session_id="s1",
            task_id="t1",
            tenant_id="default",
            error_type=RecoveryErrorType.VALIDATION_ERROR,
            source_checkpoint_id="cp-001",
        )

        assert action.recovery_pattern == RecoveryPattern.BACKTRACK

    def test_quota_exceeded_maps_to_pause(self):
        """Test that QUOTA_EXCEEDED maps to PAUSE pattern."""
        action = self.engine.initiate_recovery(
            session_id="s1",
            task_id="t1",
            tenant_id="default",
            error_type=RecoveryErrorType.QUOTA_EXCEEDED,
        )

        assert action.recovery_pattern == RecoveryPattern.PAUSE

    # ========================================================================
    # Test 2-5: Recovery Pattern Execution
    # ========================================================================

    def test_execute_replay(self):
        """Test REPLAY recovery pattern execution."""
        action = RecoveryAction(
            session_id="s1",
            task_id="t1",
            tenant_id="default",
            recovery_pattern=RecoveryPattern.REPLAY,
        )

        result = self.engine.execute_replay(action, current_attempt=1)

        assert result["success"] is True
        assert result["pattern"] == "replay"
        assert result["attempt"] == 1

    def test_execute_replay_max_attempts(self):
        """Test REPLAY fails after max attempts."""
        action = RecoveryAction(
            session_id="s1",
            task_id="t1",
            tenant_id="default",
            recovery_pattern=RecoveryPattern.REPLAY,
        )

        # Attempt 1, 2, 3 should succeed
        result1 = self.engine.execute_replay(action, current_attempt=1)
        assert result1["success"] is True

        # Attempt 4 should fail (max is 3)
        result4 = self.engine.execute_replay(action, current_attempt=4)
        assert result4["success"] is False
        assert "Max replay attempts" in result4["reason"]

    def test_execute_adapt(self):
        """Test ADAPT recovery pattern execution."""
        action = RecoveryAction(
            session_id="s1",
            task_id="t1",
            tenant_id="default",
            recovery_pattern=RecoveryPattern.ADAPT,
        )

        strategies = ["strategy_a", "strategy_b", "strategy_c"]
        result = self.engine.execute_adapt(
            action,
            alternative_strategies=strategies,
            current_attempt=1,
        )

        assert result["success"] is True
        assert result["pattern"] == "adapt"
        assert result["strategy_to_try"] == "strategy_a"

    def test_execute_adapt_tries_alternatives(self):
        """Test ADAPT tries each alternative strategy."""
        action = RecoveryAction(
            session_id="s1",
            task_id="t1",
            tenant_id="default",
            recovery_pattern=RecoveryPattern.ADAPT,
        )

        strategies = ["strategy_a", "strategy_b"]

        result1 = self.engine.execute_adapt(action, strategies, current_attempt=1)
        assert result1["strategy_to_try"] == "strategy_a"

        result2 = self.engine.execute_adapt(action, strategies, current_attempt=2)
        assert result2["strategy_to_try"] == "strategy_b"

    def test_execute_backtrack(self):
        """Test BACKTRACK recovery pattern execution."""
        action = RecoveryAction(
            session_id="s1",
            task_id="t1",
            tenant_id="default",
            recovery_pattern=RecoveryPattern.BACKTRACK,
            source_checkpoint_id="cp-001",
        )

        result = self.engine.execute_backtrack(action, current_attempt=1)

        assert result["success"] is True
        assert result["pattern"] == "backtrack"
        assert result["checkpoint_restored"] == "cp-001"

    def test_execute_pause(self):
        """Test PAUSE recovery pattern execution."""
        action = RecoveryAction(
            session_id="s1",
            task_id="t1",
            tenant_id="default",
            recovery_pattern=RecoveryPattern.PAUSE,
        )

        result = self.engine.execute_pause(action, reason="Daily quota exceeded")

        assert result["success"] is True
        assert result["pattern"] == "pause"
        assert "Daily quota exceeded" in result["paused_reason"]

    # ========================================================================
    # Test 6: Recovery History Tracking
    # ========================================================================

    def test_recovery_history_tracking(self):
        """Test that recovery actions are tracked in session history."""
        action1 = self.engine.initiate_recovery(
            session_id="s1",
            task_id="t1",
            tenant_id="default",
            error_type=RecoveryErrorType.TIMEOUT,
        )

        action2 = self.engine.initiate_recovery(
            session_id="s1",
            task_id="t1",
            tenant_id="default",
            error_type=RecoveryErrorType.STRATEGY_FAILED,
        )

        history = self.engine.get_recovery_history("s1")

        assert len(history) == 2
        assert history[0].action_id == action1.action_id
        assert history[1].action_id == action2.action_id

    def test_recovery_success_rate(self):
        """Test recovery success rate calculation."""
        # Create 3 recovery actions
        action1 = self.engine.initiate_recovery(
            session_id="s1",
            task_id="t1",
            tenant_id="default",
            error_type=RecoveryErrorType.TIMEOUT,
        )

        action2 = self.engine.initiate_recovery(
            session_id="s1",
            task_id="t1",
            tenant_id="default",
            error_type=RecoveryErrorType.STRATEGY_FAILED,
        )

        # Mark 2 as successful
        self.engine.mark_recovery_success(action1.action_id)
        self.engine.mark_recovery_success(action2.action_id)

        # Success rate should be 2/2 = 1.0
        success_rate = self.engine.recovery_success_rate("s1")
        assert success_rate == 1.0

    # ========================================================================
    # Test 7: Mark Recovery Success
    # ========================================================================

    def test_mark_recovery_success(self):
        """Test marking a recovery as successful."""
        action = self.engine.initiate_recovery(
            session_id="s1",
            task_id="t1",
            tenant_id="default",
            error_type=RecoveryErrorType.TIMEOUT,
        )

        updated_action = self.engine.mark_recovery_success(action.action_id)

        assert updated_action is not None
        assert updated_action.success is True
        assert updated_action.attempt_count == 1

    # ========================================================================
    # Test 8: Audit Logging
    # ========================================================================

    def test_recovery_action_audit_logging(self):
        """Test that recovery actions are audit-logged."""
        action = self.engine.initiate_recovery(
            session_id="s1",
            task_id="t1",
            tenant_id="default",
            error_type=RecoveryErrorType.TIMEOUT,
        )

        # Verify audit event was published
        assert len(self.hub.published_events) > 0
        audit_events = [
            e for e in self.hub.published_events if e[0] == "session.recovery_action"
        ]
        assert len(audit_events) > 0

    # ========================================================================
    # Test 9-10: Edge Cases
    # ========================================================================

    def test_backtrack_without_checkpoint(self):
        """Test BACKTRACK when no checkpoint provided."""
        action = RecoveryAction(
            session_id="s1",
            task_id="t1",
            tenant_id="default",
            recovery_pattern=RecoveryPattern.BACKTRACK,
            source_checkpoint_id=None,
        )

        result = self.engine.execute_backtrack(action)

        assert result["success"] is False
        assert "No checkpoint" in result["reason"]

    def test_recovery_engine_lifecycle(self):
        """Test recovery engine startup and shutdown."""
        engine = RecoveryEngine()

        class MockHub:
            pass

        hub = MockHub()
        engine.startup(hub)
        assert engine.hub == hub

        engine.shutdown()
        assert len(engine.recovery_actions) == 0


class TestRecoveryErrorTypeEnum:
    """Test RecoveryErrorType enum."""

    def test_error_type_values(self):
        """Test error type enum values."""
        assert RecoveryErrorType.TIMEOUT.value == "timeout"
        assert RecoveryErrorType.STRATEGY_FAILED.value == "strategy_failed"
        assert RecoveryErrorType.VALIDATION_ERROR.value == "validation_error"
        assert RecoveryErrorType.QUOTA_EXCEEDED.value == "quota_exceeded"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

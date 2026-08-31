"""Tests for SessionLifecycleManager (k=1).

10 unit + integration tests covering:
- 6 split triggers (phase exit, context limit, token burn, explicit milestone, iteration cap, stall)
- Session creation and lifecycle
- Metrics tracking
- Audit logging
"""

import pytest
from datetime import datetime, timedelta
from core.session_manager.lifecycle import (
    SessionLifecycleManager,
    SessionMetadata,
    SessionMetrics,
    SessionSplitTrigger,
    SplitTriggerEvent,
)


class MockHub:
    """Mock SubsystemHub for testing."""

    def __init__(self):
        self.published_events = []

    def subscribe(self, event_name, handler):
        pass

    def publish_event(self, event_name, event_data):
        self.published_events.append((event_name, event_data))


class TestSessionLifecycleManager:
    """Test SessionLifecycleManager."""

    def setup_method(self):
        """Setup test fixtures."""
        self.hub = MockHub()
        self.manager = SessionLifecycleManager(hub=self.hub)
        self.manager.startup(self.hub)

    def teardown_method(self):
        """Cleanup."""
        self.manager.shutdown()

    # ========================================================================
    # Test 1: Session Creation
    # ========================================================================

    def test_create_session_basic(self):
        """Test basic session creation."""
        metadata = self.manager.create_session(
            task_id="audit-task-001",
            phase="planning",
            tenant_id="default",
            user_id="user123",
        )

        assert metadata.task_id == "audit-task-001"
        assert metadata.phase == "planning"
        assert metadata.tenant_id == "default"
        assert metadata.user_id == "user123"
        assert metadata.session_id in self.manager.active_sessions

    def test_create_session_with_parent(self):
        """Test nested session creation (parent session)."""
        parent_meta = self.manager.create_session(
            task_id="parent-task",
            phase="planning",
            tenant_id="default",
        )

        child_meta = self.manager.create_session(
            task_id="child-task",
            phase="execution",
            tenant_id="default",
            parent_session_id=parent_meta.session_id,
        )

        assert child_meta.parent_session_id == parent_meta.session_id

    # ========================================================================
    # Test 2-7: Split Triggers (6 triggers)
    # ========================================================================

    def test_trigger_context_limit(self):
        """Test Trigger 2: Context Limit (≥85% of max)."""
        session = self.manager.create_session(
            task_id="task-001",
            phase="execution",
            tenant_id="default",
        )

        # Update context to 85% of max
        max_tokens = 200000
        self.manager.update_context_size(session.session_id, int(max_tokens * 0.85))

        trigger = self.manager.check_split_triggers(
            session.session_id, max_context_tokens=max_tokens
        )

        assert trigger is not None
        assert trigger.trigger_type == SessionSplitTrigger.CONTEXT_LIMIT
        assert "85" in trigger.reason or "Context" in trigger.reason

    def test_trigger_token_burn(self):
        """Test Trigger 3: Token Burn (≥95% of daily budget)."""
        session = self.manager.create_session(
            task_id="task-001",
            phase="execution",
            tenant_id="default",
        )

        # Update token budget to 95%
        self.manager.update_token_budget(session.session_id, 0.95)

        trigger = self.manager.check_split_triggers(session.session_id)

        assert trigger is not None
        assert trigger.trigger_type == SessionSplitTrigger.TOKEN_BURN

    def test_trigger_iteration_cap(self):
        """Test Trigger 5: Iteration Cap (≥50 iterations)."""
        session = self.manager.create_session(
            task_id="task-001",
            phase="execution",
            tenant_id="default",
        )

        # Simulate 50 iterations
        for _ in range(50):
            self.manager.record_iteration(session.session_id)

        trigger = self.manager.check_split_triggers(session.session_id)

        assert trigger is not None
        assert trigger.trigger_type == SessionSplitTrigger.ITERATION_CAP
        assert trigger.metadata["iterations"] == 50

    def test_trigger_stall_detected(self):
        """Test Trigger 6: Stall Detected (no progress ≥30 min)."""
        session = self.manager.create_session(
            task_id="task-001",
            phase="execution",
            tenant_id="default",
        )

        metrics = self.manager.session_metrics[session.session_id]

        # Simulate stall by setting last_progress_at to 31 minutes ago
        metrics.last_progress_at = datetime.utcnow() - timedelta(minutes=31)

        trigger = self.manager.check_split_triggers(session.session_id)

        assert trigger is not None
        assert trigger.trigger_type == SessionSplitTrigger.STALL_DETECTED

    def test_trigger_phase_exit_signal(self):
        """Test Trigger 1: Phase Exit (explicit signal)."""
        session = self.manager.create_session(
            task_id="task-001",
            phase="planning",
            tenant_id="default",
        )

        trigger = self.manager.signal_phase_exit(session.session_id)

        assert trigger is not None
        assert trigger.trigger_type == SessionSplitTrigger.PHASE_EXIT

    def test_trigger_explicit_milestone(self):
        """Test Trigger 4: Explicit Milestone (with auto_split)."""
        session = self.manager.create_session(
            task_id="task-001",
            phase="execution",
            tenant_id="default",
        )

        trigger = self.manager.signal_milestone(
            session.session_id,
            milestone_name="Phase complete",
            auto_split=True,
        )

        assert trigger is not None
        assert trigger.trigger_type == SessionSplitTrigger.EXPLICIT_MILESTONE
        assert trigger.metadata["milestone_name"] == "Phase complete"

    # ========================================================================
    # Test 8: Metrics Tracking
    # ========================================================================

    def test_record_iteration_tracking(self):
        """Test iteration counting."""
        session = self.manager.create_session(
            task_id="task-001",
            phase="execution",
            tenant_id="default",
        )

        for i in range(1, 6):
            self.manager.record_iteration(session.session_id)
            metrics = self.manager.session_metrics[session.session_id]
            assert metrics.iterations == i

    def test_context_size_tracking(self):
        """Test context size tracking."""
        session = self.manager.create_session(
            task_id="task-001",
            phase="execution",
            tenant_id="default",
        )

        self.manager.update_context_size(session.session_id, 5000)
        metrics = self.manager.session_metrics[session.session_id]
        assert metrics.context_size_tokens == 5000

    # ========================================================================
    # Test 9: Audit Logging
    # ========================================================================

    def test_split_trigger_audit_logging(self):
        """Test that split triggers are audit-logged."""
        session = self.manager.create_session(
            task_id="task-001",
            phase="execution",
            tenant_id="default",
        )

        # Trigger a split
        self.manager.update_token_budget(session.session_id, 0.96)
        trigger = self.manager.check_split_triggers(session.session_id)

        assert trigger is not None

        # Verify audit event was published
        assert len(self.hub.published_events) > 0
        audit_events = [
            e
            for e in self.hub.published_events
            if e[0] == "session.split_trigger"
        ]
        assert len(audit_events) > 0

    # ========================================================================
    # Test 10: Session Close
    # ========================================================================

    def test_close_session(self):
        """Test session closure."""
        session = self.manager.create_session(
            task_id="task-001",
            phase="execution",
            tenant_id="default",
        )

        self.manager.close_session(session.session_id)

        assert session.session_id not in self.manager.active_sessions
        assert session.session_id not in self.manager.session_metrics


class TestSessionMetadata:
    """Test SessionMetadata validation."""

    def test_metadata_requires_task_id(self):
        """Test that SessionMetadata requires task_id."""
        with pytest.raises(ValueError, match="task_id"):
            SessionMetadata(
                session_id="s1",
                task_id="",
                phase="execution",
                started_at=datetime.utcnow(),
                tenant_id="default",
            )

    def test_metadata_requires_tenant_id(self):
        """Test that SessionMetadata requires tenant_id."""
        with pytest.raises(ValueError, match="tenant_id"):
            SessionMetadata(
                session_id="s1",
                task_id="task-1",
                phase="execution",
                started_at=datetime.utcnow(),
                tenant_id="",
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

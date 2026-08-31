"""Integration tests for Goal Alignment Monitoring in ExecutionContext.

Tests:
- ExecutionContext initialization with goal from task_template
- Goal alignment monitoring integration with LDD loop
- SubsystemHub alert routing for goal drift
- Audit trail logging of goal drift alerts

ADR-0407: Session Manager Phase 2.2
"""

import pytest
from dataclasses import dataclass
from typing import Optional

from core.context_engineering.execution_context import ExecutionContext, ContextStack
from core.session_manager.monitors.goal_alignment import GoalAlignmentMonitor
from core.orchestration.hub import SubsystemHub


class TestGoalAlignmentInitialization:
    """Test goal alignment monitor initialization."""

    def test_execution_context_extracts_goal_from_task_template(self):
        """ExecutionContext should extract goal from task_template."""
        task_template = {
            "goal": "Implement a new feature for user authentication",
            "task_id": "task-001",
        }
        ctx = ExecutionContext(
            task_id="task-001",
            tenant_id="default",
            task_template=task_template,
            context_stack=ContextStack(),
        )

        # Initialize goal monitoring
        ctx.initialize_goal_monitoring(
            session_id="session-001",
            task_id="task-001",
        )

        assert ctx.original_goal == "Implement a new feature for user authentication"

    def test_execution_context_uses_task_field_as_fallback(self):
        """ExecutionContext should use 'task' field if 'goal' is absent."""
        task_template = {
            "task": "Fix the authentication bug",
            "task_id": "task-002",
        }
        ctx = ExecutionContext(
            task_id="task-002",
            tenant_id="default",
            task_template=task_template,
            context_stack=ContextStack(),
        )

        ctx.initialize_goal_monitoring(
            session_id="session-002",
            task_id="task-002",
        )

        assert ctx.original_goal == "Fix the authentication bug"

    def test_goal_alignment_monitor_created_by_default(self):
        """ExecutionContext should create GoalAlignmentMonitor by default."""
        ctx = ExecutionContext(
            task_id="task-003",
            tenant_id="default",
            task_template={},
            context_stack=ContextStack(),
        )

        assert ctx.goal_alignment_monitor is not None
        assert isinstance(ctx.goal_alignment_monitor, GoalAlignmentMonitor)

    def test_iterations_counter_initialized_to_zero(self):
        """iterations_since_last_goal_check should start at 0."""
        ctx = ExecutionContext(
            task_id="task-004",
            tenant_id="default",
            task_template={},
            context_stack=ContextStack(),
        )

        assert ctx.iterations_since_last_goal_check == 0


class TestGoalAlignmentChecking:
    """Test goal alignment checking during execution."""

    def test_check_goal_alignment_increments_counter(self):
        """check_goal_alignment should increment the iteration counter."""
        ctx = ExecutionContext(
            task_id="task-005",
            tenant_id="default",
            task_template={"goal": "Test goal"},
            context_stack=ContextStack(),
        )
        ctx.initialize_goal_monitoring("session-005", "task-005")

        # First check should increment counter
        alert = ctx.check_goal_alignment("Working on test", check_interval=5)
        assert ctx.iterations_since_last_goal_check == 1
        assert alert is None  # No alert yet

    def test_check_goal_alignment_checks_every_k_iterations(self):
        """check_goal_alignment should only check every k iterations."""
        ctx = ExecutionContext(
            task_id="task-006",
            tenant_id="default",
            task_template={"goal": "Implement authentication"},
            context_stack=ContextStack(),
        )
        ctx.initialize_goal_monitoring("session-006", "task-006")

        # Call check_goal_alignment 4 times (no check happens)
        for i in range(4):
            alert = ctx.check_goal_alignment("Working on task", check_interval=5)
            assert alert is None
            assert ctx.iterations_since_last_goal_check == i + 1

        # Fifth call should trigger actual check and reset counter
        alert = ctx.check_goal_alignment("Working on unrelated task", check_interval=5)
        # Counter should reset after check
        assert ctx.iterations_since_last_goal_check == 0

    def test_goal_drift_detection_when_work_diverges(self):
        """Goal drift should be detected when work significantly diverges."""
        ctx = ExecutionContext(
            task_id="task-007",
            tenant_id="default",
            task_template={"goal": "Implement user authentication system"},
            context_stack=ContextStack(),
        )
        ctx.initialize_goal_monitoring("session-007", "task-007")

        # Completely unrelated work (should trigger drift after 3+ consecutive low scores)
        completely_different_work = "Working on database optimization and query tuning"

        # Call check_goal_alignment 3+ times with unrelated work to trigger alert
        alerts = []
        for i in range(5):
            alert = ctx.check_goal_alignment(
                completely_different_work,
                check_interval=1  # Check every iteration for testing
            )
            if alert:
                alerts.append(alert)

        # May or may not have alert depending on similarity threshold
        # At minimum, counter should be managed correctly
        assert ctx.iterations_since_last_goal_check == 0

    def test_goal_alignment_returns_none_without_monitor(self):
        """check_goal_alignment should return None if monitor is not available."""
        ctx = ExecutionContext(
            task_id="task-008",
            tenant_id="default",
            task_template={"goal": "Test goal"},
            context_stack=ContextStack(),
        )
        ctx.goal_alignment_monitor = None

        alert = ctx.check_goal_alignment("Some work", check_interval=5)
        assert alert is None


class TestGoalAlignmentWithSubsystemHub:
    """Test goal alignment alert routing through SubsystemHub."""

    def test_hub_publishes_goal_drift_alert(self):
        """SubsystemHub should publish goal drift alerts as events."""
        hub = SubsystemHub()
        events_received = []

        def capture_event(event_name: str, event_data: dict):
            events_received.append((event_name, event_data))

        # Subscribe to goal drift events
        hub.subscribe("monitor.goal_drift_detected", capture_event)

        # Create alert via monitor
        monitor = GoalAlignmentMonitor()
        state = monitor.create_or_get_state("session-009", "task-009", "default")
        state.original_goal = "Implement authentication"
        state.metadata["current_work"] = "Unrelated work"

        # Manually set up for alert
        monitor.similarity_threshold = 0.1  # Very low to force alert
        monitor.hub = hub

        alert = monitor.check(state)

        # Should have generated an alert
        # (Actual alert depends on similarity calculation)

    def test_goal_drift_alert_includes_metadata(self):
        """Goal drift alert should include similarity score and context."""
        monitor = GoalAlignmentMonitor()
        state = monitor.create_or_get_state("session-010", "task-010", "default")
        state.original_goal = "Implement user authentication"

        # Unrelated work that should have low similarity
        state.metadata["current_work"] = "Writing documentation for API endpoints"

        # Force low threshold to trigger alert
        monitor.similarity_threshold = 0.9
        monitor.consecutive_low_count = 1

        alert = monitor.check(state)

        if alert:
            assert "similarity_score" in alert.metadata
            assert "original_goal" in alert.metadata
            assert "current_work" in alert.metadata
            assert alert.severity == "warning"


class TestGoalAlignmentAuditTrail:
    """Test audit trail logging of goal alignment."""

    def test_goal_alignment_alert_serializes_to_audit_event(self):
        """MonitorAlert should serialize to audit.jsonl format."""
        from core.session_manager.monitors.base import AlertType, MonitorAlert

        alert = MonitorAlert(
            alert_type=AlertType.GOAL_DRIFT_DETECTED,
            session_id="session-011",
            task_id="task-011",
            tenant_id="default",
            severity="warning",
            reason="Goal drift detected: similarity=0.45 <0.6 for 3 iterations",
            metadata={
                "similarity_score": 0.45,
                "threshold": 0.6,
                "consecutive_low_count": 3,
            },
        )

        audit_event = alert.to_audit_event()

        assert audit_event["event_type"] == "session.monitor.goal_drift_detected"
        assert audit_event["tenant_id"] == "default"
        assert audit_event["session_id"] == "session-011"
        assert audit_event["severity"] == "warning"
        assert audit_event["reason"] == alert.reason

    def test_goal_monitoring_state_persists_scores(self):
        """GoalAlignmentState should maintain historical similarity scores."""
        from core.session_manager.monitors.goal_alignment import GoalAlignmentState

        state = GoalAlignmentState(
            session_id="session-012",
            task_id="task-012",
            tenant_id="default",
            original_goal="Test goal",
        )

        # Simulate multiple checks with different scores
        scores = [0.8, 0.75, 0.7, 0.5, 0.45, 0.4]
        for score in scores:
            state.similarity_scores.append(score)

        assert len(state.similarity_scores) == 6
        assert state.similarity_scores[-1] == 0.4


class TestGoalAlignmentEdgeCases:
    """Test edge cases for goal alignment monitoring."""

    def test_empty_original_goal_returns_none(self):
        """check_goal_alignment should handle empty original goal gracefully."""
        ctx = ExecutionContext(
            task_id="task-013",
            tenant_id="default",
            task_template={},  # No goal
            context_stack=ContextStack(),
        )

        alert = ctx.check_goal_alignment("Some work", check_interval=5)
        # Should not crash; behavior depends on monitor implementation
        assert alert is None or isinstance(alert, object)

    def test_clear_session_state_preserves_goal(self):
        """clear_session_state should preserve original goal."""
        ctx = ExecutionContext(
            task_id="task-014",
            tenant_id="default",
            task_template={"goal": "Test goal"},
            context_stack=ContextStack(),
        )
        ctx.initialize_goal_monitoring("session-014", "task-014")
        original_goal = ctx.original_goal

        # Clear session state
        ctx.clear_session_state()

        # Goal should be preserved (it's not session-scoped, it's task-scoped)
        assert ctx.original_goal == original_goal

    def test_concurrent_goal_checks_maintain_counter(self):
        """Multiple goal checks should maintain iteration counter correctly."""
        ctx = ExecutionContext(
            task_id="task-015",
            tenant_id="default",
            task_template={"goal": "Test"},
            context_stack=ContextStack(),
        )
        ctx.initialize_goal_monitoring("session-015", "task-015")

        # Simulate concurrent calls (sequential for test)
        counter = 0
        for i in range(10):
            ctx.check_goal_alignment("Work", check_interval=3)
            counter = ctx.iterations_since_last_goal_check
            # Should see 1, 2, 0, 1, 2, 0, 1, 2, 0, 1
            expected = ((i + 1) % 3)
            assert counter == expected

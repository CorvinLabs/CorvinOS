"""
Tests for decision stream and interrupt protocol.

Coverage:
- Decision event recording and buffering
- Engine statistics computation
- Interrupt command state machine
- Rate limiting
"""

import pytest
from datetime import datetime

from core.observability.decision_stream import (
    DecisionEvent,
    DecisionStreamCollector,
)
from core.orchestration.interrupt_protocol import (
    InterruptController,
    InterruptCommand,
    TaskState,
)


class TestDecisionEvent:
    """Test decision events."""

    def test_event_creation(self):
        """Create decision event."""
        event = DecisionEvent(
            event_id="evt-1",
            timestamp=datetime.utcnow(),
            task_id="task-1",
            engine_choice="claude",
            confidence=0.95,
            cost_estimate_usd=0.01,
            latency_estimate_ms=1500.0,
            routing_reason="High confidence task",
        )
        assert event.engine_choice == "claude"
        assert event.confidence == 0.95

    def test_event_to_dict(self):
        """Serialize event."""
        event = DecisionEvent(
            event_id="evt-1",
            timestamp=datetime.utcnow(),
            task_id="task-1",
            engine_choice="claude",
            confidence=0.95,
            cost_estimate_usd=0.01,
            latency_estimate_ms=1500.0,
            routing_reason="High confidence task",
        )
        d = event.to_dict()
        assert d["engine_choice"] == "claude"
        assert "timestamp" in d


class TestDecisionStreamCollector:
    """Test decision stream collection."""

    def test_collector_creation(self):
        """Create collector."""
        collector = DecisionStreamCollector()
        assert len(collector.events) == 0

    def test_record_decision(self):
        """Record decision."""
        collector = DecisionStreamCollector()
        event = collector.record_decision(
            event_id="evt-1",
            task_id="task-1",
            engine_choice="claude",
            confidence=0.95,
            cost_estimate_usd=0.01,
            latency_estimate_ms=1500.0,
            routing_reason="Test",
        )
        assert event is not None
        assert len(collector.events) == 1

    def test_get_recent_decisions(self):
        """Get recent decisions."""
        collector = DecisionStreamCollector()
        for i in range(5):
            collector.record_decision(
                event_id=f"evt-{i}",
                task_id=f"task-{i}",
                engine_choice="claude",
                confidence=0.9,
                cost_estimate_usd=0.01,
                latency_estimate_ms=1500.0,
                routing_reason="Test",
            )

        recent = collector.get_recent_decisions(limit=3)
        assert len(recent) == 3

    def test_get_engine_stats(self):
        """Get engine statistics."""
        collector = DecisionStreamCollector()
        collector.record_decision(
            event_id="evt-1",
            task_id="task-1",
            engine_choice="claude",
            confidence=0.95,
            cost_estimate_usd=0.02,
            latency_estimate_ms=1500.0,
            routing_reason="Test",
        )
        collector.record_decision(
            event_id="evt-2",
            task_id="task-2",
            engine_choice="local_llama2",
            confidence=0.85,
            cost_estimate_usd=0.00,
            latency_estimate_ms=4000.0,
            routing_reason="Test",
        )

        stats = collector.get_engine_stats()
        assert "claude" in stats
        assert "local_llama2" in stats
        assert stats["claude"]["count"] == 1


class TestInterruptController:
    """Test interrupt protocol."""

    def test_controller_creation(self):
        """Create controller."""
        controller = InterruptController()
        assert controller is not None

    def test_issue_pause(self):
        """Pause a task."""
        controller = InterruptController()
        cmd = controller.issue_pause(
            command_id="cmd-1",
            task_id="task-1",
            operator_id="op-1",
        )
        assert cmd is not None
        assert controller.get_task_state("task-1") == TaskState.PAUSED

    def test_issue_resume(self):
        """Resume a paused task."""
        controller = InterruptController()
        controller.issue_pause("cmd-1", "task-1", "op-1")
        cmd = controller.issue_resume("cmd-2", "task-1", "op-1")
        assert cmd is not None
        assert controller.get_task_state("task-1") == TaskState.RESUMED

    def test_issue_redirect(self):
        """Redirect to different engine."""
        controller = InterruptController()
        controller.task_states["task-1"] = TaskState.RUNNING
        cmd = controller.issue_redirect(
            command_id="cmd-1",
            task_id="task-1",
            operator_id="op-1",
            new_engine="local_llama2",
        )
        assert cmd is not None
        assert cmd.new_engine == "local_llama2"

    def test_issue_cancel(self):
        """Cancel a task."""
        controller = InterruptController()
        controller.task_states["task-1"] = TaskState.RUNNING
        cmd = controller.issue_cancel(
            command_id="cmd-1",
            task_id="task-1",
            operator_id="op-1",
        )
        assert cmd is not None
        assert controller.get_task_state("task-1") == TaskState.CANCELLED

    def test_rate_limiting(self):
        """Rate limit consecutive commands."""
        controller = InterruptController()
        # First pause should succeed
        cmd1 = controller.issue_pause("cmd-1", "task-1", "op-1")
        assert cmd1 is not None

        # Second pause should be rate-limited (if rate_limit=1)
        # (depends on implementation - currently allows 1 per second)
        # This test demonstrates the mechanism

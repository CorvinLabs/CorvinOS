"""E2E tests: SessionManager EventBus integration (S3.2).

ADR-0348: Event Bus Pattern
ADR-0541: Session Bridging EventStore Protocol
"""

import pytest
from unittest.mock import Mock, MagicMock, call
from core.session_manager.eventbus_integration import (
    SessionManagerEventBusAdapter,
    get_eventbus_adapter,
    set_eventbus_hub,
)


class TestSessionManagerEventBusIntegration:
    """Test EventBus wiring for SessionManager S3.2."""

    def test_adapter_publishes_split_triggered(self):
        """Test: publish_split_triggered sends event to hub."""
        mock_hub = Mock()
        adapter = SessionManagerEventBusAdapter(hub=mock_hub)

        adapter.publish_split_triggered(
            task_id="task-001",
            split_trigger_type="context_window_exceeded",
            turn_number=45,
            checkpoint_id="ckpt-789",
        )

        mock_hub.publish_event.assert_called_once()
        call_args = mock_hub.publish_event.call_args
        assert call_args[0][0] == "session_split_triggered"
        assert call_args[0][1]["task_id"] == "task-001"
        assert call_args[0][1]["split_trigger_type"] == "context_window_exceeded"
        assert call_args[0][1]["turn_number"] == 45
        assert call_args[0][1]["checkpoint_id"] == "ckpt-789"
        assert "timestamp" in call_args[0][1]

    def test_adapter_publishes_context_reduced(self):
        """Test: publish_context_reduced sends event with reduction metrics."""
        mock_hub = Mock()
        adapter = SessionManagerEventBusAdapter(hub=mock_hub)

        adapter.publish_context_reduced(
            task_id="task-001",
            original_tokens=100000,
            reduced_tokens=9000,
            tier_summary={"system": 200, "relevant": 8000, "historical": 800},
        )

        mock_hub.publish_event.assert_called_once()
        call_args = mock_hub.publish_event.call_args
        assert call_args[0][0] == "context_reduced"
        assert call_args[0][1]["task_id"] == "task-001"
        assert call_args[0][1]["original_context_tokens"] == 100000
        assert call_args[0][1]["reduced_context_tokens"] == 9000
        assert call_args[0][1]["reduction_ratio"] == pytest.approx(0.91, abs=0.01)
        assert call_args[0][1]["tier_summary"]["relevant"] == 8000

    def test_adapter_publishes_session_recovered(self):
        """Test: publish_session_recovered sends event with recovery result."""
        mock_hub = Mock()
        adapter = SessionManagerEventBusAdapter(hub=mock_hub)

        adapter.publish_session_recovered(
            task_id="task-001",
            recovery_pattern="replay",
            recovered_turn=42,
            success=True,
            reason=None,
        )

        mock_hub.publish_event.assert_called_once()
        call_args = mock_hub.publish_event.call_args
        assert call_args[0][0] == "session_recovered"
        assert call_args[0][1]["task_id"] == "task-001"
        assert call_args[0][1]["recovery_pattern"] == "replay"
        assert call_args[0][1]["recovered_turn"] == 42
        assert call_args[0][1]["success"] is True

    def test_adapter_handles_missing_hub(self):
        """Test: adapter gracefully skips publish if hub not set."""
        adapter = SessionManagerEventBusAdapter(hub=None)

        # Should not raise, just log
        adapter.publish_split_triggered(
            task_id="task-001",
            split_trigger_type="cost_limit_reached",
            turn_number=20,
            checkpoint_id="ckpt-123",
        )
        # No exception = success

    def test_singleton_adapter_injection(self):
        """Test: set_eventbus_hub injects hub into global adapter."""
        mock_hub = Mock()
        set_eventbus_hub(mock_hub)

        adapter = get_eventbus_adapter()
        assert adapter.hub is mock_hub

    def test_event_ordering_maintained(self):
        """Test: multiple events published in order."""
        mock_hub = Mock()
        adapter = SessionManagerEventBusAdapter(hub=mock_hub)

        # Simulate a real session split workflow:
        # 1. Split detected
        adapter.publish_split_triggered(
            task_id="task-001",
            split_trigger_type="context_window_exceeded",
            turn_number=50,
            checkpoint_id="ckpt-123",
        )

        # 2. Context reduced
        adapter.publish_context_reduced(
            task_id="task-001",
            original_tokens=200000,
            reduced_tokens=18000,
            tier_summary={"system": 200, "relevant": 17000, "historical": 800},
        )

        # 3. Session recovered
        adapter.publish_session_recovered(
            task_id="task-001",
            recovery_pattern="adapt",
            recovered_turn=51,
            success=True,
        )

        assert mock_hub.publish_event.call_count == 3
        calls = mock_hub.publish_event.call_args_list
        assert calls[0][0][0] == "session_split_triggered"
        assert calls[1][0][0] == "context_reduced"
        assert calls[2][0][0] == "session_recovered"

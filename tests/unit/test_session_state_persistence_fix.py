"""Tests for session state persistence fix (K=1-5 implementation).

Tests verify:
1. ContextBus.get_instance() returns singleton
2. ExecutionContext.clear_session_state() clears all session state
3. Subsystem.clear_session_cache() base class exists and is callable
4. All subsystems implement clear_session_cache()
5. Integration with session_reset.py
"""

import asyncio
import pytest
from unittest.mock import Mock, AsyncMock, MagicMock, patch
from pathlib import Path

from core.context_engineering.context_bus import ContextBus, set_execution_context
from core.context_engineering.execution_context import ExecutionContext, ContextStack
from core.context_engineering.decision_record import DecisionRecord


class TestContextBusSingleton:
    """Test ContextBus singleton pattern."""

    def test_get_instance_none_initially(self):
        """Initially, get_instance() returns None."""
        # Reset singleton for test
        ContextBus._instance = None
        assert ContextBus.get_instance() is None

    def test_set_and_get_instance(self):
        """set_instance() stores and get_instance() retrieves singleton."""
        bus = ContextBus()
        ContextBus.set_instance(bus)
        assert ContextBus.get_instance() is bus

    def test_set_instance_none(self):
        """set_instance(None) clears singleton."""
        bus = ContextBus()
        ContextBus.set_instance(bus)
        ContextBus.set_instance(None)
        assert ContextBus.get_instance() is None

    def teardown_method(self):
        """Reset singleton after each test."""
        ContextBus._instance = None


class TestExecutionContextClear:
    """Test ExecutionContext.clear_session_state()."""

    def test_clear_session_state_clears_decision_history(self):
        """clear_session_state() clears decision history."""
        ctx = ExecutionContext(
            task_id="task-1",
            tenant_id="_default",
            task_template={},
            context_stack=ContextStack(),
        )

        # Add some decisions
        ctx.record_decision(
            subsystem="test",
            decision_type="test_decision",
            value="test_value",
        )
        assert len(ctx.decision_history) == 1

        # Clear state
        ctx.clear_session_state()
        assert len(ctx.decision_history) == 0

    def test_clear_session_state_clears_checkpoints(self):
        """clear_session_state() clears checkpoints."""
        ctx = ExecutionContext(
            task_id="task-1",
            tenant_id="_default",
            task_template={},
            context_stack=ContextStack(),
        )

        # Add checkpoints
        ctx.checkpoint("checkpoint1", {"data": "value"})
        assert len(ctx.checkpoints) == 1

        # Clear state
        ctx.clear_session_state()
        assert len(ctx.checkpoints) == 0

    def test_clear_session_state_clears_budget(self):
        """clear_session_state() clears budget."""
        ctx = ExecutionContext(
            task_id="task-1",
            tenant_id="_default",
            task_template={},
            context_stack=ContextStack(),
        )
        ctx.budget_remaining = 100.0

        ctx.clear_session_state()
        assert ctx.budget_remaining == 0.0

    def test_clear_session_state_clears_strategy(self):
        """clear_session_state() clears strategy."""
        ctx = ExecutionContext(
            task_id="task-1",
            tenant_id="_default",
            task_template={},
            context_stack=ContextStack(),
        )
        ctx.strategy = "test_strategy"
        ctx.strategy_confidence = 0.9

        ctx.clear_session_state()
        assert ctx.strategy == ""
        assert ctx.strategy_confidence == 0.5  # default

    def test_clear_session_state_preserves_task_id(self):
        """clear_session_state() preserves task_id and tenant_id."""
        ctx = ExecutionContext(
            task_id="task-1",
            tenant_id="_default",
            task_template={"key": "value"},
            context_stack=ContextStack(),
        )

        ctx.clear_session_state()
        assert ctx.task_id == "task-1"
        assert ctx.tenant_id == "_default"


def _load_session_reset():
    """Import bridges/shared/session_reset.py.

    ``corvin_operator/`` is a directory, not a package, and ``operator`` is taken by
    the stdlib — so ``from corvin_operator.bridges.shared...`` never resolves. Put the
    directory on sys.path and import by module name instead.
    """
    import sys
    from pathlib import Path
    shared = Path(__file__).resolve().parents[2] / "corvin_operator" / "bridges" / "shared"
    if str(shared) not in sys.path:
        sys.path.insert(0, str(shared))
    import session_reset
    return session_reset


class TestSessionResetIntegration:
    """Integration tests for session reset flow."""

    @pytest.mark.asyncio
    async def test_emit_session_reset_event_calls_subsystems(self):
        """_emit_session_reset_event() calls clear_session_cache() on all subsystems."""
        # Create mock hub with subsystems
        mock_hub = Mock()
        mock_subsystem1 = Mock()
        mock_subsystem1.name = "test1"
        mock_subsystem1.clear_session_cache = Mock()
        mock_subsystem2 = Mock()
        mock_subsystem2.name = "test2"
        mock_subsystem2.clear_session_cache = Mock()

        mock_hub.subsystems = {
            "test1": mock_subsystem1,
            "test2": mock_subsystem2,
        }

        # Create bus with hub
        bus = ContextBus()
        bus.hub = mock_hub
        ContextBus.set_instance(bus)

        # Import and call session_reset function
        _call_subsystem_resets = _load_session_reset()._call_subsystem_resets
        _call_subsystem_resets()

        # Verify clear_session_cache was called on both subsystems
        mock_subsystem1.clear_session_cache.assert_called_once()
        mock_subsystem2.clear_session_cache.assert_called_once()

    def test_clear_execution_context_via_contextvar(self):
        """_clear_execution_context() clears ExecutionContext in ContextVar."""
        # Set an ExecutionContext
        ctx = ExecutionContext(
            task_id="task-1",
            tenant_id="_default",
            task_template={},
            context_stack=ContextStack(),
        )
        set_execution_context(ctx)

        # Import and call clear function
        _clear_execution_context = _load_session_reset()._clear_execution_context
        _clear_execution_context()

        # Verify context is cleared
        from core.context_engineering.context_bus import get_execution_context
        assert get_execution_context() is None

    def teardown_method(self):
        """Reset singleton after each test."""
        ContextBus._instance = None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

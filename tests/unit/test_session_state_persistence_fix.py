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
from core.orchestration.subsystems.base import Subsystem
from core.orchestration.subsystems.loop_engineer import LoopEngineer
from core.orchestration.subsystems.orchestrator import Orchestrator
from core.orchestration.subsystems.strategy_advisor import StrategyAdvisor
from core.orchestration.subsystems.session_lifecycle import SessionLifecycleManager
from core.orchestration.subsystems.learning_engine import LearningEngine
from core.orchestration.subsystems.cost_controller import CostController
from core.orchestration.subsystems.safety_validator import SafetyValidator
from core.orchestration.subsystems.health_monitor import HealthMonitor


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


class TestSubsystemResetBase:
    """Test Subsystem base class clear_session_cache."""

    def test_subsystem_has_clear_session_cache(self):
        """Subsystem base class has clear_session_cache method."""
        assert hasattr(Subsystem, 'clear_session_cache')

    def test_clear_session_cache_default_noop(self):
        """clear_session_cache default implementation does nothing."""
        # Create a concrete subclass for testing
        class TestSubsystem(Subsystem):
            @property
            def name(self):
                return "test"

            @property
            def version(self):
                return "1.0.0"

            def startup(self, hub):
                pass

            async def on_event(self, event_name, event_data):
                pass

            async def handle_request(self, request_type, **kwargs):
                pass

            def shutdown(self):
                pass

        subsystem = TestSubsystem()
        # Should not raise
        subsystem.clear_session_cache()


class TestLoopEngineerReset:
    """Test LoopEngineer.clear_session_cache()."""

    def test_loop_engineer_clears_retry_count(self):
        """clear_session_cache() clears retry_count."""
        engine = LoopEngineer()
        engine.retry_count = {"task1": 3, "task2": 5}

        engine.clear_session_cache()
        assert len(engine.retry_count) == 0

    def test_loop_engineer_clears_strategy_history(self):
        """clear_session_cache() clears strategy_history."""
        engine = LoopEngineer()
        engine.strategy_history = {
            "task1": [{"strategy": "fix1"}, {"strategy": "fix2"}],
        }

        engine.clear_session_cache()
        assert len(engine.strategy_history) == 0

    def test_loop_engineer_reset_nonfatal(self):
        """clear_session_cache() is non-fatal even if clearing fails."""
        engine = LoopEngineer()
        # Mock to raise an exception
        engine.retry_count = None
        # Should not raise
        engine.clear_session_cache()


class TestOrchestratorReset:
    """Test Orchestrator.clear_session_cache()."""

    def test_orchestrator_clears_active_tasks(self):
        """clear_session_cache() clears active_tasks."""
        orch = Orchestrator()
        orch.active_tasks = {"task1": {"status": "running"}, "task2": {"status": "running"}}

        orch.clear_session_cache()
        assert len(orch.active_tasks) == 0

    def test_orchestrator_clears_dependencies(self):
        """clear_session_cache() clears dependencies."""
        orch = Orchestrator()
        orch.dependencies = {"task1": ["dep1", "dep2"]}

        orch.clear_session_cache()
        assert len(orch.dependencies) == 0


class TestStrategyAdvisorReset:
    """Test StrategyAdvisor.clear_session_cache()."""

    def test_strategy_advisor_clears_cache(self):
        """clear_session_cache() clears prediction_cache but preserves scores."""
        advisor = StrategyAdvisor()
        advisor.prediction_cache = {"strategy1": 0.8}
        advisor.strategy_scores = {"strategy1": [1.0, 1.0, 0.0]}

        advisor.clear_session_cache()
        assert len(advisor.prediction_cache) == 0
        # Strategy scores should be preserved
        assert len(advisor.strategy_scores) > 0


class TestSessionLifecycleReset:
    """Test SessionLifecycleManager.clear_session_cache()."""

    def test_session_lifecycle_clears_sessions(self):
        """clear_session_cache() clears sessions dict."""
        manager = SessionLifecycleManager()
        manager.sessions = {"session1": Mock(), "session2": Mock()}

        manager.clear_session_cache()
        assert len(manager.sessions) == 0


class TestLearningEngineReset:
    """Test LearningEngine.clear_session_cache()."""

    def test_learning_engine_preserves_db(self):
        """clear_session_cache() doesn't clear persistent DB."""
        with patch('core.paths.tenant.tenant_learning_dir'):
            engine = LearningEngine()
            engine.strategies_by_error = {"error1": []}
            engine.success_rate = {"strategy1": 0.8}

            engine.clear_session_cache()
            # Data should be preserved
            assert len(engine.strategies_by_error) > 0 or len(engine.success_rate) > 0


class TestCostControllerReset:
    """Test CostController.clear_session_cache()."""

    def test_cost_controller_clears_session_costs(self):
        """clear_session_cache() clears session cost tracking."""
        controller = CostController()
        controller.spent_today = 50.0
        controller.token_count = {"input": 1000, "output": 500}

        controller.clear_session_cache()
        assert controller.spent_today == 0.0
        assert controller.token_count == {"input": 0, "output": 0}


class TestSafetyValidatorReset:
    """Test SafetyValidator.clear_session_cache()."""

    def test_safety_validator_clears_violations(self):
        """clear_session_cache() clears violation tracking."""
        with patch('core.paths.tenant.tenant_audit_file'):
            validator = SafetyValidator()
            validator.violation_count = {"user1": 3}
            validator.consecutive_failures = {"strategy1": 2}
            validator.disabled_strategies = {"strategy1": 123456789.0}

            validator.clear_session_cache()
            assert len(validator.violation_count) == 0
            assert len(validator.consecutive_failures) == 0
            assert len(validator.disabled_strategies) == 0


class TestHealthMonitorReset:
    """Test HealthMonitor.clear_session_cache()."""

    def test_health_monitor_clears_error_counts(self):
        """clear_session_cache() clears error counts."""
        monitor = HealthMonitor()
        monitor.error_count = 5
        monitor.total_count = 10

        monitor.clear_session_cache()
        assert monitor.error_count == 0
        assert monitor.total_count == 0


def _load_session_reset():
    """Import bridges/shared/session_reset.py.

    ``operator/`` is a directory, not a package, and ``operator`` is taken by
    the stdlib — so ``from operator.bridges.shared...`` never resolves. Put the
    directory on sys.path and import by module name instead.
    """
    import sys
    from pathlib import Path
    shared = Path(__file__).resolve().parents[2] / "operator" / "bridges" / "shared"
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

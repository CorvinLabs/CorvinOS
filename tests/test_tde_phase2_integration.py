"""Tests for TDE Phase 2: Integration (10+ tests)."""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "operator" / "orchestration"))

from tde.adaptive_delegation_executor import AdaptiveDelegationExecutor, StepResult
from tde.engine_registry import EngineRegistry, get_registry
from tde.l34_delegation_gate import L34DelegationGate
from tde.loss_profile_tracker import LossProfileTracker
from tde.send_integration import SendIntegration
from initial_analysis import InitialAnalysisRequest, Classification, Entities, GlobalPlan, Step


class TestEngineRegistry:
    """Test EngineRegistry."""

    def test_registry_initialization(self):
        """Registry initializes with 3 engines."""
        registry = EngineRegistry()
        assert len(registry.engines) == 3
        assert "tiered_delegation" in registry.engines
        assert "acs" in registry.engines
        assert "claude_code" in registry.engines

    def test_get_registry_singleton(self):
        """get_registry() returns singleton."""
        reg1 = get_registry()
        reg2 = get_registry()
        assert reg1 is reg2

    def test_register_custom_engine(self):
        """Can register custom engine."""
        registry = EngineRegistry()

        class MockEngine:
            name = "test_engine"

            async def execute(self, plan, context, **kwargs):
                return {"status": "ok"}

        registry.register("test_engine", MockEngine())
        assert "test_engine" in registry.engines

    @pytest.mark.asyncio
    async def test_execute_engine(self):
        """Registry executes real engines; invalid plan yields explicit error."""
        registry = EngineRegistry()
        result = await registry.execute("claude_code", {}, {})
        # Real engine: a bare dict is not a plan → explicit failure, no fake success
        assert result["success"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_execute_unknown_engine_raises(self):
        """Unknown engine name raises."""
        registry = EngineRegistry()
        with pytest.raises(ValueError, match="Unknown engine"):
            await registry.execute("nope", {}, {})


class TestAdaptiveDelegationExecutor:
    """Test AdaptiveDelegationExecutor."""

    @pytest.fixture
    def setup(self):
        """Setup executor."""
        plan = GlobalPlan(
            steps=[
                Step(step=1, action="read", depends_on=[], can_parallelize=[2]),
                Step(step=2, action="analyze", depends_on=[], can_parallelize=[1]),
                Step(step=3, action="write", depends_on=[1, 2], can_parallelize=[]),
            ],
            estimated_duration_s=10,
            estimated_tokens=5000,
        )
        l34_gate = L34DelegationGate()
        loss_tracker = LossProfileTracker()
        executor = AdaptiveDelegationExecutor(plan, l34_gate, loss_tracker)
        return executor, plan

    def test_executor_initialization(self, setup):
        """Executor initializes."""
        executor, plan = setup
        assert executor.plan == plan
        assert executor.l34_gate is not None
        assert executor.loss_tracker is not None

    def test_parallel_batching(self, setup):
        """Steps are grouped into parallel batches."""
        executor, _ = setup
        batches = executor._group_parallel_batches()

        # Step 1 and 2 can be parallel (same batch), step 3 depends on both
        assert len(batches) >= 2
        assert 1 in batches[0] and 2 in batches[0]  # First batch has 1, 2
        assert 3 in batches[-1]  # Last batch has 3

    def test_deterministic_key_generation(self, setup):
        """Idempotency keys are deterministic."""
        executor, _ = setup
        step = Step(step=1, action="test", depends_on=[], can_parallelize=[])
        statement = {"var1": "value1", "var2": "value2"}

        key1 = executor._deterministic_key(step, statement)
        key2 = executor._deterministic_key(step, statement)

        assert key1 == key2  # Deterministic
        assert len(key1) == 16  # 16-char hash

    def test_different_statements_different_keys(self, setup):
        """Different statements produce different keys."""
        executor, _ = setup
        step = Step(step=1, action="test", depends_on=[], can_parallelize=[])

        key1 = executor._deterministic_key(step, {"var": "value1"})
        key2 = executor._deterministic_key(step, {"var": "value2"})

        assert key1 != key2

    @pytest.mark.asyncio
    async def test_execute_local_step(self, setup):
        """Execute local step."""
        executor, _ = setup
        step = Step(step=1, action="test", depends_on=[], can_parallelize=[])

        async def mock_executor(s, ctx):
            return {"result": "ok"}

        result = await executor._execute_local(step, {}, mock_executor)

        assert result.success is True
        assert result.was_delegated is False
        assert result.step_num == 1

    @pytest.mark.asyncio
    async def test_execute_full_plan(self, setup):
        """Execute full plan (mock)."""
        executor, _ = setup

        async def mock_executor(step, ctx):
            return {"step": step.step, "result": "ok"}

        results = await executor.execute({}, None, mock_executor)

        assert len(results) == 3
        assert all(isinstance(r, StepResult) for r in results)
        assert all(r.success for r in results)


class _RecordingEngine:
    """Mock engine that records calls (test seam for SendIntegration)."""

    def __init__(self, name):
        self.name = name
        self.calls = []

    async def execute(self, plan, context, **kwargs):
        self.calls.append({"plan": plan, "context": context, "kwargs": kwargs})
        return {"engine": self.name, "success": True, "results": []}


def _mock_registry():
    registry = EngineRegistry.__new__(EngineRegistry)
    registry.engines = {
        name: _RecordingEngine(name)
        for name in ("tiered_delegation", "acs", "claude_code")
    }
    return registry


class TestSendIntegration:
    """Test SendIntegration."""

    @pytest.fixture
    def integration(self):
        """Setup integration with a mock registry (no real engine spawns)."""
        return SendIntegration(registry=_mock_registry())

    def test_integration_initialization(self, integration):
        """Integration initializes with all components."""
        assert integration.parser is not None
        assert integration.loss_tracker is not None
        assert integration.detector is not None
        assert integration.l34_gate is not None
        assert integration.registry is not None

    def test_is_trivial_task(self, integration):
        """Trivial task detection works."""
        trivial = InitialAnalysisRequest(
            classification=Classification("code_gen", "simple", "claude", 0.8),
            entities=Entities(),
            global_plan=GlobalPlan(
                steps=[Step(step=1, action="a", depends_on=[], can_parallelize=[])],
                estimated_duration_s=5,
                estimated_tokens=200,  # < 500
            ),
        )
        assert integration._is_trivial_task(trivial) is True

        complex_task = InitialAnalysisRequest(
            classification=Classification("code_gen", "complex", "claude", 0.8),
            entities=Entities(),
            global_plan=GlobalPlan(
                steps=[
                    Step(step=i, action="a", depends_on=[] if i == 1 else [i - 1], can_parallelize=[])
                    for i in range(1, 6)
                ],
                estimated_duration_s=30,
                estimated_tokens=10000,  # > 500
            ),
        )
        assert integration._is_trivial_task(complex_task) is False

    @pytest.mark.asyncio
    async def test_select_engine_with_override(self, integration):
        """Engine override via slash command."""
        analysis = InitialAnalysisRequest(
            classification=Classification("code_gen", "moderate", "claude", 0.8),
            entities=Entities(),
            global_plan=GlobalPlan(
                steps=[Step(step=1, action="a", depends_on=[], can_parallelize=[])],
                estimated_duration_s=10,
                estimated_tokens=5000,
            ),
        )

        # Override to force acs
        engine, result = await integration.select_engine_and_execute(
            "/use-engine acs\nTest task",
            {},
            analysis,
        )

        assert engine == "acs"

    def test_slash_command_parsing(self, integration):
        """Slash commands are parsed correctly."""
        # Test /use-engine
        parsed = integration.parser.parse("/use-engine tiered_delegation\nTask")
        assert parsed.engine_override == "tiered_delegation"
        assert parsed.task_text == "Task"

        # Test /debug-engine
        parsed = integration.parser.parse("/debug-engine\nTask")
        assert parsed.debug_mode is True
        assert parsed.task_text == "Task"

        # Test normal task
        parsed = integration.parser.parse("Just a normal task")
        assert parsed.engine_override is None
        assert parsed.task_text == "Just a normal task"

    def test_invalid_engine_raises(self, integration):
        """Invalid engine raises ValueError."""
        with pytest.raises(ValueError, match="Unknown engine"):
            integration.parser.parse("/use-engine invalid_engine\nTask")

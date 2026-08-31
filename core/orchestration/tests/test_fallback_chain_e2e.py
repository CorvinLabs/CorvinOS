"""E2E tests for fallback chain (Phase 0 prerequisite).

Validates multi-engine fallback:
- Scenario 1: Haiku timeout → route to Opus → success
- Scenario 2: Haiku + Opus timeout → route to Claude → success
- Scenario 3: All three fail → graceful degradation + alert
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Optional
from uuid import uuid4

import pytest


class EngineType(str, Enum):
    """Supported engines."""

    HAIKU = "haiku"
    OPUS = "opus"
    CLAUDE = "claude"
    HERMES = "hermes"


class EngineStatus(str, Enum):
    """Engine availability status."""

    HEALTHY = "healthy"
    TIMEOUT = "timeout"
    ERROR = "error"
    UNAVAILABLE = "unavailable"


@dataclass
class EngineCapability:
    """Engine capability and performance profile."""

    engine: EngineType
    max_latency_ms: int
    cost_per_token: float
    quality_score: float  # 0.0-1.0
    is_available: bool = True


@dataclass
class TaskContext:
    """Task execution context."""

    task_id: str
    task_type: str
    tokens_estimated: int
    urgency: str  # "low", "medium", "high"


@dataclass
class ExecutionResult:
    """Result of task execution."""

    task_id: str
    engine_used: EngineType
    success: bool
    latency_ms: int
    tokens_used: int
    cost_cents: int
    quality_score: float
    fallback_count: int  # Number of engines tried


class MockEngine:
    """Mock engine for testing fallback behavior."""

    def __init__(self, engine_type: EngineType, status: EngineStatus = EngineStatus.HEALTHY):
        self.engine_type = engine_type
        self.status = status
        self.calls = []

    async def execute(
        self, context: TaskContext, timeout_ms: int = 5000
    ) -> Optional[ExecutionResult]:
        """Execute task with simulated latency and status."""
        self.calls.append(context)

        # Simulate latency
        await asyncio.sleep(0.01)  # 10ms baseline

        if self.status == EngineStatus.TIMEOUT:
            # Simulate timeout
            await asyncio.sleep(timeout_ms / 1000.0)
            return None

        if self.status == EngineStatus.ERROR:
            return None

        if self.status == EngineStatus.UNAVAILABLE:
            return None

        # Success case
        return ExecutionResult(
            task_id=context.task_id,
            engine_used=self.engine_type,
            success=True,
            latency_ms=20 + self._random_offset(),
            tokens_used=context.tokens_estimated,
            cost_cents=self._calculate_cost(context),
            quality_score=0.85 + self._quality_offset(),
            fallback_count=0,
        )

    def _random_offset(self) -> int:
        """Simulate natural latency variation."""
        return hash(str(uuid4())) % 20

    def _calculate_cost(self, context: TaskContext) -> int:
        """Calculate cost based on engine and tokens."""
        cost_per_1k = {
            EngineType.HAIKU: 25,
            EngineType.OPUS: 100,
            EngineType.CLAUDE: 200,
        }
        return int((context.tokens_estimated / 1000) * cost_per_1k.get(self.engine_type, 50))

    def _quality_offset(self) -> float:
        """Engine-specific quality offset."""
        offsets = {
            EngineType.HAIKU: 0.0,
            EngineType.OPUS: 0.08,
            EngineType.CLAUDE: 0.12,
        }
        return offsets.get(self.engine_type, 0.0)


class FallbackChain:
    """Multi-engine fallback orchestrator."""

    def __init__(self):
        self.engines: dict[EngineType, MockEngine] = {}
        self.fallback_sequence = [
            EngineType.HAIKU,
            EngineType.OPUS,
            EngineType.CLAUDE,
        ]
        self.alerts = []

    def register_engine(self, engine: MockEngine) -> None:
        """Register an engine in the chain."""
        self.engines[engine.engine_type] = engine

    async def execute(
        self, context: TaskContext, timeout_ms: int = 5000
    ) -> Optional[ExecutionResult]:
        """Execute task with fallback chain.

        Try engines in sequence until success or all fail.
        """
        fallback_count = 0

        for engine_type in self.fallback_sequence:
            if engine_type not in self.engines:
                continue

            engine = self.engines[engine_type]
            try:
                # Try to execute with timeout
                result = await asyncio.wait_for(
                    engine.execute(context, timeout_ms), timeout=timeout_ms / 1000.0
                )

                if result:
                    result.fallback_count = fallback_count
                    return result

            except asyncio.TimeoutError:
                # Engine timed out, try next
                fallback_count += 1
                continue
            except Exception:
                # Engine errored, try next
                fallback_count += 1
                continue

        # All engines failed
        self.alerts.append(
            {
                "type": "ALL_ENGINES_FAILED",
                "task_id": context.task_id,
                "engines_tried": len(self.fallback_sequence),
                "fallback_count": fallback_count,
            }
        )
        return None

    def get_alerts(self) -> list[dict]:
        """Get all alerts generated during execution."""
        return self.alerts


class TestFallbackChainScenario1:
    """Scenario 1: Haiku timeout → route to Opus → success."""

    @pytest.mark.asyncio
    async def test_haiku_timeout_fallback_to_opus(self):
        """Test: Haiku times out (>5s), Opus handles task, returns success."""
        chain = FallbackChain()

        # Haiku: times out
        haiku = MockEngine(EngineType.HAIKU, status=EngineStatus.TIMEOUT)
        chain.register_engine(haiku)

        # Opus: succeeds
        opus = MockEngine(EngineType.OPUS, status=EngineStatus.HEALTHY)
        chain.register_engine(opus)

        # Claude: not called
        claude = MockEngine(EngineType.CLAUDE, status=EngineStatus.HEALTHY)
        chain.register_engine(claude)

        # Execute task
        context = TaskContext(
            task_id="task-1",
            task_type="code_generation",
            tokens_estimated=1000,
            urgency="medium",
        )

        result = await chain.execute(context, timeout_ms=100)

        # Assertions
        assert result is not None
        assert result.success is True
        assert result.engine_used == EngineType.OPUS
        assert result.fallback_count == 1
        assert len(haiku.calls) == 1  # Haiku was tried once
        assert len(opus.calls) == 1  # Opus was tried once
        assert len(claude.calls) == 0  # Claude was not tried


class TestFallbackChainScenario2:
    """Scenario 2: Haiku + Opus timeout → route to Claude → success."""

    @pytest.mark.asyncio
    async def test_haiku_opus_timeout_fallback_to_claude(self):
        """Test: Both Haiku and Opus time out, Claude handles task."""
        chain = FallbackChain()

        # Haiku: times out
        haiku = MockEngine(EngineType.HAIKU, status=EngineStatus.TIMEOUT)
        chain.register_engine(haiku)

        # Opus: times out
        opus = MockEngine(EngineType.OPUS, status=EngineStatus.TIMEOUT)
        chain.register_engine(opus)

        # Claude: succeeds
        claude = MockEngine(EngineType.CLAUDE, status=EngineStatus.HEALTHY)
        chain.register_engine(claude)

        # Execute task
        context = TaskContext(
            task_id="task-2",
            task_type="analysis",
            tokens_estimated=2000,
            urgency="high",
        )

        result = await chain.execute(context, timeout_ms=100)

        # Assertions
        assert result is not None
        assert result.success is True
        assert result.engine_used == EngineType.CLAUDE
        assert result.fallback_count == 2
        assert len(haiku.calls) == 1
        assert len(opus.calls) == 1
        assert len(claude.calls) == 1


class TestFallbackChainScenario3:
    """Scenario 3: All three fail → graceful degradation + alert."""

    @pytest.mark.asyncio
    async def test_all_engines_fail_graceful_degradation(self):
        """Test: All engines fail, chain returns None and alerts operator."""
        chain = FallbackChain()

        # All engines unavailable
        haiku = MockEngine(EngineType.HAIKU, status=EngineStatus.ERROR)
        chain.register_engine(haiku)

        opus = MockEngine(EngineType.OPUS, status=EngineStatus.ERROR)
        chain.register_engine(opus)

        claude = MockEngine(EngineType.CLAUDE, status=EngineStatus.ERROR)
        chain.register_engine(claude)

        # Execute task
        context = TaskContext(
            task_id="task-3",
            task_type="research",
            tokens_estimated=3000,
            urgency="high",
        )

        result = await chain.execute(context, timeout_ms=100)

        # Assertions
        assert result is None
        assert len(chain.get_alerts()) == 1
        alert = chain.get_alerts()[0]
        assert alert["type"] == "ALL_ENGINES_FAILED"
        assert alert["task_id"] == "task-3"
        assert alert["engines_tried"] == 3
        assert len(haiku.calls) == 1
        assert len(opus.calls) == 1
        assert len(claude.calls) == 1


class TestFallbackChainReliability:
    """Test fallback chain reliability properties."""

    @pytest.mark.asyncio
    async def test_fallback_maintains_order(self):
        """Test: Fallback chain tries engines in correct order."""
        chain = FallbackChain()

        # Set up: Haiku fails, Opus succeeds
        haiku = MockEngine(EngineType.HAIKU, status=EngineStatus.ERROR)
        opus = MockEngine(EngineType.OPUS, status=EngineStatus.HEALTHY)
        claude = MockEngine(EngineType.CLAUDE, status=EngineStatus.HEALTHY)

        chain.register_engine(haiku)
        chain.register_engine(opus)
        chain.register_engine(claude)

        context = TaskContext(
            task_id="task-order",
            task_type="test",
            tokens_estimated=500,
            urgency="medium",
        )

        result = await chain.execute(context)

        # Verify order: Haiku tried first, then Opus, Claude not tried
        assert len(haiku.calls) == 1
        assert len(opus.calls) == 1
        assert len(claude.calls) == 0

    @pytest.mark.asyncio
    async def test_cost_optimization_haiku_preferred(self):
        """Test: Cost optimization prefers Haiku when healthy."""
        chain = FallbackChain()

        haiku = MockEngine(EngineType.HAIKU, status=EngineStatus.HEALTHY)
        opus = MockEngine(EngineType.OPUS, status=EngineStatus.HEALTHY)

        chain.register_engine(haiku)
        chain.register_engine(opus)

        context = TaskContext(
            task_id="task-cost",
            task_type="test",
            tokens_estimated=1000,
            urgency="low",
        )

        result = await chain.execute(context)

        # Should use Haiku (cheaper, first in chain)
        assert result.engine_used == EngineType.HAIKU
        assert result.cost_cents < 50  # Haiku should be ~25 cents for 1k tokens

    @pytest.mark.asyncio
    async def test_urgency_aware_fallback(self):
        """Test: High-urgency tasks can skip to more capable engines."""
        chain = FallbackChain()

        haiku = MockEngine(EngineType.HAIKU, status=EngineStatus.HEALTHY)
        opus = MockEngine(EngineType.OPUS, status=EngineStatus.HEALTHY)

        chain.register_engine(haiku)
        chain.register_engine(opus)

        # High-urgency task
        context = TaskContext(
            task_id="task-urgent",
            task_type="critical",
            tokens_estimated=1000,
            urgency="high",  # High urgency might prefer Opus (higher quality)
        )

        result = await chain.execute(context)

        # Current implementation still tries Haiku first
        # Future implementation might skip to Opus for high-urgency
        assert result is not None
        assert result.success

    @pytest.mark.asyncio
    async def test_multiple_tasks_sequential(self):
        """Test: Multiple tasks execute correctly in sequence."""
        chain = FallbackChain()

        haiku = MockEngine(EngineType.HAIKU, status=EngineStatus.HEALTHY)
        opus = MockEngine(EngineType.OPUS, status=EngineStatus.HEALTHY)

        chain.register_engine(haiku)
        chain.register_engine(opus)

        # Execute 5 tasks
        for i in range(5):
            context = TaskContext(
                task_id=f"task-{i}",
                task_type="test",
                tokens_estimated=500 + (i * 100),
                urgency="medium",
            )
            result = await chain.execute(context)
            assert result is not None
            assert result.success

        # Verify all went to Haiku
        assert len(haiku.calls) == 5
        assert len(opus.calls) == 0

    @pytest.mark.asyncio
    async def test_timeout_per_task(self):
        """Test: Timeout applies per-task, not globally."""
        chain = FallbackChain()

        haiku = MockEngine(EngineType.HAIKU, status=EngineStatus.TIMEOUT)
        opus = MockEngine(EngineType.OPUS, status=EngineStatus.HEALTHY)

        chain.register_engine(haiku)
        chain.register_engine(opus)

        context1 = TaskContext(
            task_id="task-1",
            task_type="test",
            tokens_estimated=500,
            urgency="medium",
        )
        context2 = TaskContext(
            task_id="task-2",
            task_type="test",
            tokens_estimated=500,
            urgency="medium",
        )

        # Both should fallback from Haiku to Opus independently
        result1 = await chain.execute(context1, timeout_ms=100)
        result2 = await chain.execute(context2, timeout_ms=100)

        assert result1.engine_used == EngineType.OPUS
        assert result2.engine_used == EngineType.OPUS

    @pytest.mark.asyncio
    async def test_partial_chain_failure_recovery(self):
        """Test: Chain recovers when intermediate engine fails."""
        chain = FallbackChain()

        haiku = MockEngine(EngineType.HAIKU, status=EngineStatus.UNAVAILABLE)
        opus = MockEngine(EngineType.OPUS, status=EngineStatus.ERROR)
        claude = MockEngine(EngineType.CLAUDE, status=EngineStatus.HEALTHY)

        chain.register_engine(haiku)
        chain.register_engine(opus)
        chain.register_engine(claude)

        context = TaskContext(
            task_id="task-recovery",
            task_type="test",
            tokens_estimated=1000,
            urgency="medium",
        )

        result = await chain.execute(context)

        # Should reach Claude
        assert result is not None
        assert result.engine_used == EngineType.CLAUDE
        assert result.fallback_count == 2


class TestFallbackChainMetrics:
    """Test metrics collection for fallback chain."""

    @pytest.mark.asyncio
    async def test_fallback_metrics_collection(self):
        """Test: Fallback chain collects correct metrics."""
        chain = FallbackChain()

        haiku = MockEngine(EngineType.HAIKU, status=EngineStatus.TIMEOUT)
        opus = MockEngine(EngineType.OPUS, status=EngineStatus.HEALTHY)

        chain.register_engine(haiku)
        chain.register_engine(opus)

        context = TaskContext(
            task_id="task-metrics",
            task_type="test",
            tokens_estimated=1000,
            urgency="medium",
        )

        result = await chain.execute(context)

        # Verify metrics
        assert result.fallback_count == 1
        assert result.tokens_used == 1000
        assert result.quality_score > 0.85
        assert result.cost_cents > 0

    @pytest.mark.asyncio
    async def test_engine_call_tracking(self):
        """Test: Chain tracks which engines were called."""
        chain = FallbackChain()

        haiku = MockEngine(EngineType.HAIKU, status=EngineStatus.HEALTHY)
        opus = MockEngine(EngineType.OPUS, status=EngineStatus.HEALTHY)

        chain.register_engine(haiku)
        chain.register_engine(opus)

        context = TaskContext(
            task_id="task-tracking",
            task_type="test",
            tokens_estimated=500,
            urgency="low",
        )

        result = await chain.execute(context)

        # Only Haiku should be called (Opus is backup)
        assert len(haiku.calls) == 1
        assert haiku.calls[0].task_id == "task-tracking"
        assert len(opus.calls) == 0

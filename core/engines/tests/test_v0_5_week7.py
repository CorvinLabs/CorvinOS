"""Tests for v0.5 Week 7 (Engine Abstraction Layer).

Tests:
- Claude engine (premium quality)
- Haiku engine (fast, cheap)
- Hermes engine (balanced)
- Local engine (fallback)
- Engine registry (discovery, health checks)
- Cost/capability matrix (routing data)
"""

from __future__ import annotations

import asyncio
import pytest

from core.engines.engine_interface import EngineType, EngineStatus, EngineRequest
from core.engines.claude_engine import ClaudeEngine
from core.engines.haiku_engine import HaikuEngine
from core.engines.hermes_engine import HermesEngine
from core.engines.local_engine import LocalEngine
from core.engines.engine_registry import EngineRegistry
from core.orchestration.cost_capability_matrix import CostCapabilityMatrix


# ============================================================================
# CLAUDE ENGINE TESTS
# ============================================================================


class TestClaudeEngine:
    """Test Claude engine."""

    @pytest.mark.asyncio
    async def test_claude_execute_success(self):
        """Test: Claude executes task and returns response."""
        engine = ClaudeEngine()
        request = EngineRequest(
            task_id="task-1",
            task_type="code_gen",
            prompt="Write hello world",
        )

        response = await engine.execute(request)

        assert response.success is True
        assert response.engine_type == EngineType.CLAUDE
        assert response.output is not None
        assert response.quality_score >= 0.95

    @pytest.mark.asyncio
    async def test_claude_cost_calculation(self):
        """Test: Claude cost is calculated correctly."""
        engine = ClaudeEngine()
        request = EngineRequest(
            task_id="task-1",
            task_type="analysis",
            prompt="Analyze this: " + "x" * 10000,
        )

        response = await engine.execute(request)

        # Cost should be >0 (expensive engine)
        assert response.cost_cents > 0
        assert response.tokens_input > 0

    @pytest.mark.asyncio
    async def test_claude_health_check(self):
        """Test: Claude health check returns healthy."""
        engine = ClaudeEngine()
        status = await engine.health_check()
        assert status == EngineStatus.HEALTHY

    def test_claude_capability(self):
        """Test: Claude capability profile is correct."""
        engine = ClaudeEngine()
        cap = engine.get_capability()

        assert cap.quality_tier == "premium"
        assert cap.max_latency_ms == 3000
        assert cap.cost_per_1m_input_tokens == 3000  # $30


# ============================================================================
# HAIKU ENGINE TESTS
# ============================================================================


class TestHaikuEngine:
    """Test Haiku engine."""

    @pytest.mark.asyncio
    async def test_haiku_execute_success(self):
        """Test: Haiku executes task successfully."""
        engine = HaikuEngine()
        request = EngineRequest(
            task_id="task-1",
            task_type="chat",
            prompt="Hello",
        )

        response = await engine.execute(request)

        assert response.success is True
        assert response.engine_type == EngineType.HAIKU
        assert response.quality_score >= 0.90

    @pytest.mark.asyncio
    async def test_haiku_is_faster_than_claude(self):
        """Test: Haiku is faster than Claude."""
        haiku = HaikuEngine()
        claude = ClaudeEngine()

        request = EngineRequest(
            task_id="task-1",
            task_type="chat",
            prompt="Quick task",
        )

        haiku_response = await haiku.execute(request)
        claude_response = await claude.execute(request)

        assert haiku_response.latency_ms < claude_response.latency_ms

    @pytest.mark.asyncio
    async def test_haiku_is_cheaper_than_claude(self):
        """Test: Haiku is cheaper than Claude."""
        haiku = HaikuEngine()
        claude = ClaudeEngine()

        request = EngineRequest(
            task_id="task-1",
            task_type="chat",
            prompt="x" * 1000,
        )

        haiku_response = await haiku.execute(request)
        claude_response = await claude.execute(request)

        assert haiku_response.cost_cents < claude_response.cost_cents

    def test_haiku_capability(self):
        """Test: Haiku capability profile."""
        engine = HaikuEngine()
        cap = engine.get_capability()

        assert cap.quality_tier == "standard"
        assert cap.max_latency_ms == 1500
        assert cap.cost_per_1m_input_tokens == 80  # $0.80


# ============================================================================
# HERMES ENGINE TESTS
# ============================================================================


class TestHermesEngine:
    """Test Hermes engine."""

    @pytest.mark.asyncio
    async def test_hermes_execute_success(self):
        """Test: Hermes executes task."""
        engine = HermesEngine()
        request = EngineRequest(
            task_id="task-1",
            task_type="analysis",
            prompt="Analyze",
        )

        response = await engine.execute(request)

        assert response.success is True
        assert response.engine_type == EngineType.HERMES
        assert response.quality_score >= 0.94


# ============================================================================
# LOCAL ENGINE TESTS
# ============================================================================


class TestLocalEngine:
    """Test Local engine (fallback)."""

    @pytest.mark.asyncio
    async def test_local_execute_success(self):
        """Test: Local engine executes task."""
        engine = LocalEngine()
        request = EngineRequest(
            task_id="task-1",
            task_type="chat",
            prompt="Quick",
        )

        response = await engine.execute(request)

        assert response.success is True
        assert response.cost_cents == 0  # Free

    @pytest.mark.asyncio
    async def test_local_is_free(self):
        """Test: Local engine has zero cost."""
        engine = LocalEngine()
        request = EngineRequest(
            task_id="task-1",
            task_type="analysis",
            prompt="x" * 5000,
        )

        response = await engine.execute(request)

        assert response.cost_cents == 0

    @pytest.mark.asyncio
    async def test_local_load_unload(self):
        """Test: Local model can be loaded/unloaded."""
        engine = LocalEngine()

        assert engine.load_model() is True
        assert engine.available is True

        engine.unload_model()
        assert engine.available is False

        request = EngineRequest(
            task_id="task-1",
            task_type="chat",
            prompt="Hello",
        )
        response = await engine.execute(request)
        assert response.success is False  # Model not loaded


# ============================================================================
# ENGINE REGISTRY TESTS
# ============================================================================


class TestEngineRegistry:
    """Test engine registry."""

    def test_registry_initialization(self):
        """Test: Registry initializes all 4 engines."""
        registry = EngineRegistry()

        assert len(registry.engines) == 4
        assert EngineType.CLAUDE in registry.engines
        assert EngineType.HAIKU in registry.engines
        assert EngineType.HERMES in registry.engines
        assert EngineType.LOCAL in registry.engines

    @pytest.mark.asyncio
    async def test_health_check_all(self):
        """Test: Health check works for all engines."""
        registry = EngineRegistry()

        health = await registry.health_check_all()

        assert len(health) == 4
        for engine_type, status in health.items():
            assert status in [
                EngineStatus.HEALTHY,
                EngineStatus.UNAVAILABLE,
            ]

    def test_get_healthy_engines(self):
        """Test: Registry identifies healthy engines."""
        registry = EngineRegistry()

        healthy = registry.get_healthy_engines()
        assert len(healthy) > 0

    def test_fallback_chain(self):
        """Test: Default fallback chain is correct."""
        registry = EngineRegistry()

        chain = registry.get_default_fallback_chain()

        # Should be: Haiku → Hermes → Claude → Local
        assert chain[0] == EngineType.HAIKU
        assert chain[-1] == EngineType.LOCAL

    def test_cost_estimation(self):
        """Test: Cost estimation works."""
        registry = EngineRegistry()

        claude_cost = registry.estimate_cost(EngineType.CLAUDE, 1000, 100)
        haiku_cost = registry.estimate_cost(EngineType.HAIKU, 1000, 100)

        # Claude should be more expensive
        assert claude_cost > haiku_cost

    def test_get_stats(self):
        """Test: Registry stats include all engines."""
        registry = EngineRegistry()

        stats = registry.get_stats()

        assert len(stats) == 4
        assert "claude-3-5-sonnet" in str(stats) or "claude" in str(stats).lower()


# ============================================================================
# COST/CAPABILITY MATRIX TESTS
# ============================================================================


class TestCostCapabilityMatrix:
    """Test cost/capability matrix."""

    def test_matrix_initialization(self):
        """Test: Matrix initializes with realistic data."""
        matrix = CostCapabilityMatrix()

        # Check that all task types are covered
        task_types = {"code_gen", "analysis", "chat", "research"}
        engines = [EngineType.CLAUDE, EngineType.HAIKU, EngineType.HERMES, EngineType.LOCAL]

        for engine in engines:
            for task_type in task_types:
                cap = matrix.get_capability(engine, task_type)
                assert cap is not None
                assert cap.quality_score > 0
                assert cap.latency_p99_ms > 0

    def test_best_quality_engine(self):
        """Test: Matrix identifies best quality engine."""
        matrix = CostCapabilityMatrix()

        best = matrix.get_best_quality_engine("code_gen")
        assert best == EngineType.CLAUDE

    def test_cheapest_engine(self):
        """Test: Matrix identifies cheapest engine."""
        matrix = CostCapabilityMatrix()

        cheapest = matrix.get_cheapest_engine("chat")
        assert cheapest == EngineType.HAIKU  # Cheapest

    def test_fastest_engine(self):
        """Test: Matrix identifies fastest engine."""
        matrix = CostCapabilityMatrix()

        fastest = matrix.get_fastest_engine("chat")
        assert fastest == EngineType.HAIKU  # Fast

    def test_cost_estimation(self):
        """Test: Cost estimation is accurate."""
        matrix = CostCapabilityMatrix()

        cost = matrix.estimate_cost(EngineType.HAIKU, "chat", 1000)

        # Should be proportional to token count and rate
        assert cost > 0

    def test_quality_estimation(self):
        """Test: Quality estimation returns reasonable values."""
        matrix = CostCapabilityMatrix()

        claude_quality = matrix.estimate_quality(EngineType.CLAUDE, "analysis")
        haiku_quality = matrix.estimate_quality(EngineType.HAIKU, "analysis")
        local_quality = matrix.estimate_quality(EngineType.LOCAL, "analysis")

        # Claude should be best
        assert claude_quality > haiku_quality
        assert haiku_quality > local_quality

    def test_matrix_export(self):
        """Test: Matrix can be exported as dict."""
        matrix = CostCapabilityMatrix()

        data = matrix.get_matrix_as_dict()

        assert "claude-3-5-sonnet" in data
        assert "code_gen" in data["claude-3-5-sonnet"]
        assert data["claude-3-5-sonnet"]["code_gen"]["quality"] == 0.98


# ============================================================================
# INTEGRATION TESTS
# ============================================================================


class TestWeek7Integration:
    """Integration tests for all Week 7 components."""

    @pytest.mark.asyncio
    async def test_all_engines_execute_same_task(self):
        """Test: All 4 engines can execute same task."""
        request = EngineRequest(
            task_id="test",
            task_type="chat",
            prompt="Quick test",
        )

        engines = [
            ClaudeEngine(),
            HaikuEngine(),
            HermesEngine(),
            LocalEngine(),
        ]

        results = []
        for engine in engines:
            response = await engine.execute(request)
            results.append((engine.engine_type, response.success, response.cost_cents))

        # All should succeed
        for engine_type, success, cost in results:
            assert success is True

        # Cost should vary by engine
        costs = [cost for _, _, cost in results]
        assert len(set(costs)) > 1  # Not all same

    def test_registry_and_matrix_together(self):
        """Test: Registry and matrix work together for routing."""
        registry = EngineRegistry()
        matrix = CostCapabilityMatrix()

        # Get healthy engines
        healthy = registry.get_healthy_engines()

        # For each healthy engine, verify matrix has data
        for engine_type in healthy:
            for task_type in ["code_gen", "analysis", "chat"]:
                cap = matrix.get_capability(engine_type, task_type)
                assert cap is not None

    @pytest.mark.asyncio
    async def test_fallback_chain_coverage(self):
        """Test: All engines in fallback chain are executable."""
        registry = EngineRegistry()
        chain = registry.get_default_fallback_chain()

        request = EngineRequest(
            task_id="test",
            task_type="chat",
            prompt="Test",
        )

        for engine_type in chain:
            engine = registry.get_engine(engine_type)
            response = await engine.execute(request)
            assert response.task_id == "test"

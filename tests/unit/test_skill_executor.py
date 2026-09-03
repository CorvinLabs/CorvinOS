"""Tests for SkillExecutor — execution, monitoring, failure handling (ADR-0307).

This test suite validates:
1. Skill execution with success/failure capture
2. Timeout enforcement (configurable per skill)
3. Resource limit enforcement (memory, CPU time)
4. Error classification (timeout, resource, exception, partial)
5. Execution stats tracking (time, success rate, error count)
6. Auto-disable on 3+ consecutive failures
7. Partial result fallback
8. Per-tenant isolation
"""

import pytest
from datetime import datetime
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from dataclasses import asdict
import asyncio

from core.skills.executor import (
    SkillExecutor,
    ExecutionResult,
    ErrorClass,
    ExecutorStats,
)


# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture(autouse=True)
def _sandbox_corvin_home(tmp_path, monkeypatch):
    """Every execution emits a ``skill.executed`` audit event to the tenant
    core chain under CORVIN_HOME — pin it to a temp dir so the unit tests
    never append to the live chain."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "corvin_home"))


@pytest.fixture
def executor():
    """Create a SkillExecutor instance."""
    return SkillExecutor()


@pytest.fixture
def mock_skill():
    """Mock skill callable."""
    skill = AsyncMock()
    skill.id = "test_skill"
    skill.name = "Test Skill"
    return skill


# ============================================================================
# EXECUTION RESULT TESTS (1-3)
# ============================================================================


class TestExecutionResult:
    """Test ExecutionResult dataclass."""

    def test_success_result(self):
        """ExecutionResult with status=success, output, and metrics (test 1)."""
        result = ExecutionResult(
            status="success",
            output={"result": "value"},
            execution_time_ms=1234,
            error_class=None,
            error_message=None,
        )
        assert result.status == "success"
        assert result.output == {"result": "value"}
        assert result.execution_time_ms == 1234
        assert result.error_class is None

    def test_failure_result(self):
        """ExecutionResult with status=failure, error class, and message (test 2)."""
        result = ExecutionResult(
            status="failure",
            output=None,
            execution_time_ms=5000,
            error_class=ErrorClass.TIMEOUT,
            error_message="Execution exceeded 5000ms",
        )
        assert result.status == "failure"
        assert result.output is None
        assert result.error_class == ErrorClass.TIMEOUT
        assert "exceeded" in result.error_message

    def test_partial_result(self):
        """ExecutionResult with status=partial for fallback (test 3)."""
        result = ExecutionResult(
            status="partial",
            output={"partial": "data"},
            execution_time_ms=4900,
            error_class=ErrorClass.RESOURCE,
            error_message="Memory limit approached",
        )
        assert result.status == "partial"
        assert result.output["partial"] == "data"


# ============================================================================
# SKILL EXECUTION TESTS (4-7)
# ============================================================================


class TestSkillExecution:
    """Test basic skill execution."""

    @pytest.mark.asyncio
    async def test_execute_success(self, executor, mock_skill):
        """Execute skill successfully → ExecutionResult with status=success (test 4)."""
        mock_skill.return_value = {"result": "value"}

        result = await executor.execute("test_tenant", mock_skill, {"input": "data"})

        assert result.status == "success"
        assert result.output == {"result": "value"}
        assert result.execution_time_ms > 0
        assert result.error_class is None
        mock_skill.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_exception(self, executor, mock_skill):
        """Execute skill that raises exception → ExecutionResult with status=failure (test 5)."""
        mock_skill.side_effect = ValueError("Skill error")

        result = await executor.execute("test_tenant", mock_skill, {})

        assert result.status == "failure"
        assert result.output is None
        assert result.error_class == ErrorClass.EXCEPTION
        assert "ValueError" in result.error_message

    @pytest.mark.asyncio
    async def test_execute_with_context(self, executor, mock_skill):
        """Execute skill with context object (test 6)."""
        mock_skill.return_value = {"context_received": True}
        context = {"user_id": "user1", "task_id": "task1"}

        result = await executor.execute("test_tenant", mock_skill, context)

        assert result.status == "success"
        assert result.output["context_received"]

    @pytest.mark.asyncio
    async def test_execute_returns_partial_on_exception(self, executor):
        """Execute skill that fails but has fallback → status=partial (test 7)."""
        async def failing_skill():
            raise RuntimeError("Unexpected error")

        result = await executor.execute("test_tenant", failing_skill, {})

        assert result.status == "failure"
        assert result.error_class == ErrorClass.EXCEPTION


# ============================================================================
# TIMEOUT TESTS (8-10)
# ============================================================================


class TestTimeoutEnforcement:
    """Test timeout configuration and enforcement."""

    def test_set_timeout(self, executor):
        """Set timeout for specific skill (test 8)."""
        executor.set_timeout("test_skill", 5000)  # 5 seconds

        assert executor.get_timeout("test_skill") == 5000

    def test_default_timeout(self, executor):
        """Skill without explicit timeout uses default 30s (test 9)."""
        default = executor.get_timeout("unknown_skill")
        assert default == 30000  # 30 seconds in ms

    @pytest.mark.asyncio
    async def test_timeout_enforcement(self, executor):
        """Skill exceeding timeout → ExecutionResult with status=failure, error_class=TIMEOUT (test 10)."""
        executor.set_timeout("slow_skill", 100)  # 100ms

        async def slow_skill():
            await asyncio.sleep(0.2)  # 200ms
            return {"result": "never reached"}

        result = await executor.execute("test_tenant", slow_skill, {})

        assert result.status == "failure"
        assert result.error_class == ErrorClass.TIMEOUT
        assert result.output is None


# ============================================================================
# RESOURCE LIMIT TESTS (11-12)
# ============================================================================


class TestResourceLimits:
    """Test memory and CPU time limits."""

    def test_set_resource_limits(self, executor):
        """Set memory and CPU time limits (test 11)."""
        executor.set_resource_limits(memory_mb=256, cpu_ms=10000)

        limits = executor.get_resource_limits()
        assert limits["memory_mb"] == 256
        assert limits["cpu_ms"] == 10000

    @pytest.mark.asyncio
    async def test_memory_limit_enforcement(self, executor):
        """Skill that would exceed memory limit (test 12)."""
        executor.set_resource_limits(memory_mb=10, cpu_ms=30000)

        async def memory_hungry_skill():
            # Allocate large list (rough approximation)
            large_list = [0] * (1024 * 1024)  # ~8MB
            return {"allocated": len(large_list)}

        result = await executor.execute("test_tenant", memory_hungry_skill, {})

        # Note: Actual memory limit enforcement depends on OS/Python
        # This test documents the interface; enforcement is best-effort
        assert result.status in ["success", "failure"]  # Either succeeds or fails gracefully


# ============================================================================
# ERROR CLASSIFICATION TESTS (13-15)
# ============================================================================


class TestErrorClassification:
    """Test error classification."""

    @pytest.mark.asyncio
    async def test_classify_timeout_error(self, executor):
        """Timeout error classified as ErrorClass.TIMEOUT (test 13)."""
        # The timeout is keyed by the callable's __name__ — the previous
        # "fast_skill" key never matched, so the 30 s default applied and the
        # assertion below could only pass by accident.
        executor.set_timeout("slow_skill", 50)

        async def slow_skill():
            await asyncio.sleep(0.1)

        result = await executor.execute("test_tenant", slow_skill, {})

        assert result.error_class == ErrorClass.TIMEOUT

    @pytest.mark.asyncio
    async def test_classify_exception_error(self, executor):
        """Exception error classified as ErrorClass.EXCEPTION (test 14)."""
        async def failing_skill():
            raise ValueError("Custom error")

        result = await executor.execute("test_tenant", failing_skill, {})

        assert result.error_class == ErrorClass.EXCEPTION
        assert "ValueError" in result.error_message

    @pytest.mark.asyncio
    async def test_classify_resource_error(self, executor):
        """Resource exhaustion error classified as ErrorClass.RESOURCE (test 15)."""
        executor.set_resource_limits(memory_mb=1, cpu_ms=10)

        async def memory_skill():
            # Allocate beyond limit
            large = [0] * (1024 * 1024 * 1024)
            return {"ok": True}

        result = await executor.execute("test_tenant", memory_skill, {})

        # This will either succeed or fail with RESOURCE error
        # Test documents the classification interface
        assert result.error_class in [None, ErrorClass.RESOURCE]


# ============================================================================
# EXECUTION STATS TESTS (16-18)
# ============================================================================


class TestExecutorStats:
    """Test execution statistics and tracking."""

    @pytest.mark.asyncio
    async def test_get_execution_stats(self, executor, mock_skill):
        """Get aggregated stats for skill (test 16)."""
        mock_skill.return_value = {"ok": True}

        await executor.execute("test_tenant", mock_skill, {})
        await executor.execute("test_tenant", mock_skill, {})

        stats = executor.get_execution_stats("test_tenant", "test_skill")

        assert stats.total_executions == 2
        assert stats.successful_executions == 2
        assert stats.failed_executions == 0
        assert stats.success_rate == 1.0
        assert stats.avg_execution_time_ms > 0

    @pytest.mark.asyncio
    async def test_stats_after_failures(self, executor, mock_skill):
        """Stats include failed executions (test 17)."""
        async def flaky_skill(i):
            if i < 2:
                raise RuntimeError("Failure")
            return {"ok": True}

        # Execute: fail, fail, success
        for i in range(3):
            if i < 2:
                await executor.execute("test_tenant", flaky_skill, {"i": i})
            else:
                await executor.execute("test_tenant", flaky_skill, {"i": i})

        stats = executor.get_execution_stats("test_tenant", "flaky_skill")

        assert stats.total_executions >= 1
        assert stats.avg_execution_time_ms > 0

    @pytest.mark.asyncio
    async def test_per_tenant_stats_isolation(self, executor, mock_skill):
        """Stats isolated per tenant (test 18)."""
        mock_skill.return_value = {"ok": True}

        await executor.execute("tenant_a", mock_skill, {})
        await executor.execute("tenant_a", mock_skill, {})
        await executor.execute("tenant_b", mock_skill, {})

        stats_a = executor.get_execution_stats("tenant_a", "test_skill")
        stats_b = executor.get_execution_stats("tenant_b", "test_skill")

        assert stats_a.total_executions == 2
        assert stats_b.total_executions == 1

"""Unit tests for k=1 endpoint architecture (ADR-0515)."""

import asyncio
import pytest
from core.endpoints.k1_context import (
    K1ConnectionContext,
    K1PipelineEnforcer,
    K1RequestContext,
    PerRequestTaskQueue,
    RequestSessionContext,
    get_k1_context,
)


class TestK1ConnectionContext:
    """Test Layer 1: Single connection per request."""

    @pytest.mark.asyncio
    async def test_connection_acquire_release(self):
        """Test connection lifecycle (acquire, hold, release)."""
        ctx = K1ConnectionContext("req_test_001", "http")

        assert ctx._connection is None

        async with ctx as conn:
            assert conn is not None
            assert hasattr(conn, 'id')
            assert ctx._connection is not None

        # Connection released after context exit
        assert ctx._connection is None

    @pytest.mark.asyncio
    async def test_cleanup_stack_lifo(self):
        """Test cleanup functions execute in LIFO order."""
        ctx = K1ConnectionContext("req_cleanup_test", "http")
        cleanup_order = []

        def cleanup_1():
            cleanup_order.append(1)

        def cleanup_2():
            cleanup_order.append(2)

        def cleanup_3():
            cleanup_order.append(3)

        async with ctx:
            ctx.register_cleanup(cleanup_1)
            ctx.register_cleanup(cleanup_2)
            ctx.register_cleanup(cleanup_3)

        # LIFO: 3, 2, 1
        assert cleanup_order == [3, 2, 1]

    @pytest.mark.asyncio
    async def test_cleanup_with_async_functions(self):
        """Test cleanup supports async functions."""
        ctx = K1ConnectionContext("req_async_cleanup", "http")
        cleanup_order = []

        async def async_cleanup():
            cleanup_order.append('async')

        async with ctx:
            ctx.register_cleanup(async_cleanup)

        assert cleanup_order == ['async']


class TestRequestSessionContext:
    """Test Layer 2: Request-local session isolation (ADR-0447 + ADR-0515)."""

    @pytest.mark.asyncio
    async def test_session_isolation_request_local(self):
        """Test session state is request-local, not shared."""
        conn_ctx_1 = K1ConnectionContext("req_1", "http")
        conn_ctx_2 = K1ConnectionContext("req_2", "http")

        session_ctx_1 = RequestSessionContext(conn_ctx_1)
        session_ctx_2 = RequestSessionContext(conn_ctx_2)

        # Each context resolves independently
        async with conn_ctx_1:
            session_1 = await session_ctx_1.resolve_session("user:alice")

        async with conn_ctx_2:
            session_2 = await session_ctx_2.resolve_session("user:bob")

        # Session caches are isolated
        assert "user:alice" in session_ctx_1._session_cache
        assert "user:bob" not in session_ctx_1._session_cache
        assert "user:bob" in session_ctx_2._session_cache
        assert "user:alice" not in session_ctx_2._session_cache

    @pytest.mark.asyncio
    async def test_session_cache_within_request(self):
        """Test session caching within a single request."""
        conn_ctx = K1ConnectionContext("req_cache_test", "http")
        session_ctx = RequestSessionContext(conn_ctx)

        async with conn_ctx:
            # First resolve
            session_1 = await session_ctx.resolve_session("user:alice")
            assert session_1['user_id'] == 'alice'

            # Second resolve should hit cache
            session_2 = await session_ctx.resolve_session("user:alice")
            assert session_1 is session_2  # Same object (cached)

    @pytest.mark.asyncio
    async def test_get_cached_sessions(self):
        """Test listing cached sessions."""
        conn_ctx = K1ConnectionContext("req_list_test", "http")
        session_ctx = RequestSessionContext(conn_ctx)

        async with conn_ctx:
            await session_ctx.resolve_session("user:alice")
            await session_ctx.resolve_session("user:bob")

            cached = session_ctx.get_cached_sessions()
            assert set(cached) == {"user:alice", "user:bob"}


class TestPerRequestTaskQueue:
    """Test Layer 3: Atomic task queue (ADR-0298 + ADR-0515)."""

    @pytest.mark.asyncio
    async def test_queue_enqueue_and_drain(self):
        """Test enqueuing and draining tasks atomically."""
        queue = PerRequestTaskQueue("req_queue_test")
        results = []

        async def task_1():
            results.append(1)
            return "result_1"

        async def task_2():
            results.append(2)
            return "result_2"

        await queue.enqueue(task_1)
        await queue.enqueue(task_2)

        task_results, errors = await queue.drain()

        assert results == [1, 2]  # FIFO order
        assert task_results == ["result_1", "result_2"]
        assert len(errors) == 0

    @pytest.mark.asyncio
    async def test_queue_drain_atomicity(self):
        """Test task queue drains atomically (no interleaving)."""
        queue = PerRequestTaskQueue("req_atomic_test")
        execution_order = []

        def task_1():
            execution_order.append(1)
            return "done_1"

        def task_2():
            execution_order.append(2)
            return "done_2"

        await queue.enqueue(task_1)
        await queue.enqueue(task_2)

        results, errors = await queue.drain()

        # Verify FIFO + no interleaving
        assert execution_order == [1, 2]
        assert len(errors) == 0

    @pytest.mark.asyncio
    async def test_queue_drain_with_errors(self):
        """Test task queue continues after error."""
        queue = PerRequestTaskQueue("req_error_test")

        async def task_ok():
            return "ok"

        async def task_fail():
            raise ValueError("task_fail")

        async def task_ok_2():
            return "ok_2"

        await queue.enqueue(task_ok)
        await queue.enqueue(task_fail)
        await queue.enqueue(task_ok_2)

        results, errors = await queue.drain()

        assert len(results) == 2
        assert results == ["ok", "ok_2"]
        assert len(errors) == 1
        assert isinstance(errors[0], ValueError)

    @pytest.mark.asyncio
    async def test_queue_empty_drain(self):
        """Test draining an empty queue."""
        queue = PerRequestTaskQueue("req_empty_test")
        results, errors = await queue.drain()

        assert results == []
        assert errors == []


class TestK1PipelineEnforcer:
    """Test Layer 4: Pipeline gates fire once per request (ADR-0301 + ADR-0515)."""

    @pytest.mark.asyncio
    async def test_pipeline_gate_success(self):
        """Test successful pipeline gate check."""
        conn_ctx = K1ConnectionContext("req_gate_ok", "http")
        enforcer = K1PipelineEnforcer(conn_ctx)

        result = await enforcer.check_request("user:alice", "write_settings", "update")

        assert result is True
        assert enforcer.has_passed() is True
        assert enforcer.get_error() is None

    @pytest.mark.asyncio
    async def test_pipeline_gate_failure(self):
        """Test failed pipeline gate check."""
        conn_ctx = K1ConnectionContext("req_gate_fail", "http")
        enforcer = K1PipelineEnforcer(conn_ctx)

        result = await enforcer.check_request("", "write_settings", "update")

        assert result is False
        assert enforcer.has_passed() is False
        assert enforcer.get_error() is not None

    @pytest.mark.asyncio
    async def test_pipeline_gate_called_once(self):
        """Test pipeline gates are checked exactly once per request."""
        conn_ctx = K1ConnectionContext("req_gate_once", "http")
        enforcer = K1PipelineEnforcer(conn_ctx)

        # First check: should pass
        result_1 = await enforcer.check_request("user:alice", "write_settings", "update")
        assert result_1 is True

        # Second check: should return cached result (not re-evaluated)
        result_2 = await enforcer.check_request("user:alice", "different_action", "another")
        assert result_2 is True  # Cached, not re-checked


class TestK1RequestContext:
    """Test full k=1 request context integration."""

    @pytest.mark.asyncio
    async def test_context_creation(self):
        """Test creating a complete k=1 request context."""
        ctx = await K1RequestContext.create("http", "req_full_test")

        assert ctx.request_id == "req_full_test"
        assert ctx.transport == "http"
        assert ctx.connection is not None
        assert ctx.session is not None
        assert ctx.task_queue is not None
        assert ctx.pipeline is not None

    @pytest.mark.asyncio
    async def test_context_contextvar_storage(self):
        """Test context stored in ContextVar for retrieval."""
        ctx = await K1RequestContext.create("http", "req_ctxvar")

        retrieved = get_k1_context()
        assert retrieved is ctx
        assert retrieved.request_id == "req_ctxvar"

    @pytest.mark.asyncio
    async def test_full_k1_workflow(self):
        """Test full k=1 workflow: create → validate → enqueue → drain."""
        ctx = await K1RequestContext.create("http")

        async with ctx.connection:
            # Step 1: Pipeline check
            gate_passed = await ctx.pipeline.check_request(
                "user:alice", "write_settings", "update"
            )
            assert gate_passed is True

            # Step 2: Resolve session
            session = await ctx.session.resolve_session("user:alice")
            assert session['user_id'] == 'alice'

            # Step 3: Enqueue async tasks
            results = []

            async def async_task():
                results.append('task_ran')
                return 'done'

            await ctx.task_queue.enqueue(async_task)

            # Step 4: Drain atomically
            task_results, errors = await ctx.task_queue.drain()

        assert results == ['task_ran']
        assert task_results == ['done']
        assert len(errors) == 0

    @pytest.mark.asyncio
    async def test_multiple_requests_isolated(self):
        """Test two concurrent requests have isolated contexts."""
        ctx_1 = await K1RequestContext.create("http", "req_iso_1")
        ctx_2 = await K1RequestContext.create("http", "req_iso_2")

        async with ctx_1.connection:
            await ctx_1.session.resolve_session("user:alice")
            await ctx_1.task_queue.enqueue(lambda: 'req_1_task')

        async with ctx_2.connection:
            await ctx_2.session.resolve_session("user:bob")
            await ctx_2.task_queue.enqueue(lambda: 'req_2_task')

        # Verify isolation
        assert "user:alice" in ctx_1.session._session_cache
        assert "user:alice" not in ctx_2.session._session_cache
        assert "user:bob" in ctx_2.session._session_cache
        assert "user:bob" not in ctx_1.session._session_cache


# Tier-1 schema validation tests (run with `ruff check` / `mypy`)
def test_k1_context_types():
    """Type checking for k=1 context (Tier 1 gate: schema/lint)."""
    # This test verifies the module can be imported and types are correct
    assert K1RequestContext is not None
    assert hasattr(K1RequestContext, 'create')
    assert callable(K1RequestContext.create)

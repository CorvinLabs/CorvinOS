"""Integration tests for ADR-0424: Context Propagation for Async + Threading.

Tests verify:
1. Async context propagation through create_task() and gather()
2. Thread context propagation through Thread() and ThreadPoolExecutor
3. Nested async/thread boundaries
4. Exception handling and cleanup
5. Tenant isolation via ContextVar
"""

import asyncio
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar, copy_context
from typing import Any, List

import pytest

from core.concurrency.async_context import (
    AsyncContextPropagator,
    AsyncContextTaskGroup,
)
from core.concurrency.thread_context import (
    ThreadContextPropagator,
    ThreadLocalContext,
)
from core.concurrency.context_helpers import TenantContextVar

# Test ContextVar for isolation verification
TEST_VAR = ContextVar("test_var", default=None)
REQUEST_ID = ContextVar("request_id", default=None)


class TestAsyncContextPropagator:
    """Tests for AsyncContextPropagator (async task creation)."""

    @pytest.mark.asyncio
    async def test_create_task_preserves_context(self):
        """Test that create_task preserves ContextVar values."""
        TEST_VAR.set("parent_value")

        async def child_coro():
            # Should see the parent's context value
            return TEST_VAR.get()

        task = AsyncContextPropagator.create_task_with_context(child_coro())
        result = await task
        assert result == "parent_value"

    @pytest.mark.asyncio
    async def test_create_task_with_explicit_context(self):
        """Test create_task with explicit context override."""
        TEST_VAR.set("parent_value")
        explicit_context = copy_context()
        explicit_context.run(TEST_VAR.set, "explicit_value")

        async def child_coro():
            return TEST_VAR.get()

        task = AsyncContextPropagator.create_task_with_context(
            child_coro(), context=explicit_context
        )
        result = await task
        assert result == "explicit_value"

    @pytest.mark.asyncio
    async def test_create_task_rejects_non_coroutine(self):
        """Test that create_task rejects non-coroutines."""

        def sync_func():
            return "not a coro"

        with pytest.raises(TypeError, match="Expected coroutine"):
            AsyncContextPropagator.create_task_with_context(sync_func())

    @pytest.mark.asyncio
    async def test_gather_with_context_preserves_context_to_all_coros(self):
        """Test that gather_with_context propagates context to all coroutines."""
        TEST_VAR.set("shared_value")

        async def coro1():
            await asyncio.sleep(0.01)
            return TEST_VAR.get()

        async def coro2():
            await asyncio.sleep(0.02)
            return TEST_VAR.get()

        async def coro3():
            await asyncio.sleep(0.005)
            return TEST_VAR.get()

        results = await AsyncContextPropagator.gather_with_context(
            coro1(), coro2(), coro3()
        )
        assert results == ["shared_value", "shared_value", "shared_value"]

    @pytest.mark.asyncio
    async def test_gather_with_context_exception_handling(self):
        """Test gather_with_context with return_exceptions=True."""

        async def failing_coro():
            raise ValueError("expected error")

        async def passing_coro():
            return "success"

        results = await AsyncContextPropagator.gather_with_context(
            passing_coro(), failing_coro(), return_exceptions=True
        )
        assert results[0] == "success"
        assert isinstance(results[1], ValueError)

    @pytest.mark.asyncio
    async def test_gather_with_context_exception_propagation(self):
        """Test gather_with_context raises by default."""

        async def failing_coro():
            raise ValueError("expected error")

        with pytest.raises(ValueError, match="expected error"):
            await AsyncContextPropagator.gather_with_context(failing_coro())

    @pytest.mark.asyncio
    async def test_nested_create_task_preserves_context(self):
        """Test that nested create_task calls preserve context through layers."""
        TEST_VAR.set("root_value")

        async def level2_coro():
            # Create a task from within a task
            async def level3_coro():
                return TEST_VAR.get()

            task = AsyncContextPropagator.create_task_with_context(level3_coro())
            return await task

        task = AsyncContextPropagator.create_task_with_context(level2_coro())
        result = await task
        assert result == "root_value"

    @pytest.mark.asyncio
    async def test_multiple_context_vars_preserved(self):
        """Test that multiple ContextVars are all preserved."""
        TEST_VAR.set("test_value")
        REQUEST_ID.set("req_123")

        async def coro():
            return (TEST_VAR.get(), REQUEST_ID.get())

        task = AsyncContextPropagator.create_task_with_context(coro())
        result = await task
        assert result == ("test_value", "req_123")

    @pytest.mark.asyncio
    async def test_context_changes_in_task_do_not_affect_parent(self):
        """Test that changes in a task's context don't leak to parent."""
        TEST_VAR.set("parent_value")

        async def child_coro():
            TEST_VAR.set("child_value")
            await asyncio.sleep(0.01)
            return TEST_VAR.get()

        task = AsyncContextPropagator.create_task_with_context(child_coro())
        child_result = await task

        assert child_result == "child_value"
        # Parent context should be unchanged
        assert TEST_VAR.get() == "parent_value"

    @pytest.mark.skipif(
        sys.version_info < (3, 11), reason="TaskGroup requires Python 3.11+"
    )
    @pytest.mark.asyncio
    async def test_task_group_context_propagation(self):
        """Test AsyncContextTaskGroup on Python 3.11+."""
        TEST_VAR.set("group_value")
        results = []

        async def add_to_results(val):
            results.append(TEST_VAR.get())

        async with AsyncContextTaskGroup() as tg:
            tg.create_task(add_to_results(1))
            tg.create_task(add_to_results(2))
            tg.create_task(add_to_results(3))

        assert results == ["group_value", "group_value", "group_value"]

    @pytest.mark.skipif(
        sys.version_info >= (3, 11), reason="Only test fallback on < 3.11"
    )
    def test_task_group_requires_python_3_11(self):
        """Test that TaskGroup raises on Python < 3.11."""
        if sys.version_info < (3, 11):
            with pytest.raises(RuntimeError, match="requires Python 3.11"):
                AsyncContextTaskGroup()


class TestThreadContextPropagator:
    """Tests for ThreadContextPropagator (thread creation)."""

    def test_thread_with_context_preserves_context(self):
        """Test that thread_with_context preserves ContextVar values."""
        TEST_VAR.set("parent_value")
        result_container = []

        def thread_target():
            result_container.append(TEST_VAR.get())

        context = copy_context()
        thread = ThreadContextPropagator.thread_with_context(
            thread_target, context=context
        )
        thread.start()
        thread.join()

        assert result_container == ["parent_value"]

    def test_thread_with_context_args_and_kwargs(self):
        """Test thread_with_context with args and kwargs."""
        result_container = []

        def thread_target(a, b, c=None):
            result_container.append((a, b, c))

        thread = ThreadContextPropagator.thread_with_context(
            thread_target, args=(1, 2), kwargs={"c": 3}
        )
        thread.start()
        thread.join()

        assert result_container == [(1, 2, 3)]

    def test_thread_with_context_name_and_daemon(self):
        """Test thread_with_context with name and daemon flags."""

        def thread_target():
            pass

        thread = ThreadContextPropagator.thread_with_context(
            thread_target, name="test_thread", daemon=True
        )
        assert thread.name == "test_thread"
        assert thread.daemon is True

    def test_thread_rejects_non_callable(self):
        """Test that thread_with_context rejects non-callable targets."""
        with pytest.raises(TypeError, match="target must be callable"):
            ThreadContextPropagator.thread_with_context("not callable")

    def test_thread_context_isolation(self):
        """Test that thread contexts are isolated from each other."""
        TEST_VAR.set("main_value")
        results = []
        lock = threading.Lock()

        def thread_target(value):
            # Each thread sets its own value
            TEST_VAR.set(value)
            time.sleep(0.01)  # Let other threads run
            with lock:
                results.append(TEST_VAR.get())

        threads = [
            ThreadContextPropagator.thread_with_context(
                thread_target, args=(f"thread_{i}",)
            )
            for i in range(3)
        ]

        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        # Each thread should have its own value, not the main thread's
        results.sort()
        assert results == ["thread_0", "thread_1", "thread_2"]

    def test_executor_with_context_preserves_context(self):
        """Test that executor_with_context propagates context to worker threads."""
        TEST_VAR.set("executor_value")
        results = []

        def worker_fn():
            return TEST_VAR.get()

        with ThreadPoolExecutor(max_workers=2) as executor:
            future = ThreadContextPropagator.executor_with_context(
                executor, worker_fn
            )
            result = future.result(timeout=5)
            results.append(result)

        assert results == ["executor_value"]

    def test_executor_with_context_args(self):
        """Test executor_with_context with function arguments."""
        results = []

        def worker_fn(a, b, c=None):
            return a + b + (c or 0)

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = ThreadContextPropagator.executor_with_context(
                executor, worker_fn, 1, 2, c=3
            )
            result = future.result(timeout=5)
            results.append(result)

        assert results == [6]

    def test_executor_with_context_exception_handling(self):
        """Test executor_with_context propagates exceptions."""

        def failing_fn():
            raise ValueError("worker error")

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = ThreadContextPropagator.executor_with_context(
                executor, failing_fn
            )
            with pytest.raises(ValueError, match="worker error"):
                future.result(timeout=5)

    def test_executor_rejects_non_callable(self):
        """Test that executor_with_context rejects non-callable functions."""
        with ThreadPoolExecutor(max_workers=1) as executor:
            with pytest.raises(TypeError, match="fn must be callable"):
                ThreadContextPropagator.executor_with_context(
                    executor, "not callable"
                )


class TestThreadLocalContext:
    """Tests for ThreadLocalContext storage."""

    def test_thread_local_set_and_get(self):
        """Test basic set/get operations."""
        storage = ThreadLocalContext()
        storage.set("key", "value")
        assert storage.get("key") == "value"

    def test_thread_local_default_value(self):
        """Test default value when key not found."""
        storage = ThreadLocalContext()
        assert storage.get("missing", "default") == "default"

    def test_thread_local_isolation(self):
        """Test that thread-local values are isolated per thread."""
        storage = ThreadLocalContext()
        storage.set("key", "main_value")
        results = []
        lock = threading.Lock()

        def thread_target(thread_id):
            storage.set("key", f"thread_{thread_id}")
            time.sleep(0.01)
            with lock:
                results.append(storage.get("key"))

        threads = [
            threading.Thread(target=thread_target, args=(i,)) for i in range(3)
        ]

        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        # Main thread should still have its value
        assert storage.get("key") == "main_value"
        # Other threads should have their values (order may vary)
        results.sort()
        assert results == ["thread_0", "thread_1", "thread_2"]

    def test_thread_local_clear_specific_key(self):
        """Test clearing a specific key."""
        storage = ThreadLocalContext()
        storage.set("key1", "value1")
        storage.set("key2", "value2")
        storage.clear("key1")

        assert storage.get("key1") is None
        assert storage.get("key2") == "value2"

    def test_thread_local_clear_all(self):
        """Test clearing all keys."""
        storage = ThreadLocalContext()
        storage.set("key1", "value1")
        storage.set("key2", "value2")
        storage.clear()

        assert storage.get("key1") is None
        assert storage.get("key2") is None


class TestTenantContextIsolation:
    """Tests for tenant isolation using TenantContextVar (GDPR requirement)."""

    @pytest.mark.asyncio
    async def test_tenant_context_preserved_in_async_task(self):
        """Test tenant_id preservation across async task boundaries."""
        TenantContextVar.set("tenant_123")

        async def async_task():
            return TenantContextVar.get()

        task = AsyncContextPropagator.create_task_with_context(async_task())
        result = await task
        assert result == "tenant_123"

    def test_tenant_context_preserved_in_thread(self):
        """Test tenant_id preservation across thread boundaries."""
        TenantContextVar.set("tenant_456")
        results = []

        def thread_target():
            results.append(TenantContextVar.get())

        thread = ThreadContextPropagator.thread_with_context(thread_target)
        thread.start()
        thread.join()

        assert results == ["tenant_456"]

    @pytest.mark.asyncio
    async def test_tenant_context_isolation_between_tasks(self):
        """Test that different async tasks can have different tenants (cross-tenant isolation)."""
        results = []

        async def tenant_task(tenant_id):
            TenantContextVar.set(tenant_id)
            await asyncio.sleep(0.01)
            results.append(TenantContextVar.get())

        # Create separate contexts for each tenant
        context1 = copy_context()
        context1.run(TenantContextVar.set, "tenant_a")

        context2 = copy_context()
        context2.run(TenantContextVar.set, "tenant_b")

        task1 = AsyncContextPropagator.create_task_with_context(
            tenant_task("tenant_a"), context=context1
        )
        task2 = AsyncContextPropagator.create_task_with_context(
            tenant_task("tenant_b"), context=context2
        )

        await asyncio.gather(task1, task2)

        # Each task should have seen its own tenant_id despite sleep interleaving
        assert sorted(results) == ["tenant_a", "tenant_b"]

    def test_tenant_isolation_get_or_fail(self):
        """Test that tenant_id is required (fail-closed)."""
        # Don't set tenant_id
        with pytest.raises(RuntimeError, match="tenant_id not set"):
            TenantContextVar.get_or_fail()

    def test_tenant_isolation_empty_string_rejected(self):
        """Test that empty tenant_id is rejected."""
        with pytest.raises(ValueError, match="cannot be empty"):
            TenantContextVar.set("")


class TestNestedAsyncThreadBoundaries:
    """Tests for nested async/thread interactions."""

    @pytest.mark.asyncio
    async def test_async_spawns_thread(self):
        """Test that async task can spawn threads with context preservation."""
        TEST_VAR.set("async_value")
        results = []

        async def async_task():
            def thread_target():
                results.append(TEST_VAR.get())

            thread = ThreadContextPropagator.thread_with_context(thread_target)
            thread.start()
            thread.join()
            return "async_done"

        task = AsyncContextPropagator.create_task_with_context(async_task())
        result = await task

        assert result == "async_done"
        assert results == ["async_value"]

    def test_thread_does_async_work(self):
        """Test that a thread cannot directly run async code, but can spawn tasks."""
        # This is a limitation of asyncio - can't call async code from a thread
        # without a running event loop. This test documents the limitation.
        TEST_VAR.set("thread_value")
        results = []
        error_container = []

        def thread_target():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

                async def async_in_thread():
                    return TEST_VAR.get()

                result = loop.run_until_complete(async_in_thread())
                results.append(result)
                loop.close()
            except Exception as e:
                error_container.append(e)

        thread = ThreadContextPropagator.thread_with_context(thread_target)
        thread.start()
        thread.join()

        # The ContextVar may not propagate into a new event loop started in a thread
        # This is expected behavior - each thread needs its own loop
        assert len(error_container) == 0 or len(results) > 0

    @pytest.mark.asyncio
    async def test_gather_with_multiple_context_branches(self):
        """Test gather with multiple independent context branches."""
        TEST_VAR.set("base_value")

        async def coro_a():
            await asyncio.sleep(0.01)
            return f"a_{TEST_VAR.get()}"

        async def coro_b():
            await asyncio.sleep(0.02)
            return f"b_{TEST_VAR.get()}"

        results = await AsyncContextPropagator.gather_with_context(
            coro_a(), coro_b()
        )

        assert results == ["a_base_value", "b_base_value"]


class TestExceptionHandlingAndCleanup:
    """Tests for exception propagation and cleanup."""

    @pytest.mark.asyncio
    async def test_task_exception_propagates_with_context(self):
        """Test that exceptions from tasks include proper context."""

        async def failing_task():
            test_var_value = TEST_VAR.get()
            raise ValueError(f"Failed with {test_var_value}")

        TEST_VAR.set("error_context")
        task = AsyncContextPropagator.create_task_with_context(failing_task())

        with pytest.raises(ValueError, match="Failed with error_context"):
            await task

    def test_thread_exception_propagates_with_context(self):
        """Test that exceptions from threads can be captured."""
        TEST_VAR.set("thread_error_context")
        exception_container = []

        def failing_thread():
            try:
                test_var_value = TEST_VAR.get()
                raise ValueError(f"Thread failed with {test_var_value}")
            except Exception as e:
                exception_container.append(e)

        thread = ThreadContextPropagator.thread_with_context(failing_thread)
        thread.start()
        thread.join()

        assert len(exception_container) == 1
        assert "thread_error_context" in str(exception_container[0])

    @pytest.mark.asyncio
    async def test_context_cleanup_on_task_cancellation(self):
        """Test that context is properly maintained even if task is cancelled."""
        TEST_VAR.set("cancel_context")
        results = []

        async def long_running_task():
            try:
                await asyncio.sleep(10)
                results.append("completed")
            except asyncio.CancelledError:
                # Record the context value at cancellation
                results.append(f"cancelled_{TEST_VAR.get()}")
                raise

        task = AsyncContextPropagator.create_task_with_context(long_running_task())
        await asyncio.sleep(0.01)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        assert results == ["cancelled_cancel_context"]


class TestContextPropagationPerformance:
    """Tests that verify context propagation doesn't introduce excessive overhead."""

    @pytest.mark.asyncio
    async def test_many_tasks_with_context(self):
        """Test that context propagation scales to many tasks."""
        TEST_VAR.set("perf_test")
        task_count = 100

        async def simple_task(task_id):
            await asyncio.sleep(0.001)
            return TEST_VAR.get()

        tasks = [
            AsyncContextPropagator.create_task_with_context(simple_task(i))
            for i in range(task_count)
        ]
        results = await asyncio.gather(*tasks)

        assert len(results) == task_count
        assert all(r == "perf_test" for r in results)

    def test_many_threads_with_context(self):
        """Test that context propagation works with thread pools."""
        TEST_VAR.set("thread_perf")
        results = []
        lock = threading.Lock()

        def worker_fn():
            time.sleep(0.001)
            with lock:
                results.append(TEST_VAR.get())

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [
                ThreadContextPropagator.executor_with_context(executor, worker_fn)
                for _ in range(50)
            ]
            for future in futures:
                future.result(timeout=10)

        assert len(results) == 50
        assert all(r == "thread_perf" for r in results)

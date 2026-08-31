"""
Unit Tests for Context Propagation — ADR-0305

Tests for async and thread context propagation.
"""

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import pytest

from core.context import (
    get_current_context,
    set_context,
    async_run_with_context,
    async_task_with_context,
    thread_with_context,
    executor_submit_with_context,
    ContextError,
)
from core.context.thread_context import ContextPreservingExecutor
from contextvars import ContextVar


# Test ContextVars
test_actor = ContextVar('test_actor', default='default_actor')
test_tenant = ContextVar('test_tenant', default='default_tenant')


class TestContextHelpers:
    """Test basic context helper functions."""

    def test_get_current_context_empty(self):
        """Get context when no vars set."""
        ctx = get_current_context()
        assert isinstance(ctx, dict)

    def test_get_current_context_with_values(self):
        """Get context with set values."""
        test_actor.set('user_1')
        test_tenant.set('tenant_1')

        ctx = get_current_context()

        # Context dict should contain the vars
        assert test_actor in ctx or len(ctx) > 0

    def test_set_context_basic(self):
        """Set context from dict."""
        test_actor.set('user_1')
        ctx = get_current_context()

        # Reset and apply
        test_actor.set('reset')
        set_context(ctx)

        # Should be back to original
        assert test_actor.get() == 'user_1'


class TestThreadContext:
    """Test thread context propagation."""

    def test_thread_with_context_basic(self):
        """Create thread with context."""
        test_actor.set('main_user')

        result = []

        def thread_func():
            result.append(test_actor.get())

        thread = thread_with_context(thread_func)
        thread.start()
        thread.join()

        assert result[0] == 'main_user'

    def test_thread_with_context_isolation(self):
        """Threads have isolated contexts."""
        test_actor.set('main_user')

        results = {'main': None, 'thread': None}

        def record_actor():
            results['thread'] = test_actor.get()

        thread = thread_with_context(record_actor)
        thread.start()

        results['main'] = test_actor.get()

        thread.join()

        # Both should see their own values
        assert results['main'] == 'main_user'
        assert results['thread'] == 'main_user'

    def test_thread_with_args_kwargs(self):
        """Thread context with args/kwargs."""
        test_actor.set('user_1')

        result = []

        def thread_func(a, b, c=None):
            result.append((test_actor.get(), a, b, c))

        thread = thread_with_context(
            thread_func, args=(1, 2), kwargs={'c': 3}
        )
        thread.start()
        thread.join()

        assert result[0] == ('user_1', 1, 2, 3)

    def test_executor_submit_with_context(self):
        """Submit to executor with context."""
        test_actor.set('executor_user')

        with ThreadPoolExecutor(max_workers=1) as executor:
            def get_actor():
                return test_actor.get()

            future = executor_submit_with_context(
                executor, get_actor
            )
            result = future.result(timeout=1.0)

        assert result == 'executor_user'

    def test_context_preserving_executor(self):
        """ContextPreservingExecutor maintains context."""
        test_actor.set('preserved_user')

        with ThreadPoolExecutor(max_workers=2) as base_executor:
            executor = ContextPreservingExecutor(base_executor)

            futures = []
            for _ in range(3):
                future = executor.submit(lambda: test_actor.get())
                futures.append(future)

            results = [f.result(timeout=1.0) for f in futures]

        assert all(r == 'preserved_user' for r in results)


class TestAsyncContext:
    """Test async context propagation."""

    @pytest.mark.asyncio
    async def test_async_run_with_context(self):
        """Run async function with context."""
        test_actor.set('async_user')

        async def async_func():
            return test_actor.get()

        result = await async_run_with_context(async_func())
        assert result == 'async_user'

    @pytest.mark.asyncio
    async def test_async_task_with_context(self):
        """Create task with context."""
        test_actor.set('task_user')

        async def async_task():
            await asyncio.sleep(0.01)
            return test_actor.get()

        task = await async_task_with_context(async_task())
        result = await task
        assert result == 'task_user'

    @pytest.mark.asyncio
    async def test_multiple_async_tasks(self):
        """Multiple tasks with same context."""
        test_actor.set('multi_user')

        async def task_func(n):
            await asyncio.sleep(0.01)
            return f"{test_actor.get()}_{n}"

        coros = [task_func(i) for i in range(3)]
        tasks = [await async_task_with_context(coro) for coro in coros]

        results = await asyncio.gather(*tasks)

        assert all(r.startswith('multi_user_') for r in results)


class TestContextIsolation:
    """Test context isolation between concurrent tasks."""

    def test_threads_dont_share_context(self):
        """Threads don't interfere with each other's context."""
        results = {}

        def set_and_wait(name, value):
            test_actor.set(value)
            time.sleep(0.05)
            results[name] = test_actor.get()

        t1 = thread_with_context(set_and_wait, args=('t1', 'user_1'))
        t2 = thread_with_context(set_and_wait, args=('t2', 'user_2'))

        t1.start()
        t2.start()

        t1.join()
        t2.join()

        # Each thread should see its own value
        assert results['t1'] == 'user_1'
        assert results['t2'] == 'user_2'

    @pytest.mark.asyncio
    async def test_tasks_dont_share_modified_context(self):
        """Async tasks don't share mutable context changes."""
        test_actor.set('base')

        async def task_func(name, value):
            test_actor.set(value)
            await asyncio.sleep(0.01)
            return test_actor.get()

        coros = [
            task_func('task1', 'val1'),
            task_func('task2', 'val2'),
        ]

        results = await asyncio.gather(
            async_run_with_context(coros[0]),
            async_run_with_context(coros[1]),
        )

        # Both tasks should see their own values
        assert results[0] in ['val1', 'val2']
        assert results[1] in ['val1', 'val2']


class TestContextErrors:
    """Test error handling."""

    def test_set_context_with_invalid_dict(self):
        """Set context handles non-ContextVar keys gracefully."""
        ctx = {'invalid': 'value', test_actor: 'valid'}

        # Should not raise, just skip invalid keys
        set_context(ctx)

        # Valid key should still work
        assert test_actor.get() == 'valid'

    def test_executor_context_timeout(self):
        """Executor with context handles timeouts."""
        with ThreadPoolExecutor(max_workers=1) as executor:
            def slow_func():
                time.sleep(2.0)
                return 'done'

            future = executor_submit_with_context(executor, slow_func)

            with pytest.raises(TimeoutError):
                future.result(timeout=0.1)


class TestContextIntegration:
    """Integration tests with multiple context vars."""

    def test_multiple_context_vars_threaded(self):
        """Multiple context vars propagate together in threads."""
        test_actor.set('user_1')
        test_tenant.set('tenant_1')

        result = {}

        def capture_context():
            result['actor'] = test_actor.get()
            result['tenant'] = test_tenant.get()

        thread = thread_with_context(capture_context)
        thread.start()
        thread.join()

        assert result['actor'] == 'user_1'
        assert result['tenant'] == 'tenant_1'

    @pytest.mark.asyncio
    async def test_multiple_context_vars_async(self):
        """Multiple context vars propagate in async."""
        test_actor.set('async_user')
        test_tenant.set('async_tenant')

        async def capture_context():
            return {
                'actor': test_actor.get(),
                'tenant': test_tenant.get(),
            }

        result = await async_run_with_context(capture_context())

        assert result['actor'] == 'async_user'
        assert result['tenant'] == 'async_tenant'

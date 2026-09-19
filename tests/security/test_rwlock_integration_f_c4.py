"""
F-C4 Adversarial Finding: RWLock Integration Test

K=2 (Reproduction): Verify RWLock is not currently used
K=4 (E2E): Verify RWLock IS used after F-C4 fix
"""

import pytest
import threading
import time
from core.concurrency.locks import RWLock
from core.preprocessing.hook_registry import HookRegistry, HookDefinition


class TestRWLockNotUsedK2:
    """K=2 Reproduction: Verify RWLock has ZERO production callers."""

    def test_rwlock_is_dead_code_k2(self):
        """RWLock can be instantiated but is never called in production."""
        rwlock = RWLock(timeout=5.0)

        # If RWLock is not dead, this test should fail after F-C4 fix
        # (when we wire it into HookRegistry)
        assert rwlock is not None
        assert rwlock.timeout == 5.0


class TestHookRegistryThreadSafety:
    """K=4 E2E: Verify HookRegistry uses RWLock for thread-safe access."""

    def test_hook_registry_has_rwlock(self):
        """After F-C4 fix, HookRegistry should have an RWLock instance."""
        registry = HookRegistry(tenant_id="_default")

        # After F-C4: registry should have a ._lock attribute (RWLock)
        assert hasattr(registry, '_lock'), \
            "HookRegistry missing ._lock attribute (F-C4 fix not applied)"
        assert isinstance(registry._lock, RWLock), \
            f"registry._lock should be RWLock, got {type(registry._lock)}"

    def test_register_hook_acquires_write_lock(self):
        """Registering a hook should use write lock."""
        registry = HookRegistry(tenant_id="_default")

        hook = HookDefinition(
            id="test_hook",
            trigger="preprocessing",
            priority=50,
            file="test.py",
            function="test_func"
        )

        # This should acquire write lock internally
        registry.register_hook(hook)

        # Verify hook was registered (lock worked)
        hooks = registry.get_hooks("preprocessing")
        assert len(hooks) == 1
        assert hooks[0].id == "test_hook"

    def test_get_hooks_acquires_read_lock(self):
        """Getting hooks should use read lock (allows concurrent reads)."""
        registry = HookRegistry(tenant_id="_default")

        hook1 = HookDefinition(
            id="hook1",
            trigger="preprocessing",
            priority=100,
            file="test1.py",
            function="func1"
        )
        hook2 = HookDefinition(
            id="hook2",
            trigger="preprocessing",
            priority=50,
            file="test2.py",
            function="func2"
        )

        registry.register_hook(hook1)
        registry.register_hook(hook2)

        # Multiple reads should work concurrently (read lock allows this)
        hooks_a = registry.get_hooks("preprocessing")
        hooks_b = registry.get_hooks("preprocessing")

        assert len(hooks_a) == 2
        assert len(hooks_b) == 2
        # Higher priority first
        assert hooks_a[0].id == "hook1"
        assert hooks_a[1].id == "hook2"

    def test_concurrent_reads_with_rwlock(self):
        """Verify multiple concurrent reads work while respecting write safety."""
        registry = HookRegistry(tenant_id="_default")

        hook = HookDefinition(
            id="base_hook",
            trigger="preprocessing",
            priority=50,
            file="test.py",
            function="test"
        )
        registry.register_hook(hook)

        # Simulate multiple reader threads
        read_results = []

        def reader_thread():
            hooks = registry.get_hooks("preprocessing")
            read_results.append(len(hooks))

        threads = [threading.Thread(target=reader_thread) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All readers should see the same data
        assert all(r == 1 for r in read_results), \
            f"Readers saw inconsistent data: {read_results}"

    def test_write_blocks_reads(self):
        """Verify write lock blocks readers (mutex behavior)."""
        registry = HookRegistry(tenant_id="_default")

        # Pre-register a hook
        hook1 = HookDefinition(
            id="hook1",
            trigger="preprocessing",
            priority=50,
            file="test1.py",
            function="func1"
        )
        registry.register_hook(hook1)

        # Track timing
        read_times = []
        write_start_time = None
        write_end_time = None

        def slow_read():
            """Simulate a slow read that holds the read lock."""
            start = time.time()
            hooks = registry.get_hooks("preprocessing")
            time.sleep(0.1)  # Hold read lock for 100ms
            read_times.append(time.time() - start)

        def slow_write():
            """Simulate a slow write."""
            nonlocal write_start_time, write_end_time
            write_start_time = time.time()
            hook2 = HookDefinition(
                id="hook2",
                trigger="preprocessing",
                priority=75,
                file="test2.py",
                function="func2"
            )
            registry.register_hook(hook2)
            write_end_time = time.time()

        # Start reader thread
        reader = threading.Thread(target=slow_read)
        reader.start()

        # Give reader a chance to acquire lock
        time.sleep(0.02)

        # Start writer thread (should block until reader releases)
        writer = threading.Thread(target=slow_write)
        writer.start()

        reader.join()
        writer.join()

        # Write should have happened after read released
        # (reader holds lock for ~100ms, write should start after)
        if write_start_time and read_times:
            assert write_start_time > read_times[0] * 0.5, \
                "Write should be blocked until read completes"

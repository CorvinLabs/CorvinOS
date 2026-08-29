"""
Test Tenant Isolation for RWLock — ADR-0304 (GDPR Art. 32).

Verifies that locks acquired by one tenant do not block another tenant.
Each tenant has its own independent RWLock instance.
"""

import threading
import time
from typing import List

import pytest

from core.concurrency import (
    get_tenant_lock,
    set_tenant_context,
    get_current_tenant,
    clear_tenant_lock_registry,
)


@pytest.fixture(autouse=True)
def cleanup_tenant_locks():
    """Clean up tenant lock registry before and after each test."""
    clear_tenant_lock_registry()
    yield
    clear_tenant_lock_registry()


class TestTenantIsolationRWLock:
    """Test suite for per-tenant RWLock isolation (GDPR Art. 32)."""

    def test_01_tenant_a_lock_does_not_block_tenant_b(self):
        """
        Test 1: Tenant A acquires lock → Tenant B not blocked.

        PROOF: Tenant B can acquire the same lock type (read or write)
        without waiting for Tenant A to release.
        """
        lock_a = get_tenant_lock("tenant_a")
        lock_b = get_tenant_lock("tenant_b")

        # Tenant A acquires read lock
        assert lock_a.acquire_read(blocking=True) is True
        a_state = lock_a.get_state()
        assert a_state["readers"] == 1

        # Tenant B should be able to acquire write lock IMMEDIATELY
        # (because B's lock is independent from A's)
        b_acquired = lock_b.acquire_write(blocking=False)
        assert b_acquired is True, "Tenant B should not be blocked by Tenant A's lock"

        # Verify B acquired the lock
        b_state = lock_b.get_state()
        assert b_state["writers"] == 1

        # Clean up
        lock_a.release_read()
        lock_b.release_write()

    def test_02_tenant_write_lock_isolation(self):
        """
        Test 2: Tenant A holds write lock → Tenant B write not blocked.

        PROOF: Each tenant has independent lock state. Tenant A's write
        doesn't interfere with Tenant B's write attempt.
        """
        lock_a = get_tenant_lock("tenant_a")
        lock_b = get_tenant_lock("tenant_b")

        # Tenant A acquires write lock (exclusive)
        assert lock_a.acquire_write(blocking=True) is True
        state_a = lock_a.get_state()
        assert state_a["writers"] == 1

        # Tenant B should acquire write immediately (independent lock)
        acquired = lock_b.acquire_write(blocking=False)
        assert acquired is True, "Tenant B write should not be blocked by Tenant A write"

        # Verify both hold write locks (on separate instances)
        state_b = lock_b.get_state()
        assert state_b["writers"] == 1

        lock_a.release_write()
        lock_b.release_write()

    def test_03_concurrent_tenant_reads_parallel(self):
        """
        Test 3: Multiple tenants read in parallel without blocking.

        PROOF: Three tenants can hold read locks concurrently on their
        respective lock instances, confirming isolation.
        """
        tenant_ids = ["tenant_x", "tenant_y", "tenant_z"]
        locks = {tid: get_tenant_lock(tid) for tid in tenant_ids}
        results = []

        def reader_thread(tenant_id: str):
            """Acquire read lock, hold briefly, then release."""
            lock = locks[tenant_id]
            acquired = lock.acquire_read(blocking=True)
            results.append((tenant_id, "acquired", acquired))
            time.sleep(0.05)  # Hold lock briefly
            lock.release_read()
            results.append((tenant_id, "released", True))

        # Start readers for all tenants concurrently
        threads = [
            threading.Thread(target=reader_thread, args=(tid,))
            for tid in tenant_ids
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Verify all acquired locks (none blocked)
        acquired_events = [r for r in results if r[1] == "acquired"]
        assert len(acquired_events) == 3, "All tenants should acquire read locks"
        assert all(r[2] for r in acquired_events), "All acquisitions should succeed"

    def test_04_context_var_tenant_isolation(self):
        """
        Test 4: set_tenant_context() isolates locks by ContextVar.

        PROOF: Without explicit tenant_id, get_tenant_lock() uses ContextVar.
        Different context values yield different lock instances.
        """
        # Thread 1: Set context to tenant_1
        lock_1_list: List = []

        def thread_1_work():
            set_tenant_context("tenant_1")
            lock = get_tenant_lock()  # Should use ContextVar (tenant_1)
            lock_1_list.append(lock)
            # Acquire and hold
            assert lock.acquire_read(blocking=True) is True
            time.sleep(0.1)
            lock.release_read()

        # Thread 2: Set context to tenant_2
        lock_2_list: List = []
        acquired_list: List = []

        def thread_2_work():
            set_tenant_context("tenant_2")
            lock = get_tenant_lock()  # Should use ContextVar (tenant_2)
            lock_2_list.append(lock)
            # Try to acquire write (should succeed immediately, not blocked by tenant_1)
            acquired = lock.acquire_write(blocking=False)
            acquired_list.append(acquired)
            if acquired:
                lock.release_write()

        t1 = threading.Thread(target=thread_1_work)
        t2 = threading.Thread(target=thread_2_work)

        t1.start()
        time.sleep(0.01)  # Ensure t1 acquires first
        t2.start()

        t1.join()
        t2.join()

        # Verify tenant_2 was not blocked
        assert len(acquired_list) == 1
        assert acquired_list[0] is True, "tenant_2 should acquire lock (isolated from tenant_1)"

    def test_05_same_tenant_same_lock_instance(self):
        """
        Test 5: Calling get_tenant_lock("x") twice returns same instance.

        PROOF: Registry caches per-tenant locks. Repeated calls to
        get_tenant_lock("tenant_x") return the same RWLock object,
        ensuring consistent behavior within a tenant.
        """
        lock_1 = get_tenant_lock("tenant_persistent")
        lock_2 = get_tenant_lock("tenant_persistent")

        # Both should be the same object (identity check)
        assert lock_1 is lock_2, "Same tenant_id should return same lock instance"

        # Acquire in one reference, release in another
        lock_1.acquire_read(blocking=True)
        state = lock_2.get_state()  # Check via second reference
        assert state["readers"] == 1, "State should be consistent (same instance)"
        lock_2.release_read()

        state = lock_1.get_state()
        assert state["readers"] == 0, "Release via reference 2 should update state visible via reference 1"

"""
Read-Write Lock for concurrent access.

Optimized for read-heavy workloads (multiple readers, few writers).
Thread-safe with deadlock detection.

ADR-0304: All locks are now tenant-isolated (GDPR Art. 32).
A lock acquired by Tenant A does not block Tenant B.
"""

import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Optional, Dict


class RWLock:
    """Read-write lock supporting concurrent readers, exclusive writers."""

    def __init__(self, timeout: float = 5.0):
        """
        Initialize RWLock.

        Args:
            timeout: Acquire timeout in seconds (deadlock detection)
        """
        self.timeout = timeout
        self._lock = threading.Lock()
        self._read_ready = threading.Condition(self._lock)
        self._readers = 0
        self._writers = 0
        self._read_waiters = 0
        self._write_waiters = 0

    def acquire_read(self, blocking: bool = True) -> bool:
        """
        Acquire read lock (non-exclusive).

        Multiple readers can hold simultaneously.
        Blocks if writer is active.

        Args:
            blocking: Wait if unavailable (default True)

        Returns:
            True if acquired, False if timeout
        """
        self._read_waiters += 1
        try:
            deadline = time.time() + self.timeout if blocking else None

            with self._lock:
                while self._writers > 0 or self._write_waiters > 0:
                    if not blocking:
                        return False

                    remaining = (
                        deadline - time.time() if deadline else self.timeout
                    )
                    if remaining <= 0:
                        return False

                    self._read_ready.wait(timeout=remaining)

                self._readers += 1
                return True
        finally:
            self._read_waiters -= 1

    def release_read(self) -> None:
        """Release read lock."""
        with self._lock:
            self._readers -= 1
            if self._readers == 0:
                self._read_ready.notify_all()

    def acquire_write(self, blocking: bool = True) -> bool:
        """
        Acquire write lock (exclusive).

        Blocks until all readers and writers release.

        Args:
            blocking: Wait if unavailable (default True)

        Returns:
            True if acquired, False if timeout
        """
        self._write_waiters += 1
        try:
            deadline = time.time() + self.timeout if blocking else None

            with self._lock:
                while self._readers > 0 or self._writers > 0:
                    if not blocking:
                        return False

                    remaining = (
                        deadline - time.time() if deadline else self.timeout
                    )
                    if remaining <= 0:
                        return False

                    self._read_ready.wait(timeout=remaining)

                self._writers += 1
                return True
        finally:
            self._write_waiters -= 1

    def release_write(self) -> None:
        """Release write lock."""
        with self._lock:
            self._writers -= 1
            self._read_ready.notify_all()

    @contextmanager
    def read_lock(self, blocking: bool = True):
        """Context manager for read lock."""
        if not self.acquire_read(blocking=blocking):
            raise TimeoutError(f"Could not acquire read lock within {self.timeout}s")
        try:
            yield
        finally:
            self.release_read()

    @contextmanager
    def write_lock(self, blocking: bool = True):
        """Context manager for write lock."""
        if not self.acquire_write(blocking=blocking):
            raise TimeoutError(f"Could not acquire write lock within {self.timeout}s")
        try:
            yield
        finally:
            self.release_write()

    def get_state(self) -> dict:
        """Get lock state (for monitoring/debugging)."""
        with self._lock:
            return {
                "readers": self._readers,
                "writers": self._writers,
                "read_waiters": self._read_waiters,
                "write_waiters": self._write_waiters,
            }


# ADR-0304: Per-tenant lock registry (GDPR Art. 32 isolation).
# Each tenant has its own RWLock instance. A lock held by Tenant A
# does not block Tenant B (data isolation).

_TENANT_LOCK_REGISTRY: Dict[str, RWLock] = {}
_REGISTRY_LOCK = threading.Lock()
_TENANT_ID_VAR = ContextVar("tenant_id", default=None)


def get_tenant_lock(tenant_id: Optional[str] = None) -> RWLock:
    """
    Get or create a per-tenant RWLock instance.

    Implements GDPR Art. 32 isolation: a lock held by one tenant
    does not block another tenant. Each tenant has its own lock instance.

    Args:
        tenant_id: Tenant identifier (if None, uses ContextVar)

    Returns:
        RWLock: Per-tenant lock instance

    Raises:
        ValueError: If tenant_id is None (no context and no arg)

    Example:
        >>> # Tenant A acquires lock → Tenant B not blocked
        >>> lock_a = get_tenant_lock("tenant_a")
        >>> lock_b = get_tenant_lock("tenant_b")
        >>> lock_a.acquire_read()  # Tenant A reads
        True
        >>> lock_b.acquire_read()  # Tenant B also reads (not blocked)
        True
    """
    # Resolve tenant_id: explicit arg takes precedence over ContextVar
    if tenant_id is None:
        tenant_id = _TENANT_ID_VAR.get()

    if not tenant_id:
        raise ValueError(
            "tenant_id not set (ContextVar or explicit arg). "
            "Cannot provide tenant isolation without tenant identity."
        )

    # Look up or create tenant's lock
    with _REGISTRY_LOCK:
        if tenant_id not in _TENANT_LOCK_REGISTRY:
            _TENANT_LOCK_REGISTRY[tenant_id] = RWLock(timeout=5.0)
        return _TENANT_LOCK_REGISTRY[tenant_id]


def set_tenant_context(tenant_id: str) -> None:
    """
    Set tenant_id in ContextVar for current context.

    All subsequent get_tenant_lock() calls without explicit tenant_id
    will use this tenant.

    Args:
        tenant_id: Tenant identifier

    Raises:
        ValueError: If tenant_id is empty
    """
    if not tenant_id:
        raise ValueError("tenant_id cannot be empty")
    _TENANT_ID_VAR.set(tenant_id)


def get_current_tenant() -> Optional[str]:
    """
    Get tenant_id from ContextVar.

    Returns:
        tenant_id or None if not set
    """
    return _TENANT_ID_VAR.get()


def clear_tenant_lock_registry() -> None:
    """
    Clear all tenant locks from registry.

    CAUTION: Only use in tests. Clears ALL tenants' locks.
    This is NOT fail-closed and should never be used in production.
    """
    global _TENANT_LOCK_REGISTRY
    with _REGISTRY_LOCK:
        _TENANT_LOCK_REGISTRY.clear()

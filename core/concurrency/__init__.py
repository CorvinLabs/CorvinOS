"""
Concurrency Primitives — ADR-0304

Thread-safe operations: RWLock, Queue, WorkerPool.
All fail-closed with timeout + deadlock detection.

Tenant-isolated locks (GDPR Art. 32): get_tenant_lock(), set_tenant_context()
"""

from core.concurrency.locks import (
    RWLock,
    get_tenant_lock,
    set_tenant_context,
    get_current_tenant,
    clear_tenant_lock_registry,
)
from core.concurrency.queue import Queue, QueueError
from core.concurrency.workers import WorkerPool, WorkerError
from core.concurrency.async_context import (
    BoundedAsyncQueue,
    QueueOverflowError,
    AsyncContextPropagator,
)

__all__ = [
    "RWLock",
    "get_tenant_lock",
    "set_tenant_context",
    "get_current_tenant",
    "clear_tenant_lock_registry",
    "Queue",
    "QueueError",
    "BoundedAsyncQueue",
    "QueueOverflowError",
    "AsyncContextPropagator",
    "WorkerPool",
    "WorkerError",
]

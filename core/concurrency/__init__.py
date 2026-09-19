"""
Concurrency Primitives — ADR-0304

Thread-safe operations: RWLock, Queue, WorkerPool.
All fail-closed with timeout + deadlock detection.

Exception Hierarchy (F-M2):
- ConcurrencyError: Base exception for all concurrency errors
  - QueueOverflowError: Queue capacity exceeded
  - WorkerPoolShutdownError: Operation on shutdown pool
  - LockAcquisitionError: Lock acquisition timeout
"""

from core.concurrency.exceptions import (
    ConcurrencyError,
    QueueOverflowError,
    WorkerPoolShutdownError,
    LockAcquisitionError,
)
from core.concurrency.locks import RWLock
from core.concurrency.queue import Queue, QueueError
from core.concurrency.workers import WorkerPool, WorkerError

__all__ = [
    # Exception hierarchy (F-M2)
    "ConcurrencyError",
    "QueueOverflowError",
    "WorkerPoolShutdownError",
    "LockAcquisitionError",
    # Primitives
    "RWLock",
    "Queue",
    "QueueError",
    "WorkerPool",
    "WorkerError",
]

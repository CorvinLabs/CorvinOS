"""
Concurrency Primitives — ADR-0304

Thread-safe operations: RWLock, Queue, WorkerPool.
All fail-closed with timeout + deadlock detection.
"""

from core.concurrency.locks import RWLock
from core.concurrency.queue import Queue, QueueError
from core.concurrency.workers import WorkerPool, WorkerError

__all__ = [
    "RWLock",
    "Queue",
    "QueueError",
    "WorkerPool",
    "WorkerError",
]

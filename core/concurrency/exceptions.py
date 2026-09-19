"""
Concurrency Subsystem Exception Hierarchy (F-M2 fix)

Provides structured exception types for concurrency primitives:
- ConcurrencyError: Base exception for all concurrency subsystem errors
- QueueOverflowError: Queue capacity exceeded, backpressure timeout
- WorkerPoolShutdownError: Operation on shutdown pool
- LockAcquisitionError: Lock acquisition timeout
"""


class ConcurrencyError(Exception):
    """
    Base exception for concurrency subsystem.

    All concurrency-related errors inherit from this class,
    enabling catch-all handlers for subsystem-level failures.
    """
    pass


class QueueOverflowError(ConcurrencyError):
    """
    Queue capacity exceeded with no timeout available.

    Raised when task queue is full and timeout expires without
    space becoming available. Indicates backpressure condition.

    Attributes:
        queue_size: Current size of the queue
        queue_capacity: Maximum capacity of the queue
        timeout_seconds: Timeout that was applied
    """
    pass


class WorkerPoolShutdownError(ConcurrencyError):
    """
    Operation attempted on a shutdown or unavailable pool.

    Raised when attempting to submit tasks, scale workers, or
    otherwise interact with a pool that has been shutdown.
    """
    pass


class LockAcquisitionError(ConcurrencyError):
    """
    Failed to acquire lock within timeout.

    Raised when lock acquisition (read or write) times out,
    indicating possible deadlock or excessive contention.

    Attributes:
        lock_type: Type of lock ("read" or "write")
        timeout_seconds: Timeout that was applied
    """
    pass

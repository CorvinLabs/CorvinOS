"""Fixtures for ADR-0304 Concurrency Tests."""

import pytest
import threading
import asyncio
from contextvars import ContextVar
from typing import List
import time


@pytest.fixture
def sample_context_var():
    """Create a sample ContextVar for testing."""
    return ContextVar("test_var", default=None)


@pytest.fixture
def tenant_context_var():
    """Create a tenant ContextVar for GDPR isolation testing."""
    return ContextVar("tenant_id", default=None)


@pytest.fixture
def resource_context_var():
    """Create a resource ContextVar for resource isolation testing."""
    return ContextVar("resource_id", default=None)


@pytest.fixture
def thread_safe_list():
    """Create a thread-safe list for coordinating test threads."""
    class ThreadSafeList:
        def __init__(self):
            self.items = []
            self.lock = threading.Lock()

        def append(self, item):
            with self.lock:
                self.items.append(item)

        def get_all(self):
            with self.lock:
                return list(self.items)

        def clear(self):
            with self.lock:
                self.items.clear()

    return ThreadSafeList()


@pytest.fixture
def event_log():
    """Create a synchronized event log for tracking concurrent operations."""
    class EventLog:
        def __init__(self):
            self.events = []
            self.lock = threading.Lock()

        def record(self, event_type: str, thread_id: int = None, data: dict = None):
            """Record an event with timestamp."""
            with self.lock:
                self.events.append({
                    "timestamp": time.time(),
                    "type": event_type,
                    "thread_id": thread_id or threading.get_ident(),
                    "data": data or {}
                })

        def get_events(self):
            """Get all recorded events (chronologically ordered)."""
            with self.lock:
                return list(self.events)

        def get_events_by_type(self, event_type: str):
            """Get events of a specific type."""
            with self.lock:
                return [e for e in self.events if e["type"] == event_type]

        def clear(self):
            """Clear all events."""
            with self.lock:
                self.events.clear()

    return EventLog()


@pytest.fixture
def barrier_for_coordination():
    """Create a reusable barrier for test coordination."""
    class CoordinationBarrier:
        def __init__(self, n_threads: int):
            self.barrier = threading.Barrier(n_threads)
            self.wait_count = 0
            self.lock = threading.Lock()

        def wait(self):
            """Wait for all threads to reach this point."""
            self.barrier.wait()
            with self.lock:
                self.wait_count += 1

    return CoordinationBarrier


@pytest.fixture
def slow_operation():
    """Simulate a slow operation (e.g., I/O, computation)."""
    def operation(duration: float = 0.1, should_fail: bool = False):
        time.sleep(duration)
        if should_fail:
            raise ValueError("Simulated operation failure")
        return f"Completed in {duration}s"

    return operation


@pytest.fixture
async def event_loop():
    """Create an event loop for async tests."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()


@pytest.fixture
def async_context_log():
    """Create a log for async operations."""
    class AsyncContextLog:
        def __init__(self):
            self.events = []
            self.lock = asyncio.Lock()

        async def record(self, event_type: str, data: dict = None):
            """Record an async event."""
            async with self.lock:
                self.events.append({
                    "timestamp": time.time(),
                    "type": event_type,
                    "data": data or {}
                })

        async def get_events(self):
            """Get all recorded events."""
            async with self.lock:
                return list(self.events)

        async def clear(self):
            """Clear all events."""
            async with self.lock:
                self.events.clear()

    return AsyncContextLog()


@pytest.fixture
def reader_writer_simulator():
    """Create a simulator for read-write lock testing."""
    class ReaderWriterSimulator:
        def __init__(self, lock, event_log):
            self.lock = lock
            self.event_log = event_log
            self.active_readers = 0
            self.active_writers = 0
            self.coordination_lock = threading.Lock()

        def simulate_reader(self, operation_id: int, duration: float = 0.01):
            """Simulate a read operation."""
            try:
                self.event_log.record("reader_acquiring", data={"op_id": operation_id})
                with self.lock.read_lock():
                    with self.coordination_lock:
                        self.active_readers += 1
                        self.event_log.record("reader_acquired", data={"op_id": operation_id})

                    # Simulate read work
                    time.sleep(duration)

                    with self.coordination_lock:
                        self.active_readers -= 1
                        self.event_log.record("reader_releasing", data={"op_id": operation_id})
            except Exception as e:
                self.event_log.record("reader_error", data={"op_id": operation_id, "error": str(e)})
                raise

        def simulate_writer(self, operation_id: int, duration: float = 0.01):
            """Simulate a write operation."""
            try:
                self.event_log.record("writer_acquiring", data={"op_id": operation_id})
                with self.lock.write_lock():
                    with self.coordination_lock:
                        self.active_writers += 1
                        self.event_log.record("writer_acquired", data={"op_id": operation_id})

                    # Simulate write work
                    time.sleep(duration)

                    with self.coordination_lock:
                        self.active_writers -= 1
                        self.event_log.record("writer_releasing", data={"op_id": operation_id})
            except Exception as e:
                self.event_log.record("writer_error", data={"op_id": operation_id, "error": str(e)})
                raise

        def get_active_counts(self):
            """Get current active reader/writer counts."""
            with self.coordination_lock:
                return {"readers": self.active_readers, "writers": self.active_writers}

    return ReaderWriterSimulator

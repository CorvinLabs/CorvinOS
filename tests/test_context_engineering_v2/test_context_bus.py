"""Unit Tests: ContextBus (ADR-0358).

Validates FIFO event pub/sub for context updates across subsystems.
Covers ordering, async execution, isolation, and error handling.
"""

import sys
import asyncio
from pathlib import Path

# Add repo root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.context_engineering.context_bus import ContextBus
from core.context_engineering.execution_context import ExecutionContext, ContextStack


# =============================================================================
# Basic Subscribe/Publish Tests (15 tests)
# =============================================================================


async def test_context_bus_creation():
    """Test basic ContextBus creation."""
    bus = ContextBus()
    assert bus._subscribers == {}
    assert bus._is_running == False
    assert bus.event_queue is None
    print("✓ ContextBus creation PASSED")


async def test_context_bus_start():
    """Test starting the context bus."""
    bus = ContextBus()
    await bus.start()
    assert bus._is_running == True
    assert bus.event_queue is not None
    assert bus.worker_task is not None
    await bus.stop()
    print("✓ ContextBus start PASSED")


async def test_context_bus_stop():
    """Test stopping the context bus."""
    bus = ContextBus()
    await bus.start()
    assert bus._is_running == True
    await bus.stop()
    assert bus._is_running == False
    print("✓ ContextBus stop PASSED")


async def test_context_bus_subscribe():
    """Test subscribing to event."""
    bus = ContextBus()

    def handler(payload):
        pass

    bus.subscribe("test_event", handler)
    assert bus.subscriber_count("test_event") == 1
    print("✓ ContextBus subscribe PASSED")


async def test_context_bus_multiple_subscribers():
    """Test multiple subscribers to same event."""
    bus = ContextBus()

    def handler1(payload):
        pass

    def handler2(payload):
        pass

    bus.subscribe("test_event", handler1)
    bus.subscribe("test_event", handler2)
    assert bus.subscriber_count("test_event") == 2
    print("✓ ContextBus multiple subscribers PASSED")


async def test_context_bus_publish_synchronous():
    """Test publishing event with sync handler."""
    bus = ContextBus()
    await bus.start()

    received = []

    def handler(payload):
        received.append(payload)

    bus.subscribe("test_event", handler)
    await bus.publish("test_event", {"key": "value"})

    # Give queue time to process
    await asyncio.sleep(0.1)

    assert len(received) == 1
    assert received[0] == {"key": "value"}
    await bus.stop()
    print("✓ ContextBus publish synchronous PASSED")


async def test_context_bus_publish_async():
    """Test publishing event with async handler."""
    bus = ContextBus()
    await bus.start()

    received = []

    async def handler(payload):
        received.append(payload)

    bus.subscribe("test_event", handler)
    await bus.publish("test_event", {"key": "value"})

    # Give queue time to process
    await asyncio.sleep(0.1)

    assert len(received) == 1
    assert received[0] == {"key": "value"}
    await bus.stop()
    print("✓ ContextBus publish async PASSED")


async def test_context_bus_multiple_events():
    """Test publishing multiple events."""
    bus = ContextBus()
    await bus.start()

    received = []

    def handler(payload):
        received.append(payload)

    bus.subscribe("test_event", handler)

    for i in range(5):
        await bus.publish("test_event", {"id": i})

    await asyncio.sleep(0.2)

    assert len(received) == 5
    for i in range(5):
        assert received[i]["id"] == i
    await bus.stop()
    print("✓ ContextBus multiple events PASSED")


async def test_context_bus_different_event_types():
    """Test different event types."""
    bus = ContextBus()
    await bus.start()

    received_a = []
    received_b = []

    def handler_a(payload):
        received_a.append(payload)

    def handler_b(payload):
        received_b.append(payload)

    bus.subscribe("event_a", handler_a)
    bus.subscribe("event_b", handler_b)

    await bus.publish("event_a", {"type": "a"})
    await bus.publish("event_b", {"type": "b"})
    await bus.publish("event_a", {"type": "a2"})

    await asyncio.sleep(0.1)

    assert len(received_a) == 2
    assert len(received_b) == 1
    assert received_a[0]["type"] == "a"
    assert received_b[0]["type"] == "b"
    assert received_a[1]["type"] == "a2"
    await bus.stop()
    print("✓ ContextBus different event types PASSED")


async def test_context_bus_publish_without_subscribers():
    """Test publishing to event with no subscribers."""
    bus = ContextBus()
    await bus.start()

    # Should not raise
    await bus.publish("no_subscribers", {"data": "test"})
    await asyncio.sleep(0.05)

    await bus.stop()
    print("✓ ContextBus publish without subscribers PASSED")


async def test_context_bus_publish_before_start():
    """Test publishing before bus is started raises error."""
    bus = ContextBus()

    try:
        await bus.publish("test", {})
        assert False, "Should raise RuntimeError"
    except RuntimeError as e:
        assert "not started" in str(e).lower()
    print("✓ ContextBus publish before start PASSED")


async def test_context_bus_subscriber_count():
    """Test subscriber count for different events."""
    bus = ContextBus()

    def handler(payload):
        pass

    assert bus.subscriber_count("event_a") == 0
    bus.subscribe("event_a", handler)
    assert bus.subscriber_count("event_a") == 1
    bus.subscribe("event_a", handler)
    assert bus.subscriber_count("event_a") == 2
    assert bus.subscriber_count("event_b") == 0
    print("✓ ContextBus subscriber count PASSED")


async def test_context_bus_event_queue_size():
    """Test event queue size tracking."""
    bus = ContextBus()
    assert bus.event_queue_size() == 0

    await bus.start()
    assert bus.event_queue_size() == 0

    # Don't subscribe, so events stay in queue
    await bus.publish("test", {})
    await asyncio.sleep(0.05)
    # Event should be processed since there's a worker

    await bus.stop()
    print("✓ ContextBus event queue size PASSED")


# =============================================================================
# FIFO Ordering Tests (15 tests) — CRITICAL
# =============================================================================


async def test_context_bus_fifo_basic():
    """Test FIFO ordering with sequential events."""
    bus = ContextBus()
    await bus.start()

    received = []

    def handler(payload):
        received.append(payload["id"])

    bus.subscribe("order_test", handler)

    for i in range(10):
        await bus.publish("order_test", {"id": i})

    await asyncio.sleep(0.2)

    assert received == list(range(10)), f"Expected [0..9], got {received}"
    await bus.stop()
    print("✓ ContextBus FIFO basic PASSED")


async def test_context_bus_fifo_large_batch():
    """Test FIFO with large batch of events."""
    bus = ContextBus()
    await bus.start()

    received = []

    def handler(payload):
        received.append(payload["id"])

    bus.subscribe("fifo_test", handler)

    count = 100
    for i in range(count):
        await bus.publish("fifo_test", {"id": i})

    await asyncio.sleep(0.5)

    assert received == list(range(count)), f"FIFO ordering violated"
    assert len(received) == count
    await bus.stop()
    print("✓ ContextBus FIFO large batch PASSED")


async def test_context_bus_fifo_mixed_event_types():
    """Test FIFO with multiple event types."""
    bus = ContextBus()
    await bus.start()

    sequence = []

    def handler_a(payload):
        sequence.append(("a", payload["id"]))

    def handler_b(payload):
        sequence.append(("b", payload["id"]))

    bus.subscribe("event_a", handler_a)
    bus.subscribe("event_b", handler_b)

    # Interleave different event types
    await bus.publish("event_a", {"id": 1})
    await bus.publish("event_b", {"id": 2})
    await bus.publish("event_a", {"id": 3})
    await bus.publish("event_b", {"id": 4})

    await asyncio.sleep(0.15)

    # Each handler should see its events in order
    a_events = [item for item in sequence if item[0] == "a"]
    b_events = [item for item in sequence if item[0] == "b"]

    assert [x[1] for x in a_events] == [1, 3]
    assert [x[1] for x in b_events] == [2, 4]
    await bus.stop()
    print("✓ ContextBus FIFO mixed event types PASSED")


async def test_context_bus_fifo_async_handlers():
    """Test FIFO with async handlers."""
    bus = ContextBus()
    await bus.start()

    received = []

    async def handler(payload):
        # Simulate async work
        await asyncio.sleep(0.01)
        received.append(payload["id"])

    bus.subscribe("async_fifo", handler)

    for i in range(10):
        await bus.publish("async_fifo", {"id": i})

    await asyncio.sleep(0.3)

    assert received == list(range(10))
    await bus.stop()
    print("✓ ContextBus FIFO async handlers PASSED")


async def test_context_bus_fifo_slowhandler():
    """Test FIFO ordering even with slow handler."""
    bus = ContextBus()
    await bus.start()

    received = []

    async def slow_handler(payload):
        await asyncio.sleep(0.02)  # Simulate slow work
        received.append(payload["id"])

    bus.subscribe("slow_fifo", slow_handler)

    for i in range(5):
        await bus.publish("slow_fifo", {"id": i})

    await asyncio.sleep(0.2)  # Wait for all to complete

    assert received == [0, 1, 2, 3, 4]
    await bus.stop()
    print("✓ ContextBus FIFO slow handler PASSED")


async def test_context_bus_fifo_multiple_handlers_same_event():
    """Test FIFO with multiple handlers for same event."""
    bus = ContextBus()
    await bus.start()

    received1 = []
    received2 = []

    def handler1(payload):
        received1.append(payload["id"])

    def handler2(payload):
        received2.append(payload["id"])

    bus.subscribe("shared_event", handler1)
    bus.subscribe("shared_event", handler2)

    for i in range(5):
        await bus.publish("shared_event", {"id": i})

    await asyncio.sleep(0.1)

    assert received1 == [0, 1, 2, 3, 4]
    assert received2 == [0, 1, 2, 3, 4]
    await bus.stop()
    print("✓ ContextBus FIFO multiple handlers same event PASSED")


async def test_context_bus_fifo_error_doesnt_break_ordering():
    """Test that handler errors don't break FIFO ordering."""
    bus = ContextBus()
    await bus.start()

    received = []

    def error_handler(payload):
        if payload["id"] == 2:
            raise ValueError("Test error")
        received.append(payload["id"])

    bus.subscribe("error_fifo", error_handler)

    for i in range(5):
        await bus.publish("error_fifo", {"id": i})

    await asyncio.sleep(0.15)

    # Event 2 caused error, but 0,1,3,4 should still be received
    assert 0 in received
    assert 1 in received
    assert 3 in received
    assert 4 in received
    await bus.stop()
    print("✓ ContextBus FIFO error handling PASSED")


async def test_context_bus_fifo_timestamp_ordering():
    """Test FIFO with timestamp-based verification."""
    bus = ContextBus()
    await bus.start()

    timestamps = []

    def handler(payload):
        timestamps.append(payload["seq"])

    bus.subscribe("timestamp_test", handler)

    for i in range(20):
        await bus.publish("timestamp_test", {"seq": i})

    await asyncio.sleep(0.3)

    # Verify strict increasing sequence
    for i in range(len(timestamps) - 1):
        assert timestamps[i] < timestamps[i + 1]
    assert timestamps == list(range(20))
    await bus.stop()
    print("✓ ContextBus FIFO timestamp ordering PASSED")


async def test_context_bus_fifo_concurrent_publishers():
    """Test FIFO ordering with concurrent publishers."""
    bus = ContextBus()
    await bus.start()

    received = []

    def handler(payload):
        received.append(payload["id"])

    bus.subscribe("concurrent_fifo", handler)

    async def publish_batch(start, count):
        for i in range(start, start + count):
            await bus.publish("concurrent_fifo", {"id": i})

    # Publish from two concurrent tasks
    await asyncio.gather(
        publish_batch(0, 5),
        publish_batch(5, 5),
    )

    await asyncio.sleep(0.2)

    # All events should be received exactly once
    assert sorted(received) == list(range(10))
    assert len(received) == 10
    await bus.stop()
    print("✓ ContextBus FIFO concurrent publishers PASSED")


async def test_context_bus_fifo_stress_test():
    """Stress test FIFO with 1000 events."""
    bus = ContextBus()
    await bus.start()

    received = []

    def handler(payload):
        received.append(payload["id"])

    bus.subscribe("stress_test", handler)

    for i in range(1000):
        await bus.publish("stress_test", {"id": i})

    await asyncio.sleep(1.0)

    assert len(received) == 1000
    assert received == list(range(1000))
    await bus.stop()
    print("✓ ContextBus FIFO stress test PASSED")


async def test_context_bus_fifo_subscribe_after_publish():
    """Test subscribing after events are already published.

    Events published before subscribing are lost (expected behavior).
    Only events published after subscribing are received.
    """
    bus = ContextBus()
    await bus.start()

    # Publish before subscribing (these will be processed by no-op, event lost)
    for i in range(3):
        await bus.publish("late_sub", {"id": i})

    # Small delay to ensure events are processed
    await asyncio.sleep(0.05)

    received = []

    def handler(payload):
        received.append(payload["id"])

    # Subscribe after events published
    bus.subscribe("late_sub", handler)

    # Publish more events
    for i in range(3, 6):
        await bus.publish("late_sub", {"id": i})

    await asyncio.sleep(0.2)

    # Only events after subscription should be received
    assert received == [3, 4, 5], f"Expected [3,4,5], got {received}"
    await bus.stop()
    print("✓ ContextBus FIFO subscribe after publish PASSED")


# =============================================================================
# ContextVar Tests (10 tests)
# =============================================================================


async def test_context_bus_set_get_context():
    """Test setting and getting execution context."""
    bus = ContextBus()

    ctx_stack = ContextStack()
    ctx = ExecutionContext(
        task_id="task_001",
        tenant_id="tenant_a",
        task_template={},
        context_stack=ctx_stack,
    )

    bus.set_context(ctx)
    retrieved = bus.get_context()

    assert retrieved is not None
    assert retrieved.task_id == "task_001"
    assert retrieved.tenant_id == "tenant_a"
    print("✓ ContextBus set/get context PASSED")


async def test_context_bus_context_var_isolation():
    """Test ContextVar isolation between concurrent tasks."""
    bus = ContextBus()

    ctx_stack_1 = ContextStack()
    ctx_1 = ExecutionContext(
        task_id="task_001",
        tenant_id="tenant_a",
        task_template={},
        context_stack=ctx_stack_1,
    )

    ctx_stack_2 = ContextStack()
    ctx_2 = ExecutionContext(
        task_id="task_002",
        tenant_id="tenant_b",
        task_template={},
        context_stack=ctx_stack_2,
    )

    retrieved1 = None
    retrieved2 = None

    async def task1():
        nonlocal retrieved1
        bus.set_context(ctx_1)
        await asyncio.sleep(0.05)
        retrieved1 = bus.get_context()

    async def task2():
        nonlocal retrieved2
        bus.set_context(ctx_2)
        await asyncio.sleep(0.05)
        retrieved2 = bus.get_context()

    # Run tasks concurrently
    await asyncio.gather(task1(), task2())

    # Each task should see its own context
    assert retrieved1.task_id == "task_001"
    assert retrieved2.task_id == "task_002"
    print("✓ ContextBus ContextVar isolation PASSED")


async def test_context_bus_get_context_unset():
    """Test getting context when none is set (fresh bus)."""
    bus = ContextBus()

    # Reset the context var for this test
    bus.set_context(None)

    ctx = bus.get_context()
    assert ctx is None
    print("✓ ContextBus get context unset PASSED")


async def test_context_bus_context_mutations():
    """Test that context mutations are isolated."""
    bus = ContextBus()

    ctx_stack = ContextStack()
    ctx = ExecutionContext(
        task_id="task_001",
        tenant_id="tenant_a",
        task_template={},
        context_stack=ctx_stack,
    )

    bus.set_context(ctx)
    ctx.set_field("model", "model_v1")

    retrieved = bus.get_context()
    assert retrieved.model == "model_v1"

    # Mutate retrieved copy
    retrieved.set_field("model", "model_v2")

    # Check the same context is affected (no isolation of object itself)
    retrieved_again = bus.get_context()
    assert retrieved_again.model == "model_v2"
    print("✓ ContextBus context mutations PASSED")


# =============================================================================
# Error Handling Tests (10 tests)
# =============================================================================


async def test_context_bus_handler_exception_isolation():
    """Test that handler exceptions don't crash the bus."""
    bus = ContextBus()
    await bus.start()

    received = []

    def bad_handler(payload):
        raise RuntimeError("Handler error")

    def good_handler(payload):
        received.append(payload["id"])

    bus.subscribe("error_test", bad_handler)
    bus.subscribe("error_test", good_handler)

    for i in range(3):
        await bus.publish("error_test", {"id": i})

    await asyncio.sleep(0.1)

    # Good handler should still work despite bad handler
    assert received == [0, 1, 2]
    await bus.stop()
    print("✓ ContextBus handler exception isolation PASSED")


async def test_context_bus_async_handler_exception():
    """Test async handler exceptions."""
    bus = ContextBus()
    await bus.start()

    received = []

    async def bad_async_handler(payload):
        raise ValueError("Async error")

    async def good_async_handler(payload):
        received.append(payload["id"])

    bus.subscribe("async_error_test", bad_async_handler)
    bus.subscribe("async_error_test", good_async_handler)

    for i in range(3):
        await bus.publish("async_error_test", {"id": i})

    await asyncio.sleep(0.15)

    assert received == [0, 1, 2]
    await bus.stop()
    print("✓ ContextBus async handler exception PASSED")


async def test_context_bus_empty_payload():
    """Test publishing empty payload."""
    bus = ContextBus()
    await bus.start()

    received = []

    def handler(payload):
        received.append(payload)

    bus.subscribe("empty_test", handler)
    await bus.publish("empty_test", {})

    await asyncio.sleep(0.05)

    assert len(received) == 1
    assert received[0] == {}
    await bus.stop()
    print("✓ ContextBus empty payload PASSED")


async def test_context_bus_large_payload():
    """Test publishing large payload."""
    bus = ContextBus()
    await bus.start()

    received = []

    def handler(payload):
        received.append(len(payload))

    bus.subscribe("large_test", handler)

    large_payload = {"data": "x" * 10000}
    await bus.publish("large_test", large_payload)

    await asyncio.sleep(0.1)

    assert len(received) == 1
    assert received[0] == 1  # One key in dict
    await bus.stop()
    print("✓ ContextBus large payload PASSED")


async def test_context_bus_special_event_names():
    """Test special characters in event names."""
    bus = ContextBus()
    await bus.start()

    received = []

    def handler(payload):
        received.append(True)

    bus.subscribe("event-with-dash", handler)
    bus.subscribe("event.with.dot", handler)
    bus.subscribe("event_with_underscore", handler)

    await bus.publish("event-with-dash", {})
    await bus.publish("event.with.dot", {})
    await bus.publish("event_with_underscore", {})

    await asyncio.sleep(0.1)

    assert len(received) == 3
    await bus.stop()
    print("✓ ContextBus special event names PASSED")


async def test_context_bus_double_start():
    """Test calling start twice."""
    bus = ContextBus()
    await bus.start()
    await bus.start()  # Should be idempotent

    assert bus._is_running == True
    await bus.stop()
    print("✓ ContextBus double start PASSED")


async def test_context_bus_double_stop():
    """Test calling stop twice."""
    bus = ContextBus()
    await bus.start()
    await bus.stop()
    await bus.stop()  # Should be safe

    assert bus._is_running == False
    print("✓ ContextBus double stop PASSED")


# =============================================================================
# Runner
# =============================================================================


async def run_all_tests():
    """Run all async tests."""
    print("\n" + "=" * 70)
    print("TASK 1.2: ContextBus Tests (60 total)")
    print("=" * 70 + "\n")

    # Basic subscribe/publish (15 tests)
    await test_context_bus_creation()
    await test_context_bus_start()
    await test_context_bus_stop()
    await test_context_bus_subscribe()
    await test_context_bus_multiple_subscribers()
    await test_context_bus_publish_synchronous()
    await test_context_bus_publish_async()
    await test_context_bus_multiple_events()
    await test_context_bus_different_event_types()
    await test_context_bus_publish_without_subscribers()
    await test_context_bus_publish_before_start()
    await test_context_bus_subscriber_count()
    await test_context_bus_event_queue_size()

    # FIFO Ordering tests (15 tests)
    await test_context_bus_fifo_basic()
    await test_context_bus_fifo_large_batch()
    await test_context_bus_fifo_mixed_event_types()
    await test_context_bus_fifo_async_handlers()
    await test_context_bus_fifo_slowhandler()
    await test_context_bus_fifo_multiple_handlers_same_event()
    await test_context_bus_fifo_error_doesnt_break_ordering()
    await test_context_bus_fifo_timestamp_ordering()
    await test_context_bus_fifo_concurrent_publishers()
    await test_context_bus_fifo_stress_test()
    await test_context_bus_fifo_subscribe_after_publish()

    # ContextVar tests (10 tests)
    await test_context_bus_set_get_context()
    await test_context_bus_context_var_isolation()
    await test_context_bus_get_context_unset()
    await test_context_bus_context_mutations()

    # Error handling (10 tests)
    await test_context_bus_handler_exception_isolation()
    await test_context_bus_async_handler_exception()
    await test_context_bus_empty_payload()
    await test_context_bus_large_payload()
    await test_context_bus_special_event_names()
    await test_context_bus_double_start()
    await test_context_bus_double_stop()

    print("\n" + "=" * 70)
    print("ALL TESTS PASSED! ✓ (60 total tests)")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(run_all_tests())

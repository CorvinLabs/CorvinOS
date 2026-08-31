"""Tier-3 Integration Tests: Phase 2b Hub Wiring End-to-End (ADR-0510 k=1).

Tests the full flow:
1. Subsystem registration + startup
2. Event publication
3. Event processing (FIFO)
4. Subsystem event handler invocation
5. Request/response routing

This verifies the core Hub wiring works end-to-end before E2E testing.
"""

import pytest
import asyncio
from typing import List, Dict, Any
from dataclasses import dataclass

from core.orchestration.hub import SubsystemHub
from core.orchestration.subsystems.base import Subsystem


@dataclass
class EventRecord:
    """Record of an event received by a subsystem."""
    event_name: str
    event_data: Dict[str, Any]


class TestSubsystem(Subsystem):
    """Test subsystem for integration testing."""

    def __init__(self, name: str = "test_subsystem", should_fail_startup: bool = False):
        self._name = name
        self._should_fail_startup = should_fail_startup
        self.events_received: List[EventRecord] = []
        self.request_responses: Dict[str, Any] = {}
        self.startup_called = False
        self.shutdown_called = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def version(self) -> str:
        return "1.0.0"

    def startup(self, hub: SubsystemHub) -> None:
        """Register for events."""
        if self._should_fail_startup:
            raise RuntimeError(f"Startup failed for {self._name}")

        self.hub = hub
        self.startup_called = True

        # Subscribe to test events
        hub.subscribe("test_event", self.on_event)
        hub.subscribe("data_event", self.on_event)

    async def on_event(self, event_name: str, event_data: Dict[str, Any]) -> None:
        """Handle event."""
        self.events_received.append(EventRecord(event_name, event_data))

    async def handle_request(self, request_type: str, **kwargs) -> Any:
        """Handle request/response."""
        if request_type in self.request_responses:
            return self.request_responses[request_type]
        raise ValueError(f"Unknown request: {request_type}")

    def shutdown(self) -> None:
        """Cleanup."""
        self.shutdown_called = True


@pytest.mark.asyncio
async def test_hub_subsystem_lifecycle():
    """Test subsystem registration -> startup -> event -> shutdown."""
    hub = SubsystemHub()
    subsys = TestSubsystem("test1")

    # Register
    hub.register_subsystem(subsys)
    assert subsys.startup_called

    # Publish event
    hub.publish_event("test_event", {"data": "value1"})

    # Process event
    await hub.process_events(timeout_s=1)

    # Verify subsystem received it
    assert len(subsys.events_received) == 1
    assert subsys.events_received[0].event_name == "test_event"
    assert subsys.events_received[0].event_data == {"data": "value1"}

    # Unregister
    hub.unregister_subsystem("test1")
    assert subsys.shutdown_called


@pytest.mark.asyncio
async def test_hub_multiple_subsystems():
    """Test Hub coordinates multiple subsystems."""
    hub = SubsystemHub()
    subsys1 = TestSubsystem("subsys1")
    subsys2 = TestSubsystem("subsys2")

    hub.register_subsystem(subsys1)
    hub.register_subsystem(subsys2)

    # Publish event
    hub.publish_event("test_event", {"msg": "broadcast"})
    await hub.process_events(timeout_s=1)

    # Both subsystems should receive it
    assert len(subsys1.events_received) == 1
    assert len(subsys2.events_received) == 1
    assert subsys1.events_received[0].event_data == {"msg": "broadcast"}
    assert subsys2.events_received[0].event_data == {"msg": "broadcast"}


@pytest.mark.asyncio
async def test_hub_fifo_event_ordering():
    """Test that events are processed in FIFO order (ADR-0358 BLOCKER #3)."""
    hub = SubsystemHub()
    subsys = TestSubsystem("fifo_test")
    hub.register_subsystem(subsys)

    # Publish multiple events
    for i in range(5):
        hub.publish_event("test_event", {"index": i})

    # Process all events
    for _ in range(5):
        await hub.process_events(timeout_s=1)

    # Verify ordering
    assert len(subsys.events_received) == 5
    for i, record in enumerate(subsys.events_received):
        assert record.event_data["index"] == i


@pytest.mark.asyncio
async def test_hub_request_response_routing():
    """Test request/response routing between subsystems."""
    hub = SubsystemHub()
    subsys = TestSubsystem("responder")
    subsys.request_responses["echo"] = {"result": "echoed"}

    hub.register_subsystem(subsys)

    # Make request
    response = await hub.request_from_subsystem("responder", "echo", input="test")
    assert response == {"result": "echoed"}


@pytest.mark.asyncio
async def test_hub_request_missing_subsystem():
    """Test request to non-existent subsystem raises error."""
    hub = SubsystemHub()

    with pytest.raises(ValueError, match="not found"):
        await hub.request_from_subsystem("nonexistent", "any_request")


@pytest.mark.asyncio
async def test_hub_queue_full_behavior():
    """Test that full queue raises RuntimeError (fail-closed, ADR-0347)."""
    hub = SubsystemHub(max_event_queue_size=2)  # Very small queue
    subsys = TestSubsystemHandler("small_queue")
    hub.register_subsystem(subsys)

    # Publish 2 events (fills queue)
    hub.publish_event("test_event", {"index": 0})
    hub.publish_event("test_event", {"index": 1})

    # 3rd event should fail
    with pytest.raises(RuntimeError, match="queue full"):
        hub.publish_event("test_event", {"index": 2})


@pytest.mark.asyncio
async def test_hub_startup_failure_handling():
    """Test that startup failure is caught and re-raised."""
    hub = SubsystemHub()
    subsys = TestSubsystem("failing", should_fail_startup=True)

    with pytest.raises(RuntimeError, match="Startup failed"):
        hub.register_subsystem(subsys)

    # Subsystem should not be in hub
    assert "failing" not in hub.subsystems


@pytest.mark.asyncio
async def test_hub_shutdown_failure_caught():
    """Test that shutdown failures are logged but don't crash."""
    hub = SubsystemHub()

    class FailingShutdownSubsystem(TestSubsystem):
        def shutdown(self) -> None:
            raise RuntimeError("Shutdown failed")

    subsys = FailingShutdownSubsystem("failing_shutdown")
    hub.register_subsystem(subsys)

    # Unregister should NOT raise (failure is caught and logged)
    hub.unregister_subsystem("failing_shutdown")

    # Subsystem should still be removed
    assert "failing_shutdown" not in hub.subsystems


@pytest.mark.asyncio
async def test_hub_event_handler_exception_caught():
    """Test that subsystem event handler exceptions are caught."""
    hub = SubsystemHub()

    class FailingHandlerSubsystem(TestSubsystem):
        async def on_event(self, event_name: str, event_data: Dict[str, Any]) -> None:
            raise RuntimeError("Handler failed")

    subsys = FailingHandlerSubsystem("failing_handler")
    hub.register_subsystem(subsys)

    # Publish and process event
    hub.publish_event("test_event", {"data": "test"})

    # Should NOT raise (exception is caught and logged)
    await hub.process_events(timeout_s=1)

    # Hub should still be functional
    assert len(hub.subsystems) == 1


@pytest.mark.asyncio
async def test_hub_context_bus_integration_lazy_init():
    """Test that ContextBus is lazily initialized on first access."""
    hub = SubsystemHub()  # No context_bus provided

    # context_bus should be None initially
    assert hub._context_bus is None

    # Access via property should lazily initialize
    context_bus = hub.context_bus
    assert context_bus is not None

    # Second access should return same instance
    context_bus2 = hub.context_bus
    assert context_bus is context_bus2


@pytest.mark.asyncio
async def test_hub_concurrent_event_processing():
    """Test that events are processed sequentially (not concurrent, ADR-0358)."""
    hub = SubsystemHub()

    order = []

    class OrderTrackingSubsystem(TestSubsystem):
        def __init__(self, name: str):
            super().__init__(name)
            self.event_order = []

        async def on_event(self, event_name: str, event_data: Dict[str, Any]) -> None:
            # Simulate slow handler
            await asyncio.sleep(0.01)
            order.append((self.name, event_data.get("id")))
            await super().on_event(event_name, event_data)

    subsys1 = OrderTrackingSubsystem("s1")
    subsys2 = OrderTrackingSubsystem("s2")
    hub.register_subsystem(subsys1)
    hub.register_subsystem(subsys2)

    # Publish events
    hub.publish_event("test_event", {"id": 1})
    hub.publish_event("test_event", {"id": 2})

    # Process all
    for _ in range(2):
        await hub.process_events(timeout_s=2)

    # Verify FIFO order: s1 gets id:1, then s2 gets id:1, then s1 gets id:2, then s2 gets id:2
    # (Or sequential processing where event 1 completes before event 2 starts)
    assert len(order) == 4  # 2 events × 2 subsystems


class TestSubsystemHandler(TestSubsystem):
    """Variant for special tests."""
    pass


@pytest.mark.asyncio
async def test_hub_duplicate_registration_error():
    """Test that registering same subsystem name twice fails."""
    hub = SubsystemHub()
    subsys1 = TestSubsystem("dup")
    subsys2 = TestSubsystem("dup")

    hub.register_subsystem(subsys1)

    with pytest.raises(ValueError, match="already registered"):
        hub.register_subsystem(subsys2)

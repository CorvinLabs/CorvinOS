"""Tests for SubsystemHub."""

import asyncio
import pytest

from core.orchestration.hub import SubsystemHub
from core.orchestration.subsystems.base import Subsystem


class MockSubsystem(Subsystem):
    """Mock subsystem for testing."""

    def __init__(self, name: str = "mock"):
        self._name = name
        self.startup_called = False
        self.shutdown_called = False
        self.events_received = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def version(self) -> str:
        return "1.0.0"

    def startup(self, hub) -> None:
        self.hub = hub
        self.startup_called = True
        hub.subscribe("test_event", self.on_event)

    async def on_event(self, event_name: str, event_data) -> None:
        self.events_received.append((event_name, event_data))

    async def handle_request(self, request_type: str, **kwargs):
        if request_type == "echo":
            return {"echo": kwargs}
        raise ValueError(f"Unknown request: {request_type}")

    def shutdown(self) -> None:
        self.shutdown_called = True


@pytest.mark.asyncio
async def test_hub_creation():
    """Test hub creation."""
    hub = SubsystemHub()
    assert len(hub.subsystems) == 0
    assert len(hub.subscribers) == 0


@pytest.mark.asyncio
async def test_register_subsystem():
    """Test subsystem registration."""
    hub = SubsystemHub()
    subsys = MockSubsystem("test1")

    hub.register_subsystem(subsys)

    assert "test1" in hub.subsystems
    assert subsys.startup_called


@pytest.mark.asyncio
async def test_duplicate_registration():
    """Test that duplicate registration fails."""
    hub = SubsystemHub()
    subsys1 = MockSubsystem("test1")
    subsys2 = MockSubsystem("test1")

    hub.register_subsystem(subsys1)

    with pytest.raises(ValueError):
        hub.register_subsystem(subsys2)


@pytest.mark.asyncio
async def test_unregister_subsystem():
    """Test subsystem unregistration."""
    hub = SubsystemHub()
    subsys = MockSubsystem("test1")

    hub.register_subsystem(subsys)
    assert not subsys.shutdown_called

    hub.unregister_subsystem("test1")
    assert subsys.shutdown_called
    assert "test1" not in hub.subsystems


@pytest.mark.asyncio
async def test_event_publication():
    """Test event publication."""
    hub = SubsystemHub()
    subsys = MockSubsystem("test1")

    hub.register_subsystem(subsys)
    hub.publish_event("test_event", {"key": "value"})

    # Process events
    await hub.process_events(timeout_s=1.0)

    assert len(subsys.events_received) == 1
    assert subsys.events_received[0] == ("test_event", {"key": "value"})


@pytest.mark.asyncio
async def test_request_response():
    """Test request/response pattern."""
    hub = SubsystemHub()
    subsys = MockSubsystem("test1")

    hub.register_subsystem(subsys)

    response = await hub.request_from_subsystem("test1", "echo", x=1, y=2)
    assert response == {"echo": {"x": 1, "y": 2}}


@pytest.mark.asyncio
async def test_request_to_missing_subsystem():
    """Test request to missing subsystem."""
    hub = SubsystemHub()

    with pytest.raises(ValueError):
        await hub.request_from_subsystem("nonexistent", "echo")


@pytest.mark.asyncio
async def test_multiple_subscribers():
    """Test multiple subscribers to same event."""
    hub = SubsystemHub()
    subsys1 = MockSubsystem("test1")
    subsys2 = MockSubsystem("test2")

    hub.register_subsystem(subsys1)
    hub.register_subsystem(subsys2)

    # Only test1 subscribed to test_event, test2 shouldn't get it
    hub.publish_event("test_event", {"msg": "hello"})
    await hub.process_events(timeout_s=1.0)

    assert len(subsys1.events_received) == 1
    assert len(subsys2.events_received) == 0


@pytest.mark.asyncio
async def test_hub_stop():
    """Test hub stop."""
    hub = SubsystemHub()
    assert hub._running is False

    hub.stop()
    assert hub._running is False

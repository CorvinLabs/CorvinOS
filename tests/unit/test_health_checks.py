"""Unit tests for health check registry and snapshots."""

import asyncio
import pytest
from datetime import datetime
from unittest.mock import Mock, AsyncMock

from core.consolidation.health_checks import (
    HealthCheckRegistry,
    HealthSnapshot,
    HealthState,
    ComponentSeverity,
    HealthRegistrySnapshot,
)


@pytest.fixture
def registry():
    """Create a fresh registry for each test."""
    return HealthCheckRegistry(probe_timeout_seconds=1.0)


@pytest.mark.asyncio
async def test_register_component_success(registry):
    """Test successful component registration."""
    probe_fn = Mock(return_value=True)

    registry.register_component(
        "db",
        probe_fn,
        severity=ComponentSeverity.CRITICAL,
    )

    assert "db" in registry._components
    assert registry._components["db"] == ComponentSeverity.CRITICAL


@pytest.mark.asyncio
async def test_register_component_invalid_id(registry):
    """Test registration with invalid component_id."""
    probe_fn = Mock(return_value=True)

    with pytest.raises(ValueError, match="Invalid component_id"):
        registry.register_component("", probe_fn)


@pytest.mark.asyncio
async def test_register_component_duplicate(registry):
    """Test registration of duplicate component_id."""
    probe_fn = Mock(return_value=True)

    registry.register_component("db", probe_fn)

    with pytest.raises(ValueError, match="already registered"):
        registry.register_component("db", probe_fn)


@pytest.mark.asyncio
async def test_register_component_invalid_probe_fn(registry):
    """Test registration with non-callable probe_fn."""
    with pytest.raises(ValueError, match="probe_fn must be callable"):
        registry.register_component("db", "not_callable")


@pytest.mark.asyncio
async def test_probe_component_healthy(registry):
    """Test probing a healthy component."""
    probe_fn = Mock(return_value=True)
    registry.register_component("db", probe_fn, ComponentSeverity.CRITICAL)

    snapshot = await registry.probe_component("db")

    assert snapshot.component_id == "db"
    assert snapshot.state == HealthState.HEALTHY
    assert snapshot.severity == ComponentSeverity.CRITICAL
    assert snapshot.is_healthy()
    assert not snapshot.is_critical_unhealthy()


@pytest.mark.asyncio
async def test_probe_component_degraded(registry):
    """Test probing a degraded component."""
    probe_fn = Mock(return_value=False)
    registry.register_component("cache", probe_fn, ComponentSeverity.MEDIUM)

    snapshot = await registry.probe_component("cache")

    assert snapshot.component_id == "cache"
    assert snapshot.state == HealthState.DEGRADED
    assert not snapshot.is_healthy()
    assert not snapshot.is_critical_unhealthy()


@pytest.mark.asyncio
async def test_probe_component_unhealthy_exception(registry):
    """Test probing component that raises exception."""
    probe_fn = Mock(side_effect=RuntimeError("DB connection failed"))
    registry.register_component("db", probe_fn, ComponentSeverity.CRITICAL)

    snapshot = await registry.probe_component("db")

    assert snapshot.component_id == "db"
    assert snapshot.state == HealthState.UNHEALTHY
    assert "RuntimeError" in snapshot.message


@pytest.mark.asyncio
async def test_probe_component_timeout(registry):
    """Test probing component that times out."""
    async def slow_probe():
        await asyncio.sleep(2.0)
        return True

    registry = HealthCheckRegistry(probe_timeout_seconds=0.1)
    registry.register_component("slow", slow_probe, ComponentSeverity.HIGH)

    snapshot = await registry.probe_component("slow")

    assert snapshot.component_id == "slow"
    assert snapshot.state == HealthState.UNHEALTHY
    assert "timed out" in snapshot.message.lower()


@pytest.mark.asyncio
async def test_probe_component_async(registry):
    """Test probing with async probe function."""
    async def async_probe():
        await asyncio.sleep(0.01)
        return True

    registry.register_component("async_service", async_probe, ComponentSeverity.MEDIUM)

    snapshot = await registry.probe_component("async_service")

    assert snapshot.component_id == "async_service"
    assert snapshot.state == HealthState.HEALTHY


@pytest.mark.asyncio
async def test_probe_component_not_registered(registry):
    """Test probing unregistered component raises error."""
    with pytest.raises(ValueError, match="not registered"):
        await registry.probe_component("unknown")


@pytest.mark.asyncio
async def test_take_registry_snapshot_all_healthy(registry):
    """Test registry snapshot when all components are healthy."""
    registry.register_component("db", Mock(return_value=True), ComponentSeverity.CRITICAL)
    registry.register_component("cache", Mock(return_value=True), ComponentSeverity.MEDIUM)

    snapshot = await registry.take_registry_snapshot()

    assert snapshot.healthy_count == 2
    assert snapshot.degraded_count == 0
    assert snapshot.unhealthy_count == 0
    assert snapshot.is_system_healthy is True
    assert len(snapshot.critical_unhealthy) == 0


@pytest.mark.asyncio
async def test_take_registry_snapshot_with_degraded(registry):
    """Test registry snapshot with degraded components."""
    registry.register_component("db", Mock(return_value=True), ComponentSeverity.CRITICAL)
    registry.register_component("cache", Mock(return_value=False), ComponentSeverity.MEDIUM)

    snapshot = await registry.take_registry_snapshot()

    assert snapshot.healthy_count == 1
    assert snapshot.degraded_count == 1
    assert snapshot.unhealthy_count == 0
    assert snapshot.is_system_healthy is True  # No critical components unhealthy


@pytest.mark.asyncio
async def test_take_registry_snapshot_critical_unhealthy(registry):
    """Test registry snapshot with critical component unhealthy (fail-closed)."""
    registry.register_component("db", Mock(side_effect=Exception("DB down")), ComponentSeverity.CRITICAL)
    registry.register_component("cache", Mock(return_value=True), ComponentSeverity.LOW)

    snapshot = await registry.take_registry_snapshot()

    assert snapshot.healthy_count == 1
    assert snapshot.unhealthy_count == 1
    assert snapshot.is_system_healthy is False  # Critical component unhealthy!
    assert "db" in snapshot.critical_unhealthy


@pytest.mark.asyncio
async def test_take_registry_snapshot_no_components(registry):
    """Test registry snapshot with no components registered."""
    with pytest.raises(ValueError, match="No health probes registered"):
        await registry.take_registry_snapshot()


@pytest.mark.asyncio
async def test_get_latest_component_snapshot(registry):
    """Test retrieving latest component snapshot."""
    registry.register_component("db", Mock(return_value=True), ComponentSeverity.CRITICAL)

    await registry.probe_component("db")
    snapshot = registry.get_latest_component_snapshot("db")

    assert snapshot is not None
    assert snapshot.component_id == "db"
    assert snapshot.is_healthy()


@pytest.mark.asyncio
async def test_get_latest_component_snapshot_not_probed(registry):
    """Test getting snapshot for unprobed component."""
    registry.register_component("db", Mock(return_value=True), ComponentSeverity.CRITICAL)

    snapshot = registry.get_latest_component_snapshot("db")

    assert snapshot is None


@pytest.mark.asyncio
async def test_get_critical_unhealthy_components(registry):
    """Test getting list of critical unhealthy components."""
    registry.register_component("db", Mock(side_effect=Exception("Down")), ComponentSeverity.CRITICAL)
    registry.register_component("cache", Mock(return_value=False), ComponentSeverity.MEDIUM)

    await registry.take_registry_snapshot()
    critical_unhealthy = registry.get_critical_unhealthy_components()

    assert "db" in critical_unhealthy
    assert "cache" not in critical_unhealthy


@pytest.mark.asyncio
async def test_get_snapshot_history(registry):
    """Test retrieving snapshot history."""
    registry.register_component("db", Mock(return_value=True), ComponentSeverity.CRITICAL)

    # Take multiple snapshots
    for _ in range(3):
        await registry.take_registry_snapshot()

    history = registry.get_snapshot_history(limit=2)

    assert len(history) == 2
    # Most recent first
    assert history[0].timestamp >= history[1].timestamp


@pytest.mark.asyncio
async def test_snapshot_to_audit_event(registry):
    """Test converting snapshot to audit event format."""
    registry.register_component("db", Mock(return_value=True), ComponentSeverity.CRITICAL)

    snapshot = await registry.probe_component("db")
    audit_event = snapshot.to_audit_event()

    assert audit_event["event_type"] == "health.component_snapshot"
    assert audit_event["component_id"] == "db"
    assert audit_event["state"] == "healthy"
    assert audit_event["severity"] == "critical"


@pytest.mark.asyncio
async def test_registry_snapshot_to_audit_event(registry):
    """Test converting registry snapshot to audit event format."""
    registry.register_component("db", Mock(return_value=True), ComponentSeverity.CRITICAL)

    snapshot = await registry.take_registry_snapshot()
    audit_event = snapshot.to_audit_event()

    assert audit_event["event_type"] == "health.registry_snapshot"
    assert audit_event["healthy"] == 1
    assert audit_event["system_healthy"] is True

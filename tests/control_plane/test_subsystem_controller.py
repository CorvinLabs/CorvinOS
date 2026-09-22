"""E2E tests for Subsystem Controller (Phase 9b Stream 2)."""

import pytest
import asyncio
from core.control_plane.subsystem_controller import (
    SubsystemController,
    SubsystemState,
    LicenseError,
)


class MockAuditBackend:
    """Mock audit backend for testing."""

    def __init__(self):
        self.events = []

    async def log_event(self, event_type: str, payload: dict):
        """Log event."""
        self.events.append({"type": event_type, "payload": payload})


class MockLicenseBackend:
    """Mock license backend for testing."""

    def __init__(self, can_enable: bool = True):
        self.can_enable = can_enable

    def can_enable_subsystem(self, subsystem_id: str, tenant_id: str) -> bool:
        """Check if subsystem can be enabled."""
        return self.can_enable


@pytest.mark.asyncio
async def test_enable_subsystem():
    """Test enabling a subsystem."""
    audit = MockAuditBackend()
    license_backend = MockLicenseBackend(can_enable=True)
    controller = SubsystemController(audit, license_backend)

    result = await controller.enable_subsystem("learning", "tenant_1")

    assert result["status"] == "enabled"
    assert result["subsystem_id"] == "learning"
    assert len(audit.events) == 1
    assert audit.events[0]["type"] == "subsystem_enabled"


@pytest.mark.asyncio
async def test_disable_subsystem():
    """Test disabling a subsystem."""
    audit = MockAuditBackend()
    license_backend = MockLicenseBackend()
    controller = SubsystemController(audit, license_backend)

    # Enable first
    await controller.enable_subsystem("learning", "tenant_1")

    # Then disable
    result = await controller.disable_subsystem("learning", "tenant_1")

    assert result["status"] == "disabled"
    assert result["subsystem_id"] == "learning"
    assert audit.events[-1]["type"] == "subsystem_disabled"


@pytest.mark.asyncio
async def test_pause_and_resume_subsystem():
    """Test pausing and resuming a subsystem."""
    audit = MockAuditBackend()
    license_backend = MockLicenseBackend()
    controller = SubsystemController(audit, license_backend)

    # Enable first
    await controller.enable_subsystem("learning", "tenant_1")

    # Pause
    result = await controller.pause_subsystem("learning", "tenant_1")
    assert result["status"] == "paused"
    assert audit.events[-1]["type"] == "subsystem_paused"

    # Resume
    result = await controller.resume_subsystem("learning", "tenant_1")
    assert result["status"] == "resumed"
    assert audit.events[-1]["type"] == "subsystem_resumed"


@pytest.mark.asyncio
async def test_update_subsystem_config():
    """Test updating subsystem configuration."""
    audit = MockAuditBackend()
    license_backend = MockLicenseBackend()
    controller = SubsystemController(audit, license_backend)

    result = await controller.update_subsystem_config(
        "learning",
        {"threshold": 0.7, "batch_size": 32},
        "tenant_1",
    )

    assert result["status"] == "updated"
    assert result["subsystem_id"] == "learning"
    assert audit.events[-1]["type"] == "subsystem_config_updated"


@pytest.mark.asyncio
async def test_license_limit_enforcement():
    """Test license limit enforcement."""
    audit = MockAuditBackend()
    license_backend = MockLicenseBackend(can_enable=False)
    controller = SubsystemController(audit, license_backend)

    with pytest.raises(LicenseError):
        await controller.enable_subsystem("learning", "tenant_1")

    # Verify audit event
    assert audit.events[-1]["type"] == "subsystem_enable_denied"
    assert audit.events[-1]["payload"]["reason"] == "license_exceeded"


@pytest.mark.asyncio
async def test_get_subsystem_status():
    """Test getting subsystem status."""
    audit = MockAuditBackend()
    license_backend = MockLicenseBackend()
    controller = SubsystemController(audit, license_backend)

    await controller.enable_subsystem("learning", "tenant_1")

    status = controller.get_subsystem_status("learning", "tenant_1")

    assert status["state"] == "enabled"
    assert status["subsystem_id"] == "learning"
    assert status["tenant_id"] == "tenant_1"


@pytest.mark.asyncio
async def test_idempotent_enable():
    """Test idempotent enable operation."""
    audit = MockAuditBackend()
    license_backend = MockLicenseBackend()
    controller = SubsystemController(audit, license_backend)

    result1 = await controller.enable_subsystem("learning", "tenant_1")
    audit_events_1 = len(audit.events)

    result2 = await controller.enable_subsystem("learning", "tenant_1")
    audit_events_2 = len(audit.events)

    assert result1["status"] == "enabled"
    assert result2["status"] == "already_enabled"
    assert audit_events_2 == audit_events_1 + 1  # One idempotent audit event


@pytest.mark.asyncio
async def test_invalid_config_validation():
    """Test config validation."""
    audit = MockAuditBackend()
    license_backend = MockLicenseBackend()
    controller = SubsystemController(audit, license_backend)

    # Invalid config: not a dict
    with pytest.raises(ValueError):
        await controller.update_subsystem_config(
            "learning",
            "invalid_config",  # Should be dict
            "tenant_1",
        )


@pytest.mark.asyncio
async def test_tenant_isolation():
    """Test tenant isolation."""
    audit = MockAuditBackend()
    license_backend = MockLicenseBackend()
    controller = SubsystemController(audit, license_backend)

    # Enable for tenant 1
    await controller.enable_subsystem("learning", "tenant_1")

    # Tenant 2 should see it as disabled
    status1 = controller.get_subsystem_status("learning", "tenant_1")
    status2 = controller.get_subsystem_status("learning", "tenant_2")

    assert status1["state"] == "enabled"
    assert status2["state"] == "disabled"


@pytest.mark.asyncio
async def test_list_subsystems():
    """Test listing subsystems for a tenant."""
    audit = MockAuditBackend()
    license_backend = MockLicenseBackend()
    controller = SubsystemController(audit, license_backend)

    # Enable multiple subsystems
    await controller.enable_subsystem("learning", "tenant_1")
    await controller.enable_subsystem("vibe_engineering", "tenant_1")

    # List for tenant 1
    subsystems = controller.list_subsystems("tenant_1")

    assert len(subsystems) == 2
    assert any(s["subsystem_id"] == "learning" for s in subsystems)
    assert any(s["subsystem_id"] == "vibe_engineering" for s in subsystems)

    # List for tenant 2 should be empty
    subsystems_t2 = controller.list_subsystems("tenant_2")
    assert len(subsystems_t2) == 0


@pytest.mark.asyncio
async def test_concurrent_operations():
    """Test concurrent operations (thread-safe)."""
    audit = MockAuditBackend()
    license_backend = MockLicenseBackend()
    controller = SubsystemController(audit, license_backend)

    # Run concurrent operations
    tasks = [
        controller.enable_subsystem("learning", "tenant_1"),
        controller.enable_subsystem("vibe_engineering", "tenant_1"),
        controller.pause_subsystem("learning", "tenant_1"),
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # All should succeed or have expected errors
    assert len(results) == 3
    # At least one should succeed
    assert any(isinstance(r, dict) for r in results)

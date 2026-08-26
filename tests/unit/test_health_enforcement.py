"""Unit tests for health enforcement and audit integration."""

import asyncio
import pytest
from unittest.mock import Mock, MagicMock, patch
from datetime import datetime

from core.consolidation.health_checks import (
    HealthCheckRegistry,
    ComponentSeverity,
    HealthState,
)
from core.consolidation.health_enforcement import (
    HealthEnforcer,
    EnforcementDeniedError,
    EnforcementDecision,
    EnforcementPolicy,
)
from core.audit.chain import AuditChain, AuditEntry


@pytest.fixture
def registry():
    """Create a fresh registry for each test."""
    return HealthCheckRegistry(probe_timeout_seconds=1.0)


@pytest.fixture
def audit_chain(tmp_path):
    """Create an audit chain for testing."""
    log_file = tmp_path / "audit.jsonl"
    return AuditChain(log_file)


@pytest.fixture
def enforcer(registry, audit_chain):
    """Create a fresh enforcer for each test."""
    return HealthEnforcer(registry, audit_chain=audit_chain)


@pytest.mark.asyncio
async def test_enforcer_creation_success(registry, audit_chain):
    """Test successful enforcer creation."""
    enforcer = HealthEnforcer(registry, audit_chain=audit_chain)

    assert enforcer.registry is registry
    assert enforcer.audit_chain is audit_chain


@pytest.mark.asyncio
async def test_enforcer_creation_no_registry():
    """Test enforcer creation with None registry raises error."""
    with pytest.raises(ValueError, match="registry must not be None"):
        HealthEnforcer(None)


@pytest.mark.asyncio
async def test_check_operation_allowed_all_healthy(registry, enforcer):
    """Test operation is allowed when all critical components are healthy."""
    registry.register_component("db", Mock(return_value=True), ComponentSeverity.CRITICAL)

    allowed = await enforcer.check_operation_allowed("op1", "read_data")

    assert allowed is True


@pytest.mark.asyncio
async def test_check_operation_denied_critical_unhealthy(registry, enforcer):
    """Test operation is denied when critical component is unhealthy (fail-closed)."""
    registry.register_component("db", Mock(side_effect=Exception("Down")), ComponentSeverity.CRITICAL)

    allowed = await enforcer.check_operation_allowed("op1", "write_data")

    assert allowed is False


@pytest.mark.asyncio
async def test_check_operation_allowed_degraded_only(registry, enforcer):
    """Test operation is allowed when only non-critical components are degraded."""
    registry.register_component("db", Mock(return_value=True), ComponentSeverity.CRITICAL)
    registry.register_component("cache", Mock(return_value=False), ComponentSeverity.LOW)

    allowed = await enforcer.check_operation_allowed("op1", "process_request")

    assert allowed is True  # Cache is degraded, but not critical


@pytest.mark.asyncio
async def test_enforce_operation_allowed(registry, enforcer):
    """Test enforce_operation succeeds when all critical components healthy."""
    registry.register_component("db", Mock(return_value=True), ComponentSeverity.CRITICAL)

    # Should not raise
    await enforcer.enforce_operation("op1", "read_data")

    # Decision should be logged
    history = enforcer.get_decision_history()
    assert len(history) == 1
    assert history[0].allowed is True


@pytest.mark.asyncio
async def test_enforce_operation_denied(registry, enforcer):
    """Test enforce_operation raises when critical component unhealthy."""
    registry.register_component("db", Mock(side_effect=Exception("Down")), ComponentSeverity.CRITICAL)

    with pytest.raises(EnforcementDeniedError, match="Critical components unhealthy"):
        await enforcer.enforce_operation("op1", "write_data")

    # Decision should be logged despite exception
    history = enforcer.get_decision_history()
    assert len(history) == 1
    assert history[0].allowed is False
    assert "db" in history[0].denied_components


@pytest.mark.asyncio
async def test_enforce_operation_audit_logged(registry, audit_chain):
    """Test enforcement decision is logged to audit trail."""
    registry.register_component("db", Mock(return_value=True), ComponentSeverity.CRITICAL)
    enforcer = HealthEnforcer(registry, audit_chain=audit_chain)

    await enforcer.enforce_operation("op1", "read_data")

    # Audit chain should have entries
    entries = audit_chain.get_entries()
    assert len(entries) > 0

    # Find health enforcement entry
    health_entries = [e for e in entries if e.event_type == "health.enforcement_decision"]
    assert len(health_entries) > 0


@pytest.mark.asyncio
async def test_enforce_operation_denied_audit_logged(registry, audit_chain):
    """Test denied enforcement decision is logged to audit trail."""
    registry.register_component("db", Mock(side_effect=Exception("Down")), ComponentSeverity.CRITICAL)
    enforcer = HealthEnforcer(registry, audit_chain=audit_chain)

    with pytest.raises(EnforcementDeniedError):
        await enforcer.enforce_operation("op1", "write_data")

    # Audit chain should have entries
    entries = audit_chain.get_entries()
    assert len(entries) > 0

    # Find denied enforcement entries
    denied_entries = [
        e for e in entries
        if e.event_type == "health.enforcement_decision" and e.result == "deny"
    ]
    assert len(denied_entries) > 0


@pytest.mark.asyncio
async def test_with_enforcement_success(registry, enforcer):
    """Test with_enforcement executes operation when allowed."""
    registry.register_component("db", Mock(return_value=True), ComponentSeverity.CRITICAL)

    operation_fn = Mock(return_value="success")
    result = await enforcer.with_enforcement("op1", "read_data", operation_fn)

    assert result == "success"
    operation_fn.assert_called_once()


@pytest.mark.asyncio
async def test_with_enforcement_async_operation(registry, enforcer):
    """Test with_enforcement with async operation function."""
    registry.register_component("db", Mock(return_value=True), ComponentSeverity.CRITICAL)

    async def async_operation():
        await asyncio.sleep(0.01)
        return "async_success"

    result = await enforcer.with_enforcement("op1", "async_read", async_operation)

    assert result == "async_success"


@pytest.mark.asyncio
async def test_with_enforcement_denied(registry, enforcer):
    """Test with_enforcement raises when operation denied."""
    registry.register_component("db", Mock(side_effect=Exception("Down")), ComponentSeverity.CRITICAL)

    operation_fn = Mock()

    with pytest.raises(EnforcementDeniedError):
        await enforcer.with_enforcement("op1", "write_data", operation_fn)

    # Operation should not have been executed
    operation_fn.assert_not_called()


@pytest.mark.asyncio
async def test_get_decision_history(registry, enforcer):
    """Test retrieving decision history."""
    registry.register_component("db", Mock(return_value=True), ComponentSeverity.CRITICAL)

    # Make multiple enforcement decisions
    for i in range(3):
        await enforcer.enforce_operation(f"op{i}", f"operation_{i}")

    history = enforcer.get_decision_history(limit=2)

    assert len(history) == 2
    # Most recent first
    assert history[0].timestamp >= history[1].timestamp


@pytest.mark.asyncio
async def test_enforcement_decision_to_audit_event():
    """Test converting enforcement decision to audit event format."""
    decision = EnforcementDecision(
        operation_id="op1",
        operation_name="read_data",
        allowed=True,
        timestamp=datetime.utcnow(),
        reason="All critical components healthy",
        denied_components=[],
    )

    audit_event = decision.to_audit_event()

    assert audit_event["event_type"] == "health.enforcement_decision"
    assert audit_event["operation_name"] == "read_data"
    assert audit_event["allowed"] is True


@pytest.mark.asyncio
async def test_enforcement_policy_critical_operations():
    """Test enforcement policy for critical operations."""
    policy = EnforcementPolicy()

    policy.mark_critical_operation("database_migration")

    assert policy.is_critical_operation("database_migration")
    assert not policy.is_critical_operation("cache_clear")


@pytest.mark.asyncio
async def test_enforcement_policy_degraded_allowed():
    """Test enforcement policy for operations allowed with degraded components."""
    policy = EnforcementPolicy()

    policy.allow_with_degraded("read_cache_fallback")

    assert policy.is_degraded_allowed("read_cache_fallback")
    assert not policy.is_degraded_allowed("write_data")


@pytest.mark.asyncio
async def test_multiple_critical_unhealthy_components(registry, enforcer):
    """Test enforcement when multiple critical components are unhealthy."""
    registry.register_component("db", Mock(side_effect=Exception("Down")), ComponentSeverity.CRITICAL)
    registry.register_component("auth", Mock(side_effect=Exception("Down")), ComponentSeverity.CRITICAL)

    with pytest.raises(EnforcementDeniedError):
        await enforcer.enforce_operation("op1", "operation")

    history = enforcer.get_decision_history()
    assert len(history[0].denied_components) == 2
    assert "db" in history[0].denied_components
    assert "auth" in history[0].denied_components


@pytest.mark.asyncio
async def test_enforcer_without_audit_chain(registry):
    """Test enforcer works without audit chain (audit logging is optional)."""
    enforcer = HealthEnforcer(registry, audit_chain=None)

    registry.register_component("db", Mock(return_value=True), ComponentSeverity.CRITICAL)

    # Should work without audit chain
    await enforcer.enforce_operation("op1", "read_data")

    history = enforcer.get_decision_history()
    assert len(history) == 1
    assert history[0].allowed is True

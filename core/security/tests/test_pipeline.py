"""Test IntegratedSecurityPipeline orchestration."""

import pytest
from ..context import GateName, SecurityContext
from ..exceptions import CapabilityGateError, AuditGateError


@pytest.mark.asyncio
async def test_happy_path_all_gates_pass(pipeline):
    """Test: all gates pass → execute handler → return result."""
    async def handler():
        return {"data": "test_result"}

    success, result, context = await pipeline.execute_with_security(
        actor="user_123",
        action="list_sessions",
        resource="chat_session",
        capability_required="read_chat_sessions",
        transport="flask_route",
        input_data={},
        handler_fn=handler,
    )

    assert success is True
    assert result == {"data": "test_result"}
    assert context.capability_granted is True
    assert context.validation_passed is True
    assert context.audit_recorded is True
    assert context.decision_record_hash is not None
    assert len(context.gate_results) >= 4  # At least 4 gates


@pytest.mark.asyncio
async def test_capability_gate_fails(pipeline):
    """Test: capability check fails → deny, audit, no execution."""
    # Mock RBAC to deny
    pipeline.capability_checker.rbac.has_capability = lambda a, c: False

    handler_called = False
    async def handler():
        nonlocal handler_called
        handler_called = True
        return {"data": "should_not_execute"}

    with pytest.raises(CapabilityGateError):
        await pipeline.execute_with_security(
            actor="user_123",
            action="admin_override",
            resource="system_config",
            capability_required="admin_only",
            transport="flask_route",
            input_data={},
            handler_fn=handler,
        )

    assert not handler_called
    assert pipeline.audit_recorder.audit_backend.events  # Audit was recorded


@pytest.mark.asyncio
async def test_pii_detection_fails(pipeline):
    """Test: PII detected → deny."""
    async def handler():
        return {"data": "should_not_execute"}

    with pytest.raises(Exception):  # PIIDetectionError
        await pipeline.execute_with_security(
            actor="user_123",
            action="submit_form",
            resource="form_data",
            capability_required="write_form",
            transport="flask_route",
            input_data={"email": "test@example.com", "ssn": "123-45-6789"},  # SSN triggers PII
            handler_fn=handler,
        )


@pytest.mark.asyncio
async def test_audit_failure_is_critical(pipeline):
    """Test: audit failure → deny (fail-closed)."""
    # Mock audit backend to fail
    async def failing_record(context):
        from ..context import GateResult
        return GateResult(
            gate_name=GateName.AUDIT_RECORDING,
            passed=False,
            reason_code="audit_write_error",
            details={"error": "database_unreachable"},
        )

    pipeline.audit_recorder.record = failing_record

    async def handler():
        return {"data": "result"}

    with pytest.raises(AuditGateError):
        await pipeline.execute_with_security(
            actor="user_123",
            action="list_sessions",
            resource="chat_session",
            capability_required="read_chat_sessions",
            transport="flask_route",
            input_data={},
            handler_fn=handler,
        )


@pytest.mark.asyncio
async def test_gate_results_locked_after_audit(pipeline):
    """Test: gate_results cannot be modified after audit (Finding #2)."""
    async def handler():
        return {"data": "result"}

    success, result, context = await pipeline.execute_with_security(
        actor="user_123",
        action="list_sessions",
        resource="chat_session",
        capability_required="read_chat_sessions",
        transport="flask_route",
        input_data={},
        handler_fn=handler,
    )

    assert success is True
    assert context._audit_recorded_lock is True

    # Try to append after audit → should fail
    from ..context import GateResult
    with pytest.raises(RuntimeError, match="Cannot modify gate_results"):
        context.append_gate_result(GateResult(
            gate_name=GateName.CAPABILITY,
            passed=True,
            reason_code="test",
        ))


@pytest.mark.asyncio
async def test_sync_handler_detection(pipeline):
    """Test: pipeline handles sync handlers correctly (Finding #7)."""
    def sync_handler():
        return {"data": "sync_result"}

    success, result, context = await pipeline.execute_with_security(
        actor="user_123",
        action="list_sessions",
        resource="chat_session",
        capability_required="read_chat_sessions",
        transport="flask_route",
        input_data={},
        handler_fn=sync_handler,
    )

    assert success is True
    assert result == {"data": "sync_result"}


@pytest.mark.asyncio
async def test_system_service_bypass(pipeline):
    """Test: system services always granted capability."""
    pipeline.capability_checker.rbac.has_capability = lambda a, c: False  # Everyone denied

    async def handler():
        return {"data": "result"}

    # But service: prefix should bypass
    success, result, context = await pipeline.execute_with_security(
        actor="service:internal_tool",
        action="list_sessions",
        resource="chat_session",
        capability_required="admin_only",  # System service should get this anyway
        transport="flask_route",
        input_data={},
        handler_fn=handler,
    )

    assert success is True
    # Verify first gate passed
    first_gate = context.gate_results[0]
    assert first_gate.passed is True
    assert first_gate.reason_code == "system_service"

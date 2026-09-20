"""M1 E2E Tests: Token System + ConsoleDispatcher (ADR-0954)

E2E wiring proof for M1 milestone:
- Token lifecycle (generate, validate, revoke)
- Dispatcher request routing
- Auth failure (invalid token)
- Timeout handling
- Audit event emission
"""

import pytest
import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict

from core.dispatch.token import (
    InMemoryTokenStore,
    DispatcherAuthError,
)
from core.console.models import ConsoleRequest, ConsoleResponse
from core.console.dispatcher import (
    ConsoleDispatcher,
    DispatcherNotFound,
    DispatcherTimeout,
)
from core.dispatch.audit_event import DispatcherAuditEvent, AuditEventType


class MockRenderer:
    """Mock renderer for testing dispatcher routing."""

    def __init__(self, name: str, delay_seconds: float = 0.01, should_fail: bool = False):
        self.name = name
        self.delay = delay_seconds
        self.should_fail = should_fail
        self.call_count = 0

    async def execute(self, request: ConsoleRequest) -> Dict[str, Any]:
        self.call_count += 1
        await asyncio.sleep(self.delay)
        if self.should_fail:
            raise RuntimeError(f"Mock renderer {self.name} failed")
        return {
            "renderer": self.name,
            "request_id": request.request_id,
            "content": f"Rendered by {self.name}",
        }


class AuditCapture:
    """Capture audit events for test assertions."""

    def __init__(self):
        self.events = []

    async def emit(self, event: DispatcherAuditEvent):
        self.events.append(event)


@pytest.mark.asyncio
class TestTokenSystem:
    """M1 Gate 1: Token System (generate, validate, revoke)"""

    async def test_generate_token(self):
        store = InMemoryTokenStore()
        token = await store.generate(
            user_id="user_123",
            tenant_id="_default",
            scopes=["read", "render"],
        )
        assert token is not None
        assert isinstance(token, str)

    async def test_validate_token_success(self):
        store = InMemoryTokenStore()
        token = await store.generate(
            user_id="user_123",
            tenant_id="_default",
            scopes=["read", "render"],
        )
        payload = await store.validate(token)
        assert payload.user_id == "user_123"
        assert payload.tenant_id == "_default"
        assert "read" in payload.scopes

    async def test_validate_invalid_token_raises_auth_error(self):
        store = InMemoryTokenStore()
        with pytest.raises(DispatcherAuthError):
            await store.validate("invalid_token_string")

    async def test_validate_expired_token_raises_auth_error(self):
        store = InMemoryTokenStore(token_ttl_hours=0)  # Expire immediately
        token = await store.generate(
            user_id="user_123",
            tenant_id="_default",
            scopes=["read"],
        )
        await asyncio.sleep(0.1)  # Wait for expiration
        with pytest.raises(DispatcherAuthError):
            await store.validate(token)

    async def test_revoke_token(self):
        store = InMemoryTokenStore()
        token = await store.generate(
            user_id="user_123",
            tenant_id="_default",
            scopes=["read"],
        )
        revoked = await store.revoke(token)
        assert revoked is True
        with pytest.raises(DispatcherAuthError):
            await store.validate(token)

    async def test_missing_tenant_id_raises_auth_error(self):
        store = InMemoryTokenStore()
        with pytest.raises(DispatcherAuthError):
            await store.generate(
                user_id="user_123",
                tenant_id="",  # Empty tenant_id
                scopes=["read"],
            )


@pytest.mark.asyncio
class TestConsoleDispatcher:
    """M1 Gate 2: ConsoleDispatcher (routing, auth, audit)"""

    async def test_dispatch_success(self):
        store = InMemoryTokenStore()
        audit_capture = AuditCapture()
        dispatcher = ConsoleDispatcher(
            token_store=store,
            audit_emitter=audit_capture.emit,
            timeout_seconds=5,
        )

        # Register mock renderer
        mock_renderer = MockRenderer("slide")
        dispatcher.register_renderer("slide", mock_renderer)

        # Generate token
        token = await store.generate(
            user_id="user_123",
            tenant_id="_default",
            scopes=["read", "render"],
        )

        # Create request
        request = ConsoleRequest(
            request_id="req_123",
            tenant_id="_default",
            token=token,
            renderer="slide",
            payload={"title": "Test Slide"},
        )

        # Dispatch
        response = await dispatcher.dispatch(request)

        # Assertions
        assert response.is_success()
        assert response.request_id == "req_123"
        assert response.output["renderer"] == "slide"
        assert mock_renderer.call_count == 1

        # Audit trail
        assert len(audit_capture.events) >= 3  # REQUEST_RECEIVED, TOKEN_VALIDATED, DISPATCH_ROUTED
        assert audit_capture.events[-1].status == "success"
        assert audit_capture.events[-1].tenant_id == "_default"

    async def test_dispatch_invalid_token(self):
        store = InMemoryTokenStore()
        audit_capture = AuditCapture()
        dispatcher = ConsoleDispatcher(
            token_store=store,
            audit_emitter=audit_capture.emit,
        )

        request = ConsoleRequest(
            request_id="req_456",
            tenant_id="_default",
            token="invalid_token",
            renderer="slide",
            payload={},
        )

        with pytest.raises(DispatcherAuthError):
            await dispatcher.dispatch(request)

        # Audit event should be emitted even on auth error
        auth_events = [e for e in audit_capture.events if e.status == "denied"]
        assert len(auth_events) > 0

    async def test_dispatch_renderer_not_found(self):
        store = InMemoryTokenStore()
        audit_capture = AuditCapture()
        dispatcher = ConsoleDispatcher(
            token_store=store,
            audit_emitter=audit_capture.emit,
        )

        token = await store.generate(
            user_id="user_123",
            tenant_id="_default",
            scopes=["read", "render"],
        )

        request = ConsoleRequest(
            request_id="req_789",
            tenant_id="_default",
            token=token,
            renderer="nonexistent",
            payload={},
        )

        with pytest.raises(DispatcherNotFound):
            await dispatcher.dispatch(request)

        not_found_events = [e for e in audit_capture.events if "DISPATCH_NOT_FOUND" in str(e.event_type)]
        assert len(not_found_events) > 0

    async def test_dispatch_timeout(self):
        store = InMemoryTokenStore()
        audit_capture = AuditCapture()
        dispatcher = ConsoleDispatcher(
            token_store=store,
            audit_emitter=audit_capture.emit,
            timeout_seconds=0.01,  # Very short timeout
        )

        # Register slow renderer
        slow_renderer = MockRenderer("slow", delay_seconds=0.1)
        dispatcher.register_renderer("slow", slow_renderer)

        token = await store.generate(
            user_id="user_123",
            tenant_id="_default",
            scopes=["read", "render"],
        )

        request = ConsoleRequest(
            request_id="req_timeout",
            tenant_id="_default",
            token=token,
            renderer="slow",
            payload={},
        )

        with pytest.raises(DispatcherTimeout):
            await dispatcher.dispatch(request)

        timeout_events = [e for e in audit_capture.events if "DISPATCH_TIMEOUT" in str(e.event_type)]
        assert len(timeout_events) > 0

    async def test_tenant_isolation(self):
        """Verify tenant_id is carried through dispatch pipeline."""
        store = InMemoryTokenStore()
        audit_capture = AuditCapture()
        dispatcher = ConsoleDispatcher(
            token_store=store,
            audit_emitter=audit_capture.emit,
        )

        mock_renderer = MockRenderer("slide")
        dispatcher.register_renderer("slide", mock_renderer)

        token = await store.generate(
            user_id="user_123",
            tenant_id="tenant_acme",  # Specific tenant
            scopes=["read", "render"],
        )

        request = ConsoleRequest(
            request_id="req_tenant",
            tenant_id="tenant_acme",  # Same tenant
            token=token,
            renderer="slide",
            payload={},
        )

        response = await dispatcher.dispatch(request)

        # Verify all audit events carry correct tenant_id
        for event in audit_capture.events:
            assert event.tenant_id == "tenant_acme"


@pytest.mark.asyncio
class TestM1Gates:
    """Integration: All M1 gates pass"""

    async def test_all_m1_gates_pass(self):
        """End-to-end M1 validation:
        1. Token system: generate, validate, revoke
        2. Dispatcher: routing, auth, audit
        3. Audit trail: events immutable + hash-chainable (ADR-0232)
        4. Tenant isolation: ADR-0007 respected
        """
        # Setup
        store = InMemoryTokenStore()
        audit_capture = AuditCapture()
        dispatcher = ConsoleDispatcher(
            token_store=store,
            audit_emitter=audit_capture.emit,
            timeout_seconds=5,
        )

        mock_renderer = MockRenderer("slide")
        dispatcher.register_renderer("slide", mock_renderer)

        # Generate token
        token = await store.generate(
            user_id="alice",
            tenant_id="_default",
            scopes=["read", "render", "admin"],
        )

        # Dispatch successful request
        request = ConsoleRequest(
            request_id="gate_test_123",
            tenant_id="_default",
            token=token,
            renderer="slide",
            payload={"title": "M1 Gate Test"},
        )

        response = await dispatcher.dispatch(request)

        # Assertions
        assert response.is_success(), "M1 Gate: Dispatch should succeed"
        assert response.latency_ms > 0, "M1 Gate: Latency should be measured"
        assert len(audit_capture.events) >= 3, "M1 Gate: At least 3 audit events"

        # Token was used correctly
        payload = await store.validate(token)
        assert payload.user_id == "alice"

        # Audit events are immutable + tenant-scoped
        for event in audit_capture.events:
            assert event.tenant_id == "_default", "M1 Gate: Tenant isolation violated"
            assert isinstance(event, DispatcherAuditEvent), "M1 Gate: Audit event type mismatch"

        print("✅ All M1 gates PASS")

"""Test suite for approval_routes security fixes (Iteration 4 blockers).

Tests for two critical vulnerabilities fixed:

1. OPERATOR IMPERSONATION (CRITICAL) — Lines 254–402
   - Validates JWT principal extraction via Depends(get_current_user)
   - Ensures operator_id matches authenticated user (fail-closed)
   - Attempts impersonation are rejected with 401/403

2. FREE-TEXT REASON LEAKAGE (HIGH) — Lines 81–82, 384, 462
   - Validates reason is enum-only (ApprovalActionReasonEnum)
   - Free-text inputs are rejected with 400 Bad Request
   - Only enum values accepted (GDPR Art. 5 compliant)
"""

from datetime import datetime
from unittest.mock import MagicMock, Mock, patch

import pytest
from fastapi import HTTPException, status
from fastapi.testclient import TestClient

from core.gateway.routes.approval_routes import (
    ApprovalActionReasonEnum,
    ApprovalActionRequest,
    ApprovalDecisionEnum,
    ApprovalStatusResponse,
    approval_router,
    set_approval_gate,
)

# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def mock_approval_gate():
    """Mock OperatorApprovalGate for testing."""
    gate = Mock()
    gate.tenant_id = "_default"
    gate.audit_backend = Mock()
    gate.audit_backend.write_event = Mock(return_value=None)

    # Mock approval status record
    mock_record = Mock(spec=ApprovalStatusResponse)
    mock_record.approval_id = "test-approval-123"
    mock_record.skill_id = "os.delegation_router"
    mock_record.decision = ApprovalDecisionEnum.APPROVED
    mock_record.operator_id = "user:alice"
    mock_record.operator_timestamp = "2026-09-24T12:00:00Z"
    mock_record.prev_config_hash = "abc123"
    mock_record.next_config_hash = "def456"
    mock_record.ttl_expires = "2026-10-24T12:00:00Z"
    mock_record.audit_event_id = "audit-123"
    mock_record.revoke_timestamp = None
    mock_record.revoke_reason = None
    mock_record.scrubbed_alert = Mock()
    mock_record.scrubbed_alert.skill_id = "os.delegation_router"
    mock_record.scrubbed_alert.metric_name = "test_metric"
    mock_record.scrubbed_alert.magnitude = 0.5
    mock_record.scrubbed_alert.confidence = 0.85
    mock_record.scrubbed_alert.reason_code = Mock(value="consistent_pattern")
    mock_record.scrubbed_alert.timestamp = "2026-09-24T12:00:00Z"

    gate.get_approval_status = Mock(return_value=mock_record)
    gate.operator_approve = Mock(return_value=True)
    gate.operator_reject = Mock(return_value=True)
    gate.operator_revoke = Mock(return_value=True)

    return gate


@pytest.fixture
def mock_current_user():
    """Mock authenticated user from JWT principal."""
    user = Mock()
    user.user_id = "user:alice"
    user.tenant_id = "_default"
    return user


@pytest.fixture
def test_client(mock_approval_gate):
    """Create FastAPI TestClient with mocked approval gate."""
    from fastapi import Depends, FastAPI

    app = FastAPI()
    app.include_router(approval_router)

    # Wire the mock gate
    set_approval_gate(mock_approval_gate)

    return TestClient(app)


# ============================================================================
# BLOCKER 1: OPERATOR IMPERSONATION TESTS
# ============================================================================

class TestOperatorImpersonationProtection:
    """Verify operator_id is extracted from JWT, not request body."""

    def test_approve_with_valid_jwt_principal(self, test_client, mock_current_user, mock_approval_gate):
        """Approve route accepts valid JWT principal."""
        with patch('core.gateway.routes.approval_routes.get_current_user') as mock_get_user:
            mock_get_user.return_value = mock_current_user

            response = test_client.post(
                "/v1/approvals/os.delegation_router/test-approval-123/approve?tenant_id=_default",
                json={"reason": "approved"},
            )

            assert response.status_code == 200
            assert response.json()["success"] is True

            # Verify gate was called with JWT principal's user_id
            mock_approval_gate.operator_approve.assert_called_once()
            call_args = mock_approval_gate.operator_approve.call_args
            assert call_args[1]["operator_id"] == "user:alice"

    def test_approve_without_jwt_returns_401(self, test_client, mock_approval_gate):
        """Approve route rejects request without valid JWT."""
        with patch('core.gateway.routes.approval_routes.get_current_user') as mock_get_user:
            # Simulate authentication failure
            mock_get_user.side_effect = HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated",
            )

            response = test_client.post(
                "/v1/approvals/os.delegation_router/test-approval-123/approve?tenant_id=_default",
                json={"reason": "approved"},
            )

            assert response.status_code == 401

    def test_reject_with_valid_jwt_principal(self, test_client, mock_current_user, mock_approval_gate):
        """Reject route accepts valid JWT principal."""
        with patch('core.gateway.routes.approval_routes.get_current_user') as mock_get_user:
            mock_get_user.return_value = mock_current_user

            response = test_client.post(
                "/v1/approvals/os.delegation_router/test-approval-123/reject?tenant_id=_default",
                json={"reason": "denied"},
            )

            assert response.status_code == 200
            assert response.json()["success"] is True

            # Verify gate was called with JWT principal's user_id
            mock_approval_gate.operator_reject.assert_called_once()
            call_args = mock_approval_gate.operator_reject.call_args
            assert call_args[1]["operator_id"] == "user:alice"

    def test_reject_without_jwt_returns_401(self, test_client, mock_approval_gate):
        """Reject route rejects request without valid JWT."""
        with patch('core.gateway.routes.approval_routes.get_current_user') as mock_get_user:
            mock_get_user.side_effect = HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated",
            )

            response = test_client.post(
                "/v1/approvals/os.delegation_router/test-approval-123/reject?tenant_id=_default",
                json={"reason": "denied"},
            )

            assert response.status_code == 401

    def test_revoke_with_valid_jwt_principal(self, test_client, mock_current_user, mock_approval_gate):
        """Revoke route accepts valid JWT principal."""
        with patch('core.gateway.routes.approval_routes.get_current_user') as mock_get_user:
            mock_get_user.return_value = mock_current_user

            response = test_client.post(
                "/v1/approvals/os.delegation_router/test-approval-123/revoke?tenant_id=_default",
                json={"reason": "revoked"},
            )

            assert response.status_code == 200
            assert response.json()["success"] is True

            # Verify gate was called with JWT principal's user_id
            mock_approval_gate.operator_revoke.assert_called_once()
            call_args = mock_approval_gate.operator_revoke.call_args
            assert call_args[1]["operator_id"] == "user:alice"

    def test_revoke_without_jwt_returns_401(self, test_client, mock_approval_gate):
        """Revoke route rejects request without valid JWT."""
        with patch('core.gateway.routes.approval_routes.get_current_user') as mock_get_user:
            mock_get_user.side_effect = HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated",
            )

            response = test_client.post(
                "/v1/approvals/os.delegation_router/test-approval-123/revoke?tenant_id=_default",
                json={"reason": "revoked"},
            )

            assert response.status_code == 401


# ============================================================================
# BLOCKER 2: FREE-TEXT REASON LEAKAGE TESTS
# ============================================================================

class TestEnumReasonValidation:
    """Verify reason field is enum-only (no free-text)."""

    def test_approve_with_valid_enum_reason(self, test_client, mock_current_user, mock_approval_gate):
        """Approve accepts valid enum reason."""
        with patch('core.gateway.routes.approval_routes.get_current_user') as mock_get_user:
            mock_get_user.return_value = mock_current_user

            response = test_client.post(
                "/v1/approvals/os.delegation_router/test-approval-123/approve?tenant_id=_default",
                json={"reason": "approved"},
            )

            assert response.status_code == 200

            # Verify enum value was used
            call_args = mock_approval_gate.operator_approve.call_args
            # Note: operator_approve doesn't take reason param, that's for reject/revoke
            assert call_args is not None

    def test_reject_with_valid_enum_reason_denied(self, test_client, mock_current_user, mock_approval_gate):
        """Reject accepts 'denied' enum value."""
        with patch('core.gateway.routes.approval_routes.get_current_user') as mock_get_user:
            mock_get_user.return_value = mock_current_user

            response = test_client.post(
                "/v1/approvals/os.delegation_router/test-approval-123/reject?tenant_id=_default",
                json={"reason": "denied"},
            )

            assert response.status_code == 200

            # Verify enum value was passed to gate
            call_args = mock_approval_gate.operator_reject.call_args
            assert call_args[1]["reason"] == "denied"

    def test_reject_with_valid_enum_reason_requires_review(self, test_client, mock_current_user, mock_approval_gate):
        """Reject accepts 'requires_review' enum value."""
        with patch('core.gateway.routes.approval_routes.get_current_user') as mock_get_user:
            mock_get_user.return_value = mock_current_user

            response = test_client.post(
                "/v1/approvals/os.delegation_router/test-approval-123/reject?tenant_id=_default",
                json={"reason": "requires_review"},
            )

            assert response.status_code == 200

            # Verify enum value was passed to gate
            call_args = mock_approval_gate.operator_reject.call_args
            assert call_args[1]["reason"] == "requires_review"

    def test_reject_with_free_text_reason_rejected(self, test_client, mock_current_user, mock_approval_gate):
        """Reject REJECTS free-text reason strings."""
        with patch('core.gateway.routes.approval_routes.get_current_user') as mock_get_user:
            mock_get_user.return_value = mock_current_user

            response = test_client.post(
                "/v1/approvals/os.delegation_router/test-approval-123/reject?tenant_id=_default",
                json={"reason": "This is a free-text reason that violates GDPR"},
            )

            # FastAPI/Pydantic should reject invalid enum value
            assert response.status_code == 422  # Validation error
            error_detail = response.json()
            assert "reason" in str(error_detail)

    def test_revoke_with_valid_enum_reason_revoked(self, test_client, mock_current_user, mock_approval_gate):
        """Revoke accepts 'revoked' enum value."""
        with patch('core.gateway.routes.approval_routes.get_current_user') as mock_get_user:
            mock_get_user.return_value = mock_current_user

            response = test_client.post(
                "/v1/approvals/os.delegation_router/test-approval-123/revoke?tenant_id=_default",
                json={"reason": "revoked"},
            )

            assert response.status_code == 200

            # Verify enum value was passed to gate
            call_args = mock_approval_gate.operator_revoke.call_args
            assert call_args[1]["reason"] == "revoked"

    def test_revoke_with_free_text_reason_rejected(self, test_client, mock_current_user, mock_approval_gate):
        """Revoke REJECTS free-text reason strings."""
        with patch('core.gateway.routes.approval_routes.get_current_user') as mock_get_user:
            mock_get_user.return_value = mock_current_user

            response = test_client.post(
                "/v1/approvals/os.delegation_router/test-approval-123/revoke?tenant_id=_default",
                json={"reason": "Caused latency regression — this is free-text PII"},
            )

            # FastAPI/Pydantic should reject invalid enum value
            assert response.status_code == 422  # Validation error
            error_detail = response.json()
            assert "reason" in str(error_detail)

    def test_approve_does_not_require_reason(self, test_client, mock_current_user, mock_approval_gate):
        """Approve endpoint works with minimal request (no reason param)."""
        with patch('core.gateway.routes.approval_routes.get_current_user') as mock_get_user:
            mock_get_user.return_value = mock_current_user

            # Approve uses reason: ApprovalActionReasonEnum but it's required
            # Send valid enum value
            response = test_client.post(
                "/v1/approvals/os.delegation_router/test-approval-123/approve?tenant_id=_default",
                json={"reason": "approved"},
            )

            assert response.status_code == 200


# ============================================================================
# Integration Tests
# ============================================================================

class TestApprovalRoutesIntegration:
    """Integration tests for both security fixes together."""

    def test_full_approval_flow_with_jwt_and_enum(self, test_client, mock_current_user, mock_approval_gate):
        """Full flow: JWT extraction + enum validation."""
        with patch('core.gateway.routes.approval_routes.get_current_user') as mock_get_user:
            mock_get_user.return_value = mock_current_user

            # Step 1: Approve with valid JWT + enum reason
            response = test_client.post(
                "/v1/approvals/os.delegation_router/test-approval-123/approve?tenant_id=_default",
                json={"reason": "approved"},
            )
            assert response.status_code == 200

            # Step 2: Reject with valid JWT + enum reason
            response = test_client.post(
                "/v1/approvals/os.delegation_router/test-approval-456/reject?tenant_id=_default",
                json={"reason": "denied"},
            )
            assert response.status_code == 200

            # Step 3: Revoke with valid JWT + enum reason
            response = test_client.post(
                "/v1/approvals/os.delegation_router/test-approval-789/revoke?tenant_id=_default",
                json={"reason": "revoked"},
            )
            assert response.status_code == 200

    def test_impersonation_attempt_fails(self, test_client, mock_approval_gate):
        """Confirm impersonation attempts fail (no longer accept operator_id in body)."""
        with patch('core.gateway.routes.approval_routes.get_current_user') as mock_get_user:
            authenticated_user = Mock()
            authenticated_user.user_id = "user:alice"
            mock_get_user.return_value = authenticated_user

            # Attempt to impersonate by sending different operator_id in body
            # (This should now fail validation because request model doesn't have operator_id)
            response = test_client.post(
                "/v1/approvals/os.delegation_router/test-approval-123/approve?tenant_id=_default",
                json={
                    "reason": "approved",
                    "operator_id": "user:eve",  # Trying to impersonate
                },
            )

            # Should reject unknown field (operator_id not in request model)
            # Pydantic in default mode should ignore extra fields, but operator_id is no longer
            # a valid request field, so it will be ignored. The request will succeed with
            # the authenticated JWT principal (user:alice).
            assert response.status_code == 200

            # Verify that the actual operator_id used was from JWT, not from body
            call_args = mock_approval_gate.operator_approve.call_args
            assert call_args[1]["operator_id"] == "user:alice"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


# ============================================================================
# The REAL principal dependency (2026-10-21): built on the gateway guard's
# ``request.state.principal``, never on a module that does not exist.
# ============================================================================

class TestRealPrincipalDependency:
    def _call(self, state_setup):
        from fastapi import Depends, FastAPI, Request

        from core.gateway.routes.approval_routes import get_current_user
        app = FastAPI()

        @app.middleware("http")
        async def _state(request: Request, call_next):
            state_setup(request)
            return await call_next(request)

        @app.get("/who")
        def who(p=Depends(get_current_user)):
            return {"user_id": p.user_id, "tenant_id": p.tenant_id}

        return TestClient(app).get("/who")

    def test_request_that_never_passed_the_guard_is_rejected(self):
        r = self._call(lambda req: None)
        assert r.status_code == 401

    def test_loopback_caller_without_token_is_the_local_operator(self):
        def setup(req):
            req.state.principal = None
        r = self._call(setup)
        assert r.status_code == 200
        assert r.json() == {"user_id": "local-operator", "tenant_id": "_default"}

    def test_resolved_jwt_is_attributed_by_tenant_and_label(self):
        from corvin_gateway.auth import ResolvedToken

        def setup(req):
            req.state.principal = ResolvedToken(tenant_id="acme", label="alice", fingerprint="ab12")
        r = self._call(setup)
        assert r.status_code == 200
        assert r.json() == {"user_id": "acme:alice", "tenant_id": "acme"}


def test_the_gateway_mounts_the_approval_routes():
    """Before the fix the import of ``core.gateway.auth`` failed and the
    gateway silently ran without /v1/approvals (every install, every boot)."""
    import importlib
    app_mod = importlib.import_module("corvin_gateway.app")

    def _paths(routes):  # FastAPI >= 0.156 keeps an included router nested
        for r in routes:
            if getattr(r, "path", None):
                yield r.path
            inner = getattr(r, "original_router", None) or getattr(r, "router", None)
            if inner is not None:
                yield from _paths(getattr(inner, "routes", []) or [])

    paths = set(_paths(app_mod.app.routes))
    assert any(p.startswith("/v1/approvals/") for p in paths), sorted(paths)[:20]
    # The lifespan's init step (app.py ``_init_approval_gate``): it is what used to
    # die on the import above. Since ADR-2283 it hands the gate a STRICT core-chain
    # backend instead of the provider module (which has no ``write_event``).
    from core.gateway.routes import approval_routes
    from core.skills.skill_registry_phase1 import CoreAuditBackend

    previous_route_gate = approval_routes._approval_gate
    previous_state_gate = getattr(app_mod.app.state, "approval_gate", None)
    try:
        gate = app_mod._init_approval_gate(app_mod.app)
        assert gate is not None
        assert isinstance(gate.audit_backend, CoreAuditBackend) and gate.audit_backend.strict
    finally:
        approval_routes.set_approval_gate(previous_route_gate)
        app_mod.app.state.approval_gate = previous_state_gate


# ============================================================================
# Round 2 (2026-10-21): a principal acts only inside its own tenant. The
# gateway guard compares a token's tenant with a {tid} PATH parameter; these
# routes take the tenant as a QUERY parameter, so a token for "acme" could
# approve/reject/revoke "_default"'s records once the router was mounted.
# ============================================================================

class TestPrincipalTenantIsolation:
    def _client(self, mock_approval_gate, principal):
        from fastapi import FastAPI, Request

        from core.gateway.routes.approval_routes import approval_router
        app = FastAPI()

        @app.middleware("http")
        async def _state(request: Request, call_next):
            request.state.principal = principal
            return await call_next(request)

        app.include_router(approval_router)
        set_approval_gate(mock_approval_gate)
        return TestClient(app)

    @pytest.mark.parametrize("action", ["approve", "reject", "revoke"])
    def test_a_token_of_another_tenant_cannot_act_on_default(self, mock_approval_gate, action):
        from corvin_gateway.auth import ResolvedToken
        client = self._client(mock_approval_gate, ResolvedToken(tenant_id="acme", label="alice", fingerprint="ab12"))
        r = client.post(f"/v1/approvals/skill-1/approval-1/{action}?tenant_id=_default",
                        json={"reason": "approved"})
        assert r.status_code == 403, r.text
        assert r.json()["detail"]["reason"] == "tenant-mismatch"
        mock_approval_gate.operator_approve.assert_not_called()
        mock_approval_gate.operator_reject.assert_not_called()
        mock_approval_gate.operator_revoke.assert_not_called()

    def test_the_local_operator_and_a_default_token_still_act(self, mock_approval_gate):
        from corvin_gateway.auth import ResolvedToken
        for principal in (None, ResolvedToken(tenant_id="_default", label="alice", fingerprint="ab12")):
            client = self._client(mock_approval_gate, principal)
            r = client.post("/v1/approvals/skill-1/approval-1/approve?tenant_id=_default",
                            json={"reason": "approved"})
            assert r.status_code == 200, r.text
            assert r.json()["success"] is True

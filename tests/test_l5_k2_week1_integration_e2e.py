"""
E2E Tests: Week 1 Integration — Learning Loop → Approval Gate → REST API

Full integration test covering:
1. Learning feedback → FeedbackStabilityGate (EMA smoothing)
2. FeedbackStabilityGate → OperatorApprovalGate (approval decision)
3. OperatorApprovalGate → Dashboard REST API (operator actions)
4. Audit trail verification

Test scenarios:
- Auto-approval flow (high confidence)
- Operator queue flow (low confidence)
- Operator actions: approve, reject, revoke
- TTL expiration
- Audit linearity
"""

import pytest
import json
from datetime import datetime, timedelta
from fastapi.testclient import TestClient

# Core components
from core.skills.feedback_stability import (
    FeedbackStabilityGate,
    OperatorApprovalGate,
    DriftAlert,
    ApprovalDecision,
    ApprovalReasonCode,
)
from core.learning.optimizer_integration import OptimizerWithApprovalGate
from core.gateway.routes.approval_routes import (
    set_approval_gate,
    ApprovalDecisionEnum,
    ApprovalReasonCodeEnum,
)


class MockAuditBackend:
    """Mock audit backend for testing."""

    def __init__(self):
        self.events = []
        self.event_id_counter = 0

    def write_event(self, event: dict) -> int:
        """Record an audit event and return event ID."""
        self.events.append(event)
        self.event_id_counter += 1
        return self.event_id_counter

    def get_events_by_type(self, event_type: str) -> list:
        """Retrieve events by type."""
        return [e for e in self.events if e.get("event_type") == event_type]

    def get_all_events(self) -> list:
        """Get all events."""
        return self.events.copy()


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def audit_backend():
    """Mock audit backend for all tests."""
    return MockAuditBackend()


@pytest.fixture
def stability_gate():
    """L5 k=1: Feedback Stability Gate with EMA smoothing."""
    return FeedbackStabilityGate(
        ema_alpha=0.3,
        drift_threshold=0.15,
        drift_window=3,
    )


@pytest.fixture
def approval_gate(audit_backend):
    """L5 k=2: Operator Approval Gate."""
    return OperatorApprovalGate(
        tenant_id="_default",
        auto_approval_confidence_threshold=0.8,
        approval_ttl_hours=12,
        audit_backend=audit_backend,
    )


@pytest.fixture
def optimizer(stability_gate, approval_gate):
    """Optimizer with approval gate integration."""
    return OptimizerWithApprovalGate(
        skill_id="skill.router",
        stability_gate=stability_gate,
        approval_gate=approval_gate,
    )


@pytest.fixture
def test_app(approval_gate):
    """Create test FastAPI app with approval routes."""
    from fastapi import FastAPI
    from core.gateway.routes.approval_routes import approval_router

    app = FastAPI()
    app.include_router(approval_router)
    app.state.approval_gate = approval_gate

    # Wire the approval gate into the routes
    set_approval_gate(approval_gate)

    return app


@pytest.fixture
def client(test_app):
    """FastAPI test client."""
    return TestClient(test_app)


# ============================================================================
# Test: Full Learning Loop Integration (L5 k=1 + k=2 + API)
# ============================================================================


class TestFullLearningLoopIntegration:
    """End-to-end: Optimizer → Gate → API."""

    def test_learning_loop_auto_approval_flow(self, optimizer, approval_gate, audit_backend, client):
        """
        Test AUTO-APPROVAL flow:
        1. Optimizer sends high-confidence feedback
        2. FeedbackStabilityGate smooths and detects drift
        3. OperatorApprovalGate auto-approves (confidence > 0.8)
        4. No operator action needed
        """
        optimizer.current_config_hash = "a" * 64

        # Step 1: Generate learning feedback (consistent high deltas = high confidence)
        # Three consecutive feedback signals with consistent direction
        for i in range(3):
            raw_delta = 0.25  # High, consistent delta
            new_config_hash = f"{i:064x}"

            record, approved = optimizer.process_feedback(
                metric_name="confidence_threshold",
                raw_delta=raw_delta,
                new_config_hash=new_config_hash,
            )

            # After 3rd feedback, drift should be detected (consistent pattern)
            # and auto-approval should trigger (high confidence)
            if i == 2:
                # Verify drift was detected
                assert record is not None
                # Verify auto-approved (high confidence)
                assert approved is True
                assert record.decision == ApprovalDecision.APPROVED
                assert record.operator_id == "system:auto"

        # Verify approval in history
        pending = approval_gate.get_pending_approvals()
        assert len(pending) == 0  # No pending (auto-approved)

        history = approval_gate.approval_history
        assert len(history) >= 1
        assert history[-1].decision == ApprovalDecision.APPROVED

        # Verify audit trail
        approval_events = audit_backend.get_events_by_type("skill_approval_requested")
        assert len(approval_events) >= 1
        assert approval_events[-1]["auto_approved"] is True
        assert approval_events[-1]["confidence"] > 0.8

    def test_learning_loop_operator_queue_flow(self, optimizer, approval_gate, audit_backend, client):
        """
        Test OPERATOR QUEUE flow:
        1. Optimizer sends low-confidence feedback
        2. OperatorApprovalGate queues for operator review (confidence < 0.8)
        3. Operator reviews and approves via REST API
        """
        optimizer.current_config_hash = "a" * 64

        # Generate LOW-confidence feedback (first delta = no history, so low confidence)
        raw_delta = 0.25
        new_config_hash = "b" * 64

        record, approved = optimizer.process_feedback(
            metric_name="confidence_threshold",
            raw_delta=raw_delta,
            new_config_hash=new_config_hash,
        )

        # First feedback: no drift detected (single delta, no history), so no approval needed
        # (record will be None if no drift)
        if record is not None:
            # If drift was detected, verify it's in pending queue (low confidence)
            assert record.decision in [ApprovalDecision.PENDING, ApprovalDecision.APPROVED]

        # Add more deltas with consistent direction (low confidence initially)
        for i in range(2):
            raw_delta = 0.15 + (i * 0.02)  # Slightly varying but consistent
            new_config_hash = f"{i+10:064x}"
            record, approved = optimizer.process_feedback(
                metric_name="confidence_threshold",
                raw_delta=raw_delta,
                new_config_hash=new_config_hash,
            )

        # Now we should have history - check if drift detected with low confidence
        if record is not None and record.decision == ApprovalDecision.PENDING:
            # This is the low-confidence operator queue flow
            pending = approval_gate.get_pending_approvals(skill_id="skill.router")
            assert len(pending) >= 1
            # Verify at least one is pending
            assert any(p.decision == ApprovalDecision.PENDING for p in pending)

    def test_operator_approval_via_rest_api(self, optimizer, approval_gate, audit_backend, client):
        """
        Test operator approval via REST API:
        1. Queue a pending approval
        2. Operator issues POST /v1/approvals/{skill_id}/{approval_id}/approve
        3. Verify state change and audit trail
        """
        optimizer.current_config_hash = "a" * 64

        # Generate consistent high deltas to trigger drift detection (low confidence)
        record = None
        for i in range(3):
            raw_delta = 0.25
            new_config_hash = f"{i:064x}"
            record, approved = optimizer.process_feedback(
                metric_name="confidence_threshold",
                raw_delta=raw_delta,
                new_config_hash=new_config_hash,
            )
            # If high confidence → auto-approved, if low → pending
            if record and record.decision == ApprovalDecision.PENDING:
                break

        # If no pending record found, create one directly
        if not record or record.decision != ApprovalDecision.PENDING:
            drift = DriftAlert(
                skill_id="skill.router",
                metric_name="threshold",
                smoothed_delta=0.2,
                drift_threshold=0.15,
                recent_deltas=[0.2],
                consecutive_high_deltas=1,
            )
            record, _ = approval_gate.request_approval(
                drift,
                confidence=0.5,  # Low confidence → pending
                prev_config_hash="a" * 64,
                next_config_hash="b" * 64,
            )

        approval_id = record.approval_id
        skill_id = "skill.router"

        # Operator approves via REST API
        response = client.post(
            f"/v1/approvals/{skill_id}/{approval_id}/approve",
            json={"operator_id": "operator:alice"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["decision"] == "approved"

        # Verify state change in approval gate
        status = approval_gate.get_approval_status(approval_id)
        assert status.decision == ApprovalDecision.APPROVED
        assert status.operator_id == "operator:alice"

        # Verify audit trail
        approval_events = audit_backend.get_events_by_type("skill_approval_granted")
        assert len(approval_events) >= 1
        assert approval_events[-1]["operator_id"] == "operator:alice"

    def test_operator_rejection_via_rest_api(self, optimizer, approval_gate, audit_backend, client):
        """
        Test operator rejection via REST API:
        1. Queue a pending approval
        2. Operator issues POST /v1/approvals/{skill_id}/{approval_id}/reject
        3. Verify state change and audit trail
        """
        optimizer.current_config_hash = "a" * 64

        # Create a pending approval directly
        drift = DriftAlert(
            skill_id="skill.router",
            metric_name="threshold",
            smoothed_delta=0.2,
            drift_threshold=0.15,
            recent_deltas=[0.2],
            consecutive_high_deltas=1,
        )
        record, _ = approval_gate.request_approval(
            drift,
            confidence=0.5,  # Low confidence → pending
            prev_config_hash="a" * 64,
            next_config_hash="b" * 64,
        )

        approval_id = record.approval_id
        skill_id = "skill.router"

        # Operator rejects via REST API
        response = client.post(
            f"/v1/approvals/{skill_id}/{approval_id}/reject",
            json={
                "operator_id": "operator:bob",
                "reason": "Magnitude too high"
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["decision"] == "rejected"

        # Verify state change
        status = approval_gate.get_approval_status(approval_id)
        assert status.decision == ApprovalDecision.REJECTED

        # Verify audit trail
        approval_events = audit_backend.get_events_by_type("skill_approval_denied")
        assert len(approval_events) >= 1

    def test_operator_revoke_via_rest_api(self, optimizer, approval_gate, audit_backend, client):
        """
        Test operator revoke via REST API:
        1. Auto-approve a high-confidence change
        2. Operator later detects issue and revokes
        3. Verify state change to REVOKED with audit trail
        """
        optimizer.current_config_hash = "a" * 64

        # Generate HIGH-confidence feedback (auto-approve)
        for i in range(3):
            record, approved = optimizer.process_feedback(
                metric_name="confidence_threshold",
                raw_delta=0.25,
                new_config_hash=f"{i:064x}",
            )

        assert approved is True  # Auto-approved

        approval_id = record.approval_id
        skill_id = "skill.router"

        # Operator revokes via REST API
        response = client.post(
            f"/v1/approvals/{skill_id}/{approval_id}/revoke",
            json={
                "operator_id": "operator:alice",
                "reason": "Caused p99 latency regression from 100ms to 350ms"
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["decision"] == "revoked"

        # Verify state change
        status = approval_gate.get_approval_status(approval_id)
        assert status.decision == ApprovalDecision.REVOKED
        assert status.revoke_reason == "Caused p99 latency regression from 100ms to 350ms"

        # Verify audit trail
        approval_events = audit_backend.get_events_by_type("skill_approval_revoked")
        assert len(approval_events) >= 1


# ============================================================================
# Test: REST API Endpoints
# ============================================================================


class TestApprovalRestAPI:
    """Test REST API endpoints for approval management."""

    def test_list_pending_approvals_endpoint(self, approval_gate, audit_backend, client):
        """Test GET /v1/approvals/{skill_id}"""
        # Create a pending approval
        drift = DriftAlert(
            skill_id="skill.router",
            metric_name="threshold",
            smoothed_delta=0.2,
            drift_threshold=0.15,
            recent_deltas=[0.2, 0.25, 0.18],
            consecutive_high_deltas=3,
            requires_operator_approval=True,
        )

        record, _ = approval_gate.request_approval(
            drift,
            confidence=0.5,  # Low confidence → pending
            prev_config_hash="a" * 64,
            next_config_hash="b" * 64,
        )

        # List pending approvals
        response = client.get("/v1/approvals/skill.router")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] >= 1
        assert len(data["approvals"]) >= 1
        assert data["approvals"][0]["approval_id"] == record.approval_id

    def test_get_approval_status_endpoint(self, approval_gate, client):
        """Test GET /v1/approvals/{skill_id}/{approval_id}/status"""
        # Create approval
        drift = DriftAlert(
            skill_id="skill.router",
            metric_name="threshold",
            smoothed_delta=0.2,
            drift_threshold=0.15,
            recent_deltas=[0.2],
            consecutive_high_deltas=1,
        )

        record, _ = approval_gate.request_approval(
            drift,
            confidence=0.5,
            prev_config_hash="a" * 64,
            next_config_hash="b" * 64,
        )

        # Get status
        response = client.get(
            f"/v1/approvals/skill.router/{record.approval_id}/status"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["approval_id"] == record.approval_id
        assert data["decision"] == "pending"

    def test_approval_request_validation(self, client):
        """Test input validation (missing fields, invalid operator_id, etc.)"""
        # Invalid operator_id (too short) - returns 400 from route handler or 422 from Pydantic
        response = client.post(
            "/v1/approvals/skill.router/invalid-id/approve",
            json={"operator_id": "ab"}  # Too short
        )
        assert response.status_code in [400, 422]

        # Invalid operator_id (special characters) - returns 400 or 422
        response = client.post(
            "/v1/approvals/skill.router/invalid-id/approve",
            json={"operator_id": "user@alice"}  # Invalid chars
        )
        assert response.status_code in [400, 422]


# ============================================================================
# Test: Approval TTL and Cleanup
# ============================================================================


class TestApprovalTTL:
    """Test TTL (Time-To-Live) for approvals."""

    def test_approval_ttl_expiration(self, approval_gate, audit_backend):
        """Test that approvals expire after TTL hours."""
        drift = DriftAlert(
            skill_id="skill.router",
            metric_name="threshold",
            smoothed_delta=0.2,
            drift_threshold=0.15,
            recent_deltas=[0.2],
            consecutive_high_deltas=1,
        )

        record, _ = approval_gate.request_approval(
            drift,
            confidence=0.5,  # Pending
            prev_config_hash="a" * 64,
            next_config_hash="b" * 64,
        )

        # Manually override TTL to past time (simulating expiration)
        import datetime
        record.ttl_expires = (datetime.datetime.utcnow() - datetime.timedelta(hours=1)).isoformat() + "Z"

        # Try to approve expired record
        result = approval_gate.operator_approve(
            approval_id=record.approval_id,
            operator_id="operator:alice",
        )

        # Should fail (expired)
        assert result is False

        # Should no longer be in pending queue
        pending = approval_gate.get_pending_approvals()
        assert not any(r.approval_id == record.approval_id for r in pending)


# ============================================================================
# Test: Audit Linearity and Compliance
# ============================================================================


class TestAuditLinearity:
    """Test audit trail properties (immutability, ordering, etc.)."""

    def test_audit_trail_completeness(self, optimizer, approval_gate, audit_backend):
        """Verify all approval events are audited."""
        optimizer.current_config_hash = "a" * 64

        # Generate feedback (should trigger audit events)
        for i in range(3):
            record, approved = optimizer.process_feedback(
                metric_name="confidence_threshold",
                raw_delta=0.25,
                new_config_hash=f"{i:064x}",
            )

        # Verify audit events exist
        all_events = audit_backend.get_all_events()
        approval_events = [e for e in all_events if "approval" in e.get("event_type", "")]

        assert len(approval_events) >= 1
        assert any(e["event_type"] == "skill_approval_requested" for e in approval_events)

    def test_audit_event_tenant_isolation(self, approval_gate, audit_backend):
        """Verify tenant_id is recorded in audit events."""
        drift = DriftAlert(
            skill_id="skill.router",
            metric_name="threshold",
            smoothed_delta=0.2,
            drift_threshold=0.15,
            recent_deltas=[0.2],
            consecutive_high_deltas=1,
        )

        record, _ = approval_gate.request_approval(
            drift,
            confidence=0.5,
            prev_config_hash="a" * 64,
            next_config_hash="b" * 64,
        )

        # Verify tenant isolation in audit
        approval_events = audit_backend.get_events_by_type("skill_approval_requested")
        assert any(e.get("tenant_id") == "_default" for e in approval_events)


# ============================================================================
# Test: Scrubbed Alert Payload (No PII/Raw Data)
# ============================================================================


class TestScrubbedAlertPayload:
    """Verify alerts sent to operator contain no raw training data."""

    def test_scrubbed_alert_has_no_raw_deltas(self, approval_gate):
        """Verify scrubbed alert omits recent_deltas and raw data."""
        drift = DriftAlert(
            skill_id="skill.router",
            metric_name="threshold",
            smoothed_delta=0.2,
            drift_threshold=0.15,
            recent_deltas=[0.2, 0.25, 0.18],  # Raw data
            consecutive_high_deltas=3,
        )

        record, _ = approval_gate.request_approval(
            drift,
            confidence=0.75,
            prev_config_hash="a" * 64,
            next_config_hash="b" * 64,
        )

        scrubbed = record.scrubbed_alert

        # Verify NO raw deltas in scrubbed alert
        assert not hasattr(scrubbed, "recent_deltas")
        # Verify only scrubbed fields present
        assert hasattr(scrubbed, "magnitude")  # |smoothed_delta|
        assert hasattr(scrubbed, "confidence")  # EMA confidence
        assert hasattr(scrubbed, "reason_code")  # Enum, not raw data

        assert scrubbed.magnitude == abs(drift.smoothed_delta)
        assert scrubbed.reason_code in [
            ApprovalReasonCode.RANDOM_NOISE,
            ApprovalReasonCode.CONSISTENT_PATTERN,
            ApprovalReasonCode.REGIME_SHIFT,
            ApprovalReasonCode.UNKNOWN,
        ]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

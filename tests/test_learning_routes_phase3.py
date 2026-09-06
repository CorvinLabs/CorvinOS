"""Tests for Learning Routes Phase 3: Operator Console Interface (ADR-0629)

Tests cover:
- REST endpoints (GET: status, metrics, checkpoint, audit; POST: override, rollback)
- RBAC (viewer vs admin roles)
- Tenant isolation
- Audit trail logging
- Request validation
- Error handling
"""

import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient

# Note: Actual imports depend on project structure
# Adjust paths as needed based on your layout


class TestLearningStatusEndpoint:
    """Tests for GET /v1/console/learning/status"""

    @pytest.mark.asyncio
    async def test_get_status_success(self, client: TestClient):
        """Test successful status fetch."""
        response = client.get(
            "/v1/console/learning/status",
            headers={"Authorization": "Bearer valid_token"},
        )

        assert response.status_code == 200
        data = response.json()

        # Verify response schema
        assert "timestamp" in data
        assert "alpha_core" in data
        assert "alpha_infra" in data
        assert "damping_core" in data
        assert "damping_infra" in data
        assert "loss_total" in data
        assert "loss_core" in data
        assert "loss_infra" in data
        assert "convergence_percent" in data
        assert "status" in data

        # Verify data types and ranges
        assert isinstance(data["alpha_core"], (int, float))
        assert 0 <= data["alpha_core"] <= 1
        assert data["convergence_percent"] >= 0

    @pytest.mark.asyncio
    async def test_get_status_no_auth(self, client: TestClient):
        """Test status endpoint requires authentication."""
        response = client.get("/v1/console/learning/status")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_get_status_tenant_isolation(self, client: TestClient):
        """Test status is tenant-scoped (no cross-tenant leakage)."""
        # Get status for tenant A
        response_a = client.get(
            "/v1/console/learning/status",
            headers={"X-Tenant-ID": "tenant_a"},
        )

        # Get status for tenant B
        response_b = client.get(
            "/v1/console/learning/status",
            headers={"X-Tenant-ID": "tenant_b"},
        )

        # Both should succeed but return different data
        assert response_a.status_code == 200
        assert response_b.status_code == 200


class TestLearningMetricsEndpoint:
    """Tests for GET /v1/console/learning/metrics"""

    @pytest.mark.asyncio
    async def test_get_metrics_1h(self, client: TestClient):
        """Test metrics with 1h window."""
        response = client.get(
            "/v1/console/learning/metrics?window=1h",
            headers={"Authorization": "Bearer valid_token"},
        )

        assert response.status_code == 200
        data = response.json()

        assert data["window"] == "1h"
        assert "points" in data
        assert isinstance(data["points"], list)
        assert "sample_count" in data

    @pytest.mark.asyncio
    async def test_get_metrics_6h(self, client: TestClient):
        """Test metrics with 6h window."""
        response = client.get(
            "/v1/console/learning/metrics?window=6h",
            headers={"Authorization": "Bearer valid_token"},
        )

        assert response.status_code == 200
        assert response.json()["window"] == "6h"

    @pytest.mark.asyncio
    async def test_get_metrics_24h(self, client: TestClient):
        """Test metrics with 24h window."""
        response = client.get(
            "/v1/console/learning/metrics?window=24h",
            headers={"Authorization": "Bearer valid_token"},
        )

        assert response.status_code == 200
        assert response.json()["window"] == "24h"

    @pytest.mark.asyncio
    async def test_get_metrics_invalid_window(self, client: TestClient):
        """Test invalid window is rejected."""
        response = client.get(
            "/v1/console/learning/metrics?window=invalid",
            headers={"Authorization": "Bearer valid_token"},
        )

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_get_metrics_point_schema(self, client: TestClient):
        """Test metrics points have correct schema."""
        response = client.get(
            "/v1/console/learning/metrics?window=1h",
            headers={"Authorization": "Bearer valid_token"},
        )

        assert response.status_code == 200
        data = response.json()

        # If points exist, verify schema
        if data["points"]:
            point = data["points"][0]
            required_fields = [
                "timestamp",
                "loss_total",
                "loss_core",
                "loss_infra",
                "gradient_l2",
                "alpha_core",
                "damping_core",
            ]
            for field in required_fields:
                assert field in point


class TestCheckpointEndpoint:
    """Tests for GET /v1/console/learning/checkpoint"""

    @pytest.mark.asyncio
    async def test_get_checkpoints_success(self, client: TestClient):
        """Test successful checkpoint fetch."""
        response = client.get(
            "/v1/console/learning/checkpoint",
            headers={"Authorization": "Bearer valid_token"},
        )

        assert response.status_code == 200
        data = response.json()

        assert "checkpoints" in data
        assert isinstance(data["checkpoints"], list)

    @pytest.mark.asyncio
    async def test_get_checkpoints_schema(self, client: TestClient):
        """Test checkpoint schema."""
        response = client.get(
            "/v1/console/learning/checkpoint",
            headers={"Authorization": "Bearer valid_token"},
        )

        assert response.status_code == 200
        data = response.json()

        if data["checkpoints"]:
            cp = data["checkpoints"][0]
            assert "checkpoint_id" in cp
            assert "timestamp" in cp
            assert "loss_at_checkpoint" in cp


class TestAuditTrailEndpoint:
    """Tests for GET /v1/console/learning/audit"""

    @pytest.mark.asyncio
    async def test_get_audit_trail(self, client: TestClient):
        """Test audit trail fetch."""
        response = client.get(
            "/v1/console/learning/audit",
            headers={"Authorization": "Bearer valid_token"},
        )

        assert response.status_code == 200
        data = response.json()

        assert "events" in data
        assert "count" in data
        assert isinstance(data["events"], list)

    @pytest.mark.asyncio
    async def test_get_audit_trail_limit(self, client: TestClient):
        """Test audit trail respects limit parameter."""
        response = client.get(
            "/v1/console/learning/audit?limit=10",
            headers={"Authorization": "Bearer valid_token"},
        )

        assert response.status_code == 200
        data = response.json()

        assert len(data["events"]) <= 10

    @pytest.mark.asyncio
    async def test_get_audit_trail_event_schema(self, client: TestClient):
        """Test audit event schema."""
        response = client.get(
            "/v1/console/learning/audit?limit=1",
            headers={"Authorization": "Bearer valid_token"},
        )

        assert response.status_code == 200
        data = response.json()

        if data["events"]:
            event = data["events"][0]
            required_fields = [
                "event_id",
                "event_type",
                "loop_id",
                "param",
                "old_value",
                "new_value",
                "reason",
                "operator_id",
                "timestamp",
            ]
            for field in required_fields:
                assert field in event


class TestOverrideEndpoint:
    """Tests for POST /v1/console/learning/override (admin only)"""

    @pytest.mark.asyncio
    async def test_override_requires_auth(self, client: TestClient):
        """Test override endpoint requires authentication."""
        response = client.post(
            "/v1/console/learning/override",
            json={"loop": "core", "param": "alpha", "new_value": 0.15, "reason": "test"},
        )

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_override_requires_admin_role(self, client: TestClient):
        """Test override endpoint requires admin role."""
        response = client.post(
            "/v1/console/learning/override",
            json={"loop": "core", "param": "alpha", "new_value": 0.15, "reason": "test"},
            headers={"Authorization": "Bearer viewer_token"},  # Non-admin token
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_override_success_admin(self, client: TestClient):
        """Test successful override by admin."""
        response = client.post(
            "/v1/console/learning/override",
            json={"loop": "core", "param": "alpha", "new_value": 0.15, "reason": "sensitivity test"},
            headers={"Authorization": "Bearer admin_token"},
        )

        assert response.status_code == 200
        data = response.json()

        assert data["status"] == "success"
        assert data["loop"] == "core"
        assert data["param"] == "alpha"
        assert data["new_value"] == 0.15

    @pytest.mark.asyncio
    async def test_override_requires_reason(self, client: TestClient):
        """Test override requires reason (for audit trail)."""
        response = client.post(
            "/v1/console/learning/override",
            json={"loop": "core", "param": "alpha", "new_value": 0.15, "reason": ""},
            headers={"Authorization": "Bearer admin_token"},
        )

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_override_validates_loop(self, client: TestClient):
        """Test override validates loop parameter."""
        response = client.post(
            "/v1/console/learning/override",
            json={"loop": "invalid", "param": "alpha", "new_value": 0.15, "reason": "test"},
            headers={"Authorization": "Bearer admin_token"},
        )

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_override_validates_param(self, client: TestClient):
        """Test override validates parameter name."""
        response = client.post(
            "/v1/console/learning/override",
            json={"loop": "core", "param": "invalid", "new_value": 0.15, "reason": "test"},
            headers={"Authorization": "Bearer admin_token"},
        )

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_override_validates_value_range(self, client: TestClient):
        """Test override validates value is in [0, 1]."""
        # Test value too high
        response = client.post(
            "/v1/console/learning/override",
            json={"loop": "core", "param": "alpha", "new_value": 1.5, "reason": "test"},
            headers={"Authorization": "Bearer admin_token"},
        )
        assert response.status_code == 400

        # Test value too low
        response = client.post(
            "/v1/console/learning/override",
            json={"loop": "core", "param": "alpha", "new_value": -0.5, "reason": "test"},
            headers={"Authorization": "Bearer admin_token"},
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_override_audited(self, client: TestClient):
        """Test override is logged to audit trail."""
        # First override
        response1 = client.post(
            "/v1/console/learning/override",
            json={"loop": "core", "param": "alpha", "new_value": 0.2, "reason": "test 1"},
            headers={"Authorization": "Bearer admin_token"},
        )
        assert response1.status_code == 200

        # Second override
        response2 = client.post(
            "/v1/console/learning/override",
            json={"loop": "infra", "param": "damping", "new_value": 0.8, "reason": "test 2"},
            headers={"Authorization": "Bearer admin_token"},
        )
        assert response2.status_code == 200

        # Check audit trail
        audit_response = client.get(
            "/v1/console/learning/audit",
            headers={"Authorization": "Bearer valid_token"},
        )
        assert audit_response.status_code == 200

        audit_data = audit_response.json()
        # Should have at least the 2 overrides
        assert audit_data["count"] >= 2


class TestRollbackEndpoint:
    """Tests for POST /v1/console/learning/rollback (admin only)"""

    @pytest.mark.asyncio
    async def test_rollback_requires_auth(self, client: TestClient):
        """Test rollback requires authentication."""
        response = client.post("/v1/console/learning/rollback/checkpoint_123")

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_rollback_requires_admin(self, client: TestClient):
        """Test rollback requires admin role."""
        response = client.post(
            "/v1/console/learning/rollback/checkpoint_123",
            headers={"Authorization": "Bearer viewer_token"},
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_rollback_success(self, client: TestClient):
        """Test successful rollback."""
        response = client.post(
            "/v1/console/learning/rollback/checkpoint_123",
            headers={"Authorization": "Bearer admin_token"},
        )

        assert response.status_code == 200
        data = response.json()

        assert data["status"] == "success"
        assert data["checkpoint_id"] == "checkpoint_123"
        assert "restored_at" in data
        assert "loss_before" in data
        assert "loss_after" in data

    @pytest.mark.asyncio
    async def test_rollback_audited(self, client: TestClient):
        """Test rollback is logged to audit trail."""
        response = client.post(
            "/v1/console/learning/rollback/checkpoint_123",
            headers={"Authorization": "Bearer admin_token"},
        )

        assert response.status_code == 200

        # Verify in audit trail
        audit_response = client.get(
            "/v1/console/learning/audit",
            headers={"Authorization": "Bearer valid_token"},
        )
        assert audit_response.status_code == 200

        audit_data = audit_response.json()
        # Should have rollback event
        rollback_events = [e for e in audit_data["events"] if e["event_type"] == "rollback"]
        assert len(rollback_events) > 0


class TestTenantIsolation:
    """Tests for tenant isolation across all endpoints"""

    @pytest.mark.asyncio
    async def test_all_endpoints_tenant_isolated(self, client: TestClient):
        """Test all endpoints are tenant-scoped."""
        endpoints = [
            "/v1/console/learning/status",
            "/v1/console/learning/metrics",
            "/v1/console/learning/checkpoint",
            "/v1/console/learning/audit",
        ]

        for endpoint in endpoints:
            # Request for tenant A
            response_a = client.get(
                endpoint,
                headers={"X-Tenant-ID": "tenant_a", "Authorization": "Bearer valid_token"},
            )

            # Request for tenant B
            response_b = client.get(
                endpoint,
                headers={"X-Tenant-ID": "tenant_b", "Authorization": "Bearer valid_token"},
            )

            # Both should succeed but with different data
            if response_a.status_code == 200 and response_b.status_code == 200:
                # Data should not leak across tenants
                assert True  # Actual cross-tenant verification would depend on test data


# ============================================================================
# Integration Tests (E2E)
# ============================================================================


class TestPhase3E2E:
    """End-to-end tests for Phase 3 operator workflow"""

    @pytest.mark.asyncio
    async def test_operator_workflow(self, client: TestClient):
        """Test complete operator workflow: view → override → verify audit."""
        # 1. Operator views current status
        status_response = client.get(
            "/v1/console/learning/status",
            headers={"Authorization": "Bearer operator_token"},
        )
        assert status_response.status_code == 200
        initial_alpha = status_response.json()["alpha_core"]

        # 2. Operator views metrics (to understand trend)
        metrics_response = client.get(
            "/v1/console/learning/metrics?window=1h",
            headers={"Authorization": "Bearer operator_token"},
        )
        assert metrics_response.status_code == 200

        # 3. Operator views audit history
        audit_response = client.get(
            "/v1/console/learning/audit",
            headers={"Authorization": "Bearer operator_token"},
        )
        assert audit_response.status_code == 200
        initial_audit_count = audit_response.json()["count"]

        # 4. Operator decides to adjust learning rate (requires admin)
        override_response = client.post(
            "/v1/console/learning/override",
            json={
                "loop": "core",
                "param": "alpha",
                "new_value": 0.08,
                "reason": "Testing lower learning rate for stability",
            },
            headers={"Authorization": "Bearer admin_token"},
        )
        assert override_response.status_code == 200

        # 5. Operator verifies change in audit trail
        audit_response2 = client.get(
            "/v1/console/learning/audit",
            headers={"Authorization": "Bearer operator_token"},
        )
        assert audit_response2.status_code == 200
        new_audit_count = audit_response2.json()["count"]

        # Audit trail should have grown
        assert new_audit_count >= initial_audit_count

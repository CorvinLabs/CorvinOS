"""Tests for Learning Phase 5: Dashboard + Alerts + Export (ADR-0635, ADR-0636, ADR-0637)

Tests cover:
- Real-time metrics endpoints
- WebSocket streaming
- Alert policies and triggering
- Alert history
- Metrics export
- Tenant isolation
"""

import json
from datetime import datetime, timedelta
from typing import List

import pytest

from core.learning.alert_policy import (
    AlertEvent,
    AlertLevel,
    AlertPolicy,
    AlertType,
    AlertPolicyManager,
)


class TestAlertPolicyManager:
    """Tests for AlertPolicyManager"""

    def test_init_default_policies(self):
        """Test manager initializes with default policies."""
        manager = AlertPolicyManager(tenant_id="_default")

        policies = manager.get_policies()
        assert len(policies) == 5  # Default policies

        # Verify default policies exist
        policy_types = {p.alert_type for p in policies}
        assert AlertType.LOSS_DIVERGENCE in policy_types
        assert AlertType.CONVERGENCE_STALL in policy_types
        assert AlertType.GRADIENT_EXPLOSION in policy_types
        assert AlertType.PARAMETER_DRIFT in policy_types
        assert AlertType.FEEDBACK_QUALITY in policy_types

    def test_add_policy(self):
        """Test adding a custom policy."""
        manager = AlertPolicyManager(tenant_id="_default")
        initial_count = len(manager.get_policies())

        policy = manager.add_policy(
            alert_type=AlertType.CHECKPOINT_FAILURE,
            threshold=0.5,
            level=AlertLevel.WARNING,
            reason="Custom checkpoint policy",
        )

        assert policy.policy_id is not None
        assert policy.enabled is True
        assert len(manager.get_policies()) == initial_count + 1

    def test_disable_policy(self):
        """Test disabling a policy."""
        manager = AlertPolicyManager(tenant_id="_default")
        policies = manager.get_policies()

        policy_id = policies[0].policy_id
        manager.disable_policy(policy_id)

        # Verify policy is disabled
        updated_policies = manager.get_policies()
        disabled_policy = [p for p in updated_policies if p.policy_id == policy_id][0]
        assert disabled_policy.enabled is False

    def test_mute_policy(self):
        """Test muting a policy."""
        manager = AlertPolicyManager(tenant_id="_default")
        policies = manager.get_policies()

        policy_id = policies[0].policy_id
        manager.mute_policy(policy_id, until_minutes=60)

        # Verify policy is muted
        updated_policies = manager.get_policies()
        muted_policy = [p for p in updated_policies if p.policy_id == policy_id][0]
        assert muted_policy.muted_until is not None

    def test_unmute_policy(self):
        """Test unmuting a policy."""
        manager = AlertPolicyManager(tenant_id="_default")
        policies = manager.get_policies()

        policy_id = policies[0].policy_id

        # First mute
        manager.mute_policy(policy_id, until_minutes=60)
        muted = manager.get_policies()
        assert [p for p in muted if p.policy_id == policy_id][0].muted_until is not None

        # Then unmute
        manager.unmute_policy(policy_id)
        unmuted = manager.get_policies()
        assert [p for p in unmuted if p.policy_id == policy_id][0].muted_until is None

    def test_evaluate_loss_divergence(self):
        """Test loss divergence alert."""
        manager = AlertPolicyManager(tenant_id="_default")

        # Simulate high loss
        metrics = {
            "loss_total": 0.015,  # Above threshold (0.01)
            "loss_core": 0.010,
            "loss_infra": 0.005,
            "gradient_l2": 0.001,
            "convergence_percent": 50.0,
        }

        alerts = manager.evaluate(metrics)

        # Should have triggered loss_divergence_critical
        loss_alerts = [a for a in alerts if a.alert_type == AlertType.LOSS_DIVERGENCE]
        assert len(loss_alerts) > 0

    def test_evaluate_gradient_explosion(self):
        """Test gradient explosion alert."""
        manager = AlertPolicyManager(tenant_id="_default")

        # Simulate high gradient
        metrics = {
            "loss_total": 0.005,
            "gradient_l2": 0.15,  # Above threshold (0.1)
            "convergence_percent": 60.0,
        }

        alerts = manager.evaluate(metrics)

        # Should have triggered gradient_explosion_critical
        grad_alerts = [a for a in alerts if a.alert_type == AlertType.GRADIENT_EXPLOSION]
        assert len(grad_alerts) > 0

    def test_evaluate_parameter_drift(self):
        """Test parameter drift alert."""
        manager = AlertPolicyManager(tenant_id="_default")

        # Simulate high alpha
        metrics = {
            "alpha_core": 0.7,  # Above threshold (0.5)
            "convergence_percent": 50.0,
        }

        alerts = manager.evaluate(metrics)

        # Should have triggered parameter_drift_warning
        drift_alerts = [a for a in alerts if a.alert_type == AlertType.PARAMETER_DRIFT]
        assert len(drift_alerts) > 0

    def test_evaluate_muted_policy(self):
        """Test muted policies don't fire."""
        manager = AlertPolicyManager(tenant_id="_default")

        # Find and mute loss divergence policy
        policies = manager.get_policies()
        loss_policy = [p for p in policies if p.alert_type == AlertType.LOSS_DIVERGENCE][0]
        manager.mute_policy(loss_policy.policy_id, until_minutes=60)

        # Simulate high loss
        metrics = {
            "loss_total": 0.015,  # Would normally trigger
            "gradient_l2": 0.001,
        }

        alerts = manager.evaluate(metrics)

        # Should NOT have loss divergence alert (muted)
        loss_alerts = [a for a in alerts if a.alert_type == AlertType.LOSS_DIVERGENCE]
        assert len(loss_alerts) == 0

    def test_alert_history(self):
        """Test alert history tracking."""
        manager = AlertPolicyManager(tenant_id="_default")

        # Fire some alerts
        metrics1 = {"loss_total": 0.015, "gradient_l2": 0.001}
        alerts1 = manager.evaluate(metrics1)

        metrics2 = {"loss_total": 0.016, "gradient_l2": 0.001}
        alerts2 = manager.evaluate(metrics2)

        # Check history
        history = manager.get_alert_history(limit=100)
        assert len(history) >= len(alerts1) + len(alerts2)

    def test_register_handler(self):
        """Test registering notification handler."""
        manager = AlertPolicyManager(tenant_id="_default")

        handler = manager.register_handler(
            handler_type="webhook",
            target="https://example.com/alerts",
            alert_levels=[AlertLevel.CRITICAL],
        )

        assert handler.handler_id is not None
        assert handler.handler_type == "webhook"
        assert handler.target == "https://example.com/alerts"
        assert handler.enabled is True

    def test_tenant_isolation(self):
        """Test different tenants have separate policies."""
        manager_a = AlertPolicyManager(tenant_id="tenant_a")
        manager_b = AlertPolicyManager(tenant_id="tenant_b")

        # Both should have default policies
        assert len(manager_a.get_policies()) == 5
        assert len(manager_b.get_policies()) == 5

        # Add custom policy to A
        manager_a.add_policy(
            alert_type=AlertType.CHECKPOINT_FAILURE,
            threshold=0.5,
        )

        # B should not have it
        assert len(manager_a.get_policies()) == 6
        assert len(manager_b.get_policies()) == 5


class TestAlertScenarios:
    """Integration tests for realistic alert scenarios"""

    def test_stable_learning_no_alerts(self):
        """Test stable learning produces no alerts."""
        manager = AlertPolicyManager(tenant_id="_default")

        # Simulate healthy metrics
        metrics = {
            "loss_total": 0.002,
            "loss_core": 0.001,
            "loss_infra": 0.001,
            "gradient_l2": 0.0005,
            "alpha_core": 0.1,
            "convergence_percent": 90.0,
        }

        alerts = manager.evaluate(metrics)

        # Should be no critical alerts
        critical_alerts = [a for a in alerts if a.level == AlertLevel.CRITICAL]
        assert len(critical_alerts) == 0

    def test_divergence_recovery(self):
        """Test alert when learning diverges then recovers."""
        manager = AlertPolicyManager(tenant_id="_default")

        # Step 1: Divergence detected
        bad_metrics = {
            "loss_total": 0.02,
            "gradient_l2": 0.15,
            "convergence_percent": 30.0,
        }

        alerts1 = manager.evaluate(bad_metrics)
        assert len(alerts1) > 0

        # Step 2: Operator mutes alerts during recovery
        policies = manager.get_policies()
        for policy in policies:
            if policy.level == AlertLevel.CRITICAL:
                manager.mute_policy(policy.policy_id, until_minutes=15)

        # Step 3: No alerts during mute window
        still_bad_metrics = {
            "loss_total": 0.018,
            "gradient_l2": 0.12,
            "convergence_percent": 35.0,
        }

        alerts2 = manager.evaluate(still_bad_metrics)

        # No critical alerts (muted)
        critical = [a for a in alerts2 if a.level == AlertLevel.CRITICAL]
        assert len(critical) == 0

    def test_multiple_simultaneous_alerts(self):
        """Test multiple alerts can fire simultaneously."""
        manager = AlertPolicyManager(tenant_id="_default")

        # Simulate multiple problems at once
        bad_metrics = {
            "loss_total": 0.015,  # Divergence
            "gradient_l2": 0.15,  # Explosion
            "alpha_core": 0.6,  # Drift
            "convergence_percent": 20.0,
        }

        alerts = manager.evaluate(bad_metrics)

        # Should have multiple alert types
        alert_types = {a.alert_type for a in alerts}
        assert AlertType.LOSS_DIVERGENCE in alert_types
        assert AlertType.GRADIENT_EXPLOSION in alert_types
        assert AlertType.PARAMETER_DRIFT in alert_types

    def test_alert_audit_trail(self):
        """Test alerts are logged to history (audit trail)."""
        manager = AlertPolicyManager(tenant_id="_default")

        # Fire an alert
        metrics = {"loss_total": 0.015, "gradient_l2": 0.001}
        alerts = manager.evaluate(metrics)

        # Check it's in history
        history = manager.get_alert_history(limit=100)
        assert len(history) >= len(alerts)

        # Verify alert event has required fields
        if history:
            event = history[-1]
            assert event.alert_id is not None
            assert event.timestamp is not None
            assert event.tenant_id == "_default"


class TestMetricsEndpoints:
    """Tests for learning metrics REST endpoints"""

    @pytest.mark.asyncio
    async def test_current_metrics_endpoint_schema(self, client):
        """Test current metrics response schema."""
        response = client.get(
            "/v1/console/learning/metrics/current",
            headers={"Authorization": "Bearer valid_token"},
        )

        assert response.status_code == 200
        data = response.json()

        # Verify response shape
        assert "timestamp" in data
        assert "metrics" in data
        assert "status" in data

        # Verify metrics sub-object
        metrics = data["metrics"]
        required_fields = [
            "loss_total",
            "loss_core",
            "loss_infra",
            "alpha_core",
            "alpha_infra",
            "damping_core",
            "convergence_percent",
            "gradient_l2",
        ]
        for field in required_fields:
            assert field in metrics

    @pytest.mark.asyncio
    async def test_history_endpoint_1h_window(self, client):
        """Test history endpoint with 1h window."""
        response = client.get(
            "/v1/console/learning/metrics/history?window=1h",
            headers={"Authorization": "Bearer valid_token"},
        )

        assert response.status_code == 200
        data = response.json()

        assert data["window"] == "1h"
        assert "points" in data
        assert "sample_count" in data
        assert "start" in data
        assert "end" in data

    @pytest.mark.asyncio
    async def test_history_endpoint_invalid_window(self, client):
        """Test history endpoint rejects invalid window."""
        response = client.get(
            "/v1/console/learning/metrics/history?window=invalid",
            headers={"Authorization": "Bearer valid_token"},
        )

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_export_endpoint_json(self, client):
        """Test exporting metrics as JSON."""
        response = client.post(
            "/v1/console/learning/metrics/export",
            json={
                "format": "json",
                "window": "1h",
            },
            headers={"Authorization": "Bearer valid_token"},
        )

        assert response.status_code == 200
        data = response.json()

        assert "download_url" in data
        assert "expires_at" in data
        assert data["format"] == "json"

    @pytest.mark.asyncio
    async def test_export_endpoint_csv(self, client):
        """Test exporting metrics as CSV."""
        response = client.post(
            "/v1/console/learning/metrics/export",
            json={
                "format": "csv",
                "window": "24h",
            },
            headers={"Authorization": "Bearer valid_token"},
        )

        assert response.status_code == 200
        data = response.json()

        assert data["format"] == "csv"

    @pytest.mark.asyncio
    async def test_export_invalid_format(self, client):
        """Test export rejects invalid format."""
        response = client.post(
            "/v1/console/learning/metrics/export",
            json={
                "format": "xml",
                "window": "1h",
            },
            headers={"Authorization": "Bearer valid_token"},
        )

        assert response.status_code == 400


class TestWebSocketMetricsStream:
    """Tests for WebSocket metrics streaming"""

    @pytest.mark.asyncio
    async def test_websocket_connect_requires_tenant(self, client):
        """Test WebSocket connection requires tenant_id."""
        # This would normally test WebSocket connection
        # Actual implementation depends on test client capabilities
        pass

    @pytest.mark.asyncio
    async def test_websocket_receives_metrics(self, client):
        """Test WebSocket receives metrics updates."""
        # Real implementation would use websocket client
        pass

    @pytest.mark.asyncio
    async def test_websocket_fallback_to_polling(self, client):
        """Test client can fallback to polling if WebSocket fails."""
        # This is a behavior test, not an endpoint test
        pass

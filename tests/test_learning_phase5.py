"""Tests for Learning Phase 5: Dashboard + Alerts + Export (ADR-0635, ADR-0636, ADR-0637)

Alert-policy unit tests, then the metrics REST/WebSocket/export routes driven
through the REAL console router with a REAL session cookie
(``tests/learning/console_client.py``). The former route tests asserted the
synthetic placeholder numbers through a ``client`` fixture that did not exist
and skipped the WebSocket entirely (``pass``); the WebSocket is now exercised
for both the unauthenticated 1008 close and a real first frame.
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

        # loss_divergence_critical is a confirmation-gated policy: evaluate()
        # holds it pending instead of firing it (Fix #11). Assert the FULL
        # cycle — held, then fired on approval — so a gate that swallows the
        # alert forever cannot pass as "correctly held".
        assert not [a for a in alerts if a.alert_type == AlertType.LOSS_DIVERGENCE]
        pending = [c for c in manager.get_pending_confirmations()
                   if c.alert_type == AlertType.LOSS_DIVERGENCE]
        assert len(pending) > 0, "critical loss divergence produced neither an alert nor a confirmation"

        assert manager.confirm_alert(pending[0].confirmation_id, approved=True)
        fired = [a for a in manager.get_alert_history() if a.alert_type == AlertType.LOSS_DIVERGENCE]
        assert len(fired) > 0, "approving the confirmation did not fire the alert"

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
        # gradient_explosion_critical is confirmation-gated like loss divergence:
        # held by evaluate(), fired on approval. Assert the full cycle.
        assert not [a for a in alerts if a.alert_type == AlertType.GRADIENT_EXPLOSION]
        pending = [c for c in manager.get_pending_confirmations()
                   if c.alert_type == AlertType.GRADIENT_EXPLOSION]
        assert len(pending) > 0, "critical gradient explosion produced neither an alert nor a confirmation"

        assert manager.confirm_alert(pending[0].confirmation_id, approved=True)
        fired = [a for a in manager.get_alert_history() if a.alert_type == AlertType.GRADIENT_EXPLOSION]
        assert len(fired) > 0, "approving the confirmation did not fire the alert"

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

        # Multiple conditions must each produce a signal. The two critical
        # policies are confirmation-gated (Fix #11) and therefore appear as
        # pending confirmations rather than in the returned list; the
        # non-gated one fires directly. Every condition is accounted for —
        # none may be silently dropped.
        alert_types = {a.alert_type for a in alerts}
        pending_types = {c.alert_type for c in manager.get_pending_confirmations()}
        assert AlertType.PARAMETER_DRIFT in alert_types
        assert AlertType.LOSS_DIVERGENCE in pending_types
        assert AlertType.GRADIENT_EXPLOSION in pending_types

        # ...and approving them fires them.
        for conf in manager.get_pending_confirmations():
            assert manager.confirm_alert(conf.confirmation_id, approved=True)
        fired_types = {a.alert_type for a in manager.get_alert_history()}
        assert {AlertType.LOSS_DIVERGENCE, AlertType.GRADIENT_EXPLOSION,
                AlertType.PARAMETER_DRIFT} <= fired_types

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


# ============================================================================
# Metrics routes — real boundary, real data
# ============================================================================

from pathlib import Path  # noqa: E402

from starlette.websockets import WebSocketDisconnect  # noqa: E402

from tests.learning.console_client import COOKIE_NAME, console_client, write_outcome  # noqa: E402


class TestMetricsEndpoints:
    """``/v1/console/learning/metrics/*`` — single prefix, computed answers."""

    def test_no_doubled_prefix(self, tmp_path: Path):
        with console_client(tmp_path) as sb:
            assert sb.client.get("/v1/console/learning/metrics/current").status_code == 200
            assert sb.client.get("/v1/console/v1/console/learning/metrics/current").status_code == 404

    def test_current_metrics_equal_status(self, tmp_path: Path):
        with console_client(tmp_path) as sb:
            write_outcome(sb, task_id="t1", success=False)
            current = sb.client.get("/v1/console/learning/metrics/current").json()
            status = sb.client.get("/v1/console/learning/status").json()
            assert current["status"] == status["status"] == "collecting"
            assert current["metrics"]["event_counts"] == status["event_counts"]
            assert current["metrics"]["outcome_loss"] == 1.0
            for key in ("loss_total", "alpha_core", "convergence_percent", "gradient_l2"):
                assert key not in current["metrics"], f"synthetic field {key} is back"

    def test_history_windows(self, tmp_path: Path):
        with console_client(tmp_path) as sb:
            write_outcome(sb, task_id="t1", success=True)
            r = sb.client.get("/v1/console/learning/metrics/history?window=6h")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["window"] == "6h" and body["start"] < body["end"]
            assert body["sample_count"] == 1 and sum(p["outcomes"] for p in body["points"]) == 1
            assert sb.client.get("/v1/console/learning/metrics/history?window=invalid").status_code == 400

    def test_requires_session(self, tmp_path: Path):
        with console_client(tmp_path) as sb:
            sb.client.cookies.clear()
            assert sb.client.get("/v1/console/learning/metrics/current").status_code == 401
            assert sb.client.get("/v1/console/learning/metrics/history").status_code == 401


class TestExport:
    def test_export_json_is_the_data_not_a_link(self, tmp_path: Path):
        with console_client(tmp_path) as sb:
            eid = write_outcome(sb, task_id="t1", success=True)
            r = sb.client.post(
                "/v1/console/learning/metrics/export", json={"format": "json", "window": "1h"},
                headers=sb.csrf_headers,
            )
            assert r.status_code == 200, r.text
            assert r.headers["content-type"].startswith("application/x-ndjson")
            assert r.headers["x-rows-exported"] == "1"
            assert "attachment" in r.headers["content-disposition"]
            row = json.loads(r.text.strip())
            assert row["event_id"] == eid and row["event_type"] == "outcome" and row["audit_ref"]
            assert "download_url" not in r.text

    def test_export_csv(self, tmp_path: Path):
        with console_client(tmp_path) as sb:
            write_outcome(sb, task_id="t1", success=True)
            r = sb.client.post(
                "/v1/console/learning/metrics/export", json={"format": "csv", "window": "24h"},
                headers=sb.csrf_headers,
            )
            assert r.status_code == 200, r.text
            lines = r.text.strip().splitlines()
            assert lines[0].split(",") == ["event_id", "event_type", "skill_id", "timestamp", "audit_ref", "lom"]
            assert len(lines) == 2

    def test_export_rejects_bad_input(self, tmp_path: Path):
        with console_client(tmp_path) as sb:
            h = sb.csrf_headers
            assert sb.client.post("/v1/console/learning/metrics/export", json={"format": "xml", "window": "1h"}, headers=h).status_code == 400
            assert sb.client.post("/v1/console/learning/metrics/export", json={"format": "json", "window": "7d"}, headers=h).status_code == 400
            assert sb.client.post("/v1/console/learning/metrics/export", json={"format": "json", "window": "custom"}, headers=h).status_code == 400
            assert sb.client.post("/v1/console/learning/metrics/export", json={"format": "json", "window": "1h"}).status_code == 403  # no CSRF


class TestWebSocketMetricsStream:
    def test_websocket_without_session_is_closed_1008(self, tmp_path: Path):
        with console_client(tmp_path) as sb:
            sb.client.cookies.clear()
            with pytest.raises(WebSocketDisconnect) as exc:
                with sb.client.websocket_connect("/v1/console/learning/metrics/stream"):
                    pass
            assert exc.value.code == 1008

    def test_websocket_ignores_tenant_query_param_and_uses_session_tenant(self, tmp_path: Path):
        with console_client(tmp_path) as sb:
            write_outcome(sb, task_id="foreign", success=True, tenant_id="acme-corp")
            write_outcome(sb, task_id="mine", success=True)
            with sb.client.websocket_connect(
                "/v1/console/learning/metrics/stream?tenant_id=acme-corp",
                cookies={COOKIE_NAME: sb.sid},
            ) as ws:
                frame = ws.receive_json()
                assert frame["type"] == "metrics"
                assert frame["data"]["tenant_id"] == "_default"
                assert frame["data"]["event_counts"]["outcome"] == 1
                ws.send_text("ping")
                assert ws.receive_text() == "pong"

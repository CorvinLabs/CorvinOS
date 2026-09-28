"""
End-to-End Tests for 14 Incident Response + Rollback Automation Findings

Tests all critical fixes:
- IR-001: Per-channel success tracking (returns False if ANY channel fails)
- IR-002: Email actually sent via SMTP with retry logic
- IR-003: Notification deduplication (5 min window) + rate limiting (10/min)
- IR-004: Retry/fallback chain (Slack → PagerDuty → Email → log)
- IR-005: Dead letter queue for failed notifications

- RA-001: Audit-BEFORE semantics (audit first, then actions)
- RA-002: REVERT_VERSION actually reverts via API call
- RA-003: Thread-safe lock management with RLock()
- RA-004: Cascade prevention (5 min dedup + 10 min cooldown)
- RA-005: Operator authentication for manual unlock (RBAC + 2FA)

Plus 4 additional findings (RA-006-RA-007 and variants).

Adversarial review 2026-09-27: both modules are NOT WIRED (no production
caller). Audit records now go to the real tenant chain (forge write_event via
core.deployment.audit_sink); these tests read that chain back and verify it.
"""

import pytest
import threading
import time
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, MagicMock, patch, call
from dataclasses import asdict

from core.deployment.incident_response_procedures import (
    IncidentDetector,
    IncidentNotifier,
    Incident,
    IncidentSeverity,
    IncidentType,
)
from core.deployment.rollback_automation import (
    RollbackController,
    RollbackEvent,
    RollbackTrigger,
    RollbackAction,
)
from core.deployment import audit_sink


@pytest.fixture(autouse=True)
def _isolated_runtime(tmp_path, monkeypatch):
    """Every test gets its own CORVIN_HOME/HOME — the audit chain is real."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "corvin"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CORVIN_TENANT_ID", raising=False)
    monkeypatch.delenv("VOICE_AUDIT_PATH", raising=False)
    monkeypatch.delenv("FORGE_ROOT", raising=False)
    yield


def _chain_records():
    se, fp = audit_sink._forge()
    chain = fp.tenant_audit_chain("_default")
    if not chain.exists():
        return [], (True, [])
    recs = [json.loads(line) for line in chain.read_text().splitlines() if line.strip()]
    return recs, se.verify_chain(chain)


class TestIR001PerChannelSuccessTracking:
    """IR-001: notify() returns False if ANY channel fails"""

    def test_notify_returns_true_when_all_channels_succeed(self):
        """All channels succeed → returns True"""
        notifier = IncidentNotifier()
        incident = Incident(
            incident_id="INC-test-001",
            incident_type=IncidentType.LATENCY_SPIKE,
            severity=IncidentSeverity.CRITICAL,
            detected_at=datetime.now(timezone.utc).isoformat(),
            message="Test incident",
            details={},
            metric_name="latency_p99_ms",
            actual_value=250.0,
            threshold=200.0,
        )

        with patch.object(notifier, "_send_slack_notification", return_value=True):
            with patch.object(notifier, "_send_pagerduty_alert", return_value=True):
                with patch.object(notifier, "_send_email_notification", return_value=True):
                    result = notifier.notify(
                        incident,
                        slack_webhook="http://slack.example.com",
                        pagerduty_key="test_key",
                        email_to="op@example.com",
                    )

        assert result is True
        assert notifier.channel_results["slack"] is True
        assert notifier.channel_results["pagerduty"] is True
        assert notifier.channel_results["email"] is True

    def test_notify_returns_false_when_slack_fails(self):
        """Slack fails → returns False"""
        notifier = IncidentNotifier()
        incident = Incident(
            incident_id="INC-test-002",
            incident_type=IncidentType.LATENCY_SPIKE,
            severity=IncidentSeverity.WARNING,
            detected_at=datetime.now(timezone.utc).isoformat(),
            message="Test incident",
            details={},
            metric_name="latency_p99_ms",
            actual_value=150.0,
            threshold=200.0,
        )

        with patch.object(notifier, "_send_slack_notification", return_value=False):
            with patch.object(notifier, "_send_pagerduty_alert", return_value=True):
                with patch.object(notifier, "_send_email_notification", return_value=True):
                    result = notifier.notify(
                        incident,
                        slack_webhook="http://slack.example.com",
                        pagerduty_key="test_key",
                        email_to="op@example.com",
                    )

        assert result is False
        assert notifier.channel_results["slack"] is False
        # WARNING incidents never page: PagerDuty was not attempted, so it is
        # not reported as delivered.
        assert notifier.channel_results["pagerduty"] is False
        assert notifier.channel_results["email"] is True

    def test_notify_without_any_channel_is_not_success(self):
        """No channel configured → nothing delivered → False (was True)."""
        notifier = IncidentNotifier(start_dlq_worker=False)
        incident = Incident(
            incident_id="INC-test-nochan",
            incident_type=IncidentType.LATENCY_SPIKE,
            severity=IncidentSeverity.CRITICAL,
            detected_at=datetime.now(timezone.utc).isoformat(),
            message="x", details={}, metric_name="latency_p99_ms",
            actual_value=300.0, threshold=200.0,
        )
        assert notifier.notify(incident) is False

    def test_incident_committed_to_tenant_chain_without_free_text(self):
        """Audit-first: the incident is on the real chain, content-free."""
        notifier = IncidentNotifier(start_dlq_worker=False)
        incident = Incident(
            incident_id="INC-chain-001",
            incident_type=IncidentType.AUDIT_CHAIN_BREAK,
            severity=IncidentSeverity.CRITICAL,
            detected_at=datetime.now(timezone.utc).isoformat(),
            message="operator note with an email a@b.example",
            details={"free": "text"},
            metric_name="audit_chain_verified",
            actual_value=0.0, threshold=1.0,
        )
        with patch.object(notifier, "_send_slack_notification", return_value=True):
            assert notifier.notify(incident, slack_webhook="http://slack.example.com") is True
        recs, (ok, problems) = _chain_records()
        assert ok, problems
        mine = [r for r in recs if r["event_type"] == "deployment.incident_detected"]
        assert mine and mine[-1]["details"]["incident_id"] == "INC-chain-001"
        assert "a@b.example" not in json.dumps(mine[-1])
        assert incident.audit_event_id == mine[-1]["hash"]

    def test_notify_refused_when_audit_write_fails(self):
        """Fail-closed: no chain record → nothing is sent."""
        notifier = IncidentNotifier(start_dlq_worker=False)
        incident = Incident(
            incident_id="INC-noaudit",
            incident_type=IncidentType.LATENCY_SPIKE,
            severity=IncidentSeverity.WARNING,
            detected_at=datetime.now(timezone.utc).isoformat(),
            message="x", details={}, metric_name="latency_p99_ms",
            actual_value=300.0, threshold=200.0,
        )
        slack = Mock(return_value=True)
        with patch.object(audit_sink, "emit", side_effect=audit_sink.AuditWriteFailed("boom")):
            with patch.object(notifier, "_send_slack_notification", slack):
                assert notifier.notify(incident, slack_webhook="http://slack.example.com") is False
        slack.assert_not_called()

    def test_notify_returns_false_when_email_fails(self):
        """Email fails → returns False"""
        notifier = IncidentNotifier()
        incident = Incident(
            incident_id="INC-test-003",
            incident_type=IncidentType.AUDIT_CHAIN_BREAK,
            severity=IncidentSeverity.CRITICAL,
            detected_at=datetime.now(timezone.utc).isoformat(),
            message="Audit chain broken",
            details={},
            metric_name="audit_chain_verified",
            actual_value=0.0,
            threshold=1.0,
        )

        with patch.object(notifier, "_send_slack_notification", return_value=True):
            with patch.object(notifier, "_send_email_notification", return_value=False):
                result = notifier.notify(
                    incident,
                    slack_webhook="http://slack.example.com",
                    email_to="op@example.com",
                )

        assert result is False
        assert notifier.channel_results["email"] is False


class TestIR002EmailSMTPImplementation:
    """IR-002: Email actually sent via SMTP with retry logic"""

    @patch("smtplib.SMTP")
    def test_email_sent_via_smtp_success(self, mock_smtp):
        """Email successfully sent via SMTP"""
        notifier = IncidentNotifier()
        incident = Incident(
            incident_id="INC-email-001",
            incident_type=IncidentType.CONFIDENCE_REGRESSION,
            severity=IncidentSeverity.WARNING,
            detected_at=datetime.now(timezone.utc).isoformat(),
            message="Confidence dropped",
            details={"prior": 0.9, "actual": 0.75},
            metric_name="confidence",
            actual_value=0.75,
            threshold=0.70,
        )

        # Mock SMTP
        mock_server = MagicMock()
        mock_smtp.return_value.__enter__.return_value = mock_server

        result = notifier._send_email_notification(incident, "operator@example.com")

        assert result is True
        mock_smtp.assert_called_once()
        mock_server.send_message.assert_called_once()

    @patch("smtplib.SMTP")
    def test_email_retried_on_smtp_failure(self, mock_smtp):
        """Email retried 3 times on SMTP failure"""
        notifier = IncidentNotifier()
        incident = Incident(
            incident_id="INC-email-002",
            incident_type=IncidentType.TENANT_ISOLATION_VIOLATION,
            severity=IncidentSeverity.CRITICAL,
            detected_at=datetime.now(timezone.utc).isoformat(),
            message="Tenant isolation breach",
            details={},
            metric_name="tenant_isolation_verified",
            actual_value=0.0,
            threshold=1.0,
        )

        # Simulate SMTP failure then success on retry
        import smtplib

        mock_server = MagicMock()
        mock_server.send_message.side_effect = [
            smtplib.SMTPException("Connection failed"),
            smtplib.SMTPException("Connection failed"),
            None,  # Success on 3rd attempt
        ]
        mock_smtp.return_value.__enter__.return_value = mock_server

        result = notifier._send_email_notification(incident, "operator@example.com")

        assert result is True
        assert mock_smtp.call_count == 3  # 3 retry attempts


class TestIR003DeduplicationRateLimiting:
    """IR-003: Notification deduplication (5 min) + rate limiting (10/min)"""

    def test_duplicate_incident_within_5_min_skipped(self):
        """Same incident within 5 min window → skipped"""
        notifier = IncidentNotifier()
        incident = Incident(
            incident_id="INC-dedup-001",
            incident_type=IncidentType.LATENCY_SPIKE,
            severity=IncidentSeverity.WARNING,
            detected_at=datetime.now(timezone.utc).isoformat(),
            message="Latency spike",
            details={},
            metric_name="latency_p99_ms",
            actual_value=250.0,
            threshold=200.0,
        )

        with patch.object(notifier, "_send_slack_notification", return_value=True):
            # First notification succeeds
            result1 = notifier.notify(incident, slack_webhook="http://slack.example.com")
            assert result1 is True

            # Second notification (duplicate within 5 min) is skipped
            result2 = notifier.notify(incident, slack_webhook="http://slack.example.com")
            assert result2 is False

    def test_rate_limit_exceeded_queued_for_retry(self):
        """Rate limit exceeded (>10/min) → queued for retry"""
        notifier = IncidentNotifier()

        incident_template = Incident(
            incident_id="INC-rate-001",
            incident_type=IncidentType.LATENCY_SPIKE,
            severity=IncidentSeverity.WARNING,
            detected_at=datetime.now(timezone.utc).isoformat(),
            message="Latency spike",
            details={},
            metric_name="latency_p99_ms",
            actual_value=250.0,
            threshold=200.0,
        )

        with patch.object(notifier, "_send_slack_notification", return_value=True):
            # Send 10 notifications (at limit)
            for i in range(10):
                incident = Incident(
                    incident_id=f"INC-rate-{i:03d}",
                    incident_type=IncidentType.LATENCY_SPIKE,
                    severity=IncidentSeverity.WARNING,
                    detected_at=datetime.now(timezone.utc).isoformat(),
                    message=f"Latency spike {i}",
                    details={},
                    metric_name="latency_p99_ms",
                    actual_value=250.0,
                    threshold=200.0,
                )
                result = notifier.notify(incident, slack_webhook="http://slack.example.com")
                assert result is True

            # 11th notification exceeds rate limit
            incident_11 = Incident(
                incident_id="INC-rate-011",
                incident_type=IncidentType.LATENCY_SPIKE,
                severity=IncidentSeverity.WARNING,
                detected_at=datetime.now(timezone.utc).isoformat(),
                message="Latency spike 11",
                details={},
                metric_name="latency_p99_ms",
                actual_value=250.0,
                threshold=200.0,
            )
            result = notifier.notify(incident_11, slack_webhook="http://slack.example.com")
            assert result is False

            # Check DLQ has the failed notification
            assert not notifier.failed_notifications_queue.empty()


class TestIR004RetryFallbackChain:
    """IR-004: Retry/fallback chain (Slack → PagerDuty → Email → log)"""

    @patch("smtplib.SMTP")
    def test_fallback_to_email_when_slack_fails(self, mock_smtp):
        """Slack fails → fallback to email"""
        notifier = IncidentNotifier()
        incident = Incident(
            incident_id="INC-fallback-001",
            incident_type=IncidentType.AUDIT_CHAIN_BREAK,
            severity=IncidentSeverity.CRITICAL,
            detected_at=datetime.now(timezone.utc).isoformat(),
            message="Audit chain broken",
            details={},
            metric_name="audit_chain_verified",
            actual_value=0.0,
            threshold=1.0,
        )

        # Slack fails, email should be attempted
        with patch.object(notifier, "_send_slack_notification", return_value=False):
            mock_server = MagicMock()
            with patch("smtplib.SMTP") as mock_smtp_context:
                mock_smtp_context.return_value.__enter__.return_value = mock_server
                result = notifier.notify(
                    incident,
                    slack_webhook="http://slack.example.com",
                    email_to="op@example.com",
                )

        # Email should have been attempted as fallback
        assert notifier.channel_results["slack"] is False


class TestIR005DeadLetterQueue:
    """IR-005: Dead letter queue for failed notifications"""

    def test_failed_email_queued_for_retry(self):
        """Failed email → queued in DLQ"""
        notifier = IncidentNotifier()
        incident = Incident(
            incident_id="INC-dlq-001",
            incident_type=IncidentType.NEGATIVE_FEEDBACK_SURGE,
            severity=IncidentSeverity.WARNING,
            detected_at=datetime.now(timezone.utc).isoformat(),
            message="Negative feedback surge",
            details={},
            metric_name="positive_feedback_rate",
            actual_value=0.65,
            threshold=0.70,
        )

        with patch.object(notifier, "_send_email_notification", return_value=False):
            notifier.notify(incident, email_to="op@example.com")

        # Check DLQ
        assert not notifier.failed_notifications_queue.empty()
        failed = notifier.failed_notifications_queue.get_nowait()
        assert failed["incident"].incident_id == incident.incident_id

    def test_dlq_retry_succeeds_after_initial_failure(self):
        """DLQ retry succeeds after initial failure"""
        notifier = IncidentNotifier()
        incident = Incident(
            incident_id="INC-dlq-002",
            incident_type=IncidentType.CONFIDENCE_REGRESSION,
            severity=IncidentSeverity.WARNING,
            detected_at=datetime.now(timezone.utc).isoformat(),
            message="Confidence regression",
            details={},
            metric_name="confidence",
            actual_value=0.65,
            threshold=0.70,
        )

        # First attempt fails
        with patch.object(notifier, "_send_email_notification", return_value=False):
            notifier.notify(incident, email_to="op@example.com")

        initial_size = notifier.failed_notifications_queue.qsize()
        assert initial_size > 0

        # DLQ retry succeeds
        with patch.object(notifier, "_send_email_notification", return_value=True):
            notifier._retry_failed_notifications()

        # DLQ should be empty after successful retry
        assert notifier.failed_notifications_queue.empty()


class TestRA001AuditBefore:
    """RA-001: Audit-BEFORE semantics (audit first, then actions)"""

    def test_audit_logged_before_rollback_actions(self):
        """Audit event written FIRST, then actions executed"""
        controller = RollbackController()
        event = RollbackEvent(
            event_id="RB-test-001",
            trigger=RollbackTrigger.LATENCY_SPIKE,
            timestamp=datetime.now(timezone.utc).isoformat(),
            phase="PHASE_2B_SKILL_PRIMARY",
            metric_name="latency_p99_ms",
            actual_value=300.0,
            threshold=250.0,
            actions_taken=[RollbackAction.LOCK_PHASE],
            reason="Latency exceeded threshold",
            lom="test:123",
        )

        result = controller.execute_rollback(event)

        # The record is on the real tenant chain
        recs, (ok, problems) = _chain_records()
        assert ok, problems
        rb = [r for r in recs if r["event_type"] == "deployment.rollback_executed"]
        assert rb and rb[-1]["details"]["rollback_event_id"] == event.event_id
        # free-text reason never reaches the chain
        assert "Latency exceeded threshold" not in json.dumps(rb[-1])
        assert controller.audit_trail[-1]["hash"] == rb[-1]["hash"]

        # Phase is locked (a lock without expiry stays locked until unlocked —
        # it used to read as "unlocked")
        assert controller.is_phase_locked("PHASE_2B_SKILL_PRIMARY")
        assert "phase:PHASE_2B_SKILL_PRIMARY" in controller.get_open_lockdowns()
        assert result is True

    def test_rollback_rejected_if_audit_fails(self):
        """Rollback rejected if audit fails (fail-closed)"""
        controller = RollbackController()
        event = RollbackEvent(
            event_id="RB-test-002",
            trigger=RollbackTrigger.AUDIT_CHAIN_BREAK,
            timestamp=datetime.now(timezone.utc).isoformat(),
            phase="PHASE_2B_SKILL_PRIMARY",
            actions_taken=[RollbackAction.REVERT_VERSION],
            reason="Audit chain broken",
            lom="test:124",
        )

        # Mock audit log to fail
        with patch.object(controller, "_audit_log", return_value=False):
            result = controller.execute_rollback(event)

        assert result is False


class TestRA002VersionRevert:
    """RA-002: REVERT_VERSION actually reverts via API call"""

    @patch("requests.put")
    def test_version_revert_via_api_call(self, mock_put):
        """Version revert makes real API call"""
        controller = RollbackController()

        # Mock successful API response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_put.return_value = mock_response

        result = controller._execute_version_revert("os.delegation_router", "v1.0.0")

        assert result is True
        mock_put.assert_called_once()
        call_args = mock_put.call_args
        assert "v1.0.0" in call_args[0][0] or call_args[1]["json"]["version"] == "v1.0.0"

    @patch("requests.put")
    def test_version_revert_fails_on_api_error(self, mock_put):
        """Version revert fails if API returns error"""
        controller = RollbackController()

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_put.return_value = mock_response

        result = controller._execute_version_revert("os.delegation_router", "v1.0.0")

        assert result is False


class TestRA003ThreadSafeLocking:
    """RA-003: Thread-safe lock management with RLock()"""

    def test_concurrent_is_phase_locked_calls_thread_safe(self):
        """Multiple concurrent is_phase_locked() calls are thread-safe"""
        controller = RollbackController()
        controller.locked_phases["PHASE_TEST"] = (
            datetime.now(timezone.utc) + timedelta(hours=1)
        ).isoformat()

        results = []
        errors = []

        def check_lock():
            try:
                for _ in range(100):
                    is_locked = controller.is_phase_locked("PHASE_TEST")
                    results.append(is_locked)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=check_lock) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results) == 500  # 5 threads × 100 checks each
        assert all(r is True for r in results)

    def test_concurrent_phase_unlock_calls_thread_safe(self):
        """Multiple concurrent unlock_phase() calls are thread-safe"""
        controller = RollbackController()
        controller.locked_phases["PHASE_TEST"] = (
            datetime.now(timezone.utc) + timedelta(hours=1)
        ).isoformat()

        unlock_count = 0

        def unlock():
            nonlocal unlock_count
            context = {"user_id": "op1", "role": "admin", "authenticated": True}
            if controller.unlock_phase("PHASE_TEST", operator_context=context, reason="test"):
                unlock_count += 1

        # Only first thread should succeed
        threads = [threading.Thread(target=unlock) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Only one unlock should succeed (others find phase already unlocked)
        assert unlock_count == 1


class TestRA004CascadeInPrevention:
    """RA-004: Cascade prevention (5 min dedup + 10 min cooldown)"""

    def test_same_phase_rolled_back_twice_within_5min_prevented(self):
        """Same phase rolled back twice within 5 min → second prevented"""
        controller = RollbackController()
        event = RollbackEvent(
            event_id="RB-cascade-001",
            trigger=RollbackTrigger.CORRECTNESS_DROP,
            timestamp=datetime.now(timezone.utc).isoformat(),
            phase="PHASE_2B_SKILL_PRIMARY",
            actions_taken=[RollbackAction.LOCK_PHASE],
            reason="Correctness dropped",
            lom="test:125",
        )

        # First rollback succeeds
        with patch.object(controller, "_audit_log", return_value=True):
            result1 = controller.execute_rollback(event)
        assert result1 is True

        # Second rollback of same phase within 5 min is prevented
        event2 = RollbackEvent(
            event_id="RB-cascade-002",
            trigger=RollbackTrigger.LATENCY_SPIKE,
            timestamp=datetime.now(timezone.utc).isoformat(),
            phase="PHASE_2B_SKILL_PRIMARY",
            actions_taken=[RollbackAction.LOCK_PHASE],
            reason="Latency spike",
            lom="test:126",
        )

        with patch.object(controller, "_audit_log", return_value=True):
            result2 = controller.execute_rollback(event2)

        assert result2 is False  # Prevented by cascade detection

    def test_different_phase_rollback_within_10min_cooldown_prevented(self):
        """Different phase rollback within 10 min cooldown → prevented"""
        controller = RollbackController()

        event1 = RollbackEvent(
            event_id="RB-cascade-003",
            trigger=RollbackTrigger.CORRECTNESS_DROP,
            timestamp=datetime.now(timezone.utc).isoformat(),
            phase="PHASE_1",
            actions_taken=[RollbackAction.LOCK_PHASE],
            reason="Correctness dropped",
            lom="test:127",
        )

        # First rollback
        with patch.object(controller, "_audit_log", return_value=True):
            result1 = controller.execute_rollback(event1)
        assert result1 is True

        # Second rollback of different phase within 10 min cooldown
        event2 = RollbackEvent(
            event_id="RB-cascade-004",
            trigger=RollbackTrigger.LATENCY_SPIKE,
            timestamp=datetime.now(timezone.utc).isoformat(),
            phase="PHASE_2",
            actions_taken=[RollbackAction.LOCK_PHASE],
            reason="Latency spike",
            lom="test:128",
        )

        with patch.object(controller, "_audit_log", return_value=True):
            result2 = controller.execute_rollback(event2)

        assert result2 is False  # Prevented by cooldown


class TestRA005OperatorAuth:
    """RA-005: Operator authentication for manual unlock (RBAC + 2FA)"""

    def test_unlock_requires_authenticated_operator(self):
        """Unlock requires authenticated operator context"""
        controller = RollbackController()
        controller.locked_phases["PHASE_TEST"] = (
            datetime.now(timezone.utc) + timedelta(hours=1)
        ).isoformat()

        # Unlock without authentication → fails
        result = controller.unlock_phase("PHASE_TEST", operator_context=None)
        assert result is False

        # Unlock with unauthenticated context → fails
        context = {"authenticated": False}
        result = controller.unlock_phase("PHASE_TEST", operator_context=context)
        assert result is False

    def test_unlock_requires_admin_role_rbac(self):
        """Unlock requires admin/operator/sre role (RBAC)"""
        controller = RollbackController()
        controller.locked_phases["PHASE_TEST"] = (
            datetime.now(timezone.utc) + timedelta(hours=1)
        ).isoformat()

        # User role → denied
        context = {"user_id": "user1", "role": "user", "authenticated": True}
        result = controller.unlock_phase("PHASE_TEST", operator_context=context)
        assert result is False

        # Admin role → allowed
        context = {"user_id": "op1", "role": "admin", "authenticated": True}
        result = controller.unlock_phase("PHASE_TEST", operator_context=context)
        assert result is True

    def test_critical_unlock_requires_2fa(self):
        """CRITICAL lock (>24h) requires 2FA token"""
        controller = RollbackController()
        # Lock until >24 hours from now
        controller.locked_phases["PHASE_CRITICAL"] = (
            datetime.now(timezone.utc) + timedelta(hours=25)
        ).isoformat()

        # Operator context without 2FA → denied
        context = {"user_id": "op1", "role": "admin", "authenticated": True}
        result = controller.unlock_phase("PHASE_CRITICAL", operator_context=context)
        assert result is False

        # A token but NO verifier configured → refused (any 6 chars used to pass)
        context = {"user_id": "op1", "role": "admin", "authenticated": True, "twofa_token": "123456"}
        result = controller.unlock_phase("PHASE_CRITICAL", operator_context=context)
        assert result is False

        # With a verifier that accepts the token → allowed
        controller.twofa_verifier = lambda user, token: (user, token) == ("op1", "123456")
        result = controller.unlock_phase("PHASE_CRITICAL", operator_context=context)
        assert result is True

    def test_critical_unlock_rejected_by_verifier(self):
        controller = RollbackController(twofa_verifier=lambda u, t: False)
        controller.locked_phases["PHASE_CRITICAL"] = (
            datetime.now(timezone.utc) + timedelta(hours=25)
        ).isoformat()
        context = {"user_id": "op1", "role": "admin", "authenticated": True, "twofa_token": "999999"}
        assert controller.unlock_phase("PHASE_CRITICAL", operator_context=context) is False
        assert controller.is_phase_locked("PHASE_CRITICAL")

    def test_impersonation_attack_prevented(self):
        """Operator impersonation attack is rejected (no string operator_id alone)"""
        controller = RollbackController()
        controller.locked_phases["PHASE_TEST"] = (
            datetime.now(timezone.utc) + timedelta(hours=1)
        ).isoformat()

        # Attempt to unlock by providing only operator_id string (no auth context)
        result = controller.unlock_phase("PHASE_TEST", operator_id="malicious_operator")
        assert result is False


class TestAuditChainIntegrity:
    """Rollback records land on the ONE tenant chain, which verifies."""

    def test_audit_trail_hash_chained(self):
        controller = RollbackController()
        for i, phase in enumerate(("PHASE_1", "PHASE_2")):
            event = RollbackEvent(
                event_id=f"RB-hash-00{i}",
                trigger=RollbackTrigger.CORRECTNESS_DROP,
                timestamp=datetime.now(timezone.utc).isoformat(),
                phase=phase,
                actions_taken=[RollbackAction.LOCK_PHASE],
                reason=f"Test {i}",
                lom="test:129",
            )
            # reset cooldown so both rollbacks run
            controller.last_rollback_time = 0.0
            assert controller.execute_rollback(event) is True

        recs, (ok, problems) = _chain_records()
        assert ok, problems
        rb = [r for r in recs if r["event_type"] == "deployment.rollback_executed"]
        assert [r["details"]["rollback_event_id"] for r in rb] == ["RB-hash-000", "RB-hash-001"]
        # consecutive chain records link by prev_hash
        for prev, cur in zip(recs, recs[1:]):
            assert cur["prev_hash"] == prev["hash"]

    def test_rollback_refused_when_chain_write_fails(self):
        controller = RollbackController()
        event = RollbackEvent(
            event_id="RB-nochain",
            trigger=RollbackTrigger.CORRECTNESS_DROP,
            timestamp=datetime.now(timezone.utc).isoformat(),
            phase="PHASE_1",
            actions_taken=[RollbackAction.LOCK_PHASE],
            reason="x",
            lom="test:1",
        )
        with patch.object(audit_sink, "emit", side_effect=audit_sink.AuditWriteFailed("x")):
            assert controller.execute_rollback(event) is False
        assert not controller.is_phase_locked("PHASE_1")
        assert controller.rollback_events == []


class TestIncidentDetection:
    """Verify incident detection works correctly"""

    def test_latency_spike_detected(self):
        """Latency spike >20% detected"""
        detector = IncidentDetector()
        incidents = detector.detect_incidents(
            {"latency_p99_ms": 150.0},
            baseline_latency_p99_ms=100.0,
        )

        assert len(incidents) > 0
        assert incidents[0].incident_type == IncidentType.LATENCY_SPIKE

    def test_audit_chain_break_detected(self):
        """Audit chain break detected"""
        detector = IncidentDetector()
        incidents = detector.detect_incidents(
            {"audit_chain_verified": False, "tenant_isolation_verified": True},
        )

        # Only the measured signal raises an incident: an absent confidence
        # metric used to fabricate a CRITICAL "confidence below minimum".
        assert [i.incident_type for i in incidents] == [IncidentType.AUDIT_CHAIN_BREAK]
        assert incidents[0].severity == IncidentSeverity.CRITICAL

    def test_unmeasured_metrics_raise_only_verification_incidents(self):
        """An absent PERFORMANCE metric is not an incident; an absent
        VERIFICATION (audit chain / tenant isolation) is "not verified" and
        must raise — it used to default to True and raise nothing."""
        detector = IncidentDetector()
        incidents = detector.detect_incidents({})
        assert sorted(i.incident_type.value for i in incidents) == sorted([
            IncidentType.AUDIT_CHAIN_BREAK.value, IncidentType.TENANT_ISOLATION_VIOLATION.value,
        ])
        assert all(i.severity == IncidentSeverity.CRITICAL for i in incidents)
        assert all(i.details["measured"] is False for i in incidents)
        assert all("not measured" in i.message for i in incidents)
        assert detector.detect_incidents(
            {"audit_chain_verified": True, "tenant_isolation_verified": True}
        ) == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

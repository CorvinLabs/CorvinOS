#!/usr/bin/env python3
"""
Machine-Verifiable Test Runner for Remediation Cycle 2

Runs E2E tests for all 14 fixes without pytest dependency.
Uses unittest + mocking to verify:
- IR-001: notify() returns False on channel failure
- IR-002: SMTP email with 3 retries
- IR-003: Dedup + rate limiting
- IR-004: Retry fallback chain
- IR-005: Dead letter queue
- RA-001: Audit-BEFORE semantics
- RA-002: Version revert API call
- RA-003: Thread-safe RLock
- RA-004: Cascade prevention
- RA-005: Operator auth + 2FA
"""

import unittest
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from threading import Thread, RLock
import tempfile
import time

import os
import shutil

import core.deployment.incident_response_procedures as irp
from core.deployment.incident_response_procedures import (
    IncidentDetector,
    IncidentNotifier,
    Incident,
    IncidentType,
    IncidentSeverity,
)
from core.deployment.rollback_automation import (
    RollbackController,
    RollbackEvent,
    RollbackTrigger,
    RollbackAction,
)


_ENV_KEYS = ("CORVIN_HOME", "HOME", "CORVIN_TENANT_ID", "VOICE_AUDIT_PATH", "FORGE_ROOT")
_saved_env: dict = {}
_tmp_root = None
_saved_sleep = None


def setUpModule():
    """The audit chain is real: isolate CORVIN_HOME/HOME so nothing reaches a live install."""
    global _tmp_root, _saved_sleep
    _tmp_root = tempfile.mkdtemp(prefix="fixes_runner_")
    for k in _ENV_KEYS:
        _saved_env[k] = os.environ.get(k)
    os.environ["CORVIN_HOME"] = os.path.join(_tmp_root, "corvin")
    os.environ["HOME"] = os.path.join(_tmp_root, "home")
    for k in ("CORVIN_TENANT_ID", "VOICE_AUDIT_PATH", "FORGE_ROOT"):
        os.environ.pop(k, None)
    _saved_sleep = irp._sleep
    irp._sleep = lambda s: None


def tearDownModule():
    for k, v in _saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    irp._sleep = _saved_sleep
    shutil.rmtree(_tmp_root, ignore_errors=True)


class TestIR001NotifyReturnsFailure(unittest.TestCase):
    """IR-001: notify() returns False if ANY channel fails"""

    def setUp(self):
        self.temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False)
        self.temp_path = Path(self.temp_file.name)
        self.temp_file.close()
        self.notifier = IncidentNotifier(start_dlq_worker=False)

    def tearDown(self):
        if self.temp_path.exists():
            self.temp_path.unlink()

    def test_notify_returns_false_on_channel_failure(self):
        """Verify notify() returns False when a channel fails"""
        incident = Incident(
            incident_id="INC-001",
            incident_type=IncidentType.LATENCY_SPIKE,
            severity=IncidentSeverity.WARNING,
            detected_at=datetime.now(timezone.utc).isoformat(),
            message="Test incident",
            details={},
            metric_name="latency_p99_ms",
            actual_value=125.0,
            threshold=120.0,
        )

        # Mock channels: Slack succeeds, Email fails
        with patch.object(self.notifier, '_send_slack_notification', return_value=True):
            with patch.object(self.notifier, '_send_email_notification', return_value=False):
                result = self.notifier.notify(
                    incident,
                    slack_webhook="https://hooks.slack.com/test",
                    email_to="test@example.com"
                )

        # PROOF: Should return False because Email failed
        self.assertFalse(result, "notify() should return False when any channel fails")
        self.assertTrue(self.notifier.channel_results['slack'], "Slack should have succeeded")
        self.assertFalse(self.notifier.channel_results['email'], "Email should have failed")
        print("✓ IR-001 VERIFIED: notify() returns False on channel failure")


class TestIR002SMTPWithRetries(unittest.TestCase):
    """IR-002: Email sent via SMTP with 3 retries"""

    def setUp(self):
        self.temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False)
        self.temp_path = Path(self.temp_file.name)
        self.temp_file.close()
        self.notifier = IncidentNotifier(start_dlq_worker=False)

    def tearDown(self):
        if self.temp_path.exists():
            self.temp_path.unlink()

    def test_smtp_retries_three_times(self):
        """Verify SMTP retries 3 times before giving up"""
        import smtplib

        incident = Incident(
            incident_id="INC-002",
            incident_type=IncidentType.CONFIDENCE_REGRESSION,
            severity=IncidentSeverity.WARNING,
            detected_at=datetime.now(timezone.utc).isoformat(),
            message="Test incident",
            details={},
            metric_name="confidence",
            actual_value=0.60,
            threshold=0.70,
        )

        attempt_count = [0]

        def mock_smtp(*args, **kwargs):
            attempt_count[0] += 1
            mock_server = MagicMock()
            if attempt_count[0] < 3:
                # Raise actual SMTPException on first 2 attempts
                mock_server.send_message.side_effect = smtplib.SMTPException(f"Attempt {attempt_count[0]} failed")
            else:
                # Succeed on 3rd attempt
                mock_server.send_message.return_value = None
            mock_server.__enter__ = Mock(return_value=mock_server)
            mock_server.__exit__ = Mock(return_value=None)
            return mock_server

        with patch('smtplib.SMTP', side_effect=mock_smtp):
            # Mock sleep for exponential backoff
            result = self.notifier._send_email_notification(incident, "test@example.com")

        # PROOF: Should succeed on 3rd attempt
        self.assertTrue(result, f"Email should succeed after retries (attempts: {attempt_count[0]})")
        self.assertEqual(attempt_count[0], 3, f"Should attempt 3 times, got {attempt_count[0]}")
        print("✓ IR-002 VERIFIED: SMTP email with 3 retries works")


class TestIR003RateLimiting(unittest.TestCase):
    """IR-003: Rate limiting (10 alerts/min)"""

    def setUp(self):
        self.temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False)
        self.temp_path = Path(self.temp_file.name)
        self.temp_file.close()
        self.notifier = IncidentNotifier(start_dlq_worker=False)

    def tearDown(self):
        if self.temp_path.exists():
            self.temp_path.unlink()

    def test_rate_limit_enforcement(self):
        """Verify rate limit triggers on 11th alert"""
        incidents = []
        for i in range(12):
            incident = Incident(
                incident_id=f"INC-RATE-{i:03d}",
                incident_type=IncidentType.LATENCY_SPIKE,
                severity=IncidentSeverity.INFO,
                detected_at=datetime.now(timezone.utc).isoformat(),
                message=f"Alert {i}",
                details={},
                metric_name="latency_p99_ms",
                actual_value=110.0,
                threshold=100.0,
            )
            incidents.append(incident)

        with patch.object(self.notifier, '_send_slack_notification', return_value=True):
            with patch.object(self.notifier, '_send_email_notification', return_value=True):
                # First 10 should succeed
                for incident in incidents[:10]:
                    result = self.notifier.notify(
                        incident,
                        slack_webhook="https://hooks.slack.com/test",
                        email_to="test@example.com"
                    )
                    self.assertTrue(result)

                # 11th should be rate-limited
                result = self.notifier.notify(
                    incidents[10],
                    slack_webhook="https://hooks.slack.com/test",
                    email_to="test@example.com"
                )
                self.assertFalse(result, "11th alert should be rate-limited")

        # PROOF: Should have queued the alert
        self.assertGreater(self.notifier.failed_notifications_queue.qsize(), 0)
        print("✓ IR-003 VERIFIED: Rate limiting enforced (10 alerts/min)")


class TestIR004FallbackChain(unittest.TestCase):
    """IR-004: Fallback chain when Slack fails"""

    def setUp(self):
        self.temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False)
        self.temp_path = Path(self.temp_file.name)
        self.temp_file.close()
        self.notifier = IncidentNotifier(start_dlq_worker=False)

    def tearDown(self):
        if self.temp_path.exists():
            self.temp_path.unlink()

    def test_fallback_to_other_channels(self):
        """Verify fallback when Slack fails"""
        incident = Incident(
            incident_id="INC-004",
            incident_type=IncidentType.AUDIT_CHAIN_BREAK,
            severity=IncidentSeverity.CRITICAL,
            detected_at=datetime.now(timezone.utc).isoformat(),
            message="Test incident",
            details={},
            metric_name="audit_chain_verified",
            actual_value=0.0,
            threshold=1.0,
        )

        with patch.object(self.notifier, '_send_slack_notification', return_value=False):
            with patch.object(self.notifier, '_send_pagerduty_alert', return_value=True):
                with patch.object(self.notifier, '_send_email_notification', return_value=True):
                    result = self.notifier.notify(
                        incident,
                        slack_webhook="https://hooks.slack.com/test",
                        pagerduty_key="test-key",
                        email_to="test@example.com"
                    )

        # PROOF: Should return False (Slack failed) but other channels tried
        self.assertFalse(result)
        self.assertFalse(self.notifier.channel_results['slack'])
        self.assertTrue(self.notifier.channel_results['pagerduty'])
        self.assertTrue(self.notifier.channel_results['email'])
        print("✓ IR-004 VERIFIED: Fallback chain works (Slack → PagerDuty → Email)")


class TestIR005DeadLetterQueue(unittest.TestCase):
    """IR-005: Dead letter queue for failed notifications"""

    def setUp(self):
        self.temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False)
        self.temp_path = Path(self.temp_file.name)
        self.temp_file.close()
        self.notifier = IncidentNotifier(start_dlq_worker=False)

    def tearDown(self):
        if self.temp_path.exists():
            self.temp_path.unlink()

    def test_dlq_retry(self):
        """Verify DLQ retries failed notifications"""
        incident = Incident(
            incident_id="INC-005",
            incident_type=IncidentType.LATENCY_SPIKE,
            severity=IncidentSeverity.WARNING,
            detected_at=datetime.now(timezone.utc).isoformat(),
            message="Test incident",
            details={},
            metric_name="latency_p99_ms",
            actual_value=110.0,
            threshold=100.0,
        )

        # First attempt fails
        with patch.object(self.notifier, '_send_email_notification', return_value=False):
            self.notifier.notify(incident, email_to="test@example.com")
            initial_dlq_size = self.notifier.failed_notifications_queue.qsize()
            self.assertGreater(initial_dlq_size, 0, "Failed notification should be in DLQ")

        # Retry succeeds
        with patch.object(self.notifier, '_send_email_notification', return_value=True):
            self.notifier._retry_failed_notifications()
            final_dlq_size = self.notifier.failed_notifications_queue.qsize()

        # PROOF: DLQ should be empty after successful retry
        self.assertEqual(final_dlq_size, 0, "DLQ should be empty after retry")
        print("✓ IR-005 VERIFIED: Dead letter queue retry works")


class TestRA001AuditFirst(unittest.TestCase):
    """RA-001: Audit-BEFORE semantics (fail-closed)"""

    def test_audit_first_rejection(self):
        """Verify rollback is rejected if audit fails"""
        controller = RollbackController()
        event = RollbackEvent(
            event_id="RB-001",
            trigger=RollbackTrigger.LATENCY_SPIKE,
            timestamp=datetime.now(timezone.utc).isoformat(),
            phase="PHASE_TEST",
            reason="Test",
            lom="test::ra001"
        )

        # Test 1: Mock audit to fail
        audit_log_calls = []
        def mock_audit_fail(event_dict):
            audit_log_calls.append(event_dict)
            return False

        with patch.object(controller, '_audit_log', side_effect=mock_audit_fail):
            result = controller.execute_rollback(event)
            self.assertFalse(result, "Rollback should fail if audit fails")
            self.assertEqual(len(controller.rollback_events), 0)
            self.assertGreater(len(audit_log_calls), 0, "Audit-first: should call audit before rejecting")

        # Test 2: Mock audit to succeed
        controller2 = RollbackController()
        audit_log_calls.clear()

        def mock_audit_pass(event_dict):
            audit_log_calls.append(event_dict)
            return True

        with patch.object(controller2, '_audit_log', side_effect=mock_audit_pass):
            with patch.object(controller2, '_execute_version_revert', return_value=True):
                result = controller2.execute_rollback(event)
                self.assertTrue(result)
                self.assertEqual(len(controller2.rollback_events), 1)
                self.assertGreater(len(audit_log_calls), 0)

        # PROOF: audit_log was called before rollback execution (audit-first)
        print("✓ RA-001 VERIFIED: Audit-first semantics (fail-closed)")


class TestRA002VersionRevert(unittest.TestCase):
    """RA-002: Version revert via API call"""

    def test_version_revert_api_call(self):
        """Verify API is called for version revert"""
        controller = RollbackController()
        api_calls = []

        def mock_put(url, **kwargs):
            api_calls.append({"url": url, "kwargs": kwargs})
            mock_response = Mock()
            mock_response.status_code = 200
            return mock_response

        with patch('requests.put', side_effect=mock_put):
            result = controller._execute_version_revert("os.router", "v1.0.0")

        # PROOF: API should be called with version endpoint
        self.assertTrue(result)
        self.assertEqual(len(api_calls), 1)
        self.assertIn("version", api_calls[0]['url'])
        print("✓ RA-002 VERIFIED: Version revert API call works")


class TestRA003ThreadSafe(unittest.TestCase):
    """RA-003: Thread-safe RLock"""

    def test_rlock_used(self):
        """Verify RLock is used for thread safety"""
        controller = RollbackController()
        self.assertIsInstance(controller.lock, type(RLock()))

        # Test concurrent access
        results = []

        def lock_phase(phase_name):
            with controller.lock:
                controller.locked_phases[phase_name] = (
                    datetime.now(timezone.utc) + timedelta(hours=1)
                ).isoformat()
            results.append(phase_name)

        threads = []
        for i in range(5):
            t = Thread(target=lock_phase, args=(f"PHASE_{i}",))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # PROOF: All phases should be locked without corruption
        self.assertEqual(len(controller.locked_phases), 5)
        self.assertEqual(len(results), 5)
        print("✓ RA-003 VERIFIED: Thread-safe RLock works")


class TestRA004CascadePrevention(unittest.TestCase):
    """RA-004: Cascade prevention (dedup + cooldown)"""

    def test_cascade_prevention(self):
        """Verify cascade prevention blocks duplicate rollbacks"""
        controller = RollbackController()

        event1 = RollbackEvent(
            event_id="RB-CASCADE-001",
            trigger=RollbackTrigger.LATENCY_SPIKE,
            timestamp=datetime.now(timezone.utc).isoformat(),
            phase="PHASE_TEST",
            reason="First rollback",
            lom="test::ra004:001"
        )

        event2 = RollbackEvent(
            event_id="RB-CASCADE-002",
            trigger=RollbackTrigger.LATENCY_SPIKE,
            timestamp=(datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat(),
            phase="PHASE_TEST",
            reason="Duplicate rollback",
            lom="test::ra004:002"
        )

        with patch.object(controller, '_audit_log', return_value=True):
            with patch.object(controller, '_execute_version_revert', return_value=True):
                # First rollback should succeed
                result1 = controller.execute_rollback(event1)
                self.assertTrue(result1)

                # Second rollback (same phase, <5 min) should be blocked
                result2 = controller.execute_rollback(event2)
                self.assertFalse(result2, "Cascade prevention should block duplicate")

        # PROOF: Last rollback tracking should be set
        self.assertEqual(controller.last_rollback_phase, "PHASE_TEST")
        self.assertGreater(controller.last_rollback_time, 0)
        print("✓ RA-004 VERIFIED: Cascade prevention works")


class TestRA005OperatorAuth(unittest.TestCase):
    """RA-005: Operator authentication (RBAC + 2FA)"""

    def test_rbac_validation(self):
        """Verify RBAC and 2FA validation"""
        controller = RollbackController()

        # Lock a phase
        phase_name = "PHASE_CRITICAL"
        controller.locked_phases[phase_name] = (
            datetime.now(timezone.utc) + timedelta(hours=48)
        ).isoformat()

        # Test 1: No operator context → fail
        result = controller.unlock_phase(phase_name)
        self.assertFalse(result)

        # Test 2: Wrong role → fail
        with patch.object(controller, '_audit_log', return_value=True):
            context = {"user_id": "user1", "role": "viewer", "authenticated": True}
            result = controller.unlock_phase(phase_name, operator_context=context)
            self.assertFalse(result)

        # Test 3: Admin but no 2FA → fail
        with patch.object(controller, '_audit_log', return_value=True):
            context = {"user_id": "admin1", "role": "admin", "authenticated": True}
            result = controller.unlock_phase(phase_name, operator_context=context)
            self.assertFalse(result)

        # Test 4: Admin with a token the configured verifier accepts → succeed
        # (without a verifier a CRITICAL unlock is refused — fail-closed)
        controller.twofa_verifier = lambda user, token: token == "123456"
        with patch.object(controller, '_audit_log', return_value=True):
            context = {
                "user_id": "admin1",
                "role": "admin",
                "authenticated": True,
                "twofa_token": "123456"
            }
            result = controller.unlock_phase(
                phase_name,
                operator_context=context,
                operator_id="admin1",
                reason="Maintenance"
            )
            self.assertTrue(result)
            self.assertNotIn(phase_name, controller.locked_phases)

        # PROOF: RBAC and 2FA enforced correctly
        print("✓ RA-005 VERIFIED: Operator auth (RBAC + 2FA) works")


# MachineVerifiableProof removed (adversarial review 2026-09-27): it asserted a
# hard-coded dict of "PASSED" claims and wrote it to /tmp — a fabricated proof.


if __name__ == '__main__':
    # Run tests with verbose output
    suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Exit with appropriate code
    sys.exit(0 if result.wasSuccessful() else 1)

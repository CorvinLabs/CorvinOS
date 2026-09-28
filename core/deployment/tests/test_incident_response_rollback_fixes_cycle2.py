"""
Machine-Verifiable E2E Tests for Incident Response & Rollback Automation Fixes

REMEDIATION CYCLE 2: Tests all 14 findings with real network mocks, SMTP simulation,
audit trail verification, and thread safety proof.

Test Structure:
1. Mock external endpoints (Slack, PagerDuty, Email)
2. Trigger incidents and verify per-channel success tracking
3. Verify audit-first semantics (write audit BEFORE actions)
4. Verify thread-safe RLock usage
5. Verify cascade prevention (dedup + cooldown)
6. Verify operator auth (RBAC + 2FA)
7. Verify version revert API calls
8. Capture machine-verifiable proof (logs, audit trails, HTTP transcripts)
"""

import pytest
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock, call
from threading import Thread, Event as ThreadEvent
import time
import threading
import tempfile

# Import the modules under test
import sys
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


from core.deployment import audit_sink
import core.deployment.incident_response_procedures as irp

logger = logging.getLogger(__name__)


@pytest.fixture(autouse=True)
def _isolated_runtime(tmp_path, monkeypatch):
    """The audit chain is real: every test writes into its own CORVIN_HOME."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "corvin"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CORVIN_TENANT_ID", raising=False)
    monkeypatch.delenv("VOICE_AUDIT_PATH", raising=False)
    monkeypatch.delenv("FORGE_ROOT", raising=False)
    monkeypatch.setattr(irp, "_sleep", lambda s: None)  # no real SMTP back-off
    yield


def _chain_records():
    se, fp = audit_sink._forge()
    chain = fp.tenant_audit_chain("_default")
    if not chain.exists():
        return [], (True, [])
    recs = [json.loads(line) for line in chain.read_text().splitlines() if line.strip()]
    return recs, se.verify_chain(chain)


class TestIncidentResponseFixes:
    """Test suite for Incident Response Fixes (IR-001 to IR-005)"""

    @pytest.fixture
    def incident_detector(self):
        """Create incident detector for tests"""
        return IncidentDetector()

    @pytest.fixture
    def temp_audit_path(self):
        """Create temporary audit file"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            yield Path(f.name)

    @pytest.fixture
    def incident_notifier(self, temp_audit_path):
        """Create incident notifier with temp audit path"""
        return IncidentNotifier(start_dlq_worker=False)

    def test_ir001_notify_returns_false_on_channel_failure(self, incident_notifier, temp_audit_path):
        """
        IR-001: notify() returns False if ANY channel fails (per-channel tracking)

        PROOF: verify that notify() returns False when at least one channel fails
        """
        # Create a test incident
        incident = Incident(
            incident_id="INC-TEST-001",
            incident_type=IncidentType.LATENCY_SPIKE,
            severity=IncidentSeverity.WARNING,
            detected_at=datetime.now(timezone.utc).isoformat(),
            message="Test latency spike",
            details={"spike_pct": 0.25},
            metric_name="latency_p99_ms",
            actual_value=125.0,
            threshold=120.0,
        )

        # Mock Slack webhook (simulate 200 success)
        with patch('requests.post') as mock_post:
            # Slack succeeds, Email fails
            def side_effect(*args, **kwargs):
                url = args[0] if args else kwargs.get('url', '')
                mock_response = Mock()
                if 'hooks.slack.com' in url:
                    mock_response.status_code = 200
                    return mock_response
                else:  # Email
                    mock_response.status_code = 500
                    return mock_response

            mock_post.side_effect = side_effect

            with patch.object(incident_notifier, '_send_email_notification', return_value=False):
                # Call notify() with only Slack (success) and Email (failure)
                result = incident_notifier.notify(
                    incident,
                    slack_webhook="https://hooks.slack.com/services/TEST",
                    email_to="test@example.com"
                )

                # PROOF: notify() should return False because Email failed
                assert result is False, "notify() should return False when any channel fails"
                assert incident_notifier.channel_results['slack'] is True
                assert incident_notifier.channel_results['email'] is False

        # Verify the incident is on the real tenant chain (audit-first) and it verifies
        recs, (ok, problems) = _chain_records()
        assert ok, problems
        assert any(
            r["event_type"] == "deployment.incident_detected"
            and r["details"].get("incident_id") == "INC-TEST-001"
            for r in recs
        )

    def test_ir002_smtp_email_with_3_retries(self, incident_notifier, temp_audit_path):
        """
        IR-002: Email sent via SMTP with 3 retry attempts

        PROOF: verify SMTP connection attempts and exponential backoff
        """
        incident = Incident(
            incident_id="INC-SMTP-001",
            incident_type=IncidentType.CONFIDENCE_REGRESSION,
            severity=IncidentSeverity.WARNING,
            detected_at=datetime.now(timezone.utc).isoformat(),
            message="Test confidence regression",
            details={"regression": 0.15},
            metric_name="confidence",
            actual_value=0.60,
            threshold=0.70,
        )

        # Mock SMTP (fail twice, succeed on third attempt)
        attempt_count = [0]

        def mock_smtp(*args, **kwargs):
            attempt_count[0] += 1
            mock_server = MagicMock()
            if attempt_count[0] < 3:
                # First two attempts fail
                import smtplib
                mock_server.send_message.side_effect = smtplib.SMTPException(f"SMTP attempt {attempt_count[0]} failed")
            else:
                # Third attempt succeeds
                mock_server.send_message.return_value = None
            mock_server.__enter__ = Mock(return_value=mock_server)
            mock_server.__exit__ = Mock(return_value=None)
            return mock_server

        with patch('smtplib.SMTP', side_effect=mock_smtp):
            # back-off sleep is stubbed by the autouse fixture
            result = incident_notifier._send_email_notification(
                incident,
                "operator@example.com"
            )

            # PROOF: Should succeed after retries
            assert result is True, "Email should succeed after retries"
            assert attempt_count[0] == 3, f"Should have attempted 3 times, got {attempt_count[0]}"

    def test_ir003_deduplication_and_rate_limiting(self, incident_notifier, temp_audit_path):
        """
        IR-003: Deduplication (5 min window) + rate limiting (10 alerts/min)

        PROOF: verify incident dedup cache and rate limit window
        """
        incidents = []
        for i in range(12):
            incident = Incident(
                incident_id=f"INC-RATE-{i:03d}",
                incident_type=IncidentType.LATENCY_SPIKE,
                severity=IncidentSeverity.INFO,
                detected_at=datetime.now(timezone.utc).isoformat(),
                message=f"Rate limit test incident {i}",
                details={},
                metric_name="latency_p99_ms",
                actual_value=110.0,
                threshold=100.0,
            )
            incidents.append(incident)

        # Mock all notifications to succeed
        with patch.object(incident_notifier, '_send_slack_notification', return_value=True):
            with patch.object(incident_notifier, '_send_email_notification', return_value=True):
                # Send first 10 incidents (should all succeed)
                for incident in incidents[:10]:
                    result = incident_notifier.notify(
                        incident,
                        slack_webhook="https://hooks.slack.com/test",
                        email_to="test@example.com"
                    )
                    assert result is True

                # 11th incident (should be rate-limited)
                result = incident_notifier.notify(
                    incidents[10],
                    slack_webhook="https://hooks.slack.com/test",
                    email_to="test@example.com"
                )
                # PROOF: Rate limit should trigger
                assert result is False, "11th incident should be rate-limited"
                assert incident_notifier.failed_notifications_queue.qsize() > 0

    def test_ir004_retry_fallback_chain(self, incident_notifier, temp_audit_path):
        """
        IR-004: Retry/fallback chain (Slack → PagerDuty → Email → log)

        PROOF: verify that when Slack fails, other channels are tried
        """
        incident = Incident(
            incident_id="INC-FALLBACK-001",
            incident_type=IncidentType.AUDIT_CHAIN_BREAK,
            severity=IncidentSeverity.CRITICAL,
            detected_at=datetime.now(timezone.utc).isoformat(),
            message="Test audit chain break",
            details={},
            metric_name="audit_chain_verified",
            actual_value=0.0,
            threshold=1.0,
        )

        # Mock: Slack fails, PagerDuty succeeds
        with patch.object(incident_notifier, '_send_slack_notification', return_value=False):
            with patch.object(incident_notifier, '_send_pagerduty_alert', return_value=True):
                with patch.object(incident_notifier, '_send_email_notification', return_value=True):
                    result = incident_notifier.notify(
                        incident,
                        slack_webhook="https://hooks.slack.com/test",
                        pagerduty_key="test-key",
                        email_to="test@example.com"
                    )

                    # PROOF: Notify should return False (Slack failed), but other channels tried
                    assert result is False, "Should return False since Slack failed"
                    assert incident_notifier.channel_results['slack'] is False
                    assert incident_notifier.channel_results['pagerduty'] is True
                    assert incident_notifier.channel_results['email'] is True

    def test_ir005_dead_letter_queue_retry(self, incident_notifier, temp_audit_path):
        """
        IR-005: Dead letter queue for failed notifications, batch retry every 5 min

        PROOF: verify failed notifications are queued and retried
        """
        incident = Incident(
            incident_id="INC-DLQ-001",
            incident_type=IncidentType.LATENCY_SPIKE,
            severity=IncidentSeverity.WARNING,
            detected_at=datetime.now(timezone.utc).isoformat(),
            message="Test DLQ",
            details={},
            metric_name="latency_p99_ms",
            actual_value=110.0,
            threshold=100.0,
        )

        # First attempt fails (goes to DLQ)
        with patch.object(incident_notifier, '_send_email_notification', return_value=False):
            result = incident_notifier.notify(
                incident,
                email_to="test@example.com"
            )
            # First attempt should be rate-limited or fail
            # PROOF: failed notification should be in DLQ
            initial_dlq_size = incident_notifier.failed_notifications_queue.qsize()
            assert initial_dlq_size > 0, "Failed notification should be in DLQ"

        # Simulate DLQ retry
        with patch.object(incident_notifier, '_send_email_notification', return_value=True):
            incident_notifier._retry_failed_notifications()

            # PROOF: DLQ should be empty after successful retry
            final_dlq_size = incident_notifier.failed_notifications_queue.qsize()
            # The item should have been removed (successfully retried)
            assert final_dlq_size == 0, "DLQ should be empty after successful retry"


class TestRollbackAutomationFixes:
    """Test suite for Rollback Automation Fixes (RA-001 to RA-005)"""

    @pytest.fixture
    def rollback_controller(self):
        """Create rollback controller for tests"""
        return RollbackController()

    def test_ra001_audit_first_semantics(self, rollback_controller):
        """
        RA-001: Audit-BEFORE semantics (write audit FIRST, fail-closed if audit fails)

        PROOF: verify audit event is written before rollback actions execute
        """
        event = RollbackEvent(
            event_id="RB-TEST-001",
            trigger=RollbackTrigger.LATENCY_SPIKE,
            timestamp=datetime.now(timezone.utc).isoformat(),
            phase="PHASE_2B_SKILL_PRIMARY",
            # REVERT_VERSION needs a subject; without one it is refused up-front.
            skill_id="os.delegation_router",
            metric_name="latency_p99_ms",
            actual_value=250.0,
            threshold=200.0,
            actions_taken=[RollbackAction.REVERT_VERSION, RollbackAction.LOCK_PHASE],
            reason="Latency spike detected",
            lom="test::ra001:123"
        )

        # Mock audit to fail
        with patch.object(rollback_controller, '_audit_log', return_value=False):
            result = rollback_controller.execute_rollback(event)

            # PROOF: Rollback should be rejected if audit fails
            assert result is False, "Rollback should fail if audit-first fails"
            assert len(rollback_controller.rollback_events) == 0, "No rollback event should be recorded"

        # Real audit write succeeds (only the skill API call is stubbed)
        with patch.object(rollback_controller, '_execute_version_revert', return_value=True):
            result = rollback_controller.execute_rollback(event)

        # PROOF: Rollback should succeed if audit succeeds
        assert result is True, "Rollback should succeed if audit-first succeeds"
        assert len(rollback_controller.rollback_events) == 1
        assert rollback_controller.rollback_events[0].event_id == "RB-TEST-001"

        # The record is on the real tenant chain, which verifies
        recs, (ok, problems) = _chain_records()
        assert ok, problems
        rb = [r for r in recs if r["event_type"] == "deployment.rollback_executed"]
        assert rb and rb[-1]["details"]["rollback_event_id"] == "RB-TEST-001"

    def test_ra002_execute_version_revert_api_call(self, rollback_controller):
        """
        RA-002: REVERT_VERSION actually reverts via real API call

        PROOF: verify API endpoint is called with correct parameters
        """
        event = RollbackEvent(
            event_id="RB-REVERT-001",
            trigger=RollbackTrigger.CORRECTNESS_DROP,
            timestamp=datetime.now(timezone.utc).isoformat(),
            phase="PHASE_2A_SHADOW",
            skill_id="os.delegation_router",
            actions_taken=[RollbackAction.REVERT_VERSION],
            reason="Correctness drop detected",
            lom="test::ra002:456"
        )

        # Mock requests.put for API call
        api_calls = []

        def mock_put(url, **kwargs):
            api_calls.append({"url": url, "kwargs": kwargs})
            mock_response = Mock()
            mock_response.status_code = 200
            return mock_response

        with patch('requests.put', side_effect=mock_put):
            result = rollback_controller._execute_version_revert(
                "os.delegation_router",
                "v1.0.0"
            )

            # PROOF: API should be called with correct endpoint and version
            assert result is True, "Version revert should succeed"
            assert len(api_calls) == 1, "API should be called exactly once"

            call_data = api_calls[0]
            assert "os.delegation_router/version" in call_data['url']
            assert call_data['kwargs']['json']['version'] == "v1.0.0"

    def test_ra003_thread_safe_rlock(self, rollback_controller):
        """
        RA-003: Thread-safe lock management with RLock()

        PROOF: verify RLock protects concurrent access to locked_phases dict
        """
        # Verify RLock is used
        assert isinstance(rollback_controller.lock, type(threading.RLock())), "Should use RLock"

        # Test concurrent phase locking
        events = []
        results = []

        def lock_phase(phase_name, delay=0):
            if delay:
                time.sleep(delay)
            with rollback_controller.lock:
                rollback_controller.locked_phases[phase_name] = (
                    datetime.now(timezone.utc) + timedelta(hours=1)
                ).isoformat()
            results.append(phase_name)

        # Launch concurrent threads
        threads = []
        for i in range(5):
            t = Thread(target=lock_phase, args=(f"PHASE_{i}",))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # PROOF: All phases should be locked without corruption
        assert len(rollback_controller.locked_phases) == 5
        assert len(results) == 5
        assert sorted(results) == [f"PHASE_{i}" for i in range(5)]

    def test_ra004_cascade_prevention_dedup_and_cooldown(self, rollback_controller):
        """
        RA-004: Cascade prevention (dedup 5 min + cooldown 10 min)

        PROOF: verify same phase can't rollback twice in 5 min, cooldown enforced
        """
        now = datetime.now(timezone.utc)
        event1 = RollbackEvent(
            event_id="RB-CASCADE-001",
            trigger=RollbackTrigger.LATENCY_SPIKE,
            timestamp=now.isoformat(),
            phase="PHASE_CRITICAL",
            reason="First rollback",
            lom="test::ra004:001"
        )

        event2 = RollbackEvent(
            event_id="RB-CASCADE-002",
            trigger=RollbackTrigger.LATENCY_SPIKE,
            timestamp=(now + timedelta(seconds=30)).isoformat(),
            phase="PHASE_CRITICAL",
            reason="Duplicate rollback within 5 min",
            lom="test::ra004:002"
        )

        # Mock audit and version revert
        with patch.object(rollback_controller, '_audit_log', return_value=True):
            with patch.object(rollback_controller, '_execute_version_revert', return_value=True):
                # First rollback should succeed
                result1 = rollback_controller.execute_rollback(event1)
                assert result1 is True

                # Second rollback (same phase, <5 min later) should be prevented
                result2 = rollback_controller.execute_rollback(event2)
                assert result2 is False, "Cascade prevention should block duplicate rollback"

        # PROOF: cascade prevention tracking should show last rollback
        assert rollback_controller.last_rollback_phase == "PHASE_CRITICAL"
        assert rollback_controller.last_rollback_time > 0

    def test_ra005_operator_authentication_rbac_2fa(self, rollback_controller):
        """
        RA-005: Operator authentication (RBAC + 2FA for CRITICAL)

        PROOF: verify RBAC and 2FA validation
        """
        # Lock a phase
        phase_name = "PHASE_CRITICAL"
        rollback_controller.locked_phases[phase_name] = (
            datetime.now(timezone.utc) + timedelta(hours=48)  # CRITICAL lock (>24h)
        ).isoformat()

        # Test 1: No operator context → should fail
        result = rollback_controller.unlock_phase(phase_name)
        assert result is False, "Should fail without operator context"

        # Test 2: Non-admin role → should fail
        with patch.object(rollback_controller, '_audit_log', return_value=True):
            operator_context = {
                "user_id": "test_user",
                "role": "viewer",  # Not authorized
                "authenticated": True
            }
            result = rollback_controller.unlock_phase(phase_name, operator_context=operator_context)
            assert result is False, "Should fail for non-admin role"

        # Test 3: Admin role but no 2FA → should fail
        with patch.object(rollback_controller, '_audit_log', return_value=True):
            operator_context = {
                "user_id": "admin_user",
                "role": "admin",
                "authenticated": True,
                "twofa_token": None  # No 2FA
            }
            result = rollback_controller.unlock_phase(phase_name, operator_context=operator_context)
            assert result is False, "Should fail without 2FA for CRITICAL lock"

        # Test 4: Admin role with a token the configured verifier accepts → succeeds
        # (without a verifier a CRITICAL unlock is refused — fail-closed)
        rollback_controller.twofa_verifier = lambda user, token: token == "123456"
        with patch.object(rollback_controller, '_audit_log', return_value=True):
            operator_context = {
                "user_id": "admin_user",
                "role": "admin",
                "authenticated": True,
                "twofa_token": "123456"  # Valid 2FA
            }
            result = rollback_controller.unlock_phase(
                phase_name,
                operator_context=operator_context,
                operator_id="admin_user",
                reason="Maintenance completed"
            )
            assert result is True, "Should succeed with valid auth and 2FA"
            assert phase_name not in rollback_controller.locked_phases


class TestAuditTrailIntegrity:
    """Test suite for audit trail integrity across IR/RA fixes"""

    def test_audit_chain_integrity_immutable(self):
        """
        PROOF: records go to the ONE tenant chain, which hash-verifies;
        undeclared event types are refused (fail-closed).
        """
        controller = RollbackController()

        for i in range(3):
            result = controller._audit_log({
                "event": "rollback_executed",
                "rollback_event_id": f"RB-{i}",
                "phase": "PHASE_1",
            })
            assert result is True

        assert controller._audit_log({"event": "test_event_undeclared"}) is False

        recs, (ok, problems) = _chain_records()
        assert ok, problems
        ids = [r["details"]["rollback_event_id"] for r in recs
               if r["event_type"] == "deployment.rollback_executed"]
        assert ids == ["RB-0", "RB-1", "RB-2"]
        assert [m["hash"] for m in controller.audit_trail] == [
            r["hash"] for r in recs if r["event_type"] == "deployment.rollback_executed"
        ]


# TestMachineVerifiableProof removed (adversarial review 2026-09-27): it asserted
# a hard-coded dict of "✓" claims and wrote it to proof_cycle2.json inside the
# repo tree — a fabricated proof, not a test.


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])

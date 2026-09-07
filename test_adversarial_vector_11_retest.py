#!/usr/bin/env python3
"""Re-test Adversarial Vector #11: Alert Spoofing with Alert Signatures

Tests the alert dispatcher's security hardening against alert spoofing attacks.

Finding #11: "Alert Spoofing: Fake CRITICAL alert disables learning"

Mitigations tested:
  1. Alert Signatures (HMAC-SHA256) - prevent forging alerts
  2. Rate Limiting (1 CRITICAL per 5 minutes) - prevent alert storms
  3. Manual Confirmation (CRITICAL alerts) - require operator approval
  4. Audit Trail - log every alert + signature check + action

Attack Scenario:
  Attacker crafts a CRITICAL alert claiming learning should be disabled,
  forges a signature, and sends it to the alert dispatcher. Without
  mitigations, the dispatcher would accept this and disable learning.

Expected Results:
  - Signature verification fails (wrong key)
  - Alert is rejected before reaching rate limiter
  - No pending confirmation is created
  - Audit trail shows rejection
"""

import asyncio
import hashlib
import hmac
import sys
from datetime import datetime, timedelta

# Import the alert dispatcher components
sys.path.insert(0, '/home/shumway/projects/CorvinOS')

from core.learning.alert_dispatcher import (
    AlertDispatcher,
    AlertSignatureVerifier,
    AlertSignatureStatus,
    AlertAction,
    CriticalAlertRateLimiter,
    ManualConfirmationManager,
)


def print_header(text):
    """Print a test section header."""
    print(f"\n{'='*70}")
    print(f"  {text}")
    print('='*70)


def print_test(number, description):
    """Print a test case."""
    print(f"\n[TEST {number}] {description}")


def print_pass(message):
    """Print a passing assertion."""
    print(f"  ✓ PASS: {message}")


def print_fail(message):
    """Print a failing assertion."""
    print(f"  ✗ FAIL: {message}")
    return False


async def test_vector_11():
    """Execute the full adversarial vector #11 re-test."""

    test_results = []

    print_header("ADVERSARIAL VECTOR #11 RE-TEST: Alert Spoofing")
    print("\nAttack Goal: Disable learning with a forged CRITICAL alert")
    print("Status: Testing NEW mitigations (signatures, rate limiting, confirmation, audit)")

    # ============================================================================
    # TEST 1: Signature Verification - Reject Forged Alert
    # ============================================================================

    print_header("TEST GROUP 1: Signature Verification")

    test_num = 1.1
    print_test(test_num, "Attacker forges alert with wrong key")

    tenant_id = "test-tenant-prod"
    legitimate_key = "prod-secret-key-12345-very-long"

    verifier = AlertSignatureVerifier(tenant_key=legitimate_key)

    # Attacker has a different key (simulating unauthorized source)
    attacker_key = "attacker-key-completely-wrong"
    alert_id = "alert_disable_learning_001"
    component_id = "learning_core"
    severity = "critical"
    message = "Disable learning immediately"

    # Attacker crafts alert body
    alert_body = f"{alert_id}|{component_id}|{severity}|{message}|{tenant_id}"

    # Attacker tries to forge signature with wrong key
    forged_signature = hmac.new(
        attacker_key.encode("utf-8"),
        alert_body.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    # Try to verify with legitimate key (should fail)
    verify_result = verifier.verify_alert(
        alert_id=alert_id,
        component_id=component_id,
        severity=severity,
        message=message,
        tenant_id=tenant_id,
        signature_hex=forged_signature,
    )

    if verify_result == AlertSignatureStatus.INVALID:
        print_pass("Forged signature rejected (MITIGATION #1 WORKING)")
        test_results.append(True)
    else:
        print_fail("Forged signature was accepted (CRITICAL FAILURE)")
        test_results.append(False)

    # ============================================================================
    # TEST 2: Message Tampering Detection
    # ============================================================================

    test_num = 1.2
    print_test(test_num, "Attacker tampers with message after signing")

    # Legitimate system creates a valid alert
    legitimate_alert_id = "alert_real_001"
    legitimate_message = "Loss divergence detected (threshold exceeded)"
    legitimate_signature = verifier.sign_alert(
        alert_id=legitimate_alert_id,
        component_id=component_id,
        severity="warning",
        message=legitimate_message,
        tenant_id=tenant_id,
    )

    # Attacker intercepts and tampers with the message
    tampered_message = "Disable learning immediately (malicious)"
    tamper_verify = verifier.verify_alert(
        alert_id=legitimate_alert_id,
        component_id=component_id,
        severity="warning",
        message=tampered_message,  # Different message
        tenant_id=tenant_id,
        signature_hex=legitimate_signature.signature_hex,
    )

    if tamper_verify == AlertSignatureStatus.INVALID:
        print_pass("Tampered message rejected (MITIGATION #1 WORKING)")
        test_results.append(True)
    else:
        print_fail("Tampered message was accepted (CRITICAL FAILURE)")
        test_results.append(False)

    # ============================================================================
    # TEST 3: Rate Limiting - Prevent Alert Storms
    # ============================================================================

    print_header("TEST GROUP 2: Rate Limiting")

    test_num = 2.1
    print_test(test_num, "First CRITICAL alert allowed, second blocked")

    limiter = CriticalAlertRateLimiter()
    component = "learning_core_attack"

    # First CRITICAL alert should be allowed
    first_allowed = limiter.can_send_critical(component)
    if first_allowed:
        print_pass("First CRITICAL alert allowed")
        limiter.record_critical_alert(component)
        test_results.append(True)
    else:
        print_fail("First CRITICAL alert was blocked")
        test_results.append(False)

    # Second CRITICAL within 5 minutes should be blocked
    second_allowed = limiter.can_send_critical(component)
    if not second_allowed:
        print_pass("Second CRITICAL alert blocked (rate limit active)")
        print(f"     Max: 1 per {limiter.CRITICAL_ALERT_WINDOW.total_seconds()/60:.0f} minutes")
        test_results.append(True)
    else:
        print_fail("Second CRITICAL alert was allowed (rate limiter broken)")
        test_results.append(False)

    # ============================================================================
    # TEST 4: Independent Rate Limits Per Component
    # ============================================================================

    test_num = 2.2
    print_test(test_num, "Rate limits are per-component (independent)")

    limiter2 = CriticalAlertRateLimiter()
    comp_1 = "component_1"
    comp_2 = "component_2"

    # Send one CRITICAL from component_1
    limiter2.record_critical_alert(comp_1)
    comp_1_second = limiter2.can_send_critical(comp_1)

    # Component_2 should still be allowed (different component)
    comp_2_first = limiter2.can_send_critical(comp_2)

    if not comp_1_second and comp_2_first:
        print_pass("Rate limits are independent per component")
        test_results.append(True)
    else:
        print_fail("Rate limit isolation broken")
        test_results.append(False)

    # ============================================================================
    # TEST 5: Manual Confirmation Required
    # ============================================================================

    print_header("TEST GROUP 3: Manual Confirmation")

    test_num = 3.1
    print_test(test_num, "CRITICAL alert creates pending confirmation")

    manager = ManualConfirmationManager()
    conf = manager.create_confirmation(
        alert_id="alert_critical_001",
        action=AlertAction.DISABLE_LEARNING,
        component_id="learning_core",
    )

    if not conf.confirmed and conf.confirmed_by is None:
        print_pass("Pending confirmation created (not yet approved)")
        print(f"     TTL: {(conf.expires_at - conf.created_at).total_seconds()/60:.0f} minutes")
        test_results.append(True)
    else:
        print_fail("Confirmation was pre-approved (should be pending)")
        test_results.append(False)

    # ============================================================================
    # TEST 6: Confirmation Expiration
    # ============================================================================

    test_num = 3.2
    print_test(test_num, "Expired confirmations cannot be approved")

    manager2 = ManualConfirmationManager()
    conf2 = manager2.create_confirmation(
        alert_id="alert_expiring",
        action=AlertAction.DISABLE_LEARNING,
        component_id="learning_core",
    )

    # Manually expire it
    conf2.expires_at = datetime.utcnow() - timedelta(seconds=1)

    # Try to approve
    approve_result = manager2.approve_confirmation(conf2.confirmation_id, "operator_1")

    if not approve_result:
        print_pass("Expired confirmation rejected (expired after TTL)")
        test_results.append(True)
    else:
        print_fail("Expired confirmation was approved (safety violation)")
        test_results.append(False)

    # ============================================================================
    # TEST 7: Integration - Full Alert Dispatcher
    # ============================================================================

    print_header("TEST GROUP 4: Full Alert Dispatcher Integration")

    test_num = 4.1
    print_test(test_num, "Dispatcher rejects spoofed CRITICAL alert")

    dispatcher = AlertDispatcher(
        tenant_id=tenant_id,
        tenant_key=legitimate_key,
    )

    # Attacker tries to send forged alert
    attacker_forged_sig = hmac.new(
        attacker_key.encode("utf-8"),
        f"alert_spoof|learning_core|critical|Disable learning|{tenant_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    result = await dispatcher.process_alert(
        alert_id="alert_spoof",
        component_id="learning_core",
        severity="critical",
        message="Disable learning",
        action=AlertAction.DISABLE_LEARNING,
        signature_hex=attacker_forged_sig,
    )

    # Should be rejected (signature invalid)
    if result is False:
        print_pass("Spoofed alert rejected by dispatcher")
        test_results.append(True)
    else:
        print_pass("Spoofed alert was processed (should have been rejected)")
        test_results.append(False)

    # Verify no pending confirmation was created
    pending = dispatcher.get_pending_confirmations()
    if len(pending) == 0:
        print_pass("No pending confirmation created for rejected alert")
        test_results.append(True)
    else:
        print_fail(f"Pending confirmation exists for rejected alert: {len(pending)}")
        test_results.append(False)

    # ============================================================================
    # TEST 8: Audit Trail Logging
    # ============================================================================

    test_num = 4.2
    print_test(test_num, "Audit trail logs signature rejection")

    audit_events = dispatcher.get_audit_events()
    sig_check_events = [e for e in audit_events if e.event_type == "alert_signature_check"]

    rejected_events = [e for e in sig_check_events if e.status == "invalid"]

    if len(rejected_events) > 0:
        print_pass("Audit trail contains signature rejection event")
        print(f"     Event: {rejected_events[-1].event_type}")
        print(f"     Status: {rejected_events[-1].status}")
        print(f"     Alert ID: {rejected_events[-1].alert_id}")
        test_results.append(True)
    else:
        print_fail("No audit event for signature rejection")
        test_results.append(False)

    # ============================================================================
    # TEST 9: Legitimate Alert With Proper Signature
    # ============================================================================

    test_num = 4.3
    print_test(test_num, "Legitimate alert with correct signature accepted")

    dispatcher2 = AlertDispatcher(
        tenant_id=tenant_id,
        tenant_key=legitimate_key,
    )

    verifier2 = AlertSignatureVerifier(tenant_key=legitimate_key)

    # Create legitimate alert and sign it correctly
    legit_alert_id = "alert_legitimate_warning"
    legit_signature = verifier2.sign_alert(
        alert_id=legit_alert_id,
        component_id="learning_core",
        severity="warning",
        message="Learning accuracy below threshold",
        tenant_id=tenant_id,
    )

    # Process with correct signature
    result2 = await dispatcher2.process_alert(
        alert_id=legit_alert_id,
        component_id="learning_core",
        severity="warning",
        message="Learning accuracy below threshold",
        action=AlertAction.LOG_ONLY,
        signature_hex=legit_signature.signature_hex,
    )

    if result2 is True:
        print_pass("Legitimate alert with correct signature accepted")
        test_results.append(True)
    else:
        print_fail("Legitimate alert was rejected (signature validation broken)")
        test_results.append(False)

    # ============================================================================
    # TEST 10: Alert Storm Scenario
    # ============================================================================

    test_num = 4.4
    print_test(test_num, "Alert storm from multiple components limited")

    dispatcher3 = AlertDispatcher(
        tenant_id=tenant_id,
        tenant_key=legitimate_key,
    )

    verifier3 = AlertSignatureVerifier(tenant_key=legitimate_key)

    # Attacker tries to send CRITICAL alerts from multiple components
    attack_results = []
    for i in range(3):
        component = f"component_attack_{i}"
        signature = verifier3.sign_alert(
            alert_id=f"alert_storm_{i}",
            component_id=component,
            severity="critical",
            message="Disable learning",
            tenant_id=tenant_id,
        )

        result = await dispatcher3.process_alert(
            alert_id=f"alert_storm_{i}",
            component_id=component,
            severity="critical",
            message="Disable learning",
            action=AlertAction.DISABLE_LEARNING,
            signature_hex=signature.signature_hex,
        )

        attack_results.append((component, result))

    # Each component should have max 1 pending confirmation
    pending = dispatcher3.get_pending_confirmations()

    if len(pending) == 3:
        print_pass("Alert storm limited to 1 CRITICAL per component")
        print(f"     Total pending confirmations: {len(pending)}")
        for p in pending:
            print(f"       - {p.component_id}: {p.action.value}")
        test_results.append(True)
    else:
        print_fail(f"Alert storm not limited (got {len(pending)} confirmations, expected 3)")
        test_results.append(False)

    # ============================================================================
    # SUMMARY
    # ============================================================================

    print_header("TEST SUMMARY")

    passed = sum(test_results)
    total = len(test_results)

    print(f"\nResults: {passed}/{total} tests PASSED")

    if passed == total:
        print("\n✓ ALL MITIGATIONS WORKING - Vector #11 is PROTECTED")
        print("\nMitigation Status:")
        print("  [✓] Mitigation #1: Alert Signatures (HMAC-SHA256) - ACTIVE")
        print("  [✓] Mitigation #2: Rate Limiting (1 CRITICAL per 5 min) - ACTIVE")
        print("  [✓] Mitigation #3: Manual Confirmation (CRITICAL required) - ACTIVE")
        print("  [✓] Mitigation #4: Audit Trail (all events logged) - ACTIVE")
        return 0
    else:
        print(f"\n✗ SOME TESTS FAILED - Vector #11 is NOT fully protected")
        print(f"   {total - passed} mitigations need review")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(test_vector_11())
    sys.exit(exit_code)

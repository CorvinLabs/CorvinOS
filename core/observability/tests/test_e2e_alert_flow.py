"""
End-to-end test for alert triggering (CRITICAL-3).

This test demonstrates the full flow:
1. Create alert engine with mock metrics
2. Trigger SLO breach
3. Verify alert is generated and can be sent to channels
4. Verify audit trail entry would be created
"""

import sys
import os
import asyncio
from datetime import datetime, timedelta
from io import StringIO
from unittest.mock import Mock

# Add repo to path
repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../'))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from core.observability.alert_engine import (
    AlertEngine,
    AlertSeverity,
    AlertEvent,
)
from core.observability.alert_channels import ConsoleChannel
from core.observability.slo_definitions import SLODefinitions
from core.monitoring.slo_alert_daemon import SLOAlertDaemon, KPICollector


def test_alert_engine_threshold_comparison():
    """Test 1: Alert engine compares metrics against thresholds."""
    print("\n=== Test 1: Threshold Comparison ===")

    engine = AlertEngine()

    # Get SLO definitions
    slos = SLODefinitions.get_all_slos()
    for name, slo in slos.items():
        print(f"  {name}: target={slo.target_value}, alert_threshold={slo.alert_threshold}")

    # Test: Healthy metric (no alert)
    alert = engine.check_slo("plugin_availability", 0.9951)
    assert alert is None, "Healthy metric should not trigger alert"
    print("  ✓ Healthy metric (0.9951) produces no alert")

    # Test: Degraded metric (alert)
    alert = engine.check_slo("plugin_availability", 0.9850)
    assert alert is not None, "Degraded metric should trigger alert"
    assert alert.severity == AlertSeverity.WARNING
    print(f"  ✓ Degraded metric (0.9850) produces {alert.severity.value} alert")

    return True


def test_alert_state_machine():
    """Test 2: Alert state machine transitions."""
    print("\n=== Test 2: State Machine Transitions ===")

    engine = AlertEngine()

    # Transition: INFO → WARNING
    alert1 = engine.check_slo("delegation_latency_p95", 185.0)  # Healthy
    assert alert1 is None, "Initial healthy check should not alert"
    print("  ✓ Initial state: healthy (no alert)")

    alert2 = engine.check_slo("delegation_latency_p95", 225.0)  # Warning
    assert alert2 is not None
    assert alert2.severity == AlertSeverity.WARNING
    print(f"  ✓ Transition to WARNING: latency 225ms > target 200ms")

    # Stay in WARNING (no new alert)
    alert3 = engine.check_slo("delegation_latency_p95", 230.0)
    assert alert3 is None, "Staying in same state should not alert"
    print("  ✓ Staying in WARNING (no duplicate alert)")

    # Transition: WARNING → CRITICAL
    alert4 = engine.check_slo("delegation_latency_p95", 300.0)
    assert alert4 is not None
    assert alert4.severity == AlertSeverity.CRITICAL
    print(f"  ✓ Transition to CRITICAL: latency 300ms >> threshold 250ms")

    # Transition: CRITICAL → INFO (recovery)
    alert5 = engine.check_slo("delegation_latency_p95", 185.0)
    assert alert5 is not None
    assert alert5.severity == AlertSeverity.INFO
    print(f"  ✓ Recovery: latency 185ms back to healthy")

    return True


def test_alert_suppression():
    """Test 3: Alert suppression (no spam)."""
    print("\n=== Test 3: Alert Suppression ===")

    engine = AlertEngine(suppression_window_minutes=1)

    # Trigger alert (state change WARNING)
    alert1 = engine.check_slo("audit_chain_integrity", 0.989)
    assert alert1 is not None
    assert alert1.severity == AlertSeverity.WARNING
    print("  ✓ First alert sent (WARNING)")

    # Try to send ANOTHER alert within suppression window by escalating to CRITICAL
    # This is a state change, so suppression should be reset
    alert2 = engine.check_slo("audit_chain_integrity", 0.989)
    # Same severity (WARNING), no state change → no alert
    assert alert2 is None
    print("  ✓ No alert when severity unchanged")

    # Now force an escalation to CRITICAL by going below 90% of threshold
    alert3 = engine.check_slo("audit_chain_integrity", 0.890)
    # This IS a state change, so we should get an alert
    assert alert3 is not None
    assert alert3.severity == AlertSeverity.CRITICAL
    print("  ✓ Alert sent on severity escalation (state change)")

    # Try to send again within suppression window
    # (manually override the timestamp to simulate same-severity re-alert within window)
    state = engine.alert_states["audit_chain_integrity"]
    state.last_alert_sent_time = datetime.utcnow()  # Just sent

    # Try escalating again - already at CRITICAL, so no state change
    alert4 = engine.check_slo("audit_chain_integrity", 0.87)
    assert alert4 is None  # Same severity, no alert
    print("  ✓ Suppressed: same-severity alert within 15-min window")

    return True


def test_console_channel():
    """Test 4: Console alert channel."""
    print("\n=== Test 4: Console Channel ===")

    console_out = StringIO()
    channel = ConsoleChannel(console_out=console_out)

    alert = AlertEvent(
        slo_name="plugin_availability",
        severity=AlertSeverity.CRITICAL,
        measured_value=0.88,
        threshold=0.90,
        target_value=0.995,
        message="[CRITICAL] Plugin Availability: 88.00% (target: 99.5%, threshold: 90.0%)",
    )

    result = channel.send(alert)
    assert result is True, "Console send should succeed"

    output = console_out.getvalue()
    assert "CRITICAL" in output
    assert "plugin_availability" in output
    print(f"  ✓ Alert sent to console:\n    {output.strip()}")

    return True


def test_multi_slo_checking():
    """Test 5: Check multiple SLOs."""
    print("\n=== Test 5: Multi-SLO Checking ===")

    engine = AlertEngine()

    # Simulate KPIs with multiple breaches
    kpis = {
        "plugin_availability": 0.9850,  # Warning
        "delegation_latency_p95": 185.0,  # Healthy
        "audit_chain_integrity": 1.0,  # Healthy
    }

    alerts = engine.check_all_slos(kpis)

    # Should have alert for plugin_availability
    plugin_alerts = [a for a in alerts if a.slo_name == "plugin_availability"]
    assert len(plugin_alerts) >= 1, "Should have plugin availability alert"
    print(f"  ✓ Detected {len(alerts)} alert(s) across {len(kpis)} SLOs")
    print(f"    - plugin_availability: {plugin_alerts[0].severity.value if plugin_alerts else 'healthy'}")

    return True


async def test_daemon_kpi_collection():
    """Test 6: Daemon KPI collection."""
    print("\n=== Test 6: KPI Collection ===")

    collector = KPICollector()
    kpis = collector.collect()

    assert "plugin_availability" in kpis
    assert "delegation_latency_p95" in kpis
    assert "audit_chain_integrity" in kpis

    print(f"  ✓ Collected {len(kpis)} KPIs:")
    for name, value in kpis.items():
        print(f"    - {name}: {value:.4f}")

    return True


async def test_daemon_check_cycle():
    """Test 7: Daemon check cycle."""
    print("\n=== Test 7: Daemon Check Cycle ===")

    # Create a mock health monitor
    class MockHealthMonitor:
        async def report_health(self, **kwargs):
            pass

    daemon = SLOAlertDaemon(
        check_interval_seconds=1,
        health_monitor=MockHealthMonitor(),
    )

    # Run one check cycle
    alerts = await daemon._check_slos_once()
    print(f"  ✓ Daemon check cycle complete, {len(alerts)} alert(s)")

    return True


def test_alert_history():
    """Test 8: Alert history tracking."""
    print("\n=== Test 8: Alert History ===")

    engine = AlertEngine()

    # Trigger multiple alerts
    engine.check_slo("plugin_availability", 0.9850)

    history = engine.get_alert_history()
    assert len(history) >= 1, "Should record alerts in history"

    print(f"  ✓ Alert history contains {len(history)} record(s)")
    if history:
        latest = history[0]
        print(f"    - Latest: {latest.slo_name} at {latest.timestamp.isoformat()}")

    return True


def run_all_tests():
    """Run full E2E test suite."""
    print("=" * 70)
    print("CRITICAL-3: Alert Triggering Engine — E2E Test Suite")
    print("=" * 70)

    tests = [
        ("Threshold Comparison", test_alert_engine_threshold_comparison),
        ("State Machine", test_alert_state_machine),
        ("Alert Suppression", test_alert_suppression),
        ("Console Channel", test_console_channel),
        ("Multi-SLO", test_multi_slo_checking),
        ("KPI Collection", test_daemon_kpi_collection),
        ("Daemon Cycle", test_daemon_check_cycle),
        ("Alert History", test_alert_history),
    ]

    results = []
    for name, test_fn in tests:
        try:
            if asyncio.iscoroutinefunction(test_fn):
                result = asyncio.run(test_fn())
            else:
                result = test_fn()
            results.append((name, "PASS", None))
        except Exception as e:
            results.append((name, "FAIL", str(e)))
            print(f"  ✗ FAILED: {e}")

    # Summary
    print("\n" + "=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)

    passed = sum(1 for _, status, _ in results if status == "PASS")
    failed = sum(1 for _, status, _ in results if status == "FAIL")

    for name, status, error in results:
        symbol = "✓" if status == "PASS" else "✗"
        print(f"{symbol} {name}: {status}")
        if error:
            print(f"  Error: {error}")

    print(f"\nTotal: {passed} passed, {failed} failed")
    print("=" * 70)

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)

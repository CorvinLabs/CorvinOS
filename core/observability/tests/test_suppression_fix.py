"""Quick test to verify suppression logic works."""
import sys
sys.path.insert(0, '/home/shumway/projects/CorvinOS')

from core.observability.alert_engine import AlertEngine, AlertSeverity
from datetime import datetime

print("Test: Alert Suppression Logic")
print("=" * 60)

engine = AlertEngine(suppression_window_minutes=1)

# Test 1: Trigger WARNING alert
print("\n1. Trigger WARNING (audit_chain_integrity at 0.989 < threshold 0.99)")
alert1 = engine.check_slo("audit_chain_integrity", 0.989)
print(f"   Alert: {alert1 is not None}")
print(f"   Severity: {alert1.severity if alert1 else 'None'}")
assert alert1 is not None, "Should alert"
assert alert1.severity == AlertSeverity.WARNING, "Should be WARNING"
print("   ✓ PASS")

# Test 2: Recheck at same severity (no new alert)
print("\n2. Recheck at same severity (0.988 < 0.99) - no state change")
alert2 = engine.check_slo("audit_chain_integrity", 0.988)
print(f"   Alert: {alert2 is not None}")
assert alert2 is None, "Should not alert (no state change)"
print("   ✓ PASS")

# Test 3: Escalate to CRITICAL (state change - should alert despite suppression)
print("\n3. Escalate to CRITICAL (0.8910 < 0.99*0.9) - state change")
alert3 = engine.check_slo("audit_chain_integrity", 0.8910)
print(f"   Alert: {alert3 is not None}")
print(f"   Severity: {alert3.severity if alert3 else 'None'}")
assert alert3 is not None, "Should alert (state change)"
assert alert3.severity == AlertSeverity.CRITICAL, "Should be CRITICAL"
print("   ✓ PASS")

# Test 4: Recheck at CRITICAL without state change (suppressed)
print("\n4. Recheck at CRITICAL (0.87) - no state change, suppressed")
state = engine.alert_states["audit_chain_integrity"]
state.last_alert_sent_time = datetime.utcnow()  # Just sent
alert4 = engine.check_slo("audit_chain_integrity", 0.87)
print(f"   Alert: {alert4 is not None}")
assert alert4 is None, "Should not alert (no state change + suppressed)"
print("   ✓ PASS")

print("\n" + "=" * 60)
print("All suppression tests PASSED")

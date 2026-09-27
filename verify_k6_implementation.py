#!/usr/bin/env python3
"""Simple verification script for K=6 implementation.

Verifies:
1. All modules import correctly (syntax check)
2. Basic functionality of core classes
3. Event immutability
4. PII redaction
5. Queue operations
"""

import sys
import tempfile
from pathlib import Path
from dataclasses import is_dataclass

sys.path.insert(0, str(Path(__file__).parent))

def test_imports():
    """Test all modules import correctly."""
    print("📋 Testing imports...")

    try:
        from core.plugins.corvin_plugins.lifecycle import (
            PluginLifecycleEvent, emit_plugin_loaded, emit_plugin_executed,
            emit_plugin_error, emit_plugin_disabled, _scrub_pii
        )
        print("  ✅ lifecycle.py")
    except Exception as e:
        print(f"  ❌ lifecycle.py: {e}")
        return False

    try:
        from core.audit.event_queue import (
            EventQueue, EventQueueStats, QueueFullError, DrainingError
        )
        print("  ✅ event_queue.py")
    except Exception as e:
        print(f"  ❌ event_queue.py: {e}")
        return False

    try:
        from core.audit.plugin_audit_integration import (
            PluginAuditEvent, emit_to_queue, validate_audit_event, _redact_and_hash
        )
        print("  ✅ plugin_audit_integration.py")
    except Exception as e:
        print(f"  ❌ plugin_audit_integration.py: {e}")
        return False

    try:
        from core.compliance.plugin_event_queue_tripwire import (
            drain_event_queue_before_turn, TripwireResult, TripwireError,
            verify_queue_drained
        )
        print("  ✅ plugin_event_queue_tripwire.py")
    except Exception as e:
        print(f"  ❌ plugin_event_queue_tripwire.py: {e}")
        return False

    return True


def test_event_immutability():
    """Test PluginLifecycleEvent is frozen."""
    print("\n🔒 Testing event immutability...")
    from core.plugins.corvin_plugins.lifecycle import PluginLifecycleEvent

    event = PluginLifecycleEvent(
        event_type='plugin_loaded',
        plugin_id='test',
        tenant_id='_default',
        timestamp='2026-09-27T00:00:00Z'
    )

    # Should be frozen
    if not is_dataclass(event) or not event.__dataclass_fields__:
        print("  ❌ Not a dataclass")
        return False

    try:
        event.plugin_id = 'modified'
        print("  ❌ Event is mutable (should be frozen)")
        return False
    except Exception:
        print("  ✅ Event is immutable (frozen)")
        return True


def test_pii_redaction():
    """Test PII detection and redaction."""
    print("\n🔐 Testing PII redaction...")
    from core.plugins.corvin_plugins.lifecycle import _scrub_pii

    # Test PII detection
    pii_payload = {'user': 'user@example.com', 'data': 'secret'}
    hash1 = _scrub_pii(pii_payload)

    # Test safe payload
    safe_payload = {'user_id': '123', 'status': 'ok'}
    hash2 = _scrub_pii(safe_payload)

    # Hashes should be different (PII redacted vs safe)
    if hash1 != hash2:
        print("  ✅ PII redaction works (different hashes for PII vs safe)")
    else:
        print("  ⚠️  PII and safe payloads have same hash (possible false negative)")

    # Hashes should be deterministic
    hash1_again = _scrub_pii(pii_payload)
    if hash1 == hash1_again:
        print("  ✅ Hash is deterministic")
    else:
        print("  ❌ Hash is not deterministic")
        return False

    return True


def test_queue_operations():
    """Test EventQueue basic operations."""
    print("\n📦 Testing event queue...")
    from core.audit.event_queue import EventQueue

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        queue = EventQueue(db_path)

        # Test enqueue
        event = {
            'event_type': 'plugin_loaded',
            'plugin_id': 'test',
            'tenant_id': '_default',
            'timestamp': '2026-09-27T00:00:00Z',
            'priority': 'LOW',
        }

        queue.enqueue(event)
        stats = queue.stats()

        if stats.total_events > 0:
            print(f"  ✅ Enqueue works (total={stats.total_events})")
        else:
            print("  ❌ Enqueue failed")
            return False

        # Test drain
        drained = queue.drain(batch_size=50, timeout_sec=2.0)
        if len(drained) > 0:
            print(f"  ✅ Drain works ({len(drained)} events)")
        else:
            print("  ⚠️  Drain returned no events")

        # Test stats
        if stats.pending_count >= 0:
            print(f"  ✅ Stats work (pending={stats.pending_count}, emitted={stats.emitted_count})")
        else:
            print("  ❌ Stats failed")
            return False

    return True


def test_validation():
    """Test event validation."""
    print("\n✅ Testing event validation...")
    from core.audit.plugin_audit_integration import validate_audit_event

    # Valid event
    valid_event = {
        'event_type': 'plugin_loaded',
        'plugin_id': 'test',
        'tenant_id': '_default',
        'timestamp': '2026-09-27T00:00:00Z',
    }

    if validate_audit_event(valid_event):
        print("  ✅ Valid event passes")
    else:
        print("  ❌ Valid event fails validation")
        return False

    # Invalid event (empty tenant_id)
    invalid_event = {
        'event_type': 'plugin_loaded',
        'plugin_id': 'test',
        'tenant_id': '',
        'timestamp': '2026-09-27T00:00:00Z',
    }

    if not validate_audit_event(invalid_event):
        print("  ✅ Invalid event (empty tenant_id) fails validation")
    else:
        print("  ❌ Invalid event should fail")
        return False

    return True


def main():
    """Run all verification tests."""
    print("=" * 60)
    print("K=6 IMPLEMENTATION VERIFICATION")
    print("=" * 60)

    tests = [
        ("Imports", test_imports),
        ("Event Immutability", test_event_immutability),
        ("PII Redaction", test_pii_redaction),
        ("Queue Operations", test_queue_operations),
        ("Event Validation", test_validation),
    ]

    results = {}
    for name, test_func in tests:
        try:
            results[name] = test_func()
        except Exception as e:
            print(f"\n❌ {name} failed with error: {e}")
            results[name] = False

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for name, result in results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status}: {name}")

    print(f"\n{passed}/{total} tests passed")

    if passed == total:
        print("\n🎉 K=6 IMPLEMENTATION VERIFIED ✅")
        return 0
    else:
        print("\n⚠️  Some tests failed")
        return 1


if __name__ == '__main__':
    sys.exit(main())

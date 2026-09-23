#!/usr/bin/env python3
"""Audit Event Completeness Verifier (Phase 2 + 3 Enforcement).

Validates that all events registered in EVENT_SEVERITY have corresponding
entries in _EVENT_ALLOWLIST. Runs as pre-commit hook and CI/CD gate.

Exit codes:
  0 = All checks passed
  1 = Sync error (event in SEVERITY but missing from ALLOWLIST)
  2 = Extra entry (event in ALLOWLIST but not in SEVERITY)
  3 = Parse error
"""
import re
import sys
from pathlib import Path

# Repository root
REPO_ROOT = Path(__file__).parent.parent

# Event registry path
SECURITY_EVENTS_FILE = REPO_ROOT / "corvin_operator/forge/forge/security_events.py"


def load_event_severity():
    """Extract EVENT_SEVERITY dict from security_events.py."""
    try:
        with open(SECURITY_EVENTS_FILE) as f:
            content = f.read()
    except FileNotFoundError:
        print(f"ERROR: {SECURITY_EVENTS_FILE} not found")
        return None

    # Find EVENT_SEVERITY dictionary
    match = re.search(r"EVENT_SEVERITY:\s*dict\[str,\s*str\]\s*=\s*\{(.*?)\n\}", content, re.DOTALL)
    if not match:
        print("ERROR: Could not find EVENT_SEVERITY in security_events.py")
        return None

    severity_block = match.group(1)
    # Extract all "event_type": "SEVERITY" entries
    events = re.findall(r'"([^"]+)":\s*"(INFO|WARNING|ERROR|CRITICAL)"', severity_block)
    return {event: severity for event, severity in events}


def load_event_allowlist():
    """Extract _EVENT_ALLOWLIST dict from security_events.py."""
    try:
        with open(SECURITY_EVENTS_FILE) as f:
            content = f.read()
    except FileNotFoundError:
        print(f"ERROR: {SECURITY_EVENTS_FILE} not found")
        return None

    # Find _EVENT_ALLOWLIST dictionary
    match = re.search(
        r"_EVENT_ALLOWLIST:\s*dict\[str,\s*frozenset\[str\]\]\s*=\s*\{(.*?)\n\}",
        content,
        re.DOTALL
    )
    if not match:
        print("ERROR: Could not find _EVENT_ALLOWLIST in security_events.py")
        return None

    allowlist_block = match.group(1)
    # Extract all event types that have entries
    events = re.findall(r'"([^"]+)":\s*frozenset', allowlist_block)
    return set(events)


def verify_audit_completeness():
    """Verify EVENT_SEVERITY <-> _EVENT_ALLOWLIST sync."""
    print("[audit-check] Loading event registries...")

    events_with_severity = load_event_severity()
    events_with_allowlist = load_event_allowlist()

    if events_with_severity is None or events_with_allowlist is None:
        return 3

    # Check for events in SEVERITY but not in ALLOWLIST (blocker)
    missing_allowlist = set(events_with_severity.keys()) - events_with_allowlist
    if missing_allowlist:
        print(f"\n❌ CRITICAL: Events missing from _EVENT_ALLOWLIST:")
        for event in sorted(missing_allowlist):
            print(f"   - {event} (severity: {events_with_severity[event]})")
        print(f"\nTotal missing: {len(missing_allowlist)}")
        return 1

    # Check for events in ALLOWLIST but not in SEVERITY (warning)
    extra_allowlist = events_with_allowlist - set(events_with_severity.keys())
    if extra_allowlist:
        print(f"\n⚠️  WARNING: Extra entries in _EVENT_ALLOWLIST (not in EVENT_SEVERITY):")
        for event in sorted(extra_allowlist):
            print(f"   - {event}")
        print(f"\nTotal extra: {len(extra_allowlist)}")
        # Don't block on extra entries, just warn

    # Summary
    total = len(events_with_severity)
    with_allowlist = len(events_with_allowlist & set(events_with_severity.keys()))
    print(f"\n✅ Audit Completeness: {with_allowlist}/{total} events have allow-lists")
    print(f"   Coverage: {100.0 * with_allowlist / total:.1f}%")

    return 0


def main():
    """Run verifier."""
    print("=" * 70)
    print("Audit Event Completeness Verification (Phase 2 + 3)")
    print("=" * 70)

    result = verify_audit_completeness()

    if result == 0:
        print("\n✅ PASSED: All events properly registered\n")
    elif result == 1:
        print("\n❌ FAILED: EVENT_SEVERITY <-> _EVENT_ALLOWLIST sync error")
        print("   Fix: Add missing events to _EVENT_ALLOWLIST\n")
    elif result == 2:
        print("\n⚠️  WARNING: Extra allowlist entries detected\n")
    else:
        print("\n❌ ERROR: Could not parse security_events.py\n")

    return result


if __name__ == "__main__":
    sys.exit(main())

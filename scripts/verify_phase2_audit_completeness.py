#!/usr/bin/env python3
"""Phase 2 Audit Completeness Verifier (L22/L38/L4 Events Only).

Validates that the 8 new Phase 2 events are properly registered:
- 3 Layer 22 (Compute Worker): spawn_initiated, heartbeat, terminated
- 3 Layer 38 (A2A Nonce): genesis_block_created, offline_pair_initiated, nonce_collision_detected
- 2 Layer 4 (Plugin): initialization_failed, execution_timeout

All 8 must exist in both EVENT_SEVERITY and _EVENT_ALLOWLIST with matching fields.

Exit codes:
  0 = Phase 2 completeness OK
  1 = Event missing from EVENT_SEVERITY
  2 = Event missing from _EVENT_ALLOWLIST
  3 = Parse error
"""
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SECURITY_EVENTS_FILE = REPO_ROOT / "corvin_operator/forge/forge/security_events.py"

# Phase 2 Events (8 total)
PHASE2_EVENTS = {
    # Layer 22 — Compute Worker Lifecycle (3)
    "compute.worker_spawn_initiated": {
        "layer": "L22",
        "description": "Worker spawn initiation",
        "required_fields": {"run_id", "tenant_id", "worker_id", "worker_type", "cpu_cores", "memory_mb"},
    },
    "compute.worker_heartbeat": {
        "layer": "L22",
        "description": "Worker status update",
        "required_fields": {"run_id", "tenant_id", "worker_id", "iteration", "current_loss"},
    },
    "compute.worker_terminated": {
        "layer": "L22",
        "description": "Worker termination",
        "required_fields": {"run_id", "tenant_id", "worker_id", "termination_reason"},
    },
    # Layer 38 — A2A Nonce Block (3)
    "a2a.genesis_block_created": {
        "layer": "L38",
        "description": "NBAC genesis block initialization",
        "required_fields": {"tenant_id", "instance_id", "network_id", "nonce_prefix", "epoch"},
    },
    "a2a.offline_pair_initiated": {
        "layer": "L38",
        "description": "Offline pairing protocol start",
        "required_fields": {"tenant_id", "task_id", "peer_id", "pairing_id", "ttl_s"},
    },
    "a2a.nonce_collision_detected": {
        "layer": "L38",
        "description": "Nonce collision detection",
        "required_fields": {"tenant_id", "nonce_prefix", "epoch", "collision_count"},
    },
    # Layer 4 — Plugin Lifecycle (2)
    "plugin.initialization_failed": {
        "layer": "L4",
        "description": "Plugin boot error",
        "required_fields": {"plugin_id", "boot_layer", "error_class", "tenant_id"},
    },
    "plugin.execution_timeout": {
        "layer": "L4",
        "description": "Plugin execution timeout",
        "required_fields": {"plugin_id", "boot_layer", "timeout_ms", "tenant_id"},
    },
}

def load_event_severity():
    """Extract EVENT_SEVERITY events from security_events.py."""
    try:
        with open(SECURITY_EVENTS_FILE) as f:
            content = f.read()
    except FileNotFoundError:
        print(f"ERROR: {SECURITY_EVENTS_FILE} not found")
        return {}

    match = re.search(r"EVENT_SEVERITY:\s*dict\[str,\s*str\]\s*=\s*\{(.*?)\n\}", content, re.DOTALL)
    if not match:
        print("ERROR: Could not find EVENT_SEVERITY in security_events.py")
        return {}

    severity_block = match.group(1)
    events = re.findall(r'"([^"]+)":\s*"(INFO|WARNING|ERROR|CRITICAL)"', severity_block)
    return {event: severity for event, severity in events}

def load_event_allowlist():
    """Extract _EVENT_ALLOWLIST events + fields from security_events.py."""
    try:
        with open(SECURITY_EVENTS_FILE) as f:
            content = f.read()
    except FileNotFoundError:
        print(f"ERROR: {SECURITY_EVENTS_FILE} not found")
        return {}

    match = re.search(
        r"_EVENT_ALLOWLIST:\s*dict\[str,\s*frozenset\[str\]\]\s*=\s*\{(.*?)\n\}",
        content,
        re.DOTALL
    )
    if not match:
        print("ERROR: Could not find _EVENT_ALLOWLIST in security_events.py")
        return {}

    allowlist_block = match.group(1)
    # Extract each event and its fields
    result = {}
    for event_match in re.finditer(r'"([^"]+)":\s*frozenset\(\{([^}]+)\}\)', allowlist_block):
        event_name = event_match.group(1)
        fields_str = event_match.group(2)
        # Parse field strings
        fields = set(re.findall(r'"([^"]+)"', fields_str))
        result[event_name] = fields

    return result

def verify_phase2_completeness():
    """Verify all Phase 2 events are registered + complete."""
    print("=" * 70)
    print("Phase 2 Audit Events Completeness Check")
    print("=" * 70)

    events_with_severity = load_event_severity()
    events_with_allowlist = load_event_allowlist()

    if not events_with_severity or not events_with_allowlist:
        return 3

    errors = []
    warnings = []

    print(f"\nVerifying {len(PHASE2_EVENTS)} Phase 2 events...\n")

    for event_name, specs in PHASE2_EVENTS.items():
        print(f"  {specs['layer']}: {event_name}")
        print(f"    Description: {specs['description']}")

        # Check EVENT_SEVERITY
        if event_name not in events_with_severity:
            error_msg = f"    ❌ Missing from EVENT_SEVERITY"
            print(error_msg)
            errors.append(error_msg)
        else:
            severity = events_with_severity[event_name]
            print(f"    ✅ Severity: {severity}")

        # Check _EVENT_ALLOWLIST
        if event_name not in events_with_allowlist:
            error_msg = f"    ❌ Missing from _EVENT_ALLOWLIST"
            print(error_msg)
            errors.append(error_msg)
        else:
            actual_fields = events_with_allowlist[event_name]
            required = specs["required_fields"]

            # Check required fields are present
            missing_fields = required - actual_fields
            if missing_fields:
                error_msg = f"    ❌ Missing fields in allow-list: {sorted(missing_fields)}"
                print(error_msg)
                errors.append(error_msg)
            else:
                print(f"    ✅ Fields: {len(actual_fields)} ({len(required)} required)")

            # Check for extra fields (warning only)
            extra_fields = actual_fields - required
            if extra_fields:
                warning_msg = f"    ⚠️  Extra fields: {sorted(extra_fields)}"
                print(warning_msg)
                warnings.append(warning_msg)

        print()

    # Summary
    phase2_in_severity = sum(1 for e in PHASE2_EVENTS if e in events_with_severity)
    phase2_in_allowlist = sum(1 for e in PHASE2_EVENTS if e in events_with_allowlist)

    print("=" * 70)
    print(f"Phase 2 Coverage: {phase2_in_severity}/{len(PHASE2_EVENTS)} in EVENT_SEVERITY")
    print(f"Phase 2 Coverage: {phase2_in_allowlist}/{len(PHASE2_EVENTS)} in _EVENT_ALLOWLIST")
    print("=" * 70)

    if errors:
        print(f"\n❌ {len(errors)} errors found:")
        for error in errors:
            print(error)
        return 1

    if warnings:
        print(f"\n⚠️  {len(warnings)} warnings (non-blocking):")
        for warning in warnings:
            print(warning)

    return 0

def main():
    """Run verifier."""
    result = verify_phase2_completeness()

    if result == 0:
        print("\n✅ Phase 2 Completeness: PASS (all 8 events properly registered)\n")
    elif result == 1:
        print("\n❌ Phase 2 Completeness: FAIL (fix EVENT_SEVERITY + _EVENT_ALLOWLIST)\n")
    elif result == 3:
        print("\n❌ ERROR: Could not parse security_events.py\n")

    return result

if __name__ == "__main__":
    sys.exit(main())

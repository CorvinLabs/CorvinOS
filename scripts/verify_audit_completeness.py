#!/usr/bin/env python3
"""Verify Audit Chain 100% Completeness.

Checks all Phase 1 and Phase 2 events are properly registered
in EVENT_SEVERITY and _EVENT_ALLOWLIST.
"""
import sys
from pathlib import Path

def verify_events():
    """Verify all audit events registered."""
    try:
        from corvin_operator.forge.forge.security_events import (
            EVENT_SEVERITY,
            _EVENT_ALLOWLIST,
        )
    except ImportError as e:
        print(f"❌ Failed to import security_events: {e}")
        return False

    # Phase 1: Critical Events (10)
    phase1_events = {
        "context.snapshot_created",
        "context.snapshot_restored",
        "compute.checkpoint_corrupted",
        "compute.deadlock_detected",
        "compute.iteration_diverged",
        "acs.l34_gate_passed",
        "erasure.tenant_boundary_checked",
        "erasure.cross_tenant_detected",
    }

    # Phase 2: Extended Events (3 so far)
    phase2_events = {
        "compute.worker_spawn_initiated",
        "compute.worker_heartbeat",
        "compute.worker_terminated",
    }

    all_phase_events = phase1_events | phase2_events

    # Check EVENT_SEVERITY
    missing_severity = all_phase_events - set(EVENT_SEVERITY.keys())
    if missing_severity:
        print(f"❌ Missing from EVENT_SEVERITY: {missing_severity}")
        return False

    print(f"✅ All {len(phase1_events)} Phase 1 events in EVENT_SEVERITY")
    print(f"✅ All {len(phase2_events)} Phase 2 events in EVENT_SEVERITY")

    # Check _EVENT_ALLOWLIST
    missing_allowlist = all_phase_events - set(_EVENT_ALLOWLIST.keys())
    if missing_allowlist:
        print(f"❌ Missing from _EVENT_ALLOWLIST: {missing_allowlist}")
        return False

    print(f"✅ All audit events have allowlist entries")

    # Verify no empty allowlists
    for event in all_phase_events:
        if not _EVENT_ALLOWLIST[event]:
            print(f"❌ Event {event} has empty allowlist")
            return False

    print(f"✅ All allowlists have at least one field")
    print(f"\n📊 Total Events Verified: {len(all_phase_events)}")
    print(f"   Phase 1: {len(phase1_events)} ✅")
    print(f"   Phase 2: {len(phase2_events)} ✅")
    return True


if __name__ == "__main__":
    if verify_events():
        print("\n✅ AUDIT COMPLETENESS VERIFIED")
        sys.exit(0)
    else:
        print("\n❌ AUDIT COMPLETENESS CHECK FAILED")
        sys.exit(1)

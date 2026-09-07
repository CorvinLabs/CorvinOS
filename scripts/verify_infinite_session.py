#!/usr/bin/env python3
"""Infinite-session round-trip verification (used by deploy/infinite_session_deploy.sh).

Runs against the REAL engine under a temporary ``CORVIN_HOME`` (never the live
root): parse a task definition → write chained snapshots audit-first through
``EventStore`` (core hash-chained writer) → sign a bridge → "restart" with a
new store instance → recover the head → verify chain, bridge and rollback log.

Exit 0 only when every step holds; exit 1 with the failing step otherwise.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="corvin-infinite-verify-") as tmp:
        os.environ["CORVIN_HOME"] = tmp
        os.environ["CORVIN_TENANT_ID"] = "_default"
        os.environ.pop("VOICE_AUDIT_PATH", None)

        from core.infinite_session import (  # noqa: PLC0415
            AuditVerifier, CryptoBinding, EventStore, RollbackManager,
            SessionBridger, TaskDefParser, VerificationStatus, snapshot_task_state,
        )

        tenant = "_default"
        plan, error = TaskDefParser.parse({
            "@context": "https://corvin.dev/schema/task-definition/v1",
            "task_id": "verify_task", "task_name": "Verify", "description": "d",
            "autonomy_level": "3", "timeout_seconds": 60,
            "phases": [
                {"phase_id": "p1", "phase_name": "P1"},
                {"phase_id": "p2", "phase_name": "P2", "dependencies": ["p1"]},
            ],
            "success_criteria": {},
        })
        if error:
            return _fail("task definition", error)
        print(f"ok  plan {plan.phase_order}")

        store = EventStore(tenant)
        snaps = []
        for phase in plan.phase_order:
            snap, err = snapshot_task_state(tenant, plan.task_id, {"phase": phase, "n": len(snaps)},
                                            phase_id=phase, store=store)
            if err:
                return _fail(f"write snapshot {phase}", err)
            snaps.append(snap)
        print(f"ok  {len(snaps)} snapshots written audit-first")

        ok, err = store.verify_snapshot_chain(tenant, plan.task_id)
        if not ok:
            return _fail("chain verification", err)
        print("ok  chain verified")

        crypto = CryptoBinding()
        bridger = SessionBridger(store, crypto)
        bridge, err = bridger.create_bridge(tenant, plan.task_id, "session_a", "session_b", snaps[-1], "p2")
        if err:
            return _fail("bridge", err)
        print(f"ok  bridge {bridge.bridge_id[:8]} signed")

        # restart
        del store
        store2 = EventStore(tenant)
        latest, err = store2.get_latest_snapshot(tenant, plan.task_id)
        if err or latest != snaps[-1]:
            return _fail("recover after restart", err or "head mismatch")
        state, err = SessionBridger(store2, crypto).resume_from_bridge(tenant, plan.task_id, bridge.bridge_id)
        if err or state["state_dict"] != snaps[-1].state_dict:
            return _fail("resume from bridge", err or "state mismatch")
        print("ok  restart → head + bridge recovered")

        result, err = AuditVerifier(store2, crypto).verify_task_chain(tenant, plan.task_id)
        if err or result.status != VerificationStatus.PASS:
            return _fail("audit verifier", err or result.status.value)
        print("ok  audit verifier PASS")

        rm = RollbackManager(tenant)
        tx, err = rm.begin_transaction(tenant, "verify.cfg", {"v": 1}, {"v": 2})
        ok, err = rm.commit_transaction(tx, tenant, "verify.cfg", {"v": 1}, {"v": 2}) if not err else (False, err)
        if not ok:
            return _fail("rollback commit", err)
        ok, err = rm.verify_chain_integrity(tenant)
        if not ok:
            return _fail("rollback chain", err)
        print("ok  rollback log chained (keyed MAC)")

        from core.learning.event_persistence import _resolve_core_audit  # noqa: PLC0415
        chain = Path(_resolve_core_audit().audit_path())
        if not chain.exists() or chain.read_text().count("infinite_session.snapshot_created") < len(snaps):
            return _fail("core audit chain", f"missing snapshot_created records in {chain}")
        print(f"ok  core audit chain has the snapshot records ({chain})")

    print("VERIFY OK")
    return 0


def _fail(step: str, reason: str) -> int:
    print(f"FAIL {step}: {reason}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())

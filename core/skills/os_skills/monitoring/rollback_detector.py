"""L5 Phase 2 rollback — a persisted, tenant-scoped trip (ADR-2092 G4).

Once the correctness judge trips, Phase 2 is off for the tenant on every surface and
across restarts until an operator clears it with :func:`reset_rollback`. The old
in-memory detector forgot its state on restart and, lacking outcomes, never tripped.

The trip is audited (``routing.rollback_triggered``) before the state file is written;
the reset is audited (``routing.rollback_reset``) likewise.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Callable, Optional

from core.skills.os_skills.monitoring import correctness_tracker, routing_ledger

STATE_FILENAME = "rollback_state.json"
AuditFn = Callable[[str, dict], None]


def state_path(tenant_id: str) -> Path:
    return routing_ledger.routing_dir(tenant_id) / STATE_FILENAME


def rollback_active(tenant_id: str) -> bool:
    """Fail-closed: only a MISSING state file, or a readable one saying
    ``tripped: false``, means inactive. A torn or unreadable file keeps Phase 2 off."""
    path = state_path(tenant_id)
    try:
        raw = path.read_text()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    try:
        data = json.loads(raw)
    except ValueError:
        return True
    if not isinstance(data, dict) or not isinstance(data.get("tripped"), bool):
        return True
    return data["tripped"]


def _write_state(tenant_id: str, data: dict) -> None:
    path = state_path(tenant_id)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


def evaluate(tenant_id: str, *, audit: Optional[AuditFn] = None,
             since_ts: float = 0.0) -> bool:
    """Judge the ledger; trip (audit-first) when the Skill trails. Returns active state."""
    if rollback_active(tenant_id):
        return True
    rows = routing_ledger.join(routing_ledger.read_records(tenant_id, since_ts=since_ts))
    metrics = correctness_tracker.compute_metrics(rows)
    if not correctness_tracker.should_rollback(metrics):
        return False
    details = {
        "tenant_id": tenant_id,
        "skill_n": metrics.skill_n,
        "bundled_n": metrics.bundled_n,
        "skill_success_rate": round(metrics.skill_success_rate or 0.0, 4),
        "bundled_success_rate": round(metrics.bundled_success_rate or 0.0, 4),
    }
    if audit is not None:
        audit("routing.rollback_triggered", details)
    _write_state(tenant_id, {"tripped": True, "at": time.time(), **details})
    return True


def reset_rollback(tenant_id: str, *, audit: Callable[[str, dict], bool]) -> None:
    """Operator action: clear a trip. Audit-first: no committed record → no reset."""
    if not audit("routing.rollback_reset", {"tenant_id": tenant_id}):
        raise RuntimeError("routing.rollback_reset did not commit — rollback stays active")
    _write_state(tenant_id, {"tripped": False, "at": time.time()})

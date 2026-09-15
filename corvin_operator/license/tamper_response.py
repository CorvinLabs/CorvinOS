"""Tamper response — ADR-0154 M4 (compliance-conformant substitute).

What ADR-0154 M4 specified, and why we do NOT build it
------------------------------------------------------
ADR-0154 M4 ("Chaos Injection on Tamper") prescribes a *silent, deceptive*
degradation on tamper detection: never log at INFO/WARNING/ERROR, inject random
latency, **silently drop a fraction of audit-write attempts**, halve session
TTLs, and finally crash with a misleading ``RuntimeError("database connection
pool exhausted")``.

That design is rejected. It collides head-on with this repo's absolute,
load-bearing constraints (CLAUDE.md, Compliance baseline):

  * "Don't lower audit-chain integrity — no event skips the hash-chain link."
  * GDPR Art. 30/32 require complete, tamper-evident audit records — silently
    dropping audit events is a compliance violation, not a deterrent.
  * Deliberately concealing a security incident (no WARNING/ERROR) is the exact
    opposite of the observability the audit layer exists to provide.
  * Intentionally crashing with a misleading cause is deceptive sabotage of the
    operator's own running system.

What we build instead (same deterrent intent, fully compliant)
--------------------------------------------------------------
On tamper detection the response is **loud, honest, and fail-closed**:

  1. Record the tamper state in memory (one entry, first detection wins).
  2. Emit a CRITICAL log line and a CRITICAL ``license.tamper_response`` audit
     event — the audit chain stays intact; we ADD an event, never drop one.
  3. Degrade to the Free tier (``validator._verified_license()`` already returns
     ``None`` on canary mismatch — licensed operations are denied).

The deterrent property is preserved: an in-process attacker who rebinds the
license trips the canary, loses all paid capability immediately, and leaves a
CRITICAL audit trail. We simply refuse to make that trail invisible or to
corrupt the operator's compliance posture to achieve it.

This module must NOT ``import anthropic`` and has no env kill-switch.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

log = logging.getLogger("corvin.license.tamper")

_lock = threading.Lock()
_state: "dict[str, Any] | None" = None


def engage(reason: str = "canary_mismatch") -> None:
    """Record + announce a tamper detection. Idempotent (first detection wins).

    Best-effort and never raises — the caller is on the enforcement hot path
    (``_verified_license``) and must keep failing closed even if auditing fails.
    """
    global _state
    first = False
    with _lock:
        if _state is None:
            _state = {"reason": reason, "detected_at": time.time(), "count": 1}
            first = True
        else:
            _state["count"] += 1
    if not first:
        # Already engaged — degradation is steady-state; avoid log/audit spam.
        return
    # Honest, loud response — the opposite of M4's silent chaos.
    log.critical(
        "license: tamper detected (reason=%s) — degrading to Free tier and "
        "denying licensed operations. This is a security incident "
        "(ADR-0154 M4 compliant response / ADR-0139 in-process boundary).",
        reason,
    )
    try:
        _emit_audit(reason)
    except Exception:  # noqa: BLE001
        # engage() is on the enforcement hot path (_verified_license) and must
        # never raise — observability is best-effort, fail-closed is not.
        log.debug("tamper_response audit emit raised (non-fatal)")


def _emit_audit(reason: str) -> None:
    """Emit a CRITICAL audit event. Adds to the chain; never drops an event."""
    try:
        import sys
        from pathlib import Path

        shared = Path(__file__).resolve().parents[1] / "bridges" / "shared"
        if str(shared) not in sys.path:
            sys.path.insert(0, str(shared))
        from audit import audit_event  # type: ignore[import]

        # audit_event is (event_type, *, details=..., severity=..., ...): the
        # reason is a metadata-only detail (a controlled reason code, never a
        # token/PII) and severity is pinned CRITICAL explicitly so the event is
        # CRITICAL even if the registry lacks the key. Passing `reason=` directly
        # raises TypeError (no such kwarg), which the broad except below would
        # SILENTLY DROP — exactly the M4 "never drop an audit event" promise this
        # module exists to keep. (Regression-tested against the real audit path.)
        audit_event(
            "license.tamper_response",
            severity="CRITICAL",
            details={"reason": str(reason)[:64]},
        )
    except Exception:  # noqa: BLE001
        # Observability is best-effort; the fail-closed degradation does not
        # depend on it (and we never silence the audit chain to hide state).
        log.debug("tamper_response audit emit failed (non-fatal)")


def is_engaged() -> bool:
    """True when tamper has been detected this process lifetime."""
    with _lock:
        return _state is not None


def status() -> "dict[str, Any] | None":
    """Return a copy of the tamper state for operator diagnostics, or None."""
    with _lock:
        return dict(_state) if _state is not None else None


def _reset_for_tests() -> None:
    """Clear tamper state — tests only."""
    global _state
    with _lock:
        _state = None

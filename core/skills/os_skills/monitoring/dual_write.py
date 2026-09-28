"""L5 Phase 2 (dual-write) — gated, bounded, audit-first (ADR-2092 G3/G4).

Two pure-ish functions the routing call site composes:

* :func:`effective_phase` — what the operator asked for (``CORVIN_ACP_PHASE``) versus
  what may run. ``phase2_dual_write`` is honoured only when the Skill actually executes
  in this process, the tenant has no tripped rollback, and the ledger-based readiness
  check passes for the surface. ``phase2_real`` is refused outright (no recovery path
  is defined for it). Anything refused runs as shadow, and the refusal is audited once
  per process per reason — never once per turn.
* :func:`clamp` — the ADR-0251 D2 bound applied to the Skill: its advice may confirm
  the bundled engine or de-escalate to ``native``; it may never escalate, never pick an
  engine the operator did not select, and never override an explicit ``/delegate``.

Serving a Skill-changed route is audit-first: the caller records
``routing.phase2_decision`` and serves the Skill's engine only if that record committed.
"""
from __future__ import annotations

import os
import threading
from typing import Callable, Optional

from core.skills.os_skills.monitoring import readiness, rollback_detector

PHASE_ENV = "CORVIN_ACP_PHASE"
REQUESTABLE = ("phase1_shadow", "phase2_dual_write", "phase2_real")
DEFAULT_THRESHOLD = 0.75

AuditFn = Callable[[str, dict], bool]
_refusals_seen: set[tuple[str, str, str]] = set()
_refusals_lock = threading.Lock()


def requested_phase() -> str:
    raw = os.environ.get(PHASE_ENV, "phase1_shadow").strip().lower()
    return raw if raw in REQUESTABLE else "phase1_shadow"


def _refuse_once(audit: Optional[AuditFn], tenant_id: str, surface: str,
                 requested: str, reason: str) -> None:
    # A pure query (no audit writer) never consumes the once-per-process slot,
    # and an uncommitted record is retried on the next turn.
    if audit is None:
        return
    key = (tenant_id, surface, reason)
    with _refusals_lock:
        if key in _refusals_seen:
            return
        _refusals_seen.add(key)
    committed = audit("routing.phase2_refused", {
        "tenant_id": tenant_id, "surface": surface,
        "phase_requested": requested, "reason": reason,
    })
    if not committed:
        with _refusals_lock:
            _refusals_seen.discard(key)


def effective_phase(*, tenant_id: str, surface: str, skill_available: bool,
                    audit: Optional[AuditFn] = None) -> str:
    """``"dual_write"`` or ``"shadow"`` — never anything the gates did not clear."""
    requested = requested_phase()
    if requested == "phase1_shadow":
        return "shadow"
    if requested == "phase2_real":
        _refuse_once(audit, tenant_id, surface, requested, "phase2_real_unsupported")
        return "shadow"
    if not skill_available:
        _refuse_once(audit, tenant_id, surface, requested, "skill_not_booted")
        return "shadow"
    if rollback_detector.rollback_active(tenant_id):
        _refuse_once(audit, tenant_id, surface, requested, "rollback_active")
        return "shadow"
    verdict = readiness.cached_evaluate(tenant_id, surface)
    if not verdict.ready:
        _refuse_once(audit, tenant_id, surface, requested, verdict.reason)
        return "shadow"
    return "dual_write"


def clamp(*, bundled: str, skill_engine: Optional[str], skill_conf: Optional[float],
          force_delegate: bool, threshold: float = DEFAULT_THRESHOLD) -> tuple[str, str, bool]:
    """Return ``(engine, source, clamped)``.

    ``source`` is ``"skill"`` only when the Skill's advice CHANGED the route; a
    confirmation or any refusal leaves the bundled engine with source ``"bundled"``.
    ``clamped`` is True when advice that differed from the bundled engine was refused.
    """
    differs = skill_engine is not None and skill_engine != bundled
    if not differs:
        return bundled, "bundled", False
    if force_delegate:
        return bundled, "bundled", True
    if skill_conf is None or skill_conf < threshold:
        return bundled, "bundled", False
    if skill_engine != "native":
        return bundled, "bundled", True
    return "native", "skill", False


def reset_for_tests() -> None:
    with _refusals_lock:
        _refusals_seen.clear()
    readiness.clear_cache()

"""L5 Phase 2 (dual-write) — gated, bounded, audit-first (ADR-2092 G3/G4).

Two pure-ish functions the routing call site composes:

* :func:`effective_phase` — what the operator asked for (``CORVIN_ACP_PHASE``) versus
  what may run. ``phase2_dual_write`` is honoured only when the Skill actually executes
  in this process, the tenant has no tripped rollback, and the ledger-based readiness
  check passes for the surface. ``phase2_real`` is refused outright (no recovery path
  is defined for it). Anything refused runs as shadow, and the refusal is audited once
  per process per reason — never once per turn. The readiness cache's transient
  ``evaluating`` placeholder also runs as shadow but is not a refusal and is never
  audited as one.
* :func:`clamp` — the ADR-0251 D2 bound applied to the Skill: its advice may confirm
  the bundled engine or de-escalate to ``native``; it may never escalate, never pick an
  engine the operator did not select, and never override an explicit ``/delegate``.

Serving a Skill-changed route is audit-first: the caller records
``routing.phase2_decision`` and serves the Skill's engine only if that record committed.
"""
from __future__ import annotations

import dataclasses
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


@dataclasses.dataclass(frozen=True)
class PhaseDecision:
    """ONE turn's phase decision (review r2_s2-2).

    Computed once per turn and carried to every reader of that turn — the console's
    engine guard and the turn itself — so they never read the readiness cache twice
    and disagree. ``reason`` is ``ready`` (cleared), ``phase1_shadow`` (shadow
    requested), :data:`readiness.EVALUATING_REASON` (no verdict yet: shadow, not a
    refusal) or the refusal reason, which is audited once per process.
    """
    tenant_id: str
    surface: str
    requested: str
    phase: str
    reason: str

    @property
    def refusal(self) -> Optional[str]:
        if self.reason in ("ready", "phase1_shadow", readiness.EVALUATING_REASON):
            return None
        return self.reason

    @property
    def gate(self) -> str:
        """The ledger's ``gate`` marker: ``pending`` when no verdict existed yet."""
        return "pending" if self.reason == readiness.EVALUATING_REASON else "verdict"


def decide(*, tenant_id: str, surface: str, skill_available: bool) -> PhaseDecision:
    """The phase gate as a pure decision: no audit, no side effect beyond starting a
    background readiness refresh."""
    requested = requested_phase()

    def _d(phase: str, reason: str) -> PhaseDecision:
        return PhaseDecision(tenant_id, surface, requested, phase, reason)
    if requested == "phase1_shadow":
        return _d("shadow", "phase1_shadow")
    if requested == "phase2_real":
        return _d("shadow", "phase2_real_unsupported")
    if not skill_available:
        return _d("shadow", "skill_not_booted")
    if rollback_detector.rollback_active(tenant_id):
        # A trip whose audit record has not committed yet is retried from here
        # (throttled, off-thread): no other path runs while Phase 2 is held off.
        try:
            rollback_detector.maybe_retry_pending(tenant_id)
        except Exception:  # noqa: BLE001
            pass
        return _d("shadow", "rollback_active")
    verdict = readiness.cached_evaluate(tenant_id, surface)
    if verdict.reason == readiness.EVALUATING_REASON:
        # The cache placeholder while a verdict is computed, not a gate verdict:
        # shadow, and no refusal record (the real reason is audited once it lands).
        return _d("shadow", readiness.EVALUATING_REASON)
    if not verdict.ready:
        return _d("shadow", verdict.reason)
    return _d("dual_write", "ready")


def restrict(decision: PhaseDecision, *, skill_available: bool) -> PhaseDecision:
    """Re-check a CARRIED decision when the turn acts on it. It only ever becomes
    more restrictive: a Skill registry that is gone or a rollback trip in between
    turns dual_write into shadow; nothing here turns shadow into dual_write."""
    if decision.phase != "dual_write":
        return decision
    if not skill_available:
        return dataclasses.replace(decision, phase="shadow", reason="skill_not_booted")
    if rollback_detector.rollback_active(decision.tenant_id):
        return dataclasses.replace(decision, phase="shadow", reason="rollback_active")
    return decision


def audit_refusal(decision: PhaseDecision, audit: Optional[AuditFn]) -> None:
    """Audit the decision's refusal once per process (a pure query passes None)."""
    if decision.refusal is not None:
        _refuse_once(audit, decision.tenant_id, decision.surface,
                     decision.requested, decision.refusal)


def effective_phase(*, tenant_id: str, surface: str, skill_available: bool,
                    audit: Optional[AuditFn] = None) -> str:
    """``"dual_write"`` or ``"shadow"`` — never anything the gates did not clear."""
    decision = decide(tenant_id=tenant_id, surface=surface, skill_available=skill_available)
    audit_refusal(decision, audit)
    return decision.phase


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
    rollback_detector.reset_for_tests()

"""ADR-0534 — the one trust gate every FEEDBACK learning event passes.

``event_store.EventStore.write_event`` calls ``admit()`` for every
``EventType.FEEDBACK`` event BEFORE the audit-first write. That store is the
single place feedback becomes learnable state (operator ratings, the
security-orchestrator and workflow-optimizer feedback handlers all write
there), so gating the primitive covers every producer, present and future —
gating sixteen console endpoints one by one would miss the seventeenth.

Order: throttle (Layer 1) → reality check (Layer 2) → trust weight (Layer 3).
Fail-closed: a rejection is audited and raises ``FeedbackRejected``; nothing
reaches the store. If the audit record itself cannot commit, the
``RuntimeError`` from ``core_audit_event`` propagates — feedback is never
accepted (or silently dropped) without its chain record.

Decisions are also appended to a content-free per-tenant ledger
(``<tenant>/learning/feedback_gate/decisions.jsonl``: ts, subject, decision,
reason). Layers 1 and 3 count over it, which is what makes them work across
requests and processes; the audit chain stays the record of truth.
"""

from __future__ import annotations

import dataclasses
import json
import os
import time
from pathlib import Path
from typing import Optional

from core.learning import adversarial_detector, feedback_throttle, reality_check
from core.learning.learning_events import EventType, LearningEvent

LEDGER_TAIL = 500  # decisions read back per admission; layers 1/3 need far fewer

# Audit event names (registered in security_events EVENT_SEVERITY + allowlist).
EV_THROTTLED = "learning.feedback_throttled"
EV_REJECTED = "learning.feedback_rejected"
EV_MISMATCH = "learning.feedback_reality_mismatch"
EV_ADVERSARIAL = "learning.adversarial_feedback_detected"
EV_ACCEPTED = "learning.feedback_accepted"


class FeedbackRejected(ValueError):
    """The gate refused a feedback signal; ``reason`` is a closed enum."""

    def __init__(self, reason: str, audit_ref: str):
        super().__init__(f"feedback rejected: {reason}")
        self.reason = reason
        self.audit_ref = audit_ref


class DecisionLedger:
    def __init__(self, learning_root: Path):
        self.path = Path(learning_root) / "feedback_gate" / "decisions.jsonl"

    def tail(self, n: int = LEDGER_TAIL) -> list[dict]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()[-n:]
        except FileNotFoundError:
            return []
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def append(self, *, ts: float, subject: str, decision: str, reason: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"ts": ts, "subject": subject, "decision": decision,
                           "reason": reason}, separators=(",", ":")) + "\n"
        # One O_APPEND write per record: concurrent writers interleave whole lines.
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)


def _audit(event: str, tenant_id: str, details: dict) -> str:
    from core.learning.event_persistence import core_audit_event  # noqa: PLC0415
    return core_audit_event(event, tenant_id=tenant_id, details=details)


def admit(event: LearningEvent, store, *, now: Optional[float] = None,
          rate: Optional[feedback_throttle.RateLimitConfig] = None,
          subject_verified: bool = False,
          ) -> LearningEvent:
    """Return the event to persist (with ``signal.trust_weight``) or raise.

    ``subject_verified`` is an in-process attestation by a caller that has
    checked the subject against its own ground truth (``grade_pattern``
    checks the pattern node exists). It replaces Layer 2 only; throttle and
    trust weight still apply. In-process code is attributed, not distrusted
    (CLAUDE.md, plugin perimeter); the gate's adversary is the SIGNAL.
    """
    if event.event_type is not EventType.FEEDBACK:
        return event
    now = time.time() if now is None else now
    rate = rate or feedback_throttle.DEFAULT_RATE_LIMIT
    tenant, subject = event.tenant_id, event.skill_id
    ledger = DecisionLedger(store.tenant_home / "learning")
    history = ledger.tail()
    base = {"tenant_id": tenant, "skill_id": subject, "feedback_event_id": event.event_id}

    def reject(audit_name: str, reason: str, extra: dict) -> FeedbackRejected:
        ref = _audit(audit_name, tenant, {**base, "reason": reason, **extra})
        ledger.append(ts=now, subject=subject, decision="rejected", reason=reason)
        return FeedbackRejected(reason, ref)

    throttle = feedback_throttle.check_rate(history, subject=subject, now=now, config=rate)
    if not throttle.allowed:
        raise reject(EV_THROTTLED, "rate_limit_exceeded", {
            "signals_in_window": throttle.signals_in_window,
            "max_allowed": rate.max_per_window, "window_sec": rate.window_sec})

    reality = (reality_check.RealityResult(True, "verified_by_caller") if subject_verified
               else reality_check.validate(event, store))
    if not reality.is_valid:
        name = EV_MISMATCH if reality.reason == "output_hash_mismatch" else EV_REJECTED
        raise reject(name, reality.reason, {"executions_in_window": reality.executions_in_window})

    trust = adversarial_detector.assess(history, subject=subject)
    if trust.alert:
        _audit(EV_ADVERSARIAL, tenant, {**base, "trust_weight": trust.trust_weight,
                                        "rejection_count": trust.rejection_count,
                                        "accepted_count": trust.accepted_count,
                                        "reason": "review_feedback_source"})
    _audit(EV_ACCEPTED, tenant, {**base, "trust_weight": trust.trust_weight,
                                 "executions_in_window": reality.executions_in_window})
    ledger.append(ts=now, subject=subject, decision="accepted", reason="valid")
    signal = dict(event.signal or {})
    signal["trust_weight"] = trust.trust_weight
    return dataclasses.replace(event, signal=signal)

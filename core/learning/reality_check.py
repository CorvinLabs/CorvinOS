"""ADR-0534 Layer 2 — reality check: feedback must describe a real execution.

The ground truth is the tenant's own ``skill_executed`` learning events. Every
one of them was written audit-first by ``event_store.EventStore.write_event``
(core chain record, then disk, joined by ``audit_ref``), so checking against
the store IS checking against the audit trail — tenant-scoped and without
re-parsing the whole hash chain per signal.

Window (decided at acceptance, 2026-10-10): the execution must lie within
``lookback_sec`` (24 h) BEFORE the feedback, plus ``skew_sec`` (5 s) of clock
skew after it. The draft's ±10 s window fits machine feedback only; an
operator rates minutes or hours later.

``claimed_output_hash`` is optional. When present it must equal
``execution_output_hash`` of at least one execution in the window, otherwise
the signal describes an output the skill never produced.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from core.learning.learning_events import EventType, LearningEvent

LOOKBACK_SEC = 24 * 3600
SKEW_SEC = 5

SKILL_PREFIX = "skill:"


def execution_output_hash(signal: Optional[dict[str, Any]]) -> Optional[str]:
    """sha256 over the canonical JSON of a ``skill_executed`` signal's ``output``.

    Public so a feedback producer can compute the same value it claims.
    """
    if not signal or "output" not in signal:
        return None
    blob = json.dumps(signal["output"], sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(blob.encode()).hexdigest()


def skill_of(subject: str) -> Optional[str]:
    """Map a FEEDBACK subject to the skill whose executions can verify it.

    ``skill:<id>`` (operator ratings) and a bare ``os.*`` id are skills.
    Anything else (``tool:<id>``, pattern ids, …) has no execution record in
    the learning store and therefore cannot be verified → ``None`` → rejected.
    """
    if subject.startswith(SKILL_PREFIX):
        return subject[len(SKILL_PREFIX):] or None
    if ":" in subject:
        return None
    return subject or None


def _parse_ts(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts.rstrip("Z"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class RealityResult:
    is_valid: bool
    reason: str  # valid | subject_not_verifiable | no_corresponding_execution | output_hash_mismatch
    executions_in_window: int = 0


def validate(feedback: LearningEvent, store, *, lookback_sec: int = LOOKBACK_SEC,
             skew_sec: int = SKEW_SEC) -> RealityResult:
    skill = skill_of(feedback.skill_id)
    if skill is None:
        return RealityResult(False, "subject_not_verifiable")
    t = _parse_ts(feedback.timestamp)
    lo, hi = t - timedelta(seconds=lookback_sec), t + timedelta(seconds=skew_sec)
    executions = [
        e for e in store.query_events(
            feedback.tenant_id, event_type=EventType.SKILL_EXECUTED, skill_id=skill,
            since=lo.strftime("%Y-%m-%d"), until=hi.strftime("%Y-%m-%d"), limit=2000,
        )
        if lo <= _parse_ts(e.timestamp) <= hi
    ]
    if not executions:
        return RealityResult(False, "no_corresponding_execution")
    claimed = (feedback.signal or {}).get("claimed_output_hash")
    if claimed is not None and not any(execution_output_hash(e.signal) == claimed for e in executions):
        return RealityResult(False, "output_hash_mismatch", len(executions))
    return RealityResult(True, "valid", len(executions))

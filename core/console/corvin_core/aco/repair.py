"""ACO Layer 5 — Self-Repair Engine (opt-in, ADR-0174).

Reads Layer 3 anomalies and applies deterministic repair actions by annotating
chat_debug.jsonl with repair.* events.  Layer 3 is repair-aware: it skips
anomalies that already have a matching repair event, so subsequent scans
converge toward zero CRITICAL/HIGH findings.

Loss signal (LDD-style):
    delta_loss = before_actionable - after_actionable
    convergence_reached = (after_critical + after_high) == 0

Repair actions are log-only — they do NOT restart processes or modify code.
Operator-level actions (gateway restart, engine swap) are outside this scope.
For the actuating Layer 5 engine (self-healing actions), see repair_actions.py (ADR-0178).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ── Repair catalogue ──────────────────────────────────────────────────────────

@dataclass
class RepairActionSpec:
    action_id: str
    anomaly_class: str
    description: str
    repair_event: str       # written to chat_debug.jsonl


_REPAIR_CATALOGUE: list[RepairActionSpec] = [
    RepairActionSpec(
        action_id="turn_flush",
        anomaly_class="stalled_turn",
        description="Annotate stalled turns as flushed so they stop blocking convergence",
        repair_event="repair.turn_flushed",
    ),
    RepairActionSpec(
        action_id="delegation_reset",
        anomaly_class="delegation_orphan",
        description="Reset delegation circuit so the next turn re-attempts delegation",
        repair_event="repair.delegation_reset",
    ),
    RepairActionSpec(
        action_id="acs_throttle",
        anomaly_class="acs_error_rate",
        description="Throttle ACS for 3 turns to break error cascade",
        repair_event="repair.acs_throttle_on",
    ),
    RepairActionSpec(
        action_id="ws_reconnect",
        anomaly_class="ws_instability",
        description="Request client WebSocket reconnect to clear stale connection",
        repair_event="repair.ws_reconnect_requested",
    ),
    RepairActionSpec(
        action_id="stream_clear",
        anomaly_class="empty_response_error",
        description="Clear stream error state so the next turn starts fresh",
        repair_event="repair.stream_cleared",
    ),
]


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class AppliedAction:
    action_id: str
    anomaly_class: str
    status: str              # "applied" | "skipped" | "dry_run" | "error"
    detail: str
    events_written: int = 0


@dataclass
class RepairResult:
    dry_run: bool
    before_critical: int = 0
    before_high: int = 0
    after_critical: int = 0
    after_high: int = 0
    actions_applied: list[AppliedAction] = field(default_factory=list)
    actions_skipped: list[AppliedAction] = field(default_factory=list)
    events_written: int = 0

    @property
    def delta_loss(self) -> int:
        before = self.before_critical + self.before_high
        after  = self.after_critical  + self.after_high
        return before - after

    @property
    def convergence_reached(self) -> bool:
        return (self.after_critical + self.after_high) == 0

    def to_dict(self) -> dict:
        return {
            "dry_run": self.dry_run,
            "before": {"critical": self.before_critical, "high": self.before_high},
            "after":  {"critical": self.after_critical,  "high": self.after_high},
            "delta_loss": self.delta_loss,
            "convergence_reached": self.convergence_reached,
            "actions_applied": [
                {
                    "action_id": a.action_id,
                    "anomaly_class": a.anomaly_class,
                    "status": a.status,
                    "detail": a.detail,
                    "events_written": a.events_written,
                }
                for a in self.actions_applied
            ],
            "actions_skipped": [
                {
                    "action_id": a.action_id,
                    "anomaly_class": a.anomaly_class,
                    "status": a.status,
                    "detail": a.detail,
                }
                for a in self.actions_skipped
            ],
            "total_events_written": self.events_written,
        }


# ── Event writer ──────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _append_event(workdir: Path, event: dict) -> None:
    log_path = workdir / "chat_debug.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")


# ── Per-class repair handlers ─────────────────────────────────────────────────

def _repair_stalled_turns(
    workdir: Path, anomalies: list, spec: RepairActionSpec, dry_run: bool,
) -> AppliedAction:
    stalled = [a for a in anomalies if a.anomaly_class == "stalled_turn"]
    if not stalled:
        return AppliedAction(spec.action_id, spec.anomaly_class, "skipped",
                             "No stalled turns to flush")
    if dry_run:
        return AppliedAction(spec.action_id, spec.anomaly_class, "dry_run",
                             f"Would flush {len(stalled)} stalled turn(s)")
    written = 0
    for anomaly in stalled:
        turn_start_ts = (anomaly.evidence[0].get("ts") if anomaly.evidence else None) or ""
        _append_event(workdir, {
            "ts": _now_iso(),
            "event": spec.repair_event,
            "action_id": spec.action_id,
            "turn_start_ts": turn_start_ts,
            "anomaly_message": anomaly.message[:200],
        })
        written += 1
    return AppliedAction(spec.action_id, spec.anomaly_class, "applied",
                         f"Flushed {written} stalled turn(s) — future scans skip them",
                         events_written=written)


def _repair_delegation_orphans(
    workdir: Path, anomalies: list, spec: RepairActionSpec, dry_run: bool,
) -> AppliedAction:
    orphans = [a for a in anomalies if a.anomaly_class == "delegation_orphan"]
    if not orphans:
        return AppliedAction(spec.action_id, spec.anomaly_class, "skipped",
                             "No delegation orphans")
    if dry_run:
        return AppliedAction(spec.action_id, spec.anomaly_class, "dry_run",
                             f"Would reset delegation circuit ({len(orphans)} orphan(s))")
    _append_event(workdir, {
        "ts": _now_iso(),
        "event": spec.repair_event,
        "action_id": spec.action_id,
        "orphan_count": len(orphans),
    })
    return AppliedAction(spec.action_id, spec.anomaly_class, "applied",
                         f"Delegation circuit reset — {len(orphans)} orphan(s) acknowledged",
                         events_written=1)


def _repair_acs_error_rate(
    workdir: Path, anomalies: list, spec: RepairActionSpec, dry_run: bool,
) -> AppliedAction:
    errors = [a for a in anomalies if a.anomaly_class == "acs_error_rate"]
    if not errors:
        return AppliedAction(spec.action_id, spec.anomaly_class, "skipped",
                             "ACS error rate within threshold")
    if dry_run:
        return AppliedAction(spec.action_id, spec.anomaly_class, "dry_run",
                             "Would enable ACS throttle")
    _append_event(workdir, {
        "ts": _now_iso(),
        "event": spec.repair_event,
        "action_id": spec.action_id,
        "throttle_turns": 3,
    })
    return AppliedAction(spec.action_id, spec.anomaly_class, "applied",
                         "ACS throttle enabled for 3 turns — error cascade broken",
                         events_written=1)


def _repair_ws_instability(
    workdir: Path, anomalies: list, spec: RepairActionSpec, dry_run: bool,
) -> AppliedAction:
    ws_issues = [a for a in anomalies if a.anomaly_class == "ws_instability"]
    if not ws_issues:
        return AppliedAction(spec.action_id, spec.anomaly_class, "skipped",
                             "WS stability within threshold")
    if dry_run:
        return AppliedAction(spec.action_id, spec.anomaly_class, "dry_run",
                             "Would request WS reconnect")
    _append_event(workdir, {
        "ts": _now_iso(),
        "event": spec.repair_event,
        "action_id": spec.action_id,
    })
    return AppliedAction(spec.action_id, spec.anomaly_class, "applied",
                         "WS reconnect requested — client reconnects on next poll",
                         events_written=1)


def _repair_stream_errors(
    workdir: Path, anomalies: list, spec: RepairActionSpec, dry_run: bool,
) -> AppliedAction:
    stream_errs = [a for a in anomalies if a.anomaly_class == "empty_response_error"]
    if not stream_errs:
        return AppliedAction(spec.action_id, spec.anomaly_class, "skipped",
                             "No empty-response errors")
    if dry_run:
        return AppliedAction(spec.action_id, spec.anomaly_class, "dry_run",
                             f"Would clear {len(stream_errs)} stream error(s)")
    _append_event(workdir, {
        "ts": _now_iso(),
        "event": spec.repair_event,
        "action_id": spec.action_id,
        "error_count": len(stream_errs),
    })
    return AppliedAction(spec.action_id, spec.anomaly_class, "applied",
                         f"Stream error state cleared ({len(stream_errs)} error(s))",
                         events_written=1)


_REPAIR_HANDLERS = {
    "stalled_turn":         _repair_stalled_turns,
    "delegation_orphan":    _repair_delegation_orphans,
    "acs_error_rate":       _repair_acs_error_rate,
    "ws_instability":       _repair_ws_instability,
    "empty_response_error": _repair_stream_errors,
}


# ── Public API ────────────────────────────────────────────────────────────────

def is_acs_throttled(workdir: Any) -> bool:
    """Return True if a repair.acs_throttle_on event is active for this session.

    The throttle expires after ``throttle_turns`` (default 3) completed turns
    (turn.done events) have been recorded since the repair event was written.
    Used by chat_runtime.py to suppress ACS delegation while recovering.
    """
    from .events import read_session_log
    events = read_session_log(workdir)
    throttles = [e for e in events if e.get("event") == "repair.acs_throttle_on"]
    if not throttles:
        return False
    last = throttles[-1]
    throttle_turns: int = int(last.get("throttle_turns", 3))
    throttle_ts: str = last.get("ts", "")
    turns_after = sum(
        1 for e in events
        if e.get("event") == "turn.done" and e.get("ts", "") > throttle_ts
    )
    return turns_after < throttle_turns


def repair_session(workdir: Any, dry_run: bool = False) -> RepairResult:
    """Run Layer 5 self-repair against all HIGH/CRITICAL anomalies.

    For each repairable anomaly class found, applies the corresponding repair
    action by writing repair.* events to chat_debug.jsonl.  Subsequent scans
    skip repaired anomalies, so the loss signal converges toward zero.

    Returns RepairResult containing before/after counts (delta_loss, convergence)
    and the list of applied/skipped actions — LDD loss signal for self-repair.
    """
    from .anomaly_detector import scan_session, SEVERITY_CRITICAL, SEVERITY_HIGH

    workdir = Path(workdir)
    anomalies = scan_session(workdir)
    actionable = [
        a for a in anomalies
        if a.severity in (SEVERITY_CRITICAL, SEVERITY_HIGH)
    ]

    result = RepairResult(
        dry_run=dry_run,
        before_critical=sum(1 for a in actionable if a.severity == SEVERITY_CRITICAL),
        before_high=sum(1 for a in actionable if a.severity == SEVERITY_HIGH),
    )

    for spec in _REPAIR_CATALOGUE:
        handler = _REPAIR_HANDLERS.get(spec.anomaly_class)
        if handler is None:
            continue
        try:
            action = handler(workdir, actionable, spec, dry_run)
        except Exception as exc:
            logger.warning("Repair handler %s failed: %s", spec.action_id, exc, exc_info=True)
            action = AppliedAction(spec.action_id, spec.anomaly_class, "error", str(exc))
        if action.status in ("applied", "dry_run"):
            result.actions_applied.append(action)
            result.events_written += action.events_written
        else:
            result.actions_skipped.append(action)

    if not dry_run and result.events_written > 0:
        # Post-repair scan — measure the loss signal
        post = scan_session(workdir)
        result.after_critical = sum(
            1 for a in post if a.severity == SEVERITY_CRITICAL
        )
        result.after_high = sum(
            1 for a in post if a.severity == SEVERITY_HIGH
        )
    else:
        # dry_run or no writes — after == before (no change)
        result.after_critical = result.before_critical
        result.after_high = result.before_high

    return result

"""ACO Layer 3 — Anomaly Detection.

Scans chat_debug.jsonl for structural anomalies in the turn lifecycle:
missing events, unexpected state transitions, latency outliers, error rates.

All checks are purely functional over the event list — no network, no DB.
"""
from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .events import read_session_log

logger = logging.getLogger(__name__)

# Fields that may contain user-authored text and must be stripped from
# evidence before it is included in API responses (GDPR / CLAUDE.md).
_PII_FIELDS = {"prompt_preview", "task_preview"}


# ── Anomaly types ─────────────────────────────────────────────────────────────

SEVERITY_CRITICAL = "CRITICAL"
SEVERITY_HIGH     = "HIGH"
SEVERITY_MEDIUM   = "MEDIUM"
SEVERITY_LOW      = "LOW"


@dataclass
class Anomaly:
    anomaly_class: str
    severity: str
    message: str
    evidence: list[dict] = field(default_factory=list)
    suggestion: str = ""

    def to_dict(self) -> dict:
        return {
            "anomaly_class": self.anomaly_class,
            "severity": self.severity,
            "message": self.message,
            "evidence_count": len(self.evidence),
            "evidence": [_strip_pii(e) for e in self.evidence[:3]],
            "suggestion": self.suggestion,
        }


def _strip_pii(event: dict) -> dict:
    """Return a shallow copy of *event* with PII fields removed."""
    return {k: v for k, v in event.items() if k not in _PII_FIELDS}


# ── Thresholds ────────────────────────────────────────────────────────────────

# A turn must produce turn.done within this many ms or it's flagged as stalled
TURN_TIMEOUT_MS = 5 * 60 * 1000          # 5 minutes
# High latency warning threshold
TURN_LATENCY_HIGH_MS = 60_000            # 60 s
# ACS run error rate that triggers a HIGH anomaly
ACS_ERROR_RATE_HIGH = 0.10               # 10 %
# WS reconnect rate per log window that's considered unstable
WS_CLOSE_RATE_HIGH = 3                   # ≥ 3 closes in the log window
# Delegation mismatch: will_delegate=True but no acs.run.start following it
DELEGATION_ORPHAN_WINDOW_EVENTS = 5      # look within 5 events after decision


# ── Checks ────────────────────────────────────────────────────────────────────

def _parse_ts(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _check_stalled_turns(events: list[dict]) -> list[Anomaly]:
    """Detect turn.start events without a matching turn.done.

    Pairing is sequential: the nth turn.start is paired with the nth turn.done
    in log order. Any turn.start without a corresponding nth done is a
    CANDIDATE stall — but a turn that is still legitimately in flight (started
    seconds or a couple of minutes ago) always lacks a turn.done too, since it
    hasn't finished yet. Only flag it once it has been running longer than
    TURN_TIMEOUT_MS: a turn.start with no ts, or one that fails to parse, is
    treated conservatively as stalled (better a false positive than hiding a
    genuinely stuck turn behind a malformed timestamp).

    Repair-aware: skips stalls that have a repair.turn_flushed event with a
    matching turn_start_ts (written by Layer 5 repair.py).
    """
    # Collect turn_start_ts values that Layer 5 has already flushed
    flushed_ts: set[str] = {
        str(e["turn_start_ts"])
        for e in events
        if e.get("event") == "repair.turn_flushed" and e.get("turn_start_ts")
    }

    now = datetime.now(timezone.utc)
    anomalies: list[Anomaly] = []
    starts = [e for e in events if e.get("event") == "turn.start"]
    dones  = [e for e in events if e.get("event") == "turn.done"]

    for idx, start in enumerate(starts):
        if idx >= len(dones):
            start_ts = start.get("ts", "")
            if start_ts in flushed_ts:
                continue   # already repaired by Layer 5
            start_dt = _parse_ts(start_ts)
            if start_dt is not None:
                elapsed_ms = (now - start_dt).total_seconds() * 1000
                if elapsed_ms < TURN_TIMEOUT_MS:
                    continue   # still legitimately in flight — not stalled yet
            anomalies.append(Anomaly(
                anomaly_class="stalled_turn",
                severity=SEVERITY_HIGH,
                message=f"turn.start at {start_ts} has no matching turn.done",
                evidence=[start],
                suggestion="Check chat_runtime.py stream_turn() exception handlers. "
                           "A turn.done must always be emitted even on error.",
            ))
    return anomalies


def _check_delegation_orphans(events: list[dict]) -> list[Anomaly]:
    """Detect delegation.decision(will_delegate=True) without acs.run.start.

    Repair-aware: returns empty list when a repair.delegation_reset event exists.
    """
    if any(e.get("event") == "repair.delegation_reset" for e in events):
        return []   # Layer 5 has already reset the delegation circuit
    anomalies: list[Anomaly] = []
    for i, ev in enumerate(events):
        if ev.get("event") != "delegation.decision":
            continue
        if not ev.get("will_delegate"):
            continue
        # Look for acs.run.start in the next N events
        window = events[i + 1 : i + 1 + DELEGATION_ORPHAN_WINDOW_EVENTS]
        if not any(e.get("event") == "acs.run.start" for e in window):
            anomalies.append(Anomaly(
                anomaly_class="delegation_orphan",
                severity=SEVERITY_HIGH,
                message=(
                    f"delegation.decision(will_delegate=True) at {ev.get('ts')} "
                    "not followed by acs.run.start within 5 events"
                ),
                evidence=[_strip_pii(ev)] + [_strip_pii(e) for e in window],
                suggestion="Check chat_runtime.py delegation path — acs.run.start "
                           "must be emitted immediately after delegation decision.",
            ))
    return anomalies


def _check_acs_error_rate(events: list[dict]) -> list[Anomaly]:
    """Flag high ACS run error rate.

    Repair-aware: skips check when a repair.acs_throttle_on event exists,
    indicating Layer 5 has already throttled ACS to break the error cascade.
    """
    if any(e.get("event") == "repair.acs_throttle_on" for e in events):
        return []
    anomalies: list[Anomaly] = []
    runs_done = [e for e in events if e.get("event") == "acs.run.done"]
    if not runs_done:
        return anomalies
    errors = [e for e in runs_done if e.get("status") == "error"]
    rate = len(errors) / len(runs_done)
    if rate >= ACS_ERROR_RATE_HIGH:
        anomalies.append(Anomaly(
            anomaly_class="acs_error_rate",
            severity=SEVERITY_HIGH,
            message=(
                f"ACS error rate {rate:.0%} ({len(errors)}/{len(runs_done)} runs) "
                f"exceeds threshold {ACS_ERROR_RATE_HIGH:.0%}"
            ),
            evidence=errors[:3],
            suggestion="Check ACS worker logs for recurring error patterns. "
                       "Common causes: engine timeout, quota exhaustion, L34 rejection.",
        ))
    return anomalies


def _check_latency_outliers(events: list[dict]) -> list[Anomaly]:
    """Detect turns with anomalously high elapsed_ms."""
    anomalies: list[Anomaly] = []
    turns_done = [
        e for e in events
        if e.get("event") == "turn.done" and isinstance(e.get("elapsed_ms"), int)
    ]
    if len(turns_done) < 2:
        return anomalies

    latencies = [e["elapsed_ms"] for e in turns_done]
    p95 = sorted(latencies)[int(len(latencies) * 0.95)]
    mean = statistics.mean(latencies)

    for e in turns_done:
        ms = e["elapsed_ms"]
        # p95 * 3 guard: skip when p95==0 to avoid flagging every fast turn
        p95_outlier = p95 > 0 and len(latencies) >= 5 and ms >= p95 * 3
        if ms >= TURN_LATENCY_HIGH_MS or p95_outlier:
            anomalies.append(Anomaly(
                anomaly_class="latency_outlier",
                severity=SEVERITY_MEDIUM,
                message=(
                    f"turn.done elapsed_ms={ms} "
                    f"(p95={p95}, mean={mean:.0f}) at {e.get('ts')}"
                ),
                evidence=[e],
                suggestion="Check engine timeout configuration and ACS worker budget.",
            ))
    return anomalies


def _check_ws_instability(events: list[dict]) -> list[Anomaly]:
    """Flag frequent WS disconnects.

    Repair-aware: skips check when a repair.ws_reconnect_requested event exists.
    """
    if any(e.get("event") == "repair.ws_reconnect_requested" for e in events):
        return []
    anomalies: list[Anomaly] = []
    closes = [e for e in events if e.get("event") == "ws.close"]
    if len(closes) >= WS_CLOSE_RATE_HIGH:
        abnormal = [e for e in closes if not e.get("wasClean")]
        anomalies.append(Anomaly(
            anomaly_class="ws_instability",
            severity=SEVERITY_MEDIUM if len(closes) < 10 else SEVERITY_HIGH,
            message=(
                f"{len(closes)} WS closes in log window "
                f"({len(abnormal)} abnormal/non-clean)"
            ),
            evidence=closes[:3],
            suggestion="Check uvicorn idle timeout, proxy config, or network blips. "
                       "See logs for close codes (1000=clean, 1006=abnormal).",
        ))
    return anomalies


def _check_stream_errors(events: list[dict]) -> list[Anomaly]:
    """Detect stream.error events without prior stream.delta (empty response path)."""
    anomalies: list[Anomaly] = []
    for i, ev in enumerate(events):
        if ev.get("event") != "stream.error":
            continue
        # Look back for stream.delta in the same turn window
        lookback = events[max(0, i - 10) : i]
        has_delta = any(e.get("event") == "stream.delta" for e in lookback)
        if not has_delta:
            anomalies.append(Anomaly(
                anomaly_class="empty_response_error",
                severity=SEVERITY_HIGH,
                message=(
                    f"stream.error at {ev.get('ts')} without prior stream.delta — "
                    "empty response path, user received no content"
                ),
                evidence=[ev],
                suggestion="Check adapter.py error handling and engine connectivity.",
            ))
    return anomalies


# ── Public API ────────────────────────────────────────────────────────────────

CHECKS = [
    _check_stalled_turns,
    _check_delegation_orphans,
    _check_acs_error_rate,
    _check_latency_outliers,
    _check_ws_instability,
    _check_stream_errors,
]

SEVERITY_ORDER = {
    SEVERITY_CRITICAL: 0,
    SEVERITY_HIGH: 1,
    SEVERITY_MEDIUM: 2,
    SEVERITY_LOW: 3,
}


def scan_session(workdir: Any) -> list[Anomaly]:
    """Run all anomaly checks against a session's chat_debug.jsonl.

    Returns list of Anomaly objects sorted by severity (CRITICAL first).
    """
    events = read_session_log(workdir)
    anomalies: list[Anomaly] = []
    for check in CHECKS:
        try:
            anomalies.extend(check(events))
        except Exception as exc:
            logger.warning("ACO check %s failed: %s", check.__name__, exc, exc_info=True)
    # Deduplicate by (class, message)
    seen: set[tuple] = set()
    unique: list[Anomaly] = []
    for a in anomalies:
        key = (a.anomaly_class, a.message[:100])
        if key not in seen:
            seen.add(key)
            unique.append(a)
    unique.sort(key=lambda a: SEVERITY_ORDER.get(a.severity, 9))
    return unique


def scan_session_to_dict(workdir: Any) -> dict:
    anomalies = scan_session(workdir)
    return {
        "total": len(anomalies),
        "critical": sum(1 for a in anomalies if a.severity == SEVERITY_CRITICAL),
        "high": sum(1 for a in anomalies if a.severity == SEVERITY_HIGH),
        "medium": sum(1 for a in anomalies if a.severity == SEVERITY_MEDIUM),
        "low": sum(1 for a in anomalies if a.severity == SEVERITY_LOW),
        "anomalies": [a.to_dict() for a in anomalies],
    }

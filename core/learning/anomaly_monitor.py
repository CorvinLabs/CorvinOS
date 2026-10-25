"""Learning-loop anomalies, computed from what the loop already persisted (ADR-2044 Phase 3, ADR-2284).

Four conditions, each a measurement over stored data — never a value someone writes:

* ``skill.high_rejection_rate`` — the ADR-0534 gate refused more than 20 % of the feedback on one
  subject in the last 24 h (``<tenant>/learning/feedback_gate/decisions.jsonl``, at least 10).
* ``skill.config_oscillation`` — a skill's learned config got more than 3 new versions in one hour
  (``<tenant>/skills/<skill>_config.json``).
* ``skill.confidence_regression`` — the mean ``output.confidence`` of a skill's ``skill_executed``
  events in the last 24 h is at least 0.10 below the six days before (at least 20 each side).
* ``skill.convergence_stall`` (ADR-2284) — a skill's newest OUTCOME learning events (at least 20, at
  most ``STALL_MAX_EVENTS``), split chronologically into four equal windows: the loss
  ``1 - success_rate`` stays above 0.20 in every window and moves less than 0.02 between them
  (max - min). Success is the signal's ``success`` bool, else ``passed``; other events are skipped.

Edge-triggered: a condition is recorded once when it starts (audit-first — the caller marks it
active only after the chain record committed) and again only after it cleared. The consumer is the
learning host's persisted state, read by ``GET /v1/console/api/learning/daemon`` and shown in the
console's Learning Loops view.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

REJECTION_WINDOW_S = 24 * 3600
REJECTION_MIN_SAMPLES = 10
REJECTION_RATE = 0.20

OSCILLATION_WINDOW_S = 3600
OSCILLATION_MAX_CHANGES = 3

CONFIDENCE_RECENT_S = 24 * 3600
CONFIDENCE_BASELINE_S = 7 * 24 * 3600
CONFIDENCE_MIN_SAMPLES = 20
CONFIDENCE_DROP = 0.10

STALL_MIN_EVENTS = 20
STALL_MAX_EVENTS = 200
STALL_WINDOWS = 4
STALL_MIN_LOSS = 0.20
STALL_MAX_SPREAD = 0.02

EVENTS = {
    "skill.high_rejection_rate": ("skill_id", "rejection_count", "sample_count", "rate", "window_seconds"),
    "skill.config_oscillation": ("skill_id", "param_name", "change_count", "window_seconds"),
    "skill.confidence_regression": ("skill_id", "confidence_before", "confidence_after",
                                    "samples_before", "samples_after"),
    "skill.convergence_stall": ("skill_id", "sample_count", "window_count", "loss_min", "loss_max",
                                "loss_spread"),
}


@dataclass(frozen=True)
class Anomaly:
    event: str
    skill_id: str
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.event}:{self.skill_id}"


def _rejection_anomalies(decisions: Iterable[dict], now: float) -> list[Anomaly]:
    counts: dict[str, list[int]] = {}
    lo = now - REJECTION_WINDOW_S
    for d in decisions:
        try:
            ts = float(d.get("ts", 0.0))
        except (TypeError, ValueError):
            continue
        if not lo <= ts <= now or not d.get("subject"):
            continue
        c = counts.setdefault(str(d["subject"]), [0, 0])
        c[0] += 1
        c[1] += d.get("decision") == "rejected"
    out = []
    for subject, (total, rejected) in sorted(counts.items()):
        if total >= REJECTION_MIN_SAMPLES and rejected / total > REJECTION_RATE:
            out.append(Anomaly("skill.high_rejection_rate", subject, {
                "rejection_count": rejected, "sample_count": total,
                "rate": round(rejected / total, 4), "window_seconds": REJECTION_WINDOW_S}))
    return out


def _parse_ts(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _oscillation_anomalies(skills_dir: Path, now: float) -> list[Anomaly]:
    out = []
    if not skills_dir.is_dir():
        return out
    for path in sorted(skills_dir.glob("*_config.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        versions = data.get("versions") if isinstance(data, dict) else None
        if not isinstance(versions, list):
            continue
        recent = [v for v in versions if isinstance(v, dict)
                  and (ts := _parse_ts(v.get("timestamp"))) is not None
                  and now - OSCILLATION_WINDOW_S <= ts <= now]
        if len(recent) > OSCILLATION_MAX_CHANGES:
            skill_id = str(recent[-1].get("skill_id") or path.stem[: -len("_config")])
            out.append(Anomaly("skill.config_oscillation", skill_id, {
                "param_name": "config", "change_count": len(recent),
                "window_seconds": OSCILLATION_WINDOW_S}))
    return out


def _confidence_of(event: Any) -> Optional[float]:
    signal = getattr(event, "signal", None)
    if not isinstance(signal, dict):
        return None
    output = signal.get("output")
    value = output.get("confidence") if isinstance(output, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if 0.0 <= value <= 1.0 else None


def _regression_anomalies(events: Iterable[Any], now: float) -> list[Anomaly]:
    recent: dict[str, list[float]] = {}
    before: dict[str, list[float]] = {}
    for ev in events:
        ts = _parse_ts(getattr(ev, "timestamp", None))
        conf = _confidence_of(ev)
        skill = getattr(ev, "skill_id", None)
        if ts is None or conf is None or not skill:
            continue
        if now - CONFIDENCE_RECENT_S <= ts <= now:
            recent.setdefault(skill, []).append(conf)
        elif now - CONFIDENCE_BASELINE_S <= ts < now - CONFIDENCE_RECENT_S:
            before.setdefault(skill, []).append(conf)
    out = []
    for skill in sorted(set(recent) & set(before)):
        a, b = before[skill], recent[skill]
        if len(a) < CONFIDENCE_MIN_SAMPLES or len(b) < CONFIDENCE_MIN_SAMPLES:
            continue
        mean_a, mean_b = sum(a) / len(a), sum(b) / len(b)
        if mean_a - mean_b >= CONFIDENCE_DROP:
            out.append(Anomaly("skill.confidence_regression", skill, {
                "confidence_before": round(mean_a, 4), "confidence_after": round(mean_b, 4),
                "samples_before": len(a), "samples_after": len(b)}))
    return out


def _outcome_success(event: Any) -> Optional[bool]:
    signal = getattr(event, "signal", None)
    if not isinstance(signal, dict):
        return None
    for key in ("success", "passed"):
        value = signal.get(key)
        if isinstance(value, bool):
            return value
    return None


def _stall_anomalies(events: Iterable[Any]) -> list[Anomaly]:
    """``events`` chronological (oldest first); the newest ``STALL_MAX_EVENTS`` per skill count."""
    per_skill: dict[str, list[bool]] = {}
    for ev in events:
        ok = _outcome_success(ev)
        skill = getattr(ev, "skill_id", None)
        if ok is None or not skill:
            continue
        per_skill.setdefault(str(skill), []).append(ok)
    out = []
    for skill, results in sorted(per_skill.items()):
        results = results[-STALL_MAX_EVENTS:]
        size = len(results) // STALL_WINDOWS
        if len(results) < STALL_MIN_EVENTS or size == 0:
            continue
        results = results[len(results) - size * STALL_WINDOWS:]  # drop the oldest remainder
        losses = [1.0 - sum(results[i * size:(i + 1) * size]) / size for i in range(STALL_WINDOWS)]
        spread = max(losses) - min(losses)
        if min(losses) > STALL_MIN_LOSS and spread < STALL_MAX_SPREAD:
            out.append(Anomaly("skill.convergence_stall", skill, {
                "sample_count": size * STALL_WINDOWS, "window_count": STALL_WINDOWS,
                "loss_min": round(min(losses), 4), "loss_max": round(max(losses), 4),
                "loss_spread": round(spread, 4)}))
    return out


def scan(tenant_id: str, *, now: Optional[float] = None) -> list[Anomaly]:
    """Every anomaly that holds right now for ``tenant_id`` (read-only)."""
    from core.learning.event_store import EventStore  # noqa: PLC0415
    from core.learning.feedback_gate import DecisionLedger  # noqa: PLC0415
    from core.learning.learning_events import EventType  # noqa: PLC0415
    from core.paths.tenant import tenant_home  # noqa: PLC0415

    now = datetime.now(timezone.utc).timestamp() if now is None else now
    home = tenant_home(tenant_id)
    found = _rejection_anomalies(DecisionLedger(home / "learning").tail(), now)
    found += _oscillation_anomalies(home / "skills", now)
    since = datetime.fromtimestamp(now - CONFIDENCE_BASELINE_S, tz=timezone.utc)
    events = EventStore(home, tenant_id=tenant_id).query_events(
        tenant_id, event_type=EventType.SKILL_EXECUTED,
        since=(since - timedelta(days=1)).date().isoformat(), limit=20000)
    found += _regression_anomalies(events, now)
    outcomes = EventStore(home, tenant_id=tenant_id).query_events(
        tenant_id, event_type=EventType.OUTCOME, limit=20000)
    found += _stall_anomalies(outcomes)
    return found


__all__ = ["Anomaly", "EVENTS", "scan"]

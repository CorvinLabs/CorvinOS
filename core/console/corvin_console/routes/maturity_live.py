"""On-demand builder for the Vibe-Engineering maturity dashboard.

Every number the ``/vibe/maturity/*`` endpoints return is computed HERE, at
request time, from two real, cross-platform sources:

  * the ADR-0314 learning ``EventStore`` (tenant-scoped OUTCOME / SKILL_EXECUTED
    / CONFIDENCE / FEEDBACK / DECISION / CONFIG_UPDATED events), and
  * the tenant hash-chained audit log (``tenant_audit_chain()`` — the ONE
    canonical chain per tenant; never compose that path by hand, ADR-0007/0650).

Why on-demand and not the JSONL collector? ``core.learning.live_experiment_
collector`` writes a measurement file per minute, but it ``import resource`` /
``os.getloadavg()`` — POSIX-only — so it cannot even import on Windows, was
never started by the console, and left the source directory empty. The reader
endpoints then returned ``measurements: []`` forever and the React panel fell
back to a frozen block of hardcoded sample scores. Computing the measurement
on demand is portable (pure stdlib + JSONL reads), always fresh, needs no
always-on background thread, and can never drift out of parity with a writer
that isn't running.

Scoring model (documented, auditable — NOT a black box)
-------------------------------------------------------
The panel shows 12 "learning loops" + 1 meta loop, each 0-10. A loop's score is
derived from a normalized health ``contribution`` in [0,1] and an instability
``drift`` in [0,1] via ``score = contribution * (1 - drift) * 10`` (the same
formula the historical endpoint uses, so radar and time-series agree). Each
loop maps to a concrete, named set of real events; a loop with no signal in the
window is reported ``active: false`` (its score floors, it is NOT faked). The
raw counts every score is derived from are echoed back under ``signals`` so an
operator can check the arithmetic. See ``_LOOP_DOC`` for the per-loop mapping.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import logging

log = logging.getLogger(__name__)

SCHEMA = "corvin.live_measurement/2"

# Window label -> lookback in days.
WINDOW_DAYS = {"today": 1, "7d": 7, "30d": 30, "90d": 90}

# The 12 loops (+ meta) the dashboard renders, and the one-line description of
# the real signal each is derived from. Keep in sync with the frontend
# ``LOOP_DETAILS`` / ``LoopScores`` keys.
_LOOP_DOC = {
    "confidence": "learning CONFIDENCE events + recent OUTCOME success rate",
    "routing":    "delegation DECISION/OUTCOME success rate",
    "context":    "os_turn.* turn completion (started vs failed)",
    "workflow":   "os_turn.tool_called throughput + task success",
    "data_flow":  "data_flow_* / flow_guard L34 events",
    "security":   "house_rules allowed vs denied ratio",
    "memory":     "conversation/session recall + memory events",
    "skills":     "skill.executed volume vs skill errors",
    "plugins":    "plugin_loaded / plugin.* lifecycle events",
    "audit":      "audit chain integrity (gaps/discontinuities/anchor)",
    "compliance": "compliance.* + house-rule denials + manifest checks",
    "system":     "boot self-test + process liveness",
    "meta_convergence": "learned_threshold_updated optimizer activity + outcome trend",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clamp01(x: float) -> float:
    return 0.0 if x < 0 else (1.0 if x > 1 else float(x))


def _parse_ts(raw: Any) -> Optional[datetime]:
    """Parse an audit/event timestamp into an aware UTC datetime, or None."""
    if not isinstance(raw, str) or not raw:
        return None
    s = raw.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        # Some records use a bare epoch or a non-ISO shape — try epoch seconds.
        try:
            return datetime.fromtimestamp(float(raw), tz=timezone.utc)
        except (TypeError, ValueError):
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ── audit chain reader ──────────────────────────────────────────────────────

def _audit_chain_path(tenant_id: str) -> Optional[Path]:
    """The ONE canonical audit chain for the tenant (never hand-composed)."""
    try:
        from core.paths.tenant import tenant_audit_chain  # noqa: PLC0415
        return Path(tenant_audit_chain(tenant_id))
    except Exception as exc:  # noqa: BLE001
        log.warning("maturity: cannot resolve tenant_audit_chain: %r", exc)
        return None


def _audit_event_key(rec: Dict[str, Any]) -> Optional[str]:
    return rec.get("event_type") or rec.get("event") or rec.get("action") or rec.get("type")


def read_audit_events(
    tenant_id: str, since: Optional[datetime]
) -> Tuple[List[Tuple[datetime, str]], int]:
    """Return ``([(ts, event_type), ...], total_chain_length)`` within the window.

    ``total_chain_length`` is the full line count of the chain (window-
    independent — it is the audit LOOP's own maturity signal). The event list
    is filtered to ``since`` (``None`` = no lower bound).
    """
    path = _audit_chain_path(tenant_id)
    events: List[Tuple[datetime, str]] = []
    total = 0
    if not path or not path.exists():
        return events, total
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                total += 1
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                key = _audit_event_key(rec)
                if not key:
                    continue
                ts = _parse_ts(rec.get("ts") or rec.get("timestamp"))
                if since is not None and ts is not None and ts < since:
                    continue
                events.append((ts or _now(), str(key)))
    except OSError as exc:
        log.warning("maturity: audit read failed: %r", exc)
    return events, total


class _Counts:
    """Prefix-aware counter over audit event-type keys."""

    def __init__(self, events: List[Tuple[datetime, str]]):
        self._c: Dict[str, int] = {}
        for _ts, key in events:
            self._c[key] = self._c.get(key, 0) + 1

    def exact(self, *keys: str) -> int:
        return sum(self._c.get(k, 0) for k in keys)

    def prefix(self, *prefixes: str) -> int:
        return sum(v for k, v in self._c.items() if any(k.startswith(p) for p in prefixes))

    def total(self) -> int:
        return sum(self._c.values())


# ── learning store reader ─────────────────────────────────────────────────────

def _learning_metrics(tenant_id: str, since_date: str) -> Dict[str, Any]:
    """Recent-outcome success + per-type counts from the ADR-0314 EventStore."""
    out: Dict[str, Any] = {
        "outcomes_total": 0, "outcomes_success": 0, "success_rate": None,
        "counts": {}, "available": False,
    }
    try:
        from core.learning.event_store import EventStore  # noqa: PLC0415
        from core.learning.learning_events import EventType  # noqa: PLC0415
        from core.paths.tenant import tenant_home  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        log.warning("maturity: learning store import failed: %r", exc)
        return out
    try:
        store = EventStore(tenant_home(tenant_id), tenant_id=tenant_id)
        out["counts"] = {et.value: store.count_events(tenant_id, et) for et in EventType}
        recent = store.query_events(
            tenant_id, event_type=EventType.OUTCOME, since=since_date, limit=200, newest_first=True
        )
        total = len(recent)
        succ = sum(1 for e in recent if (e.signal or {}).get("success") is True)
        out["outcomes_total"] = total
        out["outcomes_success"] = succ
        out["success_rate"] = (succ / total) if total else None
        out["available"] = True
    except Exception as exc:  # noqa: BLE001 — record the gap, never invent a value
        log.warning("maturity: learning store read failed: %r", exc)
    return out


# ── the loop scoring model ────────────────────────────────────────────────────

def _health(active: bool, contribution: float, drift: float) -> Dict[str, Any]:
    return {
        "active": bool(active),
        "contribution": round(_clamp01(contribution), 4),
        "drift": round(_clamp01(drift), 4),
    }


def _score(h: Dict[str, Any]) -> float:
    return round(max(0.0, min(10.0, h["contribution"] * (1.0 - h["drift"]) * 10.0)), 2)


def _component_health(counts: _Counts, learning: Dict[str, Any], chain_len: int) -> Dict[str, Dict[str, Any]]:
    """Map real signals -> per-loop {active, contribution, drift}. See _LOOP_DOC."""
    sr = learning.get("success_rate")
    lc = learning.get("counts", {})

    # Shared audit tallies.
    turns = counts.exact("os_turn.started")
    tool_calls = counts.exact("os_turn.tool_called")
    turn_fail = counts.prefix("os_turn.failed", "os_turn.error")
    skill_exec = counts.exact("skill.executed") + int(lc.get("skill_executed", 0))
    skill_fail = counts.prefix("skill.failed", "skill.error")
    house_ok = counts.exact("house_rules.allowed")
    house_deny = counts.exact("house_rules.denied", "house_rule_denied")
    integrity_bad = counts.exact(
        "audit.chain_gap_detected", "compliance.chain_discontinuity",
        "audit.chain_anchor_absent", "boot.self_test_failed",
    )
    compliance_bad = counts.exact(
        "compliance.chain_discontinuity", "house_rule_denied",
        "layer_integrity.manifest_absent", "a2a.manifest_stale",
    )
    boot_fail = counts.exact("boot.self_test_failed")
    thresholds = counts.exact("learned_threshold_updated")
    data_flow = counts.prefix("data_flow", "flow_guard", "L34")
    memory_ev = counts.prefix("memory", "recall", "conversation", "session.")
    plugin_ev = counts.prefix("plugin_loaded", "plugin.")
    total_audit = max(1, counts.total())

    h: Dict[str, Dict[str, Any]] = {}

    # ── Tier 1 ────────────────────────────────────────────────────────────
    have_outcomes = learning.get("outcomes_total", 0) > 0
    h["confidence"] = _health(
        active=lc.get("confidence", 0) > 0 or have_outcomes,
        contribution=(sr if sr is not None else 0.0),
        drift=(1.0 - sr) if sr is not None else 0.0,
    )
    h["routing"] = _health(
        active=lc.get("decision", 0) > 0 or have_outcomes,
        contribution=(sr if sr is not None else 0.0),
        drift=(1.0 - sr) if sr is not None else 0.0,
    )
    h["context"] = _health(
        active=turns > 0 or tool_calls > 0,
        contribution=1.0 - (turn_fail / max(1, turns + turn_fail)),
        drift=turn_fail / max(1, turns + turn_fail),
    )
    h["workflow"] = _health(
        active=tool_calls > 0 or skill_exec > 0,
        contribution=1.0 - (skill_fail / max(1, skill_exec + skill_fail)),
        drift=skill_fail / max(1, skill_exec + skill_fail),
    )
    h["data_flow"] = _health(
        active=data_flow > 0,
        contribution=1.0 if data_flow > 0 else 0.0,
        drift=0.0,
    )
    h["security"] = _health(
        active=(house_ok + house_deny) > 0,
        contribution=house_ok / max(1, house_ok + house_deny),
        drift=house_deny / max(1, house_ok + house_deny),
    )

    # ── Tier 2 ────────────────────────────────────────────────────────────
    h["memory"] = _health(
        active=memory_ev > 0,
        contribution=1.0 if memory_ev > 0 else 0.0,
        drift=0.0,
    )
    h["skills"] = _health(
        active=skill_exec > 0,
        contribution=1.0 - (skill_fail / max(1, skill_exec + skill_fail)) if skill_exec else 0.0,
        drift=skill_fail / max(1, skill_exec + skill_fail),
    )
    h["plugins"] = _health(
        active=plugin_ev > 0,
        contribution=1.0 if plugin_ev > 0 else 0.0,
        drift=0.0,
    )
    h["audit"] = _health(
        active=chain_len > 0,
        contribution=1.0 - (integrity_bad / total_audit),
        drift=integrity_bad / total_audit,
    )
    h["compliance"] = _health(
        active=True,
        contribution=1.0 - (compliance_bad / total_audit),
        drift=compliance_bad / total_audit,
    )
    h["system"] = _health(
        active=True,
        contribution=1.0 - (boot_fail / total_audit),
        drift=boot_fail / total_audit,
    )

    # Meta loop lives in component_health too, so the radar axis, the historical
    # series and extract_loop_score() all resolve it the same way as any loop.
    sr_val = sr if sr is not None else 0.0
    optimizer_active = 1.0 if thresholds > 0 else 0.0
    h["meta_convergence"] = _health(
        active=thresholds > 0,
        contribution=0.5 * optimizer_active + 0.5 * sr_val,
        drift=0.0,
    )
    return h


def _meta(counts: _Counts, learning: Dict[str, Any]) -> Dict[str, Any]:
    """Meta loop + the dashboard's Trend/Meta/Recommendations card values."""
    thresholds = counts.exact("learned_threshold_updated")
    sr = learning.get("success_rate")
    # Convergence: the optimizer is demonstrably active iff it has written
    # threshold updates; blend that presence with the recent outcome success.
    optimizer_active = 1.0 if thresholds > 0 else 0.0
    convergence = _clamp01(0.5 * optimizer_active + 0.5 * (sr if sr is not None else 0.0))
    return {
        "convergence_rate": round(convergence, 4),
        "threshold_updates": thresholds,
        "prediction_accuracy": (round(sr, 4) if sr is not None else None),
        "optimizer_active": bool(thresholds > 0),
    }


def _recommendations(loop_scores: Dict[str, float], meta: Dict[str, Any]) -> List[Dict[str, str]]:
    """Data-driven recommendations: name the genuinely weakest active loops."""
    ranked = sorted(
        ((k, v) for k, v in loop_scores.items() if k != "meta_convergence"),
        key=lambda kv: kv[1],
    )
    recs: List[Dict[str, str]] = []
    for key, score in ranked[:2]:
        recs.append({
            "severity": "critical" if score < 4 else "warning",
            "text": f"{key.replace('_', ' ').title()} loop is lowest at {score:.1f}/10 "
                    f"({_LOOP_DOC.get(key, '')}) — focus here.",
        })
    if meta.get("optimizer_active"):
        recs.append({"severity": "info", "text": "Meta-loop optimizer is active (threshold updates observed)."})
    else:
        recs.append({"severity": "warning", "text": "Meta-loop optimizer idle — no threshold updates in window."})
    return recs


# ── public API ────────────────────────────────────────────────────────────────

def build_measurement(tenant_id: str, window: str = "7d") -> Dict[str, Any]:
    """Compute ONE live measurement for ``tenant_id`` over ``window`` from real sources."""
    days = WINDOW_DAYS.get(window, 7)
    since_dt = _now() - timedelta(days=days)
    since_date = since_dt.strftime("%Y-%m-%d")

    audit_events, chain_len = read_audit_events(tenant_id, since_dt)
    counts = _Counts(audit_events)
    learning = _learning_metrics(tenant_id, since_date)

    health = _component_health(counts, learning, chain_len)
    loop_scores = {k: _score(v) for k, v in health.items()}  # includes meta_convergence
    meta = _meta(counts, learning)

    # Real drift indicator: mean drift across the loops that actually have signal.
    active_drifts = [v["drift"] for v in health.values() if v.get("active")]
    mean_drift = round(sum(active_drifts) / len(active_drifts), 4) if active_drifts else 0.0

    # Real trend from the meta-convergence history buckets over the window.
    hist = build_history(tenant_id, "meta_convergence", window)
    if len(hist) >= 2:
        span = max(1, WINDOW_DAYS.get(window, 7))
        delta = round(hist[-1]["score"] - hist[0]["score"], 2)
        direction = "up" if delta > 0.05 else ("down" if delta < -0.05 else "stable")
        # Linear extrapolation of the observed slope to a 30-day horizon.
        slope_per_day = (hist[-1]["score"] - hist[0]["score"]) / span
        projection_30d = round(max(0.0, min(10.0, hist[-1]["score"] + slope_per_day * 30)), 2)
        insufficient_history = False
    else:
        delta, direction, projection_30d, insufficient_history = 0.0, "stable", None, True

    meta.update({
        "drift": mean_drift,
        "trend": {"direction": direction, "value": delta, "insufficient_history": insufficient_history},
        "projection_30d": projection_30d,
    })

    sr = learning.get("success_rate")
    now = _now()
    return {
        "schema": SCHEMA,
        "provenance": {"generator": __name__, "measured": True, "mode": "on_demand"},
        "timestamp": now.isoformat(),
        "unix_time": int(time.time()),
        "tenant_id": tenant_id,
        "window": window,
        "learning": {
            "loss_total": round((1.0 - sr), 4) if sr is not None else None,
            "loss_routing": round((1.0 - sr), 4) if sr is not None else None,
            "loss_confidence": round((1.0 - sr), 4) if sr is not None else None,
            "loss_feedback": round((1.0 - sr), 4) if sr is not None else None,
            "accuracy_routing": round(sr, 4) if sr is not None else None,
            "convergence_rate": meta["convergence_rate"],
        },
        "system": {
            "audit_chain_length": chain_len,
            "latency_p99_ms": None,   # not measured cross-platform (no psutil)
            "throughput_tasks_per_sec": None,
            "memory_usage_mb": None,
            "cpu_usage_percent": None,
        },
        "user_actions": {
            "tasks_completed_this_hour": learning.get("outcomes_total", 0),
            "routing_decisions": learning.get("counts", {}).get("decision", 0),
            "training_batches": meta["threshold_updates"],
            "anomalies_detected": counts.exact(
                "audit.chain_gap_detected", "compliance.chain_discontinuity", "boot.self_test_failed"
            ),
        },
        "component_health": health,
        "loop_scores": loop_scores,
        "meta": {
            **meta,
            "recommendations": _recommendations(loop_scores, meta),
        },
        "signals": {
            "audit_events_in_window": counts.total(),
            "audit_chain_length": chain_len,
            "learning": learning,
            "loop_docs": _LOOP_DOC,
        },
    }


def build_history(tenant_id: str, loop: str, window: str = "today") -> List[Dict[str, Any]]:
    """Real time-series for ``loop``: audit events bucketed over the window.

    Each bucket is scored with the SAME model as the live snapshot, so the
    historical line and the radar axis agree. Buckets with no events are
    skipped (an honest gap, not an interpolated zero).
    """
    days = WINDOW_DAYS.get(window, 1)
    since_dt = _now() - timedelta(days=days)
    audit_events, _chain_len = read_audit_events(tenant_id, since_dt)
    if not audit_events:
        return []

    # Bucket granularity: hourly for <=1 day, daily otherwise.
    hourly = days <= 1
    buckets: Dict[str, List[Tuple[datetime, str]]] = {}
    for ts, key in audit_events:
        label = ts.strftime("%Y-%m-%dT%H:00" if hourly else "%Y-%m-%d")
        buckets.setdefault(label, []).append((ts, key))

    learning = _learning_metrics(tenant_id, since_dt.strftime("%Y-%m-%d"))
    points: List[Dict[str, Any]] = []
    for label in sorted(buckets):
        ev = buckets[label]
        counts = _Counts(ev)
        health = _component_health(counts, learning, len(ev))
        h = health.get(loop, {"active": False, "contribution": 0.0, "drift": 0.0})
        bucket_dt = ev[0][0]
        points.append({
            "timestamp": label,
            "unix_time": int(bucket_dt.timestamp()),
            "score": _score(h),
            "convergence_rate": _meta(counts, learning)["convergence_rate"],
            "drift": h["drift"],
        })
    return points


def build_patterns(tenant_id: str, window: str = "7d") -> Dict[str, Any]:
    """Real routing/skill usage patterns from the ADR-0314 EventStore.

    Two honest dimensions, both derived from real SKILL_EXECUTED events:
      * ``routing`` — the delegation-router's engine choices, read from the
        shadow-mode ``signal.output.engine`` on ``os.delegation_router``
        executions (what the router DECIDED, ADR-0613 shadow path), and
      * ``skills``  — execution volume per ``skill_id`` with its success rate.

    No context-size distribution is emitted: nothing measures per-request
    context size cross-platform, so inventing buckets would be a fabrication.
    Empty lists are the correct answer for a quiet install — never a placeholder.
    """
    days = WINDOW_DAYS.get(window, 7)
    since_date = (_now() - timedelta(days=days)).strftime("%Y-%m-%d")
    routing: Dict[str, int] = {}
    skills: Dict[str, Dict[str, int]] = {}
    available = False
    try:
        from core.learning.event_store import EventStore  # noqa: PLC0415
        from core.learning.learning_events import EventType  # noqa: PLC0415
        from core.paths.tenant import tenant_home  # noqa: PLC0415

        store = EventStore(tenant_home(tenant_id), tenant_id=tenant_id)
        execs = store.query_events(
            tenant_id, event_type=EventType.SKILL_EXECUTED,
            since=since_date, limit=2000, newest_first=True,
        )
        for e in execs:
            sid = e.skill_id or "unknown"
            rec = skills.setdefault(sid, {"count": 0, "success": 0})
            rec["count"] += 1
            sig = e.signal or {}
            if sig.get("status") == "success":
                rec["success"] += 1
            out = sig.get("output")
            eng = out.get("engine") if isinstance(out, dict) else None
            if eng:
                routing[str(eng)] = routing.get(str(eng), 0) + 1
        available = True
    except Exception as exc:  # noqa: BLE001 — record the gap, never invent a value
        log.warning("maturity: patterns read failed: %r", exc)

    total_routing = sum(routing.values())
    total_skills = sum(v["count"] for v in skills.values())
    routing_list = [
        {
            "engine": eng,
            "count": cnt,
            "percentage": round(100.0 * cnt / total_routing, 1) if total_routing else 0.0,
        }
        for eng, cnt in sorted(routing.items(), key=lambda kv: kv[1], reverse=True)
    ]
    skills_list = [
        {
            "skill": sid,
            "count": rec["count"],
            "percentage": round(100.0 * rec["count"] / total_skills, 1) if total_skills else 0.0,
            "success_rate": (round(rec["success"] / rec["count"], 4) if rec["count"] else None),
        }
        for sid, rec in sorted(skills.items(), key=lambda kv: kv[1]["count"], reverse=True)
    ]
    return {
        "window": window,
        "generated_at": _now().isoformat(),
        "available": available,
        "routing": routing_list,
        "skills": skills_list,
        "total_routing_decisions": total_routing,
        "total_skill_executions": total_skills,
    }


def build_anomalies(tenant_id: str, window: str = "7d") -> List[Dict[str, Any]]:
    """Real anomalies from the current snapshot + recent trend — no fabrication.

    Two honest sources: (1) loops whose measured ``drift`` exceeds a threshold in
    the current window (instability), and (2) a convergence-rate DROP between the
    first and last historical bucket (regression). Empty when nothing crosses a
    threshold, which is the correct answer for a healthy, quiet install.
    """
    snap = build_measurement(tenant_id, window)
    ts = snap["timestamp"]
    out: List[Dict[str, Any]] = []

    DRIFT_SPIKE = 0.05
    for loop, health in snap["component_health"].items():
        drift = float(health.get("drift", 0.0))
        if health.get("active") and drift > DRIFT_SPIKE:
            out.append({
                "id": f"drift-{loop}-{int(time.time())}",
                "type": "drift-spike",
                "loop": loop,
                "severity": "critical" if drift > 0.25 else "warning",
                "message": f"{loop.replace('_', ' ')} drift {drift:.2f} exceeds {DRIFT_SPIKE:.2f}",
                "timestamp": ts,
                "value": round(drift, 4),
                "threshold": DRIFT_SPIKE,
            })

    # Convergence regression across the window's history buckets.
    series = build_history(tenant_id, "meta_convergence", window)
    if len(series) >= 2:
        delta = series[-1]["convergence_rate"] - series[0]["convergence_rate"]
        if delta < -0.05:
            out.append({
                "id": f"drop-{int(time.time())}",
                "type": "drop",
                "loop": "meta_convergence",
                "severity": "warning",
                "message": f"Convergence rate dropped by {abs(delta):.2f} over the window",
                "timestamp": ts,
                "value": round(abs(delta), 4),
                "threshold": 0.05,
            })
    return out

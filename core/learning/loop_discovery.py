"""Discover the learning loops this install is ACTUALLY running (ADR-0907).

ADR-0906 makes a plugin's ``learning_loops:`` manifest section the way a
*plugin* declares a loop. Nothing declares the loops CorvinOS runs itself —
the OS skills whose executions, outcomes and operator feedback are already in
the tenant's event store, and the CEL pipeline stages whose confidence the
outcome loop grades after every context-engineered turn. Those loops are real,
they have real numbers, and before this module the Learning Loops panel could
not see a single one of them.

**Every number here is read from a store some other part of the system already
writes.** Nothing is synthesised, defaulted or smoothed:

* OS-skill loops come from ``core.learning.event_store.EventStore`` — the same
  store, constructed the same way, that ``GET /v1/console/learning/status``
  reports from. Two readers of one store cannot disagree; two readers of two
  stores eventually do.
* CEL stage loops come from ``ce_stage_grades.json``, the store
  ``core.learning.earned_tree`` projects for the Learnings tree, written by the
  outcome-feedback loop (ADR-0269 G4) and operator overrides (ADR-0285 G3).

**A health score is reported only where something measured it.**
``health_score`` is ``None`` when a loop has produced no outcome and no grade,
and ``health_basis`` always names what the number is counted from. A loop that
has merely executed 2 294 times has no health score — an execution is not an
outcome, and a neutral 0.5 in that slot is a fabricated measurement wearing the
same field name as a real one (ADR-0763).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Upper bound on the events read per discovery pass. The store's own query API
# windows from the NEWEST end, so hitting this bound means the oldest events
# fall outside the window — which is why ``scanned``/``truncated`` travel with
# the result instead of a total that silently describes a different period.
_MAX_SCAN = 50_000

# The CEL pipeline stages, in pipeline order. Kept in step with
# ``core.learning.earned_tree._STAGES`` — same ids, same order, same labels.
_CEL_STAGES: tuple[tuple[str, str], ...] = (
    ("memory", "Memory retrieval"),
    ("graph", "ADR graph traversal"),
    ("skill", "Skill injection"),
    ("approach_synthesis", "Approach synthesis"),
    ("blocker_id", "Blocker identification"),
    ("llm_synthesis", "LLM synthesis"),
    ("toolforge", "Tool forging"),
    ("skillforge", "Skill forging"),
)


@dataclass(frozen=True)
class CoreLoopObservation:
    """One learning loop CorvinOS runs itself, as measured from its own store."""

    loop_id: str
    owner: str                      # the skill / stage the loop belongs to
    origin: str                     # "os_skill" | "cel_stage"
    description: str
    event_source: str               # where these numbers were read from
    event_count_total: int
    event_count_7d: int
    last_event_ts: Optional[datetime]
    health_score: Optional[float]   # None when nothing measured one
    health_basis: str               # what the score counts, "" when there is none
    outcome_total: int
    outcome_success: int
    feedback_count: int

    @property
    def status(self) -> str:
        """`active` / `dormant` / `stale` / `degrading` / `unknown`.

        Mirrors ``learning_loop_service.compute_status`` thresholds: a measured
        score below 0.5 is degrading regardless of age, otherwise the age of the
        last event decides — under 24h active, under 7d dormant, beyond that
        stale.

        ``unknown`` exists because one real source cannot support the others'
        claim: the CEL stage-grade records carry no timestamp, so the age of the
        newest grade is not knowable from the store. Every age-based status
        would be an assertion about a number that was never read. "Stale" is a
        statement that a loop stopped emitting; a store that does not date its
        records cannot be the evidence for it.
        """
        if self.health_score is not None and self.health_score < 0.5:
            return "degrading"
        if self.last_event_ts is None:
            return "unknown"
        age_h = (_now() - self.last_event_ts).total_seconds() / 3600.0
        if age_h < 24:
            return "active"
        if age_h < 24 * 7:
            return "dormant"
        return "stale"


@dataclass(frozen=True)
class DiscoveryResult:
    """Observations plus the window they were counted over."""

    loops: list[CoreLoopObservation]
    scanned: int
    truncated: bool


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(raw: Any) -> Optional[datetime]:
    """Parse an event timestamp to an aware UTC datetime, or None."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ── OS-skill loops, from the tenant event store ─────────────────────────────


def _describe_os_skill(skill_id: str) -> str:
    """A plain description of what the loop does, or a generic one."""
    known = {
        "os.delegation_router": (
            "Routes a task to a worker engine and learns from how the task "
            "finished (ADR-0613, shadow mode — the decision is audited, the "
            "bundled answer still stands)."
        ),
        "os.capabilities": "Resolves which capabilities a turn may use.",
        "os.headless_mode": "Decides whether a turn runs without an operator present.",
        "os.plugin_health_monitoring": "Watches installed plugins for health regressions.",
        "os.plugin_builder": "Generates plugin scaffolding from a request.",
        "os.workflow_optimizer": "Learns execution chains from finished workflows.",
        "os.vibe_engineering": "Feeds the Learnings dashboard.",
    }
    return known.get(skill_id, f"OS skill {skill_id}.")


def _os_skill_loops(tenant_id: str) -> tuple[list[CoreLoopObservation], int, bool]:
    """Group the tenant's learning events by skill into one loop each."""
    try:
        from core.learning.event_store import EventStore
        from core.paths.tenant import tenant_home
    except Exception as exc:  # noqa: BLE001
        logger.warning("learning event store unavailable: %s", exc)
        return [], 0, False

    try:
        store = EventStore(tenant_home(tenant_id), tenant_id=tenant_id)
        events = store.query_events(tenant_id, limit=_MAX_SCAN)
    except Exception as exc:  # noqa: BLE001
        logger.warning("learning event query failed: %s", exc)
        return [], 0, False

    scanned = len(events)
    truncated = scanned >= _MAX_SCAN
    cutoff = _now() - timedelta(days=7)

    agg: dict[str, dict[str, Any]] = {}
    for ev in events:
        skill_id = getattr(ev, "skill_id", None)
        if not skill_id:
            continue
        slot = agg.setdefault(
            skill_id,
            {"total": 0, "recent": 0, "last": None, "out": 0, "succ": 0, "fb": 0},
        )
        slot["total"] += 1

        ts = _parse_ts(getattr(ev, "timestamp", None))
        if ts is not None:
            if slot["last"] is None or ts > slot["last"]:
                slot["last"] = ts
            if ts >= cutoff:
                slot["recent"] += 1

        etype = getattr(ev, "event_type", None)
        etype = getattr(etype, "value", etype)
        if etype == "outcome":
            slot["out"] += 1
            signal = getattr(ev, "signal", None) or {}
            if signal.get("success") is True:
                slot["succ"] += 1
        elif etype == "feedback":
            slot["fb"] += 1

    loops: list[CoreLoopObservation] = []
    for skill_id, slot in agg.items():
        out, succ = slot["out"], slot["succ"]
        if out:
            score: Optional[float] = succ / out
            basis = f"{succ} of {out} recorded task outcomes succeeded (all time)"
        else:
            score, basis = None, ""
        loops.append(
            CoreLoopObservation(
                loop_id=f"core:{skill_id}",
                owner=skill_id,
                origin="os_skill",
                description=_describe_os_skill(skill_id),
                event_source="learning event store (learning/events/*.jsonl)",
                event_count_total=slot["total"],
                event_count_7d=slot["recent"],
                last_event_ts=slot["last"],
                health_score=score,
                health_basis=basis,
                outcome_total=out,
                outcome_success=succ,
                feedback_count=slot["fb"],
            )
        )
    return loops, scanned, truncated


# ── CEL stage loops, from the stage-grade store ─────────────────────────────


def _cel_stage_loops(tenant_id: str) -> list[CoreLoopObservation]:
    """One loop per CEL pipeline stage, scored from its earned grades."""
    try:
        from corvin_operator.forge.forge.paths import tenant_global_dir

        path = Path(tenant_global_dir(tenant_id)) / "ce_stage_grades.json"
        raw = json.loads(path.read_text("utf-8")) if path.is_file() else {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("CEL stage grade store unreadable: %s", exc)
        return []

    if not isinstance(raw, dict):
        return []

    loops: list[CoreLoopObservation] = []
    for stage_id, label in _CEL_STAGES:
        slot = raw.get(stage_id)
        if not isinstance(slot, dict):
            continue
        grades = slot.get("grades")
        grades = grades if isinstance(grades, list) else []
        scores = [
            float(g["score"])
            for g in grades
            if isinstance(g, dict)
            and isinstance(g.get("score"), (int, float))
            and not isinstance(g.get("score"), bool)
        ]
        if not scores:
            continue

        by_loop = sum(
            1 for g in grades if isinstance(g, dict) and g.get("grader") == "__loop__"
        )
        by_operator = len(scores) - by_loop
        parts = []
        if by_loop:
            parts.append(f"{by_loop} graded by the outcome loop")
        if by_operator:
            parts.append(f"{by_operator} graded by the operator")

        loops.append(
            CoreLoopObservation(
                loop_id=f"core:cel.{stage_id}",
                owner=f"cel.{stage_id}",
                origin="cel_stage",
                description=(
                    f"{label} — a stage of the context-engineering pipeline; its "
                    "confidence is earned from the outcome of every turn that ran it."
                ),
                event_source="CEL stage-grade store (ce_stage_grades.json)",
                event_count_total=len(scores),
                # The grade records carry no timestamp, so no 7-day slice of them
                # can be computed. Reporting the total here would label an
                # all-time count as a weekly one.
                event_count_7d=0,
                last_event_ts=None,
                health_score=sum(scores) / len(scores),
                health_basis=f"mean of {len(scores)} grades ({', '.join(parts)})",
                outcome_total=0,
                outcome_success=0,
                feedback_count=by_operator,
            )
        )
    return loops


# ── Entry point ─────────────────────────────────────────────────────────────


def discover_core_loops(tenant_id: str) -> DiscoveryResult:
    """Every learning loop this install runs itself, measured from its stores.

    Never raises: a source that cannot be read contributes nothing and logs.
    An empty result means the stores hold nothing, not that discovery failed.
    """
    loops, scanned, truncated = _os_skill_loops(tenant_id)
    loops.extend(_cel_stage_loops(tenant_id))
    loops.sort(key=lambda loop: (-loop.event_count_total, loop.loop_id))
    return DiscoveryResult(loops=loops, scanned=scanned, truncated=truncated)


# ── Per-loop detail reads ───────────────────────────────────────────────────


@dataclass(frozen=True)
class CoreLoopEvent:
    """One recorded event of a core loop, content-free."""

    timestamp: datetime
    event_type: str
    skill_id: str
    outcome: Optional[str]      # "success" / "failure" for outcome events
    signal: Optional[str]       # short, non-PII summary of the signal


@dataclass(frozen=True)
class CoreLoopTrendPoint:
    """One day of a core loop's activity."""

    date: str                   # YYYY-MM-DD
    event_count: int
    health_score: Optional[float]   # daily outcome success rate, None when no outcome that day


def core_loop_owner(loop_id: str) -> Optional[str]:
    """The skill/stage a ``core:`` loop id names, or None if it is not one."""
    if not loop_id.startswith("core:"):
        return None
    return loop_id[len("core:"):] or None


def _signal_summary(signal: Any) -> Optional[str]:
    """A short, content-free rendering of an event signal.

    Only a fixed allowlist of scalar fields is read. The signal dict is written
    by many emitters and is NOT guaranteed content-free, so rendering it whole
    would be the PII leak the audit rules forbid.
    """
    if not isinstance(signal, dict):
        return None
    allowed = ("status", "engine", "duration_ms", "exit_code", "task_type")
    parts = [
        f"{k}={signal[k]}"
        for k in allowed
        if isinstance(signal.get(k), (str, int, float, bool))
    ]
    return ", ".join(parts) or None


def core_loop_events(tenant_id: str, loop_id: str, limit: int = 100) -> list[CoreLoopEvent]:
    """The most recent recorded events of one core loop, newest last.

    Empty for a CEL stage loop: its grade records carry no timestamp, so they
    cannot be presented as a dated event log without inventing the dates.
    """
    owner = core_loop_owner(loop_id)
    if not owner or owner.startswith("cel."):
        return []

    try:
        from core.learning.event_store import EventStore
        from core.paths.tenant import tenant_home

        store = EventStore(tenant_home(tenant_id), tenant_id=tenant_id)
        events = store.query_events(tenant_id, skill_id=owner, limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("core loop event read failed for %s: %s", loop_id, exc)
        return []

    rows: list[CoreLoopEvent] = []
    for ev in events:
        ts = _parse_ts(getattr(ev, "timestamp", None))
        if ts is None:
            continue
        etype = getattr(ev, "event_type", None)
        etype = getattr(etype, "value", etype)
        signal = getattr(ev, "signal", None) or {}
        outcome = None
        if etype == "outcome":
            outcome = "success" if signal.get("success") is True else "failure"
        rows.append(
            CoreLoopEvent(
                timestamp=ts,
                event_type=str(etype or "unknown"),
                skill_id=owner,
                outcome=outcome,
                signal=_signal_summary(signal),
            )
        )
    return rows


def core_loop_trend(tenant_id: str, loop_id: str, days: int = 7) -> list[CoreLoopTrendPoint]:
    """Daily activity for one core loop over ``days``, oldest first.

    ``health_score`` is the day's outcome success rate and is ``None`` on a day
    that recorded no outcome — a day with 40 executions and no outcome measured
    nothing, and carrying the previous day's score forward would draw a line
    through data that does not exist.

    Empty for a CEL stage loop (undated grade records).
    """
    owner = core_loop_owner(loop_id)
    if not owner or owner.startswith("cel."):
        return []

    try:
        from core.learning.event_store import EventStore
        from core.paths.tenant import tenant_home

        store = EventStore(tenant_home(tenant_id), tenant_id=tenant_id)
        events = store.query_events(tenant_id, skill_id=owner, limit=_MAX_SCAN)
    except Exception as exc:  # noqa: BLE001
        logger.warning("core loop trend read failed for %s: %s", loop_id, exc)
        return []

    start = (_now() - timedelta(days=days - 1)).date()
    buckets: dict[str, dict[str, int]] = {}
    for offset in range(days):
        key = (start + timedelta(days=offset)).isoformat()
        buckets[key] = {"n": 0, "out": 0, "succ": 0}

    for ev in events:
        ts = _parse_ts(getattr(ev, "timestamp", None))
        if ts is None:
            continue
        key = ts.date().isoformat()
        slot = buckets.get(key)
        if slot is None:
            continue
        slot["n"] += 1
        etype = getattr(ev, "event_type", None)
        etype = getattr(etype, "value", etype)
        if etype == "outcome":
            slot["out"] += 1
            if (getattr(ev, "signal", None) or {}).get("success") is True:
                slot["succ"] += 1

    return [
        CoreLoopTrendPoint(
            date=key,
            event_count=slot["n"],
            health_score=(slot["succ"] / slot["out"]) if slot["out"] else None,
        )
        for key, slot in sorted(buckets.items())
    ]

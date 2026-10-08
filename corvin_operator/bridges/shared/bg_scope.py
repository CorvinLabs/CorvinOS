"""Background-child scope tracking (ADR-2236, PLAN-0938).

A *scope* is one user-visible unit of work (a bridge turn, a ``/task``, a console
turn). The claude CLI announces its background children — ``Bash
run_in_background``, ``Monitor``, a background ``Agent`` — with deterministic
``system`` events and keeps the process alive until every child has ended,
emitting one ``result`` per wake-up. Measured on CLI 2.1.294; raw captures live
under ``tests/fixtures/bgscope``.

This module is the *sensor side*: a pure reducer (``ScopeTracker``) that turns
those events into a set of open children plus transitions. It does no I/O; the
only side effect in the file is ``emit_audit`` (best effort, never raises).

Rules it enforces (ADR-2236):

* D1  the CLI's own events are the source of truth; ``background_tasks_changed``
      is a FULL snapshot and heals any missed delta.
* D3  ``classify_result`` separates the user's own turn from a wake-up result.
* D9  the sub-agent ``prompt`` and the child's ``output_file`` are never kept;
      only the (model-authored) ``description`` is held in memory and must be
      passed through ``safe_description`` before it leaves the process.

Unknown subtypes and fields are ignored, never raised on: the CLI event schema
is not versioned, so the parser is tolerant and the contract test
(``test_bg_scope_contract.py``) is what notices drift.
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

# --- states ---------------------------------------------------------------

RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
UNKNOWN = "unknown"          # ended, but not reported as completed/failed (or healed)
TERMINAL = frozenset({COMPLETED, FAILED, UNKNOWN})

# Statuses the CLI uses for a child that is still going.
_LIVE_STATUSES = frozenset({"running", "pending", "in_progress", "started", ""})

# --- tunables -------------------------------------------------------------

#: A child that is missing from a snapshot is only declared gone when it has been
#: tracked for at least this long — a snapshot can race ahead of ``task_started``.
SNAPSHOT_GRACE_S = 1.0
#: Bound for the tool_use_id -> tool name map (one entry per tool call of a turn).
_TOOL_MAP_CAP = 512
DESCRIPTION_MAX = 80

_EXIT_RE = re.compile(r"exit code\s+(-?\d+)", re.IGNORECASE)
_TOOL_KIND = {
    "bash": "bash", "powershell": "bash",
    "monitor": "monitor",
    "agent": "agent", "task": "agent",
    "workflow": "workflow",
}
_TYPE_KIND = {"local_bash": "bash", "local_agent": "agent"}


@dataclass(frozen=True)
class Child:
    task_id: str
    kind: str                       # bash | monitor | agent | workflow | other
    task_type: str                  # raw CLI task_type (local_bash, local_agent, ...)
    description: str                # RAW, model-authored; scrub before it leaves (D9)
    started_at: float
    state: str = RUNNING
    ended_at: float | None = None
    exit_code: int | None = None
    run_id: str = ""


@dataclass(frozen=True)
class Transition:
    kind: str                       # child_started | child_finished | healed | all_children_done
    child: Child | None
    children_open: int


def parse_exit_code(summary: str | None) -> int | None:
    m = _EXIT_RE.search(summary or "")
    return int(m.group(1)) if m else None


def _terminal_state(status: Any) -> str | None:
    """Map a CLI status to a terminal state; None when the child is still live."""
    s = str(status or "").strip().lower()
    if s in _LIVE_STATUSES:
        return None
    if s == "completed":
        return COMPLETED
    if s == "failed":
        return FAILED
    return UNKNOWN


def safe_description(text: str, limit: int = DESCRIPTION_MAX) -> str:
    """Single line, no control characters, no e-mail/secret-looking tokens, cut to *limit*.

    A child's description is model-authored free text (ADR-2236 D9). This is the
    one function every status line, audit field or voice summary must use.
    """
    t = re.sub(r"[\x00-\x1f\x7f]+", " ", str(text or ""))
    t = re.sub(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", "<email>", t)
    t = re.sub(r"\b(?:sk|pk|ghp|gho|xox[abp])[-_][A-Za-z0-9_-]{12,}\b", "<secret>", t)
    t = re.sub(r"\b[A-Za-z0-9_-]{32,}\b", "<token>", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"


class ScopeTracker:
    """Reducer over the CLI's background-task events for ONE scope."""

    def __init__(self, scope_id: str = "", tenant_id: str = "_default") -> None:
        self.scope_id = scope_id
        self.tenant_id = tenant_id
        self._children: dict[str, Child] = {}
        # task_updated reports an end WITHOUT the exit code; task_notification (the
        # authoritative report) follows and carries it. The first is held here.
        self._pending_end: dict[str, tuple[str, float]] = {}
        # A snapshot usually names a child one event BEFORE task_started, which is the
        # event that identifies its tool (Monitor vs Bash). Such a child is announced
        # only when task_started enriches it, or on the next event / flush().
        self._unannounced: dict[str, None] = {}
        self._tool_names: dict[str, str] = {}
        self.results_seen = 0
        self.wakeups = 0
        self.peak_open = 0

    # -- inputs -----------------------------------------------------------

    def note_tool_use(self, tool_use_id: str, tool_name: str) -> None:
        """Remember which tool produced a tool_use id (Bash vs Monitor share a task_type)."""
        if not tool_use_id or not tool_name:
            return
        if len(self._tool_names) >= _TOOL_MAP_CAP:
            self._tool_names.pop(next(iter(self._tool_names)))
        self._tool_names[str(tool_use_id)] = str(tool_name)

    def note_result(self, raw: dict) -> str:
        """Count a ``result`` event; returns ``"wakeup"`` or ``"user"``."""
        self.results_seen += 1
        cls = classify_result(raw)
        if cls == "wakeup":
            self.wakeups += 1
        return cls

    def feed(self, raw: dict, now: float | None = None) -> list[Transition]:
        """Apply one raw ``system`` event; returns the transitions it caused."""
        if not isinstance(raw, dict) or raw.get("type") != "system":
            return []
        now = time.time() if now is None else now
        sub = raw.get("subtype")
        before = len(self.open_children)
        out: list[Transition] = []
        if sub != "task_started":
            out += self.flush()
        if sub == "task_started":
            out += self._on_started(raw, now)
        elif sub == "task_updated":
            patch = raw.get("patch")
            out += self._on_update(str(raw.get("task_id") or ""),
                                   patch.get("status") if isinstance(patch, dict) else None,
                                   None, now, final=False)
        elif sub == "task_notification":
            out += self._on_update(str(raw.get("task_id") or ""), raw.get("status"),
                                   parse_exit_code(raw.get("summary")), now, final=True)
        elif sub == "background_tasks_changed":
            out += self._on_snapshot(raw.get("tasks"), now)
        after = len(self.open_children)
        self.peak_open = max(self.peak_open, after)
        if before > 0 and after == 0:
            out.append(Transition("all_children_done", None, 0))
        return out

    # -- reducers ---------------------------------------------------------

    def _kind_for(self, tool_use_id: str, task_type: str) -> str:
        name = self._tool_names.get(str(tool_use_id or ""), "").lower()
        return _TOOL_KIND.get(name) or _TYPE_KIND.get(task_type, "other")

    def _on_started(self, raw: dict, now: float) -> list[Transition]:
        if raw.get("is_backgrounded") is False:
            return []                      # a foreground sub-agent is not a background child
        tid = str(raw.get("task_id") or "")
        if not tid:
            return []
        task_type = str(raw.get("task_type") or "")
        kind = self._kind_for(raw.get("tool_use_id") or "", task_type)
        existing = self._children.get(tid)
        if existing is not None:
            # The snapshot saw this child first: enrich it in place (`prompt` is
            # deliberately dropped) and announce it exactly once, now that the tool is known.
            self._children[tid] = replace(
                existing, kind=kind, task_type=task_type or existing.task_type,
                description=str(raw.get("description") or existing.description),
                run_id=str(raw.get("run_id") or existing.run_id))
            if tid in self._unannounced:
                del self._unannounced[tid]
                return [Transition("child_started", self._children[tid],
                                   len(self.open_children))]
            return []
        child = Child(task_id=tid, kind=kind, task_type=task_type,
                      description=str(raw.get("description") or ""),
                      started_at=now, run_id=str(raw.get("run_id") or ""))
        self._children[tid] = child
        return [Transition("child_started", child, len(self.open_children))]

    def _on_update(self, tid: str, status: Any, exit_code: int | None,
                   now: float, *, final: bool) -> list[Transition]:
        child = self._children.get(tid)
        if child is None:
            return []
        if child.state in TERMINAL:
            # A late report may only ADD the exit code; never a second transition.
            if exit_code is not None and child.exit_code is None:
                self._children[tid] = replace(child, exit_code=exit_code)
            return []
        state = _terminal_state(status)
        if state is None:
            return []
        if not final:
            self._pending_end[tid] = (state, now)    # provisional: wait for the notification
            return []
        ended = self._pending_end.pop(tid, (state, now))[1]
        self._children[tid] = replace(child, state=state, ended_at=ended, exit_code=exit_code)
        return [Transition("child_finished", self._children[tid], len(self.open_children))]

    def flush(self) -> list[Transition]:
        """Announce children that were only seen in a snapshot so far."""
        out: list[Transition] = []
        for tid in list(self._unannounced):
            child = self._children.get(tid)
            del self._unannounced[tid]
            if child is not None:
                out.append(Transition("child_started", child, len(self.open_children)))
        return out

    def finalize(self, now: float | None = None) -> list[Transition]:
        """Announce stragglers and close provisional ends (call at stream end / result)."""
        now = time.time() if now is None else now
        out: list[Transition] = self.flush()
        for tid, (state, ts) in list(self._pending_end.items()):
            child = self._children.get(tid)
            self._pending_end.pop(tid, None)
            if child is not None and child.state == RUNNING:
                self._children[tid] = replace(child, state=state, ended_at=ts)
                out.append(Transition("child_finished", self._children[tid],
                                      len(self.open_children)))
        if any(t.kind == "child_finished" for t in out) and not self.open_children:
            out.append(Transition("all_children_done", None, 0))
        return out

    def _on_snapshot(self, tasks: Any, now: float) -> list[Transition]:
        if not isinstance(tasks, list):
            return []
        out: list[Transition] = []
        listed: set[str] = set()
        for t in tasks:
            if not isinstance(t, dict) or not t.get("task_id"):
                continue
            tid = str(t["task_id"])
            listed.add(tid)
            if tid not in self._children:
                task_type = str(t.get("task_type") or "")
                child = Child(task_id=tid, kind=_TYPE_KIND.get(task_type, "other"),
                              task_type=task_type, description=str(t.get("description") or ""),
                              started_at=now, run_id=str(t.get("run_id") or ""))
                self._children[tid] = child
                self._unannounced[tid] = None
        for tid, child in list(self._children.items()):
            if child.state == RUNNING and tid not in listed \
                    and (now - child.started_at) >= SNAPSHOT_GRACE_S:
                if tid in self._pending_end:
                    # the notification was missed but task_updated told us how it ended
                    state, ts = self._pending_end.pop(tid)
                    self._children[tid] = replace(child, state=state, ended_at=ts)
                    out.append(Transition("child_finished", self._children[tid],
                                          len(self.open_children)))
                else:
                    self._children[tid] = replace(child, state=UNKNOWN, ended_at=now)
                    out.append(Transition("healed", self._children[tid],
                                          len(self.open_children)))
        return out

    # -- views ------------------------------------------------------------

    @property
    def open_children(self) -> list[Child]:
        return [c for c in self._children.values() if c.state == RUNNING]

    @property
    def all_children(self) -> list[Child]:
        return list(self._children.values())

    def oldest_open_age(self, now: float | None = None) -> float:
        now = time.time() if now is None else now
        ages = [now - c.started_at for c in self.open_children]
        return max(ages) if ages else 0.0


def classify_result(raw: dict) -> str:
    """``"wakeup"`` when the result answers a task notification, else ``"user"``."""
    origin = raw.get("origin") if isinstance(raw, dict) else None
    if isinstance(origin, dict) and origin.get("kind") == "task-notification":
        return "wakeup"
    return "user"


# --- audit (best effort; ADR-2236 D10) --------------------------------------

AUDIT_EVENTS = (
    "bgscope.child_started", "bgscope.child_finished", "bgscope.waiting",
    "bgscope.completed", "bgscope.child_cap_exceeded", "bgscope.wakeup_cap_exceeded",
    "bgscope.cancelled",
)


def emit_audit(event_type: str, *, tenant_id: str, scope_id: str, **fields: Any) -> bool:
    """Write one ``bgscope.*`` record to the tenant audit chain. Never raises.

    Fields are structured scalars only (no description, prompt or output text);
    the event allowlist in ``forge.security_events`` drops anything else.
    """
    if event_type not in AUDIT_EVENTS:
        return False
    try:
        forge_path = str(Path(__file__).resolve().parents[2] / "forge")
        if forge_path not in sys.path:
            sys.path.insert(0, forge_path)
        from forge import security_events as _sec  # type: ignore
        from forge.paths import tenant_audit_chain as _tac  # type: ignore

        _sec.write_event(
            _tac(tenant_id), event_type,
            details={"tenant_id": tenant_id, "scope_id": str(scope_id)[:64], **fields},
        )
        return True
    except Exception:  # noqa: BLE001 — an audit failure must never break a turn
        return False


def audit_transition(tracker: ScopeTracker, t: Transition) -> None:
    """Map a tracker transition to its audit record."""
    base = dict(tenant_id=tracker.tenant_id, scope_id=tracker.scope_id,
                children_open=t.children_open)
    c = t.child
    if t.kind == "child_started" and c is not None:
        emit_audit("bgscope.child_started", child_id=c.task_id, kind=c.kind, **base)
    elif t.kind in ("child_finished", "healed") and c is not None:
        dur = int(((c.ended_at or c.started_at) - c.started_at) * 1000)
        emit_audit("bgscope.child_finished", child_id=c.task_id, kind=c.kind,
                   state=c.state, exit_code=c.exit_code if c.exit_code is not None else -1,
                   duration_ms=dur, healed=(t.kind == "healed"), **base)

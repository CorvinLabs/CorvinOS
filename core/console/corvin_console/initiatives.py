"""Initiatives board — live status of running and finished initiative tasks.

What it is
----------
An operator runs a handful of time-boxed initiatives ("loops") at once — a PoC,
a fix sprint that gates the next phase, a multi-week production phase. Each has
a window, a task queue, preconditions and decision gates. Until now that status
lived in hand-edited markdown whose numbers ("7% — 1/14 days", "13 days, 8 h
left") were stale the moment they were typed.

This module keeps the operator-authored FACTS in one tenant-scoped file and
derives every time-dependent number on read:

    <tenant_home>/global/initiatives.json

Stored (operator-authored): titles, windows, deadlines, task status + progress,
precondition states, gate criteria + decisions.
Derived (never stored): elapsed-time share, time left, task completion share,
overdue flags, blocked-by-gate state, the initiative status and the next
checkpoint. So a number on the board is either something a person wrote down or
arithmetic over it — nothing is estimated and nothing is invented. A missing
file is an empty board, not sample data (ADR-0763).

Runs and history
----------------
Each initiative is a *run*. A run is **finished** when the operator closes it
(``closed: {at, outcome}`` — completed or cancelled) or when every task is
done; it is **active** otherwise. Finished runs stay in the file and form the
history: their clock freezes at ``finished_at`` and the board reports how they
landed against the deadline (``schedule``). Nothing is ever deleted to "clean
up" — reopening a run removes only its ``closed`` record.

Evidence (keeping the facts themselves current)
----------------------------------------------
A task — or a precondition — may declare ``evidence``: repo-relative pytest
targets and/or filesystem paths that must exist. ``initiatives_verify`` (run by
the ``corvin-initiatives-verify`` timer and on demand) executes them and stores
the result under ``verification``. For a task WITH evidence, the board derives
from that result instead of trusting a hand-typed number:

* ``progress`` = share of passing tests + present paths;
* ``status``   = ``done`` when everything passes, else ``running`` once
  anything passes (the operator's ``blocked`` is kept);
* a hand-set ``done`` that the evidence contradicts is flagged
  ``claim_conflict`` and shown as running;
* a result older than :data:`VERIFICATION_STALE_S` is flagged stale.

Gate criteria may name ``requires_tasks``; their state is then derived (ok when
all named tasks are done, fail once the gate time passed without that).

Writes go through :func:`update_task` / :func:`set_gate_decision` /
:func:`close_run` only, which
validate, write atomically at 0o600 and return the fresh board. The console
route layer audits each mutation.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_FILENAME = "initiatives.json"
SCHEMA_VERSION = 1

TASK_STATUSES = ("pending", "running", "done", "blocked")
CHECK_STATES = ("ok", "pending", "fail")
GATE_DECISIONS = ("pending", "go", "no_go")
#: How a run was closed by the operator. A run whose tasks are all done is
#: finished as "completed" without being closed explicitly.
CLOSE_OUTCOMES = ("completed", "cancelled")
FINISHED_STATUSES = ("done", "cancelled")
#: A verification older than this is shown as stale (timer runs every 30 min).
VERIFICATION_STALE_S = 2 * 3600


class InitiativeError(ValueError):
    """Invalid board file or invalid mutation (maps to HTTP 400/404)."""


class NotFound(InitiativeError):
    pass


# ── Path ─────────────────────────────────────────────────────────────────────

def board_path(tenant_id: str) -> Path | None:
    """``<tenant_home>/global/initiatives.json`` via the shared tenant resolver.

    Same rule as ``usage_epoch``: an unresolvable tenant root means "no board",
    never a guessed path.
    """
    try:
        from core.paths import tenant_home  # noqa: PLC0415

        return Path(tenant_home(tenant_id)) / "global" / _FILENAME
    except Exception:  # noqa: BLE001
        try:
            from forge import paths as _forge_paths  # type: ignore  # noqa: PLC0415

            return Path(_forge_paths.tenant_global_dir(tenant_id)) / _FILENAME
        except Exception:  # noqa: BLE001
            return None


# ── Time helpers ─────────────────────────────────────────────────────────────

def _parse_ts(value: Any, field: str) -> float | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise InitiativeError(f"{field}: expected ISO-8601 string")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InitiativeError(f"{field}: not ISO-8601 ({value!r})") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


# ── Read + derive ────────────────────────────────────────────────────────────

def _load_raw(tenant_id: str) -> tuple[dict[str, Any], Path | None]:
    path = board_path(tenant_id)
    if path is None or not path.is_file():
        return {"version": SCHEMA_VERSION, "initiatives": []}, path
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InitiativeError(f"{_FILENAME} is unreadable: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("initiatives"), list):
        raise InitiativeError(f"{_FILENAME} must be an object with an 'initiatives' list")
    return data, path


def _derive_verification(item: dict[str, Any], now: float) -> dict[str, Any] | None:
    """Public view of an item's evidence + last verification, or None."""
    ev = item.get("evidence")
    if not isinstance(ev, dict) or not (ev.get("tests") or ev.get("paths")):
        return None
    v = item.get("verification") if isinstance(item.get("verification"), dict) else None
    if v is None:
        return {"state": "unverified", "at": None, "age_s": None, "stale": True,
                "passed": 0, "failed": 0, "errors": 0, "skipped": 0,
                "paths_present": 0, "paths_total": len(ev.get("paths") or []),
                "missing_paths": [], "summary": "not verified yet", "score_pct": None}
    at = _parse_ts(v.get("at"), "verification.at")
    age = None if at is None else max(0, round(now - at))
    passed, failed, errors = int(v.get("passed", 0)), int(v.get("failed", 0)), int(v.get("errors", 0))
    p_ok, p_tot = int(v.get("paths_present", 0)), int(v.get("paths_total", 0))
    units = passed + failed + errors + p_tot
    score = round((passed + p_ok) / units * 100) if units else None
    ok = units > 0 and failed == 0 and errors == 0 and p_ok == p_tot
    return {
        "state": "ok" if ok else ("partial" if (passed + p_ok) > 0 else "failing"),
        "at": v.get("at"), "age_s": age,
        "stale": age is None or age > VERIFICATION_STALE_S,
        "passed": passed, "failed": failed, "errors": errors, "skipped": int(v.get("skipped", 0)),
        "paths_present": p_ok, "paths_total": p_tot,
        "missing_paths": list(v.get("missing_paths") or []),
        "summary": str(v.get("summary") or ""),
        "score_pct": score,
        "first_ok_at": v.get("first_ok_at"),
    }


def _derive_task(task: dict[str, Any], now: float) -> dict[str, Any]:
    status = task.get("status", "pending")
    if status not in TASK_STATUSES:
        raise InitiativeError(f"task {task.get('id')!r}: unknown status {status!r}")
    progress = 100 if status == "done" else int(task.get("progress") or 0)
    progress = max(0, min(100, progress))
    completed_at = task.get("completed_at") or None
    verification = _derive_verification(task, now)
    claim_conflict = False
    progress_source = "manual"
    if verification is not None and verification["state"] != "unverified":
        progress_source = "evidence"
        if verification["state"] == "ok":
            status, progress = "done", 100
            completed_at = completed_at or verification.get("first_ok_at") or verification["at"]
        else:
            claim_conflict = status == "done"
            if status != "blocked":
                status = "running" if verification["state"] == "partial" else (
                    "running" if claim_conflict else status)
            progress = verification["score_pct"] or 0
            completed_at = None
    due_ts = _parse_ts(task.get("due"), f"task {task.get('id')!r}.due")
    overdue = due_ts is not None and status != "done" and now > due_ts
    return {
        "id": str(task.get("id", "")),
        "title": str(task.get("title", "")),
        "group": task.get("group") or None,
        "status": status,
        "progress": progress,
        "due": task.get("due") or None,
        "overdue": overdue,
        "completed_at": completed_at,
        "note": task.get("note") or None,
        "progress_source": progress_source,
        "verification": verification,
        "claim_conflict": claim_conflict,
    }


def _derive_checks(items: Any, where: str, *, now: float | None = None,
                   tasks: list[dict[str, Any]] | None = None,
                   due: float | None = None) -> list[dict[str, Any]]:
    by_id = {t["id"]: t for t in tasks or []}
    out = []
    for c in items or []:
        state = c.get("state", "pending")
        if state not in CHECK_STATES:
            raise InitiativeError(f"{where}: unknown state {state!r}")
        source = "manual"
        verification = _derive_verification(c, now) if now is not None else None
        if verification is not None and verification["state"] != "unverified":
            state, source = ("ok" if verification["state"] == "ok" else "fail"), "evidence"
        req = c.get("requires_tasks")
        if isinstance(req, list) and req:
            missing = [r for r in req if r not in by_id]
            if missing:
                raise InitiativeError(f"{where}: requires_tasks names unknown tasks {missing}")
            if all(by_id[r]["status"] == "done" for r in req):
                state = "ok"
            elif due is not None and now is not None and now > due:
                state = "fail"
            else:
                state = "pending"
            source = "tasks"
        out.append({"label": str(c.get("label", "")), "state": state,
                    "detail": c.get("detail") or None, "source": source,
                    "verification": verification})
    return out


def _derive_initiative(ini: dict[str, Any], gates_by_ref: dict[str, dict], now: float) -> dict[str, Any]:
    iid = str(ini.get("id", ""))
    start = _parse_ts(ini.get("start"), f"{iid}.start")
    deadline = _parse_ts(ini.get("deadline"), f"{iid}.deadline")
    tasks = [_derive_task(t, now) for t in ini.get("tasks") or []]

    # Finished? An explicit close wins; otherwise all tasks done.
    closed = ini.get("closed") if isinstance(ini.get("closed"), dict) else None
    outcome: str | None = None
    finished_ts: float | None = None
    if closed is not None:
        outcome = closed.get("outcome")
        if outcome not in CLOSE_OUTCOMES:
            raise InitiativeError(f"{iid}.closed.outcome must be one of {CLOSE_OUTCOMES}")
        finished_ts = _parse_ts(closed.get("at"), f"{iid}.closed.at")
    elif tasks and all(t["status"] == "done" for t in tasks):
        outcome = "completed"
        done_ts = [_parse_ts(t["completed_at"], "completed_at") for t in tasks if t["completed_at"]]
        finished_ts = max((x for x in done_ts if x is not None), default=None)
    finished = outcome is not None
    if finished:
        # A finished run has nothing left to be late for.
        for t in tasks:
            t["overdue"] = False
    clock = min(now, finished_ts) if finished and finished_ts is not None else now

    counts = {s: 0 for s in TASK_STATUSES}
    for t in tasks:
        counts[t["status"]] += 1
    overdue = sum(1 for t in tasks if t["overdue"])
    task_pct = round(sum(t["progress"] for t in tasks) / len(tasks)) if tasks else None

    time_pct = None
    if start is not None and deadline is not None and deadline > start:
        time_pct = round(max(0.0, min(1.0, (clock - start) / (deadline - start))) * 100)

    gates = []
    for g in ini.get("gates") or []:
        decision = g.get("decision", "pending")
        if decision not in GATE_DECISIONS:
            raise InitiativeError(f"{iid} gate {g.get('id')!r}: unknown decision {decision!r}")
        gates.append({
            "id": str(g.get("id", "")),
            "title": str(g.get("title", "")),
            "at": g.get("at") or None,
            "decision": decision,
            "on_go": g.get("on_go") or None,
            "on_no_go": g.get("on_no_go") or None,
            "criteria": _derive_checks(g.get("criteria"), f"{iid} gate {g.get('id')!r}", now=now,
                                       tasks=tasks, due=_parse_ts(g.get("at"), f"{iid} gate.at")),
        })

    # Blocked by another initiative's gate until that gate says "go".
    blocker = None
    ref = ini.get("blocked_by")
    if isinstance(ref, dict):
        key = f"{ref.get('initiative')}/{ref.get('gate')}"
        gate = gates_by_ref.get(key)
        decision = gate.get("decision", "pending") if gate else "unknown"
        if decision != "go":
            blocker = {"initiative": ref.get("initiative"), "gate": ref.get("gate"),
                       "gate_title": (gate or {}).get("title"), "decision": decision}

    # Status precedence: an explicit operator override, then facts.
    if finished:
        status = "cancelled" if outcome == "cancelled" else "done"
    elif ini.get("status_override"):
        status = str(ini["status_override"])
    elif blocker is not None:
        status = "blocked"
    elif start is not None and now < start:
        status = "scheduled"
    elif overdue or (deadline is not None and now > deadline):
        status = "at_risk"
    else:
        status = "running"

    # Next checkpoint: earliest future timestamp among explicit checkpoints,
    # open task due dates and pending gates.
    candidates: list[tuple[float, str]] = []
    for cp in ini.get("checkpoints") or []:
        ts = _parse_ts(cp.get("at"), f"{iid}.checkpoints")
        if ts is not None and ts >= now:
            candidates.append((ts, str(cp.get("label", "Checkpoint"))))
    for t in tasks:
        ts = _parse_ts(t["due"], "due")
        if ts is not None and ts >= now and t["status"] != "done":
            candidates.append((ts, f"Task due: {t['title']}"))
    for g in gates:
        ts = _parse_ts(g["at"], "gate.at")
        if ts is not None and ts >= now and g["decision"] == "pending":
            candidates.append((ts, f"Gate: {g['title']}"))
    next_cp = None
    if candidates and not finished:
        ts, label = min(candidates)
        next_cp = {"at": _iso(ts), "label": label}

    return {
        "id": iid,
        "label": ini.get("label") or None,
        "title": str(ini.get("title", "")),
        "description": ini.get("description") or None,
        "cadence": ini.get("cadence") or None,
        "start": ini.get("start") or None,
        "deadline": ini.get("deadline") or None,
        "status": status,
        "blocked_by": blocker,
        "time_progress_pct": time_pct,
        "task_progress_pct": task_pct,
        "task_counts": {**counts, "total": len(tasks), "overdue": overdue},
        "tasks": tasks,
        "preconditions": _derive_checks(ini.get("preconditions"), f"{iid}.preconditions", now=now),
        "gates": gates,
        "next_checkpoint": next_cp,
        "phase": "finished" if finished else "active",
        "outcome": outcome,
        "finished_at": _iso(finished_ts) if finished_ts is not None else None,
        "duration_s": (round(finished_ts - start) if finished and finished_ts is not None and start is not None
                       else round(max(0.0, now - start)) if start is not None and not finished else None),
        # Signed seconds finished before (+) / after (-) the deadline; None while active.
        "schedule_delta_s": (round(deadline - finished_ts)
                             if finished and finished_ts is not None and deadline is not None else None),
    }


def _verify_running(tenant_id: str) -> bool:
    try:
        from . import initiatives_verify  # noqa: PLC0415

        return initiatives_verify.is_running(tenant_id)
    except Exception:  # noqa: BLE001
        return False


def board(tenant_id: str, *, now: float | None = None) -> dict[str, Any]:
    """The derived board for *tenant_id*. Raises InitiativeError on a bad file."""
    now = time.time() if now is None else now
    raw, path = _load_raw(tenant_id)
    gates_by_ref = {
        f"{ini.get('id')}/{g.get('id')}": g
        for ini in raw["initiatives"] for g in (ini.get("gates") or [])
    }
    initiatives = [_derive_initiative(i, gates_by_ref, now) for i in raw["initiatives"]]

    totals = {s: 0 for s in TASK_STATUSES}
    overdue = 0
    for ini in initiatives:
        for s in TASK_STATUSES:
            totals[s] += ini["task_counts"][s]
        overdue += ini["task_counts"]["overdue"]

    ver_ats = [v["at"] for i in initiatives for v in
               [t["verification"] for t in i["tasks"]] + [c["verification"] for c in i["preconditions"]]
               if v and v.get("at")]
    stale_count = sum(1 for i in initiatives for t in i["tasks"]
                      if t["verification"] and t["verification"]["stale"])
    conflicts = sum(1 for i in initiatives for t in i["tasks"] if t["claim_conflict"])

    revision = None
    if path is not None and path.is_file():
        st = path.stat()
        revision = f"{st.st_mtime_ns}-{st.st_size}"

    return {
        "server_time": _iso(now),
        "revision": revision,
        "source": _FILENAME if revision else None,
        "initiatives": initiatives,
        "verification": {"last_at": max(ver_ats) if ver_ats else None,
                         "running": _verify_running(tenant_id),
                         "stale_tasks": stale_count, "claim_conflicts": conflicts,
                         "stale_after_s": VERIFICATION_STALE_S},
        "totals": {**totals, "overdue": overdue,
                   "total": sum(totals.values()),
                   "initiatives_blocked": sum(1 for i in initiatives if i["status"] == "blocked"),
                   "runs_active": sum(1 for i in initiatives if i["phase"] == "active"),
                   "runs_finished": sum(1 for i in initiatives if i["phase"] == "finished")},
    }


# ── Mutations ────────────────────────────────────────────────────────────────

def _write_raw(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".initiatives.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.chmod(tmp, 0o600)  # before replace: replace carries the temp's mode
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _find(raw: dict[str, Any], iid: str) -> dict[str, Any]:
    for ini in raw["initiatives"]:
        if str(ini.get("id")) == iid:
            return ini
    raise NotFound(f"initiative {iid!r} not found")


def update_task(tenant_id: str, iid: str, tid: str, *,
                status: str | None = None, progress: int | None = None,
                now: float | None = None) -> dict[str, Any]:
    """Set a task's status and/or progress. Returns the fresh board."""
    now = time.time() if now is None else now
    if status is None and progress is None:
        raise InitiativeError("nothing to update")
    if status is not None and status not in TASK_STATUSES:
        raise InitiativeError(f"status must be one of {TASK_STATUSES}")
    if progress is not None and not (0 <= int(progress) <= 100):
        raise InitiativeError("progress must be 0..100")
    raw, path = _load_raw(tenant_id)
    if path is None:
        raise InitiativeError("tenant home unresolvable")
    ini = _find(raw, iid)
    task = next((t for t in ini.get("tasks") or [] if str(t.get("id")) == tid), None)
    if task is None:
        raise NotFound(f"task {tid!r} not found in {iid!r}")
    if status is not None:
        if status == "done" and task.get("status") != "done":
            task["completed_at"] = _iso(now)
        elif status != "done":
            task.pop("completed_at", None)
        task["status"] = status
    if progress is not None:
        task["progress"] = int(progress)
    _write_raw(path, raw)
    return board(tenant_id, now=now)


def set_gate_decision(tenant_id: str, iid: str, gid: str, decision: str,
                      *, now: float | None = None) -> dict[str, Any]:
    """Record a gate decision (pending/go/no_go). Returns the fresh board."""
    if decision not in GATE_DECISIONS:
        raise InitiativeError(f"decision must be one of {GATE_DECISIONS}")
    raw, path = _load_raw(tenant_id)
    if path is None:
        raise InitiativeError("tenant home unresolvable")
    ini = _find(raw, iid)
    gate = next((g for g in ini.get("gates") or [] if str(g.get("id")) == gid), None)
    if gate is None:
        raise NotFound(f"gate {gid!r} not found in {iid!r}")
    gate["decision"] = decision
    _write_raw(path, raw)
    return board(tenant_id, now=now)


def close_run(tenant_id: str, iid: str, outcome: str | None,
              *, now: float | None = None) -> dict[str, Any]:
    """Close a run as completed/cancelled, or reopen it with ``outcome=None``.

    Closing records ``closed: {at, outcome}``; reopening removes only that
    record — tasks, gates and their history are untouched.
    """
    now = time.time() if now is None else now
    if outcome is not None and outcome not in CLOSE_OUTCOMES:
        raise InitiativeError(f"outcome must be one of {CLOSE_OUTCOMES} or null")
    raw, path = _load_raw(tenant_id)
    if path is None:
        raise InitiativeError("tenant home unresolvable")
    ini = _find(raw, iid)
    if outcome is None:
        ini.pop("closed", None)
    else:
        ini["closed"] = {"at": _iso(now), "outcome": outcome}
    _write_raw(path, raw)
    return board(tenant_id, now=now)

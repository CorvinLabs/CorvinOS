"""Every task / run / job this install knows about, in one normalised list.

CorvinOS has no single task store. Chat turns, background ``/task`` runs, ACS
runs, gateway runs, forge tool runs, compute runs, workflow and flow runs,
scheduled reminders and the operator's initiative tasks each persist their own
records in their own shape. Two host-level sources (ADR-2060, ``host_activity.py``)
add the operator's interactive Claude Code sessions (``agent``) and this
checkout's commits (``commit``) — default tenant only. This module reads each store where it already lives
(nothing is copied or migrated), maps it onto one record shape and one status
vocabulary, and tells the caller which sources exist, which are empty and which
are not available on this build.

Record shape (``TaskRecord`` dicts)::

    {id, type, type_label, subtype, title, status, raw_status,
     created_at, started_at, ended_at,          # ISO-8601 UTC or None
     sort_ts, duration_s, stale_reason, detail}

Normalised ``status``:

    active   → queued · running · paused · scheduled
    finished → done · failed · cancelled
    stale    → claims to be active but shows no sign of life (no heartbeat,
               no end record, no worker) for longer than STALE_AFTER_S — it is
               NOT shown as running, because nothing says it is.

A2A exchanges (``a2a``, both directions) come from the Agent Hub feed; ``/task``
and self-delegated background tasks from the completion registry, with their
worker's turns folded in. A record may carry ``steps`` — the Claude Code
subagents of a chat turn, or the worker turns of a background task.

Privacy: a bridge chat task's instruction is usually another person's
message. Only the operator's own turns get an instruction preview — web-chat
and CLI turns, and bridge turns the adapter stamped ``from_operator`` (sender
explicitly on that bridge's whitelist). Everyone else's is titled by channel
and persona only; A2A runs by direction and peer only.

Cost: ~5 k chat-task files are re-read only when their (mtime, size) changed —
every other poll is a stat per file (``_FILE_CACHE``). The aggregate is cached
for ``AGGREGATE_TTL_S`` so concurrent pollers share one scan.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

STALE_AFTER_S = 2 * 3600
AGGREGATE_TTL_S = 2.0
ACTIVE = ("queued", "running", "paused", "scheduled")
FINISHED = ("done", "failed", "cancelled")

TYPE_LABELS = {
    "initiative": "Initiative",
    "chat": "Chat",
    "background": "Background task",
    "acs": "ACS",
    "workflow": "Workflow",
    "flow": "Flow",
    "gateway": "Gateway run",
    "a2a": "A2A",
    "forge": "Forge tool",
    "compute": "Compute",
    "scheduled": "Scheduled",
    "skill_creator": "Skill creator",
    "agent": "Agent session",
    "commit": "Commit",
}

_FILE_CACHE: dict[str, tuple[int, int, Any]] = {}
_FILE_CACHE_LOCK = threading.Lock()
_AGG_CACHE: dict[str, tuple[float, dict]] = {}
_AGG_LOCK = threading.Lock()


# ── helpers ──────────────────────────────────────────────────────────────────

def _ts(value: Any) -> float | None:
    """Epoch seconds from an epoch float/int or an ISO string; None otherwise."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    return None


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> Any:
    """JSON file content, re-parsed only when (mtime_ns, size) changed."""
    try:
        st = path.stat()
    except OSError:
        return None
    key = str(path)
    with _FILE_CACHE_LOCK:
        hit = _FILE_CACHE.get(key)
        if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
            return hit[2]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    with _FILE_CACHE_LOCK:
        _FILE_CACHE[key] = (st.st_mtime_ns, st.st_size, data)
    return data


def _record(*, id: str, type: str, title: str, status: str, raw_status: Any,
            created: float | None = None, started: float | None = None,
            ended: float | None = None, subtype: str | None = None,
            detail: str | None = None, now: float,
            last_alive: float | None = None) -> dict[str, Any]:
    stale_reason = None
    if status in ("queued", "running"):
        alive = last_alive or started or created
        if alive is not None and now - alive > STALE_AFTER_S:
            hours = (now - alive) / 3600
            stale_reason = f"no sign of life for {hours:.0f} h — no end record was written"
            status = "stale"
    start_for_duration = started or created
    if ended is not None and start_for_duration is not None:
        duration = max(0.0, ended - start_for_duration)
    elif status in ("running",) and start_for_duration is not None:
        duration = max(0.0, now - start_for_duration)
    else:
        duration = None
    return {
        "id": id, "type": type, "type_label": TYPE_LABELS.get(type, type),
        "subtype": subtype, "title": title, "status": status,
        "raw_status": None if raw_status is None else str(raw_status),
        "created_at": _iso(created), "started_at": _iso(started), "ended_at": _iso(ended),
        "sort_ts": ended or started or created or 0.0,
        "duration_s": None if duration is None else round(duration, 1),
        "stale_reason": stale_reason, "detail": detail,
    }


def _short(value: Any) -> str:
    """'Sep 24, 18:00 UTC' — fixed en-US, UTC (ADR-0764)."""
    ts = _ts(value)
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%b %d, %H:%M UTC") if ts else str(value)


def _preview(text: Any, n: int = 90) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _corvin_root() -> Path:
    from core.paths.tenant import corvin_home  # noqa: PLC0415

    return Path(corvin_home())


_CHANNEL_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


def _operator_uids(channel: str) -> frozenset[str]:
    """Senders EXPLICITLY on a bridge's whitelist — the operator's own accounts.

    Read from the settings file the bridge daemon itself uses
    (``<corvin_home>/bridges/<channel>/settings.json``, ADR-0008 §8.3). Only an
    explicit entry counts: an empty whitelist, or a chat opened to everyone
    (``audience: all``), makes nobody the operator. Read uncached — the file
    also holds the bridge token, which must not sit in a module-level cache."""
    if not _CHANNEL_RE.match(channel or ""):
        return frozenset()
    try:
        data = json.loads((_corvin_root() / "bridges" / channel / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return frozenset()
    wl = data.get("whitelist") if isinstance(data, dict) else None
    return frozenset(str(x) for x in wl if x) if isinstance(wl, list) else frozenset()


# ── subagents inside a turn (Claude Code transcripts) ────────────────────────
# A worker turn that fans out into Claude Code subagents leaves one transcript
# per subagent under ``<claude_home>/projects/<encoded workdir>/<session>/
# subagents/agent-*.jsonl`` (+ ``.meta.json``: agentType, description). They are
# steps of the turn, not tasks of their own: measured 2026-09-27, all 57
# subagents since 2026-09-26 started and ended inside their turn's window.
# Only the first line's timestamp and the file mtime are read — never the
# prompt or the output.

_SUB_CACHE: dict[str, tuple[int, int, dict | None]] = {}
_SUB_HEAD_MAX = 2 * 1024 * 1024
STEP_RUNNING_S = 300
STEP_ITEMS_MAX = 25


def _subagent_entry(path: Path) -> dict | None:
    try:
        st = path.stat()
    except OSError:
        return None
    key = str(path)
    with _FILE_CACHE_LOCK:
        hit = _SUB_CACHE.get(key)
        if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
            return hit[2]
    start = None
    try:
        with path.open("rb") as fh:
            for _ in range(5):
                line = fh.readline(_SUB_HEAD_MAX)
                if not line:
                    break
                try:
                    start = _ts(json.loads(line).get("timestamp"))
                except (ValueError, AttributeError):
                    continue
                if start is not None:
                    break
    except OSError:
        pass
    meta = _read_json(path.with_name(path.stem + ".meta.json"))
    meta = meta if isinstance(meta, dict) else {}
    entry = None if start is None else {
        "start": start, "end": st.st_mtime,
        "agent_type": str(meta.get("agentType") or "")[:40],
        "description": str(meta.get("description") or "")[:200],
    }
    with _FILE_CACHE_LOCK:
        _SUB_CACHE[key] = (st.st_mtime_ns, st.st_size, entry)
    return entry


def _subagents(workdir: Path) -> list[dict]:
    from . import host_activity as ha  # noqa: PLC0415

    proj = ha.claude_home() / "projects" / ha._encode_cwd(str(workdir))
    if not proj.is_dir():
        return []
    return [e for e in (_subagent_entry(f) for f in proj.glob("*/subagents/agent-*.jsonl")) if e]


def _steps(subs: list[dict], *, owned: bool, turn_running: bool, now: float) -> dict:
    items = []
    running = 0
    for s in sorted(subs, key=lambda s: s["start"]):
        live = turn_running and now - s["end"] < STEP_RUNNING_S
        running += live
        kind = s["agent_type"] or "Sub"
        items.append({
            "title": _preview(s["description"]) if owned and s["description"] else f"{kind} subagent",
            "agent_type": s["agent_type"] or None,
            "status": "running" if live else "done",
            "started_at": _iso(s["start"]), "ended_at": None if live else _iso(s["end"]),
            "duration_s": round(max(0.0, (now if live else s["end"]) - s["start"]), 1),
        })
    return {"total": len(items), "running": running, "items": items[-STEP_ITEMS_MAX:]}


def _steps_text(steps: dict | None) -> str | None:
    if not steps or not steps["total"]:
        return None
    n = steps["total"]
    txt = f"{n} subagent{'' if n == 1 else 's'}"
    return txt + (f" ({steps['running']} running)" if steps["running"] else "")


def _join(*parts: str | None) -> str | None:
    s = " · ".join(p for p in parts if p)
    return s or None


# ── sources ──────────────────────────────────────────────────────────────────
# Each source: (type, callable(tenant_home, now) -> iterator of records,
#               callable(tenant_home) -> availability note or None)

# Detached background workers run under ``voice/<bridge>/bgtask__<chat>__<id>``
# (adapter ``engine_chat_key`` → ``_safe_id``); the trailing id is the
# completion-registry task id.
_BG_REF_RE = re.compile(r"__((?:bgt|cn)_[0-9a-f]+)$")

_CHAT_STATUS = {"pending": "queued", "running": "running", "completed": "done",
                "failed": "failed", "cancelled": "cancelled"}


def _chat_channel(rel_parts: tuple[str, ...]) -> tuple[str, str]:
    """(type, subtype) from the session directory under sessions/."""
    head = rel_parts[0] if rel_parts else ""
    if head == "voice" and len(rel_parts) >= 3:
        bridge, chat = rel_parts[1], rel_parts[2]
        if chat.startswith("bgtask"):
            return "background", bridge
        return "chat", bridge
    if head.startswith("web:"):
        return "chat", "web"
    if head.startswith("cli:"):
        return "chat", "cli"
    return "chat", head.split(":")[0] or "other"


def _chat_tasks(home: Path, now: float) -> Iterator[dict]:
    root = home / "sessions"
    if not root.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(root):
        # Skip heavy subtrees that never contain task records.
        dirnames[:] = [d for d in dirnames if d not in ("acs", "outputs", "uploads", "artifacts", "media")]
        if os.path.basename(dirpath) != "tasks":
            continue
        rel = Path(dirpath).relative_to(root).parts[:-1]
        typ, sub = _chat_channel(rel)
        workdir = Path(dirpath).parent
        bg_ref = None
        if typ == "background":
            m = _BG_REF_RE.search(rel[-1] if rel else "")
            bg_ref = m.group(1) if m else None
        turns = []
        for fn in filenames:
            if not fn.endswith(".json") or fn.endswith(".events.jsonl"):
                continue
            path = Path(dirpath) / fn
            d = _read_json(path)
            if not isinstance(d, dict) or "task_id" not in d:
                continue
            turns.append((path, d))
        if not turns:
            continue
        subs = _subagents(workdir)
        # Turns of one chat run one at a time, so a subagent belongs to the
        # latest turn that had started when it did (and had not yet ended).
        windows = sorted(((_ts(d.get("created_at")) or 0.0, d["task_id"]) for _, d in turns), reverse=True)
        by_turn: dict[str, list[dict]] = {}
        for s in subs:
            for created, tid in windows:
                if created - 5 <= s["start"]:
                    by_turn.setdefault(tid, []).append(s)
                    break
        for path, d in turns:
            inp = d.get("input") if isinstance(d.get("input"), dict) else {}
            persona = inp.get("persona") or "assistant"
            # A turn's instruction is shown only when it is the operator's own:
            # a web/CLI turn, or a bridge turn the adapter stamped from_operator
            # (sender explicitly on that bridge's whitelist). Anyone else's
            # message stays untitled.
            owned = sub in ("web", "cli") or inp.get("from_operator") is True
            if owned:
                title = _preview(inp.get("instruction")) or f"{sub} turn"
            else:
                title = f"{sub.capitalize()} {'background task' if typ == 'background' else 'message'} · {persona}"
            events = path.with_suffix(".events.jsonl")
            last_alive = None
            try:
                last_alive = events.stat().st_mtime
            except OSError:
                pass
            raw = d.get("status")
            ended = _ts(d.get("ended_at"))
            mine = [s for s in by_turn.get(d["task_id"], []) if ended is None or s["start"] <= ended + 5]
            steps = _steps(mine, owned=owned, turn_running=raw in ("running", "pending"), now=now) if mine else None
            if steps:
                last_alive = max(filter(None, [last_alive] + [s["end"] for s in mine]))
            rec = _record(
                id=f"chat:{d['task_id']}", type=typ, subtype=sub, title=title,
                status=_CHAT_STATUS.get(raw, "running"), raw_status=raw,
                created=_ts(d.get("created_at")), started=_ts(d.get("started_at")),
                ended=ended, now=now, last_alive=last_alive,
                detail=_join((d.get("result_summary") or None) if raw != "running" else f"persona {persona}",
                             _steps_text(steps)),
            )
            if steps:
                rec["steps"] = steps
            if bg_ref:
                rec["_bg_ref"] = bg_ref
            yield rec


def _acs_runs(home: Path, now: float) -> Iterator[dict]:
    seen: set[str] = set()
    status_map = {"success": "done", "failed": "failed", "budget_exhausted": "failed"}
    for mf in sorted((home / "global" / "acs" / "runs").glob("*/manifest.json")):
        d = _read_json(mf)
        if not isinstance(d, dict):
            continue
        rid = str(d.get("run_id") or mf.parent.name)
        seen.add(rid)
        raw = d.get("status")
        detail = d.get("budget_breach") or (f"{d.get('iterations')} iterations, "
                                            f"{d.get('workers_spawned')} workers" if d.get("iterations") else None)
        yield _record(id=f"acs:{rid}", type="acs", subtype=str(d.get("source") or "acs"),
                      title=str(d.get("workflow_id") or "ACS run"),
                      status=status_map.get(raw, "running"), raw_status=raw,
                      started=_ts(d.get("started_at")), ended=_ts(d.get("completed_at")),
                      detail=detail, now=now)
    # Session-scoped run dirs not (yet) in the global index — e.g. still running.
    for mf in (home / "sessions").glob("*/acs/runs/*/manifest.json"):
        rid = mf.parent.name
        if rid in seen:
            continue
        d = _read_json(mf) or {}
        res = _read_json(mf.parent / "result.json")
        started = _ts(d.get("started_at"))
        if isinstance(res, dict):
            raw = res.get("status")
            status = status_map.get(raw, "done" if raw else "done")
            ended = _ts(res.get("completed_at")) or _ts(res.get("ended_at"))
        else:
            raw, status, ended = "no result yet", "running", None
        try:
            last_alive = max(p.stat().st_mtime for p in mf.parent.iterdir())
        except (OSError, ValueError):
            last_alive = None
        yield _record(id=f"acs:{rid}", type="acs", subtype="session",
                      title=str(d.get("workflow_id") or "ACS run"), status=status,
                      raw_status=raw, started=started, ended=ended, now=now,
                      last_alive=last_alive)


def _gateway_runs(home: Path, now: float) -> Iterator[dict]:
    status_map = {"accepted": "queued", "running": "running", "completed": "done",
                  "failed": "failed", "budget_exceeded": "failed"}
    for f in (home / "global" / "gateway" / "runs").glob("run_*.json"):
        d = _read_json(f)
        if not isinstance(d, dict):
            continue
        spec = ((d.get("request") or {}).get("spec") or {}) if isinstance(d.get("request"), dict) else {}
        raw = d.get("status")
        terminal = raw in ("completed", "failed", "budget_exceeded")
        yield _record(id=f"gateway:{d.get('run_id')}", type="gateway",
                      subtype=str(spec.get("persona") or "run"),
                      title=f"Gateway run · {spec.get('persona') or 'default persona'}",
                      status=status_map.get(raw, "running"), raw_status=raw,
                      created=_ts(d.get("created_at")),
                      ended=_ts(d.get("updated_at")) if terminal else None,
                      last_alive=_ts(d.get("updated_at")), now=now,
                      detail=_preview(d.get("error"), 140) if d.get("error") else None)


def _forge_runs(home: Path, now: float) -> Iterator[dict]:
    corvin = home.parent.parent  # <corvin_home>/tenants/<tid> → <corvin_home>
    roots = [home / "global" / "forge" / "runs"]
    if home.name == "_default":
        # Forge's MCP runner writes host-level dirs that carry no tenant. They
        # belong to the host owner's tenant only — never to another tenant.
        roots += [corvin / "global" / "forge" / "runs", corvin / "forge" / "runs",
                  corvin / "sessions" / "default" / "forge" / "runs"]
    status_map = {"ok": "done", "replayed": "done", "error": "failed", "timeout": "failed"}
    for root in roots:
        if not root.is_dir():
            continue
        for mf in root.glob("*/run_manifest.json"):
            m = _read_json(mf) or {}
            c = _read_json(mf.parent / "run_completion.json")
            if isinstance(c, dict):
                raw = c.get("status")
                status, ended = status_map.get(raw, "failed"), _ts(c.get("completed_at"))
            else:
                raw, status, ended = "no completion record", "running", None
            yield _record(id=f"forge:{m.get('run_id') or mf.parent.name}", type="forge",
                          subtype=root.parent.parent.name, title=str(m.get("tool") or "tool run"),
                          status=status, raw_status=raw, started=_ts(m.get("started_at")),
                          ended=ended, now=now)


def _compute(home: Path, now: float) -> Iterator[dict]:
    status_map = {"queued": "queued", "running": "running", "converged": "done",
                  "stalled": "failed", "budget_exhausted": "done", "failed": "failed",
                  "aborted": "cancelled"}
    for sp in (home / "compute" / "runs").glob("*/summary.json"):
        s = _read_json(sp) or {}
        m = _read_json(sp.parent / "manifest.json") or {}
        raw = s.get("state")
        yield _record(id=f"compute:{sp.parent.name}", type="compute", subtype="optimiser run",
                      title=f"{m.get('tool_name') or 'compute run'} · {m.get('strategy') or ''}".strip(" ·"),
                      status=status_map.get(raw, "running"), raw_status=raw,
                      created=_ts(m.get("accepted_at")), started=_ts(s.get("started_at")),
                      ended=_ts(s.get("last_iteration_at")) if raw not in ("queued", "running") else None,
                      last_alive=_ts(s.get("last_iteration_at")), now=now,
                      detail=s.get("convergence_reason"))
    for kind, fname in (("pipelines", "pipeline_summary.json"), ("hac", "hac_summary.json")):
        for sp in (home / "compute" / kind).glob(f"*/{fname}"):
            s = _read_json(sp) or {}
            raw = s.get("state")
            yield _record(id=f"compute:{kind}:{sp.parent.name}", type="compute", subtype=kind,
                          title=str(s.get("name") or f"{kind} {sp.parent.name[:8]}"),
                          status=status_map.get(raw, "running"), raw_status=raw,
                          started=_ts(s.get("started_at")),
                          ended=_ts(s.get("completed_at") or s.get("last_iteration_at")) if raw not in ("queued", "running") else None,
                          now=now)
    for jf in (home / "global" / "compute" / "jobs").glob("*.json"):
        j = _read_json(jf) or {}
        raw = j.get("status")
        rec = _record(id=f"compute:job:{j.get('job_id') or jf.stem}", type="compute", subtype="job",
                      title=str(j.get("name") or "compute job"), status=status_map.get(raw, "queued"),
                      raw_status=raw, created=_ts(j.get("created_at")),
                      last_alive=_ts(j.get("updated_at")), now=now)
        if rec["status"] == "stale":
            rec["stale_reason"] = "queued, but no worker on this build processes compute jobs"
        yield rec


def _workflow_runs(home: Path, now: float) -> Iterator[dict]:
    status_map = {"running": "running", "resumed": "running", "paused": "paused",
                  "checkpoint": "paused", "completed": "done", "failed": "failed"}
    for mf in (home / "workflows").glob("*/runs/*.meta.json"):
        d = _read_json(mf) or {}
        raw = d.get("status")
        yield _record(id=f"workflow:{d.get('id') or mf.name[:-10]}", type="workflow",
                      subtype="plugin", title=str(d.get("workflow_id") or mf.parent.parent.name),
                      status=status_map.get(raw, "running"), raw_status=raw,
                      started=_ts(d.get("started_at")), ended=_ts(d.get("completed_at")),
                      last_alive=mf.stat().st_mtime, now=now,
                      detail=_preview(d.get("error"), 140) if d.get("error") else None)
    for f in (home / "workflow_runs").glob("*.json"):
        d = _read_json(f) or {}
        yield _record(id=f"workflow:awp:{d.get('run_id') or f.stem}", type="workflow", subtype="AWP",
                      title=str(d.get("workflow_name") or "workflow"), status="paused",
                      raw_status=d.get("status") or "paused",
                      started=_ts(d.get("started_at") or d.get("paused_at")), now=now)


def _flow_runs(home: Path, now: float) -> Iterator[dict]:
    for mf in (home / "global" / "flows" / "runs").glob("*.manifest.jsonl"):
        events = []
        try:
            for line in mf.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    events.append(json.loads(line))
        except (OSError, ValueError):
            continue
        types = [e.get("type") for e in events]
        started = next((e for e in events if e.get("type") == "mesh_flow.run_started"), {})
        done = next((e for e in events if e.get("type") == "mesh_flow.run_completed"), None)
        if done:
            status, raw = "done", "completed"
        elif "mesh_flow.budget_exceeded" in types:
            status, raw = "failed", "budget_exceeded"
        elif "mesh_flow.run_paused" in types or "mesh_flow.checkpoint_paused" in types:
            status, raw = "paused", "paused"
        else:
            status, raw = "running", "running"
        yield _record(id=f"flow:{mf.name.split('.')[0]}", type="flow", subtype="CorvinFlow",
                      title=str(started.get("flow_id") or "flow run"), status=status, raw_status=raw,
                      started=_ts(started.get("ts")), ended=_ts((done or {}).get("ts")),
                      last_alive=mf.stat().st_mtime, now=now)


def _scheduled(home: Path, now: float) -> Iterator[dict]:
    d = _read_json(home / "voice" / "schedule.json")
    items = d if isinstance(d, list) else (d or {}).get("tasks", []) if isinstance(d, dict) else []
    for it in items:
        if not isinstance(it, dict):
            continue
        # The reminder text is a person's message; show only its schedule.
        nxt = _ts(it.get("next_run"))
        yield _record(id=f"scheduled:{it.get('id')}", type="scheduled",
                      subtype="cron" if it.get("cron") else "once",
                      title=f"Scheduled reminder{' · ' + str(it['cron']) if it.get('cron') else ''}",
                      status="scheduled", raw_status="scheduled", created=_ts(it.get("created")),
                      now=now, detail=f"next run {_iso(nxt)}" if nxt else None)


def _skill_creator(home: Path, now: float) -> Iterator[dict]:
    mod = sys.modules.get("corvin_console.routes.skill_creator_api")
    runs = getattr(mod, "_generation_runs", None) if mod else None
    if not isinstance(runs, dict):
        return
    tid = home.name
    status_map = {"accepted": "queued", "running": "running", "success": "done", "failed": "failed"}
    for rid, r in list(runs.items()):
        if not isinstance(r, dict) or r.get("tenant_id", tid) != tid:
            continue
        raw = r.get("status")
        yield _record(id=f"skill_creator:{rid}", type="skill_creator", subtype="generation",
                      title=str(r.get("message") or r.get("phase") or "skill generation"),
                      status=status_map.get(raw, "running"), raw_status=raw,
                      created=_ts(r.get("created_at")), now=now)


def _initiatives(tenant_id: str, now: float) -> Iterator[dict]:
    from . import initiatives as board_mod  # noqa: PLC0415

    try:
        b = board_mod.board(tenant_id, now=now)
    except board_mod.InitiativeError:
        return
    status_map = {"pending": "queued", "running": "running", "done": "done", "blocked": "paused"}
    for ini in b["initiatives"]:
        for t in ini["tasks"]:
            st = status_map.get(t["status"], "queued")
            if ini["status"] == "cancelled" and st != "done":
                st = "cancelled"
            rec = _record(id=f"initiative:{ini['id']}/{t['id']}", type="initiative",
                          subtype=ini.get("label") or ini["id"],
                          title=f"{ini.get('label') + ' · ' if ini.get('label') else ''}{t['title']}",
                          status=st, raw_status=t["status"], now=now,
                          ended=_ts(t.get("completed_at")),
                          detail=f"{t['progress']}%" + (f" · due {_short(t['due'])}" if t.get("due") else ""))
            # Initiative tasks are planned work, not processes: "no heartbeat"
            # says nothing about them, so they are never marked stale.
            if rec["status"] == "stale":
                rec["status"], rec["stale_reason"] = st, None
            yield rec


def _agent_sessions(home: Path, now: float) -> Iterator[dict]:
    """Interactive Claude Code sessions on this host (ADR-2060, host_activity.py).

    Titled by the session's own ai-title, never prompt text. ``busy`` is
    running; ``idle`` is paused (waiting for the operator's input)."""
    from . import host_activity as ha  # noqa: PLC0415

    if home.name != ha.HOST_TENANT:
        return
    for s in ha.agent_sessions(now):
        if s["live"]:
            status = "running" if s["state"] == "busy" else "paused"
        else:
            status = "done"
        title = s.get("title") or "Claude Code session"
        rec = _record(id=f"agent:{s['session_id']}", type="agent", subtype=s.get("project") or "session",
                      title=title, status=status, raw_status=s["state"], started=s.get("started"),
                      ended=None if s["live"] else s.get("last_active"), now=now,
                      last_alive=s.get("last_active"),
                      detail=(f"{s['name']} · " if s.get("name") else "")
                      + ({"busy": "working", "idle": "waiting for input"}.get(s["state"], "ended")))
        if s["live"] and rec["status"] == "stale":
            # The process is verifiably alive (pid + start time): a long think
            # without a transcript write is not a dead session.
            rec["status"], rec["stale_reason"] = status, None
        yield rec


def _commits(home: Path, now: float) -> Iterator[dict]:
    """Non-merge commits of this install's checkout, last 7 days (ADR-2060)."""
    from . import host_activity as ha  # noqa: PLC0415

    if home.name != ha.HOST_TENANT:
        return
    for c in ha.git_commits(now=now):
        refs = ha.adr_refs(c["subject"])
        yield _record(id=f"commit:{c['repo']}:{c['short']}", type="commit", subtype=c["repo"],
                      title=_preview(c["subject"], 120), status="done", raw_status="committed",
                      ended=c["ts"], started=c["ts"], now=now,
                      detail=c["short"] + (f" · {', '.join(refs)}" if refs else ""))


_BG_STATE = {"pending": "queued", "ready": "done", "delivered": "done"}


def _background_registry(home: Path, now: float) -> Iterator[dict]:
    """``/task`` and self-delegated background tasks, from the moment they are
    registered (``completion_notify``: ``<corvin_home>/pending_notifications``).

    The registry is host-wide; each record names its tenant. It is the task's
    own lifecycle (pending → ready → delivered); the detached worker's engine
    turns are folded into it by :func:`_fold_background`. Delivered records are
    pruned by completion_notify after a TTL — the worker turn stays listed."""
    qdir = _corvin_root() / "pending_notifications"
    if not qdir.is_dir():
        return
    for f in qdir.glob("*.json"):
        d = _read_json(f)
        if not isinstance(d, dict) or not d.get("id") or str(d.get("tenant_id") or "_default") != home.name:
            continue
        state = str(d.get("state") or "")
        status = _BG_STATE.get(state, "running")
        if state == "pending" and d.get("producer_pid"):
            status = "running"
        if status == "done" and d.get("ok") is False:
            status = "failed"
        channel = str(d.get("channel") or "")
        owned = bool(d.get("sender")) and str(d.get("sender")) in _operator_uids(channel)
        title = _preview(d.get("label")) if owned and d.get("label") else \
            f"{channel.capitalize() or 'Background'} background task"
        yield _record(id=f"background:{d['id']}", type="background", subtype=channel or None,
                      title=title, status=status, raw_status=state, created=_ts(d.get("created_at")),
                      ended=_ts(d.get("ready_at")), now=now,
                      detail={"pending": "waiting for a worker" if status == "queued" else "worker running",
                              "ready": "result ready, not yet delivered", "delivered": "result delivered"}.get(state))


def _fold_background(records: list[dict]) -> list[dict]:
    """Fold each detached worker's engine turns into its registry record, so a
    background task is listed once, with its turns as steps."""
    reg = {r["id"][len("background:"):]: r for r in records if r["id"].startswith("background:")}
    out = []
    for r in records:
        ref = r.pop("_bg_ref", None)
        parent = reg.get(ref) if ref else None
        if parent is None:
            out.append(r)
            continue
        steps = parent.setdefault("steps", {"total": 0, "running": 0, "items": []})
        steps["total"] += 1
        steps["running"] += r["status"] == "running"
        steps["items"] = (steps["items"] + [{
            "title": "Worker turn", "agent_type": None, "status": r["status"],
            "started_at": r["started_at"] or r["created_at"], "ended_at": r["ended_at"],
            "duration_s": r["duration_s"]}])[-STEP_ITEMS_MAX:]
        if parent["status"] in ("queued", "stale") and r["status"] == "running":
            parent["status"], parent["stale_reason"], parent["detail"] = "running", None, "worker running"
        parent["detail"] = _join(parent["detail"], _steps_text(r.get("steps")))
    return out


# ── A2A (L38) — both directions, from the Agent Hub feed ─────────────────────
# ``<tenant>/global/a2a_feed/messages.jsonl``: a ``task`` record per exchange
# and a ``response`` record once it is answered. Only routing metadata is kept
# from it — never ``text``/``data``/attachments, which are the peer's content.

_A2A_KEYS = ("direction", "kind", "task_id", "peer_id", "peer_label", "status", "ts", "duration_ms", "error")
_A2A_CACHE: dict[str, tuple[int, int, list[dict]]] = {}
_A2A_DONE = {"ok", "success", "done", "completed"}
_A2A_REFUSED = {"rejected", "filtered", "refused", "denied", "cancelled"}
_ERR_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


def _a2a_rows(path: Path) -> list[dict]:
    try:
        st = path.stat()
    except OSError:
        return []
    key = str(path)
    with _FILE_CACHE_LOCK:
        hit = _A2A_CACHE.get(key)
        if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
            return hit[2]
    rows: list[dict] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue   # a torn last line while the writer appends
                if isinstance(d, dict) and d.get("task_id"):
                    rows.append({k: d.get(k) for k in _A2A_KEYS})
    except OSError:
        return []
    with _FILE_CACHE_LOCK:
        _A2A_CACHE[key] = (st.st_mtime_ns, st.st_size, rows)
    return rows


def _a2a(home: Path, now: float) -> Iterator[dict]:
    runs: dict[tuple[str, str], dict[str, dict]] = {}
    for r in _a2a_rows(home / "global" / "a2a_feed" / "messages.jsonl"):
        if r["kind"] == "task":
            side, slot = ("in" if r["direction"] == "in" else "out"), "task"
        elif r["kind"] == "response":
            side, slot = ("in" if r["direction"] == "out" else "out"), "resp"
        else:
            continue
        runs.setdefault((side, str(r["task_id"])), {})[slot] = r
    for (side, tid), run in runs.items():
        task, resp = run.get("task"), run.get("resp")
        first = task or resp
        peer = first.get("peer_label") or first.get("peer_id") or "peer"
        if resp is None:
            status = "running"
        else:
            rs = str(resp.get("status") or "").lower()
            status = "done" if rs in _A2A_DONE else "cancelled" if rs in _A2A_REFUSED else "failed"
        err = str((resp or {}).get("error") or "")
        dur = (resp or {}).get("duration_ms")
        yield _record(
            id=f"a2a:{side}:{tid}", type="a2a", subtype="inbound" if side == "in" else "outbound",
            title=f"Task {'from' if side == 'in' else 'to'} {_preview(peer, 60)}",
            status=status, raw_status=(resp or task or {}).get("status"),
            created=_ts((task or {}).get("ts")), ended=_ts((resp or {}).get("ts")), now=now,
            detail=_join(f"{'received' if side == 'in' else 'sent'} · task {tid[:12]}",
                         (err if _ERR_TOKEN_RE.match(err) else "error") if err else None,
                         f"{dur / 1000:.1f} s" if isinstance(dur, (int, float)) and dur > 0 else None),
        )


_SOURCES: list[tuple[str, Callable[[Path, float], Iterator[dict]]]] = [
    ("chat", _chat_tasks), ("background", _background_registry), ("a2a", _a2a),
    ("acs", _acs_runs), ("gateway", _gateway_runs),
    ("forge", _forge_runs), ("compute", _compute), ("workflow", _workflow_runs),
    ("flow", _flow_runs), ("scheduled", _scheduled), ("skill_creator", _skill_creator),
    ("agent", _agent_sessions), ("commit", _commits),
]


def _source_notes(home: Path) -> dict[str, str]:
    notes: dict[str, str] = {}
    if importlib.util.find_spec("corvin_marketplace") is None and not any((home / "workflows").glob("*/runs/*.meta.json")):
        notes["workflow"] = ("The workflow plugin does not load on this build (corvin_marketplace is not "
                             "importable), so no workflow runs are being created.")
    if "corvin_console.routes.skill_creator_api" not in sys.modules:
        notes["skill_creator"] = "Skill-creator runs are held in memory by the console process only."
    notes.setdefault("skill_creator", "In memory only — cleared when the console restarts.")
    from . import host_activity as ha  # noqa: PLC0415

    if home.name != ha.HOST_TENANT:
        notes["agent"] = notes["commit"] = ha.HOST_NOTE
    return notes


# ── aggregate ────────────────────────────────────────────────────────────────

def _tenant_home(tenant_id: str) -> Path:
    from core.paths import tenant_home  # noqa: PLC0415

    return Path(tenant_home(tenant_id))


def collect(tenant_id: str, *, now: float | None = None) -> dict[str, Any]:
    """All records for *tenant_id*, cached for AGGREGATE_TTL_S."""
    now_given = now is not None
    now = time.time() if now is None else now
    if not now_given:
        with _AGG_LOCK:
            hit = _AGG_CACHE.get(tenant_id)
            if hit and time.monotonic() - hit[0] < AGGREGATE_TTL_S:
                return hit[1]
    t0 = time.perf_counter()
    home = _tenant_home(tenant_id)
    records: list[dict] = []
    errors: dict[str, str] = {}
    for typ, fn in _SOURCES:
        try:
            records.extend(fn(home, now))
        except Exception as exc:  # noqa: BLE001 — one broken source must not blank the board
            errors[typ] = f"{type(exc).__name__}: {exc}"[:200]
    try:
        records.extend(_initiatives(tenant_id, now))
    except Exception as exc:  # noqa: BLE001
        errors["initiative"] = f"{type(exc).__name__}: {exc}"[:200]
    records = _fold_background(records)
    result = {"records": records, "errors": errors, "notes": _source_notes(home),
              "scan_ms": round((time.perf_counter() - t0) * 1000, 1), "now": now}
    if not now_given:
        with _AGG_LOCK:
            _AGG_CACHE[tenant_id] = (time.monotonic(), result)
    return result


def query(tenant_id: str, *, types: set[str] | None = None, finished_limit: int = 100,
          finished_offset: int = 0, now: float | None = None) -> dict[str, Any]:
    """The payload the console renders: active + stale in full, finished paged."""
    agg = collect(tenant_id, now=now)
    recs = agg["records"]
    by_type: dict[str, dict[str, int]] = {}
    for r in recs:
        c = by_type.setdefault(r["type"], {"active": 0, "finished": 0, "stale": 0, "failed": 0})
        if r["status"] in ACTIVE:
            c["active"] += 1
        elif r["status"] == "stale":
            c["stale"] += 1
        else:
            c["finished"] += 1
            if r["status"] == "failed":
                c["failed"] += 1
    sel = [r for r in recs if not types or r["type"] in types]
    active = sorted((r for r in sel if r["status"] in ACTIVE or r["status"] == "stale"),
                    key=lambda r: (r["status"] == "stale", -r["sort_ts"]))
    finished = sorted((r for r in sel if r["status"] in FINISHED), key=lambda r: -r["sort_ts"])
    day_ago = agg["now"] - 86400
    return {
        "server_time": _iso(agg["now"]),
        "scan_ms": agg["scan_ms"],
        "types": [{"type": t, "label": TYPE_LABELS.get(t, t), **by_type.get(t, {"active": 0, "finished": 0, "stale": 0, "failed": 0}),
                   "note": agg["notes"].get(t), "error": agg["errors"].get(t)}
                  for t in TYPE_LABELS],
        "active": active,
        "finished": finished[finished_offset: finished_offset + finished_limit],
        "finished_total": len(finished),
        "totals": {
            "active": sum(1 for r in recs if r["status"] in ACTIVE),
            "running": sum(1 for r in recs if r["status"] == "running"),
            "stale": sum(1 for r in recs if r["status"] == "stale"),
            "finished_24h": sum(1 for r in recs if r["status"] in FINISHED and r["sort_ts"] >= day_ago),
            "failed_24h": sum(1 for r in recs if r["status"] == "failed" and r["sort_ts"] >= day_ago),
            "all": len(recs),
        },
    }

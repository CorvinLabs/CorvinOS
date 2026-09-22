"""Every task / run / job this install knows about, in one normalised list.

CorvinOS has no single task store. Chat turns, background ``/task`` runs, ACS
runs, gateway runs, forge tool runs, compute runs, workflow and flow runs,
scheduled reminders and the operator's initiative tasks each persist their own
records in their own shape. This module reads each store where it already lives
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

Privacy: a bridge chat task's instruction is another person's message. Only
web-chat and CLI tasks (the operator's own console/terminal) get an
instruction preview; bridge tasks are titled by channel and persona only.

Cost: ~5 k chat-task files are re-read only when their (mtime, size) changed —
every other poll is a stat per file (``_FILE_CACHE``). The aggregate is cached
for ``AGGREGATE_TTL_S`` so concurrent pollers share one scan.
"""
from __future__ import annotations

import importlib.util
import json
import os
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
    "forge": "Forge tool",
    "compute": "Compute",
    "scheduled": "Scheduled",
    "skill_creator": "Skill creator",
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


# ── sources ──────────────────────────────────────────────────────────────────
# Each source: (type, callable(tenant_home, now) -> iterator of records,
#               callable(tenant_home) -> availability note or None)

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
        for fn in filenames:
            if not fn.endswith(".json") or fn.endswith(".events.jsonl"):
                continue
            path = Path(dirpath) / fn
            d = _read_json(path)
            if not isinstance(d, dict) or "task_id" not in d:
                continue
            inp = d.get("input") if isinstance(d.get("input"), dict) else {}
            persona = inp.get("persona") or "assistant"
            if sub in ("web", "cli"):
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
            yield _record(
                id=f"chat:{d['task_id']}", type=typ, subtype=sub, title=title,
                status=_CHAT_STATUS.get(raw, "running"), raw_status=raw,
                created=_ts(d.get("created_at")), started=_ts(d.get("started_at")),
                ended=_ts(d.get("ended_at")), now=now, last_alive=last_alive,
                detail=(d.get("result_summary") or None) if raw != "running" else f"persona {persona}",
            )


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


_SOURCES: list[tuple[str, Callable[[Path, float], Iterator[dict]]]] = [
    ("chat", _chat_tasks), ("acs", _acs_runs), ("gateway", _gateway_runs),
    ("forge", _forge_runs), ("compute", _compute), ("workflow", _workflow_runs),
    ("flow", _flow_runs), ("scheduled", _scheduled), ("skill_creator", _skill_creator),
]


def _source_notes(home: Path) -> dict[str, str]:
    notes: dict[str, str] = {}
    if importlib.util.find_spec("corvin_marketplace") is None and not any((home / "workflows").glob("*/runs/*.meta.json")):
        notes["workflow"] = ("The workflow plugin does not load on this build (corvin_marketplace is not "
                             "importable), so no workflow runs are being created.")
    if "corvin_console.routes.skill_creator_api" not in sys.modules:
        notes["skill_creator"] = "Skill-creator runs are held in memory by the console process only."
    notes.setdefault("skill_creator", "In memory only — cleared when the console restarts.")
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

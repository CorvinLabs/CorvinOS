"""scheduler.py — recurring + one-shot reminders for the bridges.

Tasks live in a single JSON file at `~/.cache/corvin-voice/schedule.json`,
maintained atomically (tmp-file + rename). The adapter calls
`materialize_due()` once per main-loop tick (~30s); any task whose `next_run`
has elapsed is dropped into the bridges' shared inbox/ as a virtual user
message tagged `_scheduled = true`. From there the normal adapter pipeline
takes over — so the user gets the reply on whichever messenger the task
was scheduled from.

One-shot vs recurring:
  - one_shot:  `at` (ISO-8601, UTC or local with offset) → fires once, then
               removed from the schedule.
  - recurring: `cron` (5-field crontab string) → after firing, `next_run` is
               recomputed by `_advance_cron()`.

Schema of one schedule entry:

    {
      "id":        "abc123",          // 6-char random
      "channel":   "telegram",        // bridge channel
      "chat_id":   "123456789",       // routing target
      "sender":    "123456789",       // for the inbox envelope's `from`
      "text":      "standup reminder",
      "persona":   "inbox",           // optional persona pin
      "created":   1735000000,        // ts
      "next_run":  1735086400,        // ts; updated for recurring after fire
      "cron":      "0 8 * * 1-5",     // or null for one-shot
      "last_fire": 1735000000         // ts; for de-dupe
    }

The task dispatch is intentionally minimal — every fire writes a normal
inbox JSON, so personas, auto-routing, voice-note synthesis, and reaction
indicators all keep working without changes.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Protects all read-modify-write operations on the schedule file.
_SCHEDULE_LOCK = threading.Lock()

try:
    from .paths import voice_dir  # type: ignore
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from paths import voice_dir  # type: ignore


def _schedule_file() -> Path:
    """Honour ``XDG_CACHE_HOME`` (legacy override) or root under ``voice_dir()``."""
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / "corvin-voice" / "schedule.json"
    return voice_dir() / "schedule.json"


# Persistent location; follows the corvinOS root by default.
SCHEDULE_FILE = _schedule_file()


# ─── load / save (atomic) ──────────────────────────────────────────────────

def load() -> list[dict[str, Any]]:
    """Read the schedule file; missing file → empty list."""
    try:
        return json.loads(SCHEDULE_FILE.read_text())
    except FileNotFoundError:
        return []
    except json.JSONDecodeError:
        # Corrupt file: keep going with an empty list rather than crashing
        # the adapter. The user can `/schedule list` to see it's empty and
        # re-add tasks.
        return []


def save(items: list[dict[str, Any]]) -> None:
    """Atomic write: tmp-file + rename so a crash mid-write can't corrupt."""
    SCHEDULE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SCHEDULE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items, indent=2, ensure_ascii=False))
    shutil.move(str(tmp), str(SCHEDULE_FILE))


# ─── tiny cron parser ─────────────────────────────────────────────────────
# Standard 5 fields:  minute  hour  day-of-month  month  day-of-week
# Each field: `*`, integer, range a-b, list a,b,c, step `*/n` or `a-b/n`.
# Day-of-week 0..6, Sun..Sat (also 7 = Sun for compat).

def _parse_field(expr: str, lo: int, hi: int) -> set[int]:
    """Expand a single cron field into the set of matching integers."""
    out: set[int] = set()
    for part in expr.split(","):
        step = 1
        if "/" in part:
            base, step_s = part.split("/", 1)
            step = int(step_s)
            if step < 1:
                raise ValueError(f"cron step must be >= 1, got {step!r}")
        else:
            base = part
        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            a, b = base.split("-", 1)
            start, end = int(a), int(b)
        else:
            start = end = int(base)
        for v in range(start, end + 1, step):
            if lo <= v <= hi:
                out.add(v)
    return out


def _matches_cron(cron: str, dt: datetime) -> bool:
    """True iff `dt` (naive local time) matches the 5-field cron expression."""
    fields = cron.split()
    if len(fields) != 5:
        raise ValueError(f"cron expression must have 5 fields, got: {cron!r}")
    minutes = _parse_field(fields[0], 0, 59)
    hours = _parse_field(fields[1], 0, 23)
    doms = _parse_field(fields[2], 1, 31)
    months = _parse_field(fields[3], 1, 12)
    raw_dows = _parse_field(fields[4], 0, 7)
    dows = {(d % 7) for d in raw_dows}  # 7 = 0 = Sun
    py_dow = (dt.weekday() + 1) % 7  # Monday=0..Sunday=6 → cron 1..0
    return (
        dt.minute in minutes
        and dt.hour in hours
        and dt.day in doms
        and dt.month in months
        and py_dow in dows
    )


def _advance_cron(cron: str, after_ts: float, max_iter: int = 366 * 24 * 60) -> float:
    """Return the next minute-aligned epoch ts >= after_ts that matches `cron`.

    Iterates minute-by-minute. Capped at one year to prevent runaways.
    """
    start = datetime.fromtimestamp(after_ts).replace(second=0, microsecond=0)
    # Step forward one minute first so calling _advance_cron immediately after
    # a fire doesn't re-fire the same minute.
    cur = start
    for _ in range(max_iter):
        cur = datetime.fromtimestamp(cur.timestamp() + 60).replace(
            second=0, microsecond=0
        )
        if _matches_cron(cron, cur):
            return cur.timestamp()
    raise ValueError(f"no matching minute within a year for cron: {cron!r}")


# ─── public API ────────────────────────────────────────────────────────────

def parse_when(spec: str) -> tuple[float | None, str | None]:
    """Parse the user-provided `when` string into (next_run_ts, cron_or_None).

    Accepted forms:
      - ISO 8601 datetime (`2026-05-06T08:00`, `2026-05-06 08:00:00+02:00`)
      - "in 30m", "in 2h", "in 90s", "in 3d"  → relative offset
      - 5-field cron string (`0 8 * * 1-5`)
    Returns (None, None) on parse failure.
    """
    spec = spec.strip()
    # Relative offset
    if spec.startswith("in "):
        rest = spec[3:].strip().lower()
        try:
            if rest.endswith("s"):
                seconds = int(rest[:-1])
            elif rest.endswith("m"):
                seconds = int(rest[:-1]) * 60
            elif rest.endswith("h"):
                seconds = int(rest[:-1]) * 3600
            elif rest.endswith("d"):
                seconds = int(rest[:-1]) * 86400
            else:
                seconds = int(rest)
            return (time.time() + seconds, None)
        except ValueError:
            return (None, None)

    # Cron heuristic: exactly 5 whitespace-separated tokens, none looking like
    # a date.
    parts = spec.split()
    if len(parts) == 5 and not any(":" in p or "-" in p and len(p) > 5 for p in parts):
        try:
            ts = _advance_cron(spec, time.time())
            return (ts, spec)
        except ValueError:
            return (None, None)

    # ISO 8601
    try:
        dt = datetime.fromisoformat(spec)
        if dt.tzinfo is None:
            ts = dt.timestamp()  # naive → local time
        else:
            ts = dt.astimezone(timezone.utc).timestamp()
        return (ts, None)
    except ValueError:
        return (None, None)


def add_task(
    *,
    channel: str,
    chat_id: str,
    sender: str,
    text: str,
    when: str,
    persona: str | None = None,
    kind: str = "text",
    workflow_name: str | None = None,
    workflow_inputs: dict[str, Any] | None = None,
    tenant_id: str = "_default",
) -> dict[str, Any]:
    """Add a new schedule entry. Returns the entry; raises ValueError on bad `when`.

    `when` is parsed by `parse_when`. For recurring tasks pass a cron string.

    Two `kind` values are supported:
      * `"text"` (default) — the existing reminder path. `text` is dropped
        into the bridge inbox at fire time and Claude formulates the reply.
      * `"workflow"` — a Layer-26 AWP workflow run. `workflow_name` and
        `workflow_inputs` are mandatory; the materializer executes
        `python -m corvin_workflows run <name> <kvs>` and writes the
        report straight into the channel outbox (no LLM round-trip).
    """
    if kind not in ("text", "workflow"):
        raise ValueError(f"unknown task kind: {kind!r}")
    if kind == "workflow":
        if not workflow_name:
            raise ValueError("kind=workflow requires workflow_name")
    next_ts, cron = parse_when(when)
    if next_ts is None:
        raise ValueError(f"could not parse `when`: {when!r}")
    item: dict[str, Any] = {
        "id": secrets.token_hex(3),
        "channel": channel,
        "chat_id": chat_id,
        "tenant_id": tenant_id,
        "sender": sender,
        "text": text,
        "persona": persona,
        "created": time.time(),
        "next_run": next_ts,
        "cron": cron,
        "last_fire": 0.0,
        "kind": kind,
    }
    if kind == "workflow":
        item["workflow_name"] = workflow_name
        item["workflow_inputs"] = dict(workflow_inputs or {})
    with _SCHEDULE_LOCK:
        items = load()
        items.append(item)
        save(items)
    return item


def list_tasks(*, channel: str | None = None, chat_id: str | None = None) -> list[dict[str, Any]]:
    """Return a copy of the schedule, optionally filtered to one chat."""
    with _SCHEDULE_LOCK:
        items = load()
    if channel is not None:
        items = [i for i in items if i.get("channel") == channel]
    if chat_id is not None:
        items = [i for i in items if str(i.get("chat_id")) == str(chat_id)]
    return items


def remove_task(task_id: str) -> bool:
    """Remove the entry with the given id. Returns True iff something was removed."""
    with _SCHEDULE_LOCK:
        items = load()
        new = [i for i in items if i.get("id") != task_id]
        if len(new) != len(items):
            save(new)
            return True
        return False


def _bridges_root() -> Path:
    """Locate the bridges/ root that contains the per-channel outbox dirs.

    `scheduler.py` lives at .../operator/bridges/shared/scheduler.py — so
    bridges/ is exactly one level up. The override env `ADAPTER_OUTBOX` is
    honoured by tests (single absolute path → channel-agnostic outbox).
    """
    env = os.environ.get("ADAPTER_OUTBOX")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent


def _run_workflow_to_outbox(
    item: dict[str, Any], now: float, bridges_root: Path | None = None
) -> bool:
    """Execute the workflow named by the task and write the rendered report
    into `bridges/<channel>/outbox/<id>.json`. Returns True on success.

    The workflow runs via subprocess so any error in the workflow runtime
    cannot crash the scheduler. Output is captured via stdout and wrapped
    in the standard outbox envelope shape the daemons already understand.
    """
    import subprocess

    workflow_name = item.get("workflow_name") or ""
    workflow_inputs = item.get("workflow_inputs") or {}
    pkg_root = (
        Path(__file__).resolve().parent.parent.parent.parent / "core" / "workflows"
    )

    cmd = [
        sys.executable, "-m", "corvin_workflows",
        "run", workflow_name,
    ]
    import re as _re
    for k, v in workflow_inputs.items():
        _k = str(k)
        if not _re.match(r'^[a-zA-Z0-9_\-]+$', _k):
            continue  # skip keys with unsafe characters (no shell=True, but be conservative)
        cmd.append(f"{_k}={v}")

    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        str(pkg_root) + (os.pathsep + existing if existing else "")
    )

    channel = item["channel"]
    # Write into the SHARED outbox the daemons actually poll, NOT a per-channel
    # dir. Every messenger daemon polls operator/bridges/shared/outbox
    # (SHARED=resolve(__dirname,'..','shared')); the per-channel
    # bridges/<channel>/outbox dirs are never polled, so scheduled workflow
    # reports written there were silently orphaned (13 unread sched_wf_*.json
    # piled up in bridges/console/outbox). The envelope carries `channel`, which
    # the shared poller uses to route to the right daemon.
    _env_outbox = os.environ.get("ADAPTER_OUTBOX")
    if _env_outbox:
        # Channel-agnostic single outbox dir (tests / custom deploys) — the env
        # IS the dir, matching adapter.py / notification_relay.py semantics.
        outbox_dir = Path(os.path.expanduser(os.path.expandvars(_env_outbox)))
    else:
        outbox_dir = (bridges_root or _bridges_root()) / "shared" / "outbox"
    try:
        outbox_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False

    # Write a "starting" notification BEFORE the subprocess so the user gets
    # immediate feedback instead of silence for up to 120 s.
    _start_msg_id = f"sched_wf_start_{item['id']}_{int(now)}"
    _start_payload = {
        "msg_id": _start_msg_id,
        "chat_id": str(item["chat_id"]),
        "text": f"⏳ Workflow `{workflow_name}` gestartet…",
        "channel": channel,
        "_scheduled_workflow": True,
        "ts": now,
    }
    try:
        (outbox_dir / f"{_start_msg_id}.json").write_text(
            json.dumps(_start_payload, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        pass  # best-effort — don't block the actual run

    try:
        result = subprocess.run(
            cmd, env=env, capture_output=True, text=True, timeout=120
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        body = f"⚠ workflow `{workflow_name}` failed to run: {e}"
        rc = 99
    else:
        body = result.stdout or "(no output)"
        if result.returncode != 0 and result.stderr:
            body += "\n\n⚠ stderr:\n" + result.stderr[:500]
        rc = result.returncode

    header = (
        f"⏰ **Scheduled workflow run** — `{workflow_name}` "
        f"(task `{item['id']}`)\n\n"
    )
    text = header + body

    # Truncate to Discord's 2000-char hard limit minus headroom for daemon
    # chunking. Long reports get a "(truncated)" marker.
    LIMIT = 1900
    if len(text) > LIMIT:
        text = text[: LIMIT - 40] + "\n…(truncated — use /workflow run for full output)"

    msg_id = f"sched_wf_{item['id']}_{int(now)}"
    payload = {
        "msg_id": msg_id,
        "chat_id": str(item["chat_id"]),
        "text": text,
        "channel": channel,
        "_scheduled_workflow": True,
        "ts": now,
        "exit_code": rc,
    }
    # EU AI Act Art. 50 §4 — this report body is AI-generated content delivered
    # out-of-band (an autonomous, system-initiated notification), so it carries
    # the same provenance marking every normal AI reply gets (adapter._envelope /
    # completion_notify). Without this the scheduler's autonomous push would be
    # an unmarked AI message, inconsistent with the rest of the system.
    from provenance import build_provenance  # type: ignore
    payload["_final"] = True
    payload["provenance"] = build_provenance(channel, item["chat_id"])
    out_path = outbox_dir / f"{msg_id}.json"
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(out_path)
    except OSError:
        return False
    return True


def materialize_due(inbox_dir: Path, now: float | None = None) -> list[dict[str, Any]]:
    """Drop a virtual inbox file for every text-task whose `next_run` has
    elapsed; for workflow-tasks execute the workflow and write straight into
    the channel outbox.

    For one-shot tasks (`cron` is None): the entry is removed after firing.
    For recurring tasks: `next_run` is advanced via `_advance_cron`.

    Returns the list of fired entries (already with their post-fire state).
    Designed to be called from the adapter's main poll loop.
    """
    if now is None:
        now = time.time()
    with _SCHEDULE_LOCK:
        items = load()
    fired: list[dict[str, Any]] = []
    keep: list[dict[str, Any]] = []
    for it in items:
        try:
            nr = float(it.get("next_run", 0))
        except (TypeError, ValueError):
            keep.append(it)
            continue
        if nr > now:
            keep.append(it)
            continue
        # De-dupe: don't fire the same task twice within 30 s.
        if it.get("last_fire", 0) and now - it["last_fire"] < 30:
            keep.append(it)
            continue

        kind = it.get("kind", "text")
        if kind == "workflow":
            ok = _run_workflow_to_outbox(it, now=now)
            if not ok:
                # Outbox write failed — keep the task, retry next tick.
                keep.append(it)
                continue
        else:
            # Materialise into the shared inbox (the legacy text-reminder path).
            msg_id = f"sched_{it['id']}_{int(now)}"
            envelope = {
                "id": msg_id,
                "channel": it["channel"],
                "from": it["sender"],
                "chat_id": it["chat_id"],
                "text": it["text"],
                "ts": int(now * 1000),
                "_scheduled": True,
            }
            if it.get("tenant_id"):
                envelope["tenant_id"] = it["tenant_id"]
            if it.get("persona"):
                envelope["_persona_hint"] = it["persona"]
            try:
                inbox_dir.mkdir(parents=True, exist_ok=True)
                (inbox_dir / f"{msg_id}.json").write_text(
                    json.dumps(envelope, ensure_ascii=False, indent=2)
                )
            except OSError:
                # File-system trouble — leave the task in place, retry next tick.
                keep.append(it)
                continue

        fired.append({**it, "last_fire": now})
        if it.get("cron"):
            try:
                next_run = _advance_cron(it["cron"], now)
            except ValueError:
                # Bad cron → treat as one-shot, drop it.
                continue
            keep.append({**it, "next_run": next_run, "last_fire": now})
        # one-shot: just don't add to keep
    if fired:
        fired_ids = {f["id"] for f in fired}
        cron_updates = {it["id"]: it for it in keep if it.get("cron")}
        with _SCHEDULE_LOCK:
            # Reload to pick up tasks added or removed while we were firing,
            # then merge our cron-advancement updates. This prevents the TOCTOU
            # race where a concurrent add_task/remove_task call is overwritten
            # by a stale save of the pre-fire snapshot.
            current = load()
            merged: list[dict[str, Any]] = []
            seen_ids: set[str] = set()
            for it in current:
                tid = it.get("id", "")
                if tid in fired_ids:
                    if tid in cron_updates:
                        merged.append(cron_updates[tid])
                    # one-shot: drop it
                else:
                    merged.append(it)
                seen_ids.add(tid)
            # Carry forward any keep entries not present in the reloaded file
            # (edge case: concurrent remove_task deleted them, so they're gone).
            for it in keep:
                if it.get("id") not in seen_ids and it.get("id") not in fired_ids:
                    merged.append(it)
            save(merged)
    return fired


def humanize(item: dict[str, Any], now: float | None = None) -> str:
    """One-line summary for `/schedule list`."""
    if now is None:
        now = time.time()
    nr = item.get("next_run", 0)
    when_dt = datetime.fromtimestamp(nr).strftime("%Y-%m-%d %H:%M")
    delta = nr - now
    if delta > 0:
        if delta < 60:
            rel = f"in {int(delta)}s"
        elif delta < 3600:
            rel = f"in {int(delta / 60)}m"
        elif delta < 86400:
            rel = f"in {int(delta / 3600)}h"
        else:
            rel = f"in {int(delta / 86400)}d"
    else:
        rel = "due now"
    cron_part = f" (cron: {item['cron']})" if item.get("cron") else ""
    persona_part = f" [{item['persona']}]" if item.get("persona") else ""
    return f"{item['id']}{persona_part}: {when_dt} ({rel}){cron_part} — {item['text']}"

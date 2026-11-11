"""Live work on a project: who is working, which tools ran, which files changed (CONCEPT-0128, ADR-2320, REVIEW-0060).

An in-memory, per-tenant, per-project event channel. It is a VIEW of what already happens - the chat ledger and the audit chain stay
the record. Bounded (ring buffer, subscribers, streams per tenant), lost on restart (``epoch`` makes a restart visible), and safe by
construction: an event has a CLOSED set of fields; a tool event carries a derived ``detail`` (program name, file NAME, host) and never
an input, a command body, a URL query, a prompt, an answer or a file's content. One stated exception: ``turn_started`` carries the chat's
title, the same string the operator already sees in the chat list (it is derived from the first words of the first prompt).

Producers: ``chat_runtime`` (via :class:`TurnTracker`) for the turns of a chat attached to the project, and a per-project watcher that
polls git while somebody watches, so a change made by anything else shows up too. Consumers: SSE streams in ``routes/projects.py``.
"""
from __future__ import annotations

import asyncio
import collections
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any, Optional

_log = logging.getLogger("corvin.projects.activity")

EPOCH = str(int(time.time() * 1000))        # this process; a different epoch tells a client the console restarted
MAX_EVENTS = 300
MAX_STREAMS_PER_TENANT = 8
STREAM_LIFETIME_S = 10.0                    # uvicorn force-cancels in-flight responses at 15 s and never tells the app (REVIEW-0060 #1)
WATCH_MIN_INTERVAL_S = 3.0
WATCH_MAX_INTERVAL_S = 30.0
WATCH_GIT_TIMEOUT_S = 8
# After the last sign of work (a turn started, a tool ran, a turn finished) the watcher keeps looking for this long even while the host is
# saturated. The last write of a turn lands just before the turn ends, and a turn's end (the CLI exiting) is exactly when the host is busiest:
# the watcher then paused and the file showed up only with the NEXT turn (measured live 2026-11-09, one run in five).
WATCH_GRACE_S = 20.0
IDLE_AGENT_TTL_S = 1800.0
PRUNE_AFTER_S = 86400.0
_MAX_BURST = 40
_MAX_CHANNELS_BEFORE_PRUNE = 200

KINDS = ("turn_started", "tool_use", "turn_finished", "file_changed", "file_settled", "files_burst")
REASONS = ("done", "error", "closed")
_STR_LIMITS = {"session_id": 64, "title": 80, "name": 64, "id": 64, "status": 16, "path": 300, "area": 16, "reason": 12, "detail": 80}
_PROGRAM_RE = re.compile(r"[A-Za-z0-9._/-]{1,24}")
_HOST_RE = re.compile(r"^[a-z0-9.-]{1,80}$")
_FILE_TOOLS = {"read", "edit", "multiedit", "write", "notebookedit"}


class StreamLimit(RuntimeError):
    """Too many open streams for this tenant."""


def describe_tool(name: str, tool_input: Any) -> str:
    """The ONE place that turns a tool call into what an event may say. Bash: the program name only; file tools: the file NAME;
    WebFetch: the host; everything else: nothing. The raw input never leaves this function."""
    try:
        n = (name or "").lower().replace("_", "")
        inp = tool_input if isinstance(tool_input, dict) else {}
        if n == "bash":
            cmd = str(inp.get("command") or "").strip()
            first = cmd.split(None, 1)[0] if cmd else ""
            m = _PROGRAM_RE.fullmatch(first.rsplit("/", 1)[-1]) if first else None
            return m.group(0) if m else ""
        if n in _FILE_TOOLS:
            fp = str(inp.get("file_path") or inp.get("notebook_path") or "")
            return Path(fp).name[:80] if fp else ""
        if n == "webfetch":
            from urllib.parse import urlparse  # noqa: PLC0415
            host = (urlparse(str(inp.get("url") or "")).hostname or "").lower()
            return host if _HOST_RE.match(host) else ""
    except Exception:  # noqa: BLE001 - a description must never break a turn
        pass
    return ""


class _Channel:
    def __init__(self) -> None:
        self.events: collections.deque[dict[str, Any]] = collections.deque(maxlen=MAX_EVENTS)
        self.seq = 0
        self.agents: dict[str, dict[str, Any]] = {}
        self.subs: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = set()
        self.watcher: Optional[asyncio.Task] = None
        self.files: Optional[dict[str, tuple]] = None     # baseline of the last look; survives watcher restarts
        self.state = "off"                                # off | running | paused
        self.last_work = 0.0                              # monotonic time of the last turn/tool event
        self.last_touch = time.time()


_lock = threading.Lock()
_channels: dict[tuple[str, str], _Channel] = {}


def _chan(tenant: str, project: str) -> _Channel:
    key = (tenant, project)
    ch = _channels.get(key)
    if ch is None:
        if len(_channels) >= _MAX_CHANNELS_BEFORE_PRUNE:
            _prune_locked()
        ch = _channels[key] = _Channel()
    return ch


def _prune_locked() -> None:
    now = time.time()
    for k in [k for k, c in _channels.items() if not c.subs and not any(a["state"] == "working" for a in c.agents.values())
              and now - c.last_touch > PRUNE_AFTER_S]:
        del _channels[k]


def _clean(fields: dict[str, Any]) -> dict[str, Any]:
    """The closed field set. Anything else - above all an ``input`` dict - is dropped here, at the single entry."""
    out: dict[str, Any] = {}
    for k, v in fields.items():
        if k in _STR_LIMITS and isinstance(v, str):
            out[k] = v[: _STR_LIMITS[k]]
        elif k in ("additions", "deletions", "count") and (isinstance(v, int) and not isinstance(v, bool) or v is None):
            out[k] = v
        elif k in ("ok", "sub") and isinstance(v, bool):
            out[k] = v
        elif k == "links" and isinstance(v, list):
            out[k] = [{"id": str(x.get("id", ""))[:24], "kind": str(x.get("kind", ""))[:12]} for x in v[:6] if isinstance(x, dict)]
    if out.get("reason") is not None and out["reason"] not in REASONS and out.get("status") is None:
        out.pop("reason")
    return out


def _expire_idle(ch: _Channel, now: float) -> None:
    for k in [k for k, v in ch.agents.items() if v["state"] == "idle" and now - v.get("finished", now) > IDLE_AGENT_TTL_S]:
        del ch.agents[k]


def _apply_to_agents(ch: _Channel, ev: dict[str, Any]) -> None:
    sid = ev.get("session_id")
    if sid:
        now, a = ev["ts"], ch.agents.get(sid)
        if ev["kind"] == "turn_started":
            ch.agents[sid] = {"session_id": sid, "title": ev.get("title", ""), "state": "working", "since": now, "tool": None, "detail": "", "tools": 0}
        elif a is not None and ev["kind"] == "tool_use":
            a.update(tool=ev.get("name"), detail=ev.get("detail", ""), tools=a["tools"] + 1)
        elif a is not None and ev["kind"] == "turn_finished":
            a.update(state="idle", tool=None, detail="", finished=now, ok=ev.get("ok", True))
    _expire_idle(ch, ev["ts"])


def post_event(tenant: str, project: str, kind: str, **fields: Any) -> Optional[dict[str, Any]]:
    """Record one event and hand it to every open stream. Safe from any thread; NEVER raises into the caller's turn."""
    try:
        if kind not in KINDS:
            raise ValueError(f"unknown activity kind {kind!r}")
        with _lock:
            ch = _chan(tenant, project)
            ch.seq += 1
            ch.last_touch = time.time()
            if kind in ("turn_started", "tool_use", "turn_finished"):
                ch.last_work = time.monotonic()
            ev = {"seq": ch.seq, "id": f"{EPOCH}:{ch.seq}", "ts": ch.last_touch, "kind": kind, **_clean({k: v for k, v in fields.items() if k != "id"})}
            if kind == "tool_use" and isinstance(fields.get("id"), str):
                ev["tool_id"] = fields["id"][:64]
            ch.events.append(ev)
            _apply_to_agents(ch, ev)
            subs = list(ch.subs)
        for loop, q in subs:
            try:
                loop.call_soon_threadsafe(_offer, q, ev)
            except RuntimeError:           # that loop is gone; the stream's own cleanup removes it
                pass
        return ev
    except Exception:  # noqa: BLE001
        _log.debug("activity.publish failed", exc_info=True)
        return None


def _offer(q: asyncio.Queue, ev: dict[str, Any]) -> None:
    try:
        q.put_nowait(ev)
    except asyncio.QueueFull:
        pass                               # a slow reader loses live events, not the backlog (it re-reads with `after`)


def parse_cursor(raw: Optional[str]) -> tuple[Optional[str], int]:
    """``"<epoch>:<seq>"`` -> (epoch, seq); anything else -> (None, 0)."""
    if raw and re.fullmatch(r"[0-9]{1,16}:[0-9]{1,12}", raw):
        e, s = raw.split(":")
        return e, int(s)
    return None, 0


def snapshot(tenant: str, project: str, cursor: Optional[str] = None) -> dict[str, Any]:
    """The state a client needs to start: agents, the backlog after its cursor, and whether its cursor is stale (another epoch)."""
    epoch, after = parse_cursor(cursor)
    reset = epoch is not None and epoch != EPOCH
    with _lock:
        ch = _channels.get((tenant, project))
        if ch is None:
            return {"epoch": EPOCH, "seq": 0, "oldest_seq": 0, "reset": reset, "gap": False, "events": [], "agents": [], "watcher": "off",
                    "note": "no activity since the console started"}
        _expire_idle(ch, time.time())
        oldest = ch.events[0]["seq"] if ch.events else ch.seq + 1
        use_after = 0 if reset else after
        gap = (not reset) and after > 0 and after + 1 < oldest and bool(ch.events)
        return {"epoch": EPOCH, "seq": ch.seq, "oldest_seq": oldest, "reset": reset, "gap": gap,
                "events": [e for e in ch.events if e["seq"] > use_after], "agents": [dict(a) for a in ch.agents.values()],
                "watcher": ch.state, "note": "a live view; kept in memory since the console started"}


def stream_count(tenant: str) -> int:
    with _lock:
        return sum(len(c.subs) for (t, _p), c in _channels.items() if t == tenant)


def subscribe(tenant: str, project: str, root_for: Any) -> asyncio.Queue:
    """Open a stream (inside the event loop). ``root_for()`` returns the project's folder (re-read each watcher round, so a moved
    project is followed). Starts the project's file watcher if it is not running."""
    with _lock:
        if sum(len(c.subs) for (t, _p), c in _channels.items() if t == tenant) >= MAX_STREAMS_PER_TENANT:
            raise StreamLimit("too many open activity streams")
        ch = _chan(tenant, project)
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        ch.subs.add((loop, q))
        ch.last_touch = time.time()
        if ch.watcher is None or ch.watcher.done():
            ch.watcher = loop.create_task(_watch(tenant=tenant, project=project, root_for=root_for))
    return q


def unsubscribe(tenant: str, project: str, q: asyncio.Queue) -> None:
    with _lock:
        ch = _channels.get((tenant, project))
        if ch is not None:
            ch.subs = {(lp, qq) for lp, qq in ch.subs if qq is not q}


def _key(f: dict[str, Any]) -> tuple:
    return (f["status"], f.get("additions"), f.get("deletions"))


async def _watch(tenant: str, project: str, root_for: Any) -> None:
    """Poll the project's git status while anyone is watching; publish what changed since the last look (the first ever look is silent)."""
    from . import changes  # noqa: PLC0415
    try:
        from corvin_console import loop_health  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        loop_health = None
    interval = WATCH_MIN_INTERVAL_S
    while True:
        with _lock:
            ch = _channels.get((tenant, project))
            if ch is None or not ch.subs:
                if ch is not None:
                    ch.state, ch.watcher = "off", None
                return
        try:
            with _lock:
                working = any(a["state"] == "working" for a in ch.agents.values())
            # Optional work yields to a busy host - EXCEPT while an agent works on this project: that is exactly when the user watches, and
            # starting a real turn is itself what stalls the loop and marks the host saturated (found live, 2026-11-08). One git status in a
            # worker thread every few seconds is the cost.
            if working:
                with _lock:
                    ch.last_work = time.monotonic()
            recent = (time.monotonic() - ch.last_work) < WATCH_GRACE_S
            if loop_health is not None and loop_health.host_saturated() and not working and not recent:
                with _lock:
                    ch.state = "paused"
            else:
                root = await asyncio.to_thread(root_for)          # may read the plugin registry / stat the KB: never on the loop
                if root is not None:
                    t0 = time.monotonic()
                    files, truncated = await asyncio.to_thread(changes.working_changes_ex, Path(root), count_lines=False, timeout=WATCH_GIT_TIMEOUT_S)
                    took = time.monotonic() - t0
                    now = {f["path"]: f for f in files}
                    with _lock:
                        ch.state = "running"
                        base, ch.files = ch.files, {p: _key(f) for p, f in now.items()}
                    if base is not None:
                        new = [f for p, f in now.items() if base.get(p) != _key(f)]
                        gone = [] if truncated else [p for p in base if p not in now]       # a cut list would fake "settled" events
                        if len(new) + len(gone) > _MAX_BURST:
                            post_event(tenant=tenant, project=project, kind="files_burst", count=len(new) + len(gone))
                        else:
                            for f in new:
                                post_event(tenant=tenant, project=project, kind="file_changed", path=f["path"], status=f["status"], additions=f.get("additions"),
                                        deletions=f.get("deletions"), area=f["area"], links=f.get("links", []))
                            for p in gone:
                                post_event(tenant=tenant, project=project, kind="file_settled", path=p)
                    interval = min(WATCH_MAX_INTERVAL_S, max(WATCH_MIN_INTERVAL_S, took * 5))   # back off on a slow round
        except Exception:  # noqa: BLE001 - a failed round keeps the old baseline and never kills the watcher
            _log.debug("activity watcher round failed", exc_info=True)
            interval = min(WATCH_MAX_INTERVAL_S, interval * 2)
        await asyncio.sleep(interval)


class TurnTracker:
    """One chat turn on an attached project. Resolves the project ONCE, publishes the finish exactly once, swallows every error."""

    def __init__(self, tenant: str, project: str, session_id: str, title: str) -> None:
        self.tenant, self.project, self.session_id, self.title = tenant, project, session_id, (title or "")[:80]
        self._finished = False
        self.reason = "closed"                # a closed generator cannot be told from a cancel; the label says so

    def start(self) -> None:
        post_event(tenant=self.tenant, project=self.project, kind="turn_started", session_id=self.session_id, title=self.title)

    def tool(self, name: str, tool_input: Any, *, tool_id: str = "", sub: bool = False) -> None:
        post_event(tenant=self.tenant, project=self.project, kind="tool_use", session_id=self.session_id, name=str(name or "")[:64],
                detail=describe_tool(name, tool_input), id=tool_id, sub=bool(sub))

    def finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        post_event(tenant=self.tenant, project=self.project, kind="turn_finished", session_id=self.session_id, ok=self.reason == "done", reason=self.reason)


def tracker_for(tenant: str, session_id: str, title: str) -> Optional[TurnTracker]:
    """A tracker if this chat is attached to a project of the tenant, else None. Never raises."""
    try:
        from . import attachment, registry  # noqa: PLC0415
        pid = attachment.attached(tenant_id=tenant, sid=session_id)
        from . import builtin  # noqa: PLC0415
        e = None if not pid or builtin.is_builtin(pid) else registry.get(tenant_id=tenant, project_id=pid)
        if pid and (builtin.is_builtin(pid) or (e is not None and not registry.is_trashed(e))):
            return TurnTracker(tenant, pid, session_id, title)
    except Exception:  # noqa: BLE001
        _log.debug("activity.tracker_for failed", exc_info=True)
    return None

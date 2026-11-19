"""SSE streams for the console — Phase D.

Single endpoint ``GET /v1/console/audit/stream`` that tails the
tenant's hash-chain via a byte-cursor and emits each new line as
an SSE frame. Filters by severity + event_prefix mirror the
Phase-B ``/audit/tail`` query-shape so the UI can re-use them.

The Agents-Live view in the SPA reuses this same endpoint with
``event_prefix=`` set to the union of agent-relevant prefixes
(``gateway.``, ``bridge.``, ``tool.``, ``skill.``, ``console.``).

Wire format (per SSE spec):

    event: audit.event
    data: {"ts":..., "event_type":"...", "severity":"...", ...}

    event: audit.checkpoint
    data: {"ts":...}

The ``audit.checkpoint`` frame fires once after the initial sweep
(useful as a "stream is alive" probe in tests + clients), and is
NOT emitted again during the live-tail loop.

Stream is read-only. It does NOT write into the chain.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from pathlib import Path
from typing import Annotated, Any, AsyncIterator

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from .. import auth as session_auth
from ..deps import require_session

from .. import _bootstrap
_forge_paths = _bootstrap.forge_paths


router = APIRouter()

# Tail loop tunables.
_DEFAULT_POLL_S = 1.0
_DEFAULT_MAX_S = 1800.0   # 30 min hard cap per connection

# Chain I/O bounds (review 2026-11-19 round 3, R3-NEW-10). The subscribe
# backfill used to ``read()`` the WHOLE chain inside the async generator - on
# a 500 MB chain that is seconds of event-loop time and 0.5 GB of memory per
# subscriber. Both readers below are BLOCKING and are only ever called through
# an awaited ``asyncio.to_thread``.
#: Tail window the subscribe backfill looks at. History older than this is not
#: offered; the ``audit.checkpoint`` frame says so (``history_truncated``).
_BACKFILL_MAX_SCAN_BYTES = 8 * 1024 * 1024
#: Most bytes one live-tail poll reads. A burst larger than this is delivered
#: over several polls - nothing is skipped.
_POLL_MAX_BYTES = 4 * 1024 * 1024

# Hash-chain bookkeeping the SPA never needs to render.
_STRIPPED_FIELDS = frozenset({
    "hash", "prev_hash", "mac", "instance_sig", "instance_id",
})


def _audit_path(tid: str) -> Path:
    return _forge_paths.tenant_global_dir(tid) / "forge" / "audit.jsonl"


def _project(ev: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in ev.items() if k not in _STRIPPED_FIELDS}
    # SECURITY (L16 / GDPR Art. 5 — metadata only): the SSE audit stream must NOT
    # leak raw audit `details` (user / uid / command / error / file / name …).
    # Sanitize via the SAME allowlist+PII-denylist the REST audit-tail uses, so
    # the two stay in lockstep (the REST route already sanitized; the stream did
    # not). Fail-CLOSED: if the sanitizer can't load, drop details rather than
    # emit them raw.
    det = out.get("details")
    if isinstance(det, dict):
        try:
            from .audit_tail import _sanitize_details as _sd  # noqa: PLC0415
            out["details"] = _sd(det)
        except Exception:  # noqa: BLE001
            out["details"] = {}
    return out


def _format_frame(event_name: str, data: dict[str, Any]) -> bytes:
    """Encode one SSE frame. Order is load-bearing per the spec
    (``event:`` BEFORE ``data:``)."""
    return f"event: {event_name}\ndata: {json.dumps(data, default=str)}\n\n".encode("utf-8")


def _matches_filter(
    ev: dict[str, Any],
    *,
    severity: str | None,
    prefixes: list[str] | None,
) -> bool:
    if severity:
        if ev.get("severity") != severity:
            return False
    if prefixes:
        et = ev.get("event_type", "")
        if not any(et.startswith(p) for p in prefixes):
            return False
    return True


def _parse_line(raw: bytes) -> dict[str, Any] | None:
    line = raw.strip()
    if not line:
        return None
    try:
        ev = json.loads(line.decode("utf-8", errors="replace"))
    except ValueError:
        return None
    return ev if isinstance(ev, dict) else None


def _backfill(
    path: Path,
    *,
    floor: float,
    severity: str | None,
    prefixes: list[str] | None,
    history_count: int,
    max_scan_bytes: int = _BACKFILL_MAX_SCAN_BYTES,
) -> tuple[list[bytes], int, bool]:
    """Subscribe sweep. BLOCKING - call through ``asyncio.to_thread``.

    Streams the last ``max_scan_bytes`` of the chain line by line and returns
    ``(frames, cursor, truncated)``: the SSE frames of the trailing
    ``history_count`` matching events, the byte cursor for the live tail, and
    whether the chain is larger than the window.

    The cursor is the offset just past the LAST COMPLETE LINE (one ending in
    a newline). A record the writer is still appending is left for the live tail
    instead of being parsed as garbage and lost.
    """
    try:
        fh = path.open("rb")
    except OSError:
        return [], 0, False
    recent: deque[dict[str, Any]] = deque(maxlen=history_count) if history_count > 0 else deque(maxlen=0)
    with fh:
        try:
            size = fh.seek(0, 2)
            start = max(0, size - max_scan_bytes)
            skip_partial = False
            if start > 0:
                fh.seek(start - 1)
                skip_partial = fh.read(1) != b"\n"
            fh.seek(start)
            cursor = start
            for raw in fh:
                if not raw.endswith(b"\n"):
                    break  # half-written last line: not consumed
                cursor += len(raw)
                if skip_partial:
                    skip_partial = False
                    continue
                ev = _parse_line(raw)
                if ev is None:
                    continue
                ts = ev.get("ts")
                if not isinstance(ts, (int, float)) or ts < floor:
                    continue
                if _matches_filter(ev, severity=severity, prefixes=prefixes):
                    recent.append(ev)
        except OSError:
            return [], 0, False
    frames = [_format_frame("audit.event", {"event": _project(ev)}) for ev in recent]
    return frames, cursor, start > 0


def _read_new(
    path: Path,
    cursor: int,
    *,
    severity: str | None,
    prefixes: list[str] | None,
    max_bytes: int = _POLL_MAX_BYTES,
) -> tuple[list[bytes], int]:
    """One live-tail poll. BLOCKING - call through ``asyncio.to_thread``.

    Reads at most ``max_bytes`` after ``cursor`` and returns ``(frames,
    new_cursor)``. The cursor only ever advances to the end of the last
    complete line in the chunk, so a line cut by the chunk boundary (or still
    being written) is read again, whole, by the next poll. Before this, the
    cursor advanced by the full chunk and the cut line was dropped for good.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return [], cursor
    if size < cursor:
        # The file shrank (rotation/replacement). Re-anchor at its end; the
        # stream is a live view, not a replay.
        return [], size
    if size == cursor:
        return [], cursor
    try:
        with path.open("rb") as fh:
            fh.seek(cursor)
            chunk = fh.read(min(size - cursor, max_bytes))
    except OSError:
        return [], cursor
    end = chunk.rfind(b"\n")
    if end < 0:
        if len(chunk) >= max_bytes:
            # One line longer than the whole budget: skip it rather than stall.
            return [], cursor + len(chunk)
        return [], cursor
    frames: list[bytes] = []
    for raw in chunk[:end + 1].split(b"\n"):
        ev = _parse_line(raw)
        if ev is None or not _matches_filter(ev, severity=severity, prefixes=prefixes):
            continue
        frames.append(_format_frame("audit.event", {"event": _project(ev)}))
    return frames, cursor + end + 1


@router.get("/audit/stream")
async def audit_stream(
    request: Request,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    since: float | None = Query(default=None, ge=0,
                                 description="wall-clock floor; default=now"),
    severity: str | None = Query(default=None,
                                  description="filter: INFO|WARNING|CRITICAL"),
    event_prefix: str | None = Query(
        default=None,
        description="comma-separated event-type prefixes; OR-combined"),
    poll_interval_s: float = Query(default=_DEFAULT_POLL_S, ge=0.1, le=10.0),
    max_seconds: float = Query(default=_DEFAULT_MAX_S, ge=0.1, le=3600.0),
    history_count: int = Query(default=20, ge=0, le=500,
                                description="emit up to N pre-existing events on subscribe"),
) -> StreamingResponse:
    """Live-tail the owner's audit chain.

    On subscribe:
      1. Scan the last 8 MiB of the chain, emit up to ``history_count``
         events that match the filters (oldest of those first), then
      2. Anchor the byte cursor to the end of the last complete line, then
      3. Emit one ``audit.checkpoint`` frame, then
      4. Poll the file every ``poll_interval_s`` and emit each new
         line that matches the filters.

    The connection self-terminates after ``max_seconds`` (default 30
    min); the SPA reconnects automatically via EventSource.
    """
    tid = rec.tenant_id
    path = _audit_path(tid)
    floor = since if since is not None else 0.0

    prefixes: list[str] | None = None
    if event_prefix:
        prefixes = [p.strip() for p in event_prefix.split(",") if p.strip()]
        if not prefixes:
            prefixes = None

    async def gen() -> AsyncIterator[bytes]:
        deadline = time.time() + max_seconds

        # Initial sweep: bounded tail of the file -> backfill + cursor anchor.
        # Off the event loop; the chain can be hundreds of megabytes.
        frames, cursor, truncated = await asyncio.to_thread(
            _backfill, path, floor=floor, severity=severity, prefixes=prefixes,
            history_count=history_count,
        )
        for frame in frames:
            yield frame

        yield _format_frame("audit.checkpoint", {
            "ts": time.time(),
            "cursor": cursor,
            "tenant_id": tid,
            "history_truncated": truncated,
        })

        # Live-tail loop.
        while time.time() < deadline:
            if await request.is_disconnected():
                return
            await asyncio.sleep(poll_interval_s)
            frames, cursor = await asyncio.to_thread(
                _read_new, path, cursor, severity=severity, prefixes=prefixes,
            )
            for frame in frames:
                yield frame

        # Polite stream-end signal so the SPA can decide whether to
        # reconnect or surface a "stopped" indicator.
        yield _format_frame("audit.stream_end", {"ts": time.time(),
                                                  "reason": "max_seconds"})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":     "no-cache",
            "X-Accel-Buffering": "no",   # nginx: don't buffer SSE
            "Connection":        "keep-alive",
        },
    )

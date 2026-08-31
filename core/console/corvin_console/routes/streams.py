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
      1. Walk the file backwards, emit up to ``history_count`` events
         that match the filters (oldest of those first), then
      2. Anchor the byte cursor to current EOF, then
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
        cursor = 0
        deadline = time.time() + max_seconds

        # Initial sweep: tail of the file → backfill + cursor anchor.
        if path.exists():
            try:
                with path.open("rb") as fh:
                    raw = fh.read()
            except OSError:
                raw = b""
            cursor = len(raw)
            text = raw.decode("utf-8", errors="replace")
            recent: list[dict[str, Any]] = []
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = ev.get("ts")
                if not isinstance(ts, (int, float)):
                    continue
                if ts < floor:
                    continue
                if not _matches_filter(ev, severity=severity, prefixes=prefixes):
                    continue
                recent.append(ev)
            # Keep only the trailing ``history_count`` after the filter.
            for ev in recent[-history_count:]:
                yield _format_frame("audit.event", {"event": _project(ev)})

        yield _format_frame("audit.checkpoint", {
            "ts": time.time(),
            "cursor": cursor,
            "tenant_id": tid,
        })

        # Live-tail loop.
        while time.time() < deadline:
            if await request.is_disconnected():
                return
            await asyncio.sleep(poll_interval_s)
            if not path.exists():
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size <= cursor:
                continue
            try:
                with path.open("rb") as fh:
                    fh.seek(cursor)
                    chunk = fh.read(size - cursor)
            except OSError:
                continue
            cursor += len(chunk)
            text = chunk.decode("utf-8", errors="replace")
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not _matches_filter(ev, severity=severity, prefixes=prefixes):
                    continue
                yield _format_frame("audit.event", {"event": _project(ev)})

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

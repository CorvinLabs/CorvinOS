"""SSE stream for settings-file change notifications.

GET /settings/stream

Watches all tenant configuration files, bridge settings, and engine keys for
mtime changes and emits ``settings.changed`` events. The frontend uses these
to invalidate its React-Query caches — keeping every settings page live
without manual refresh or aggressive polling.

Wire format (per SSE spec):

    event: settings.changed
    data: {"domains": ["ldd", "bridge.discord"], "ts": 1234567890.0}

    event: heartbeat
    data: {"ts": 1234567890.0}

Domain vocabulary
-----------------
tenant          <global>/tenant.corvin.yaml
ldd             <global>/ldd.json
dialectic       <global>/dialectic.json
relay           <global>/relay.json
branding        <global>/branding.yaml
data_policy     <global>/data_policy.yaml
engines         ~/.config/corvin-voice/service.env
bridge.<ch>     operator/bridges/<ch>/settings.json
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Annotated, AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from .. import auth as session_auth
from ..deps import require_session

from .. import _bootstrap
_forge_paths = _bootstrap.forge_paths
_REPO = _bootstrap._REPO

router = APIRouter()

_POLL_S = 2.0
_HEARTBEAT_TICKS = 15       # heartbeat every 15 × 2 s = 30 s
_MAX_S = 1800.0             # 30 min hard cap per connection

_BRIDGES = ("discord", "telegram", "whatsapp", "slack", "email", "signal", "teams")


def _watched_files(tid: str) -> dict[str, Path]:
    global_dir = _forge_paths.tenant_global_dir(tid)
    files: dict[str, Path] = {
        "tenant":         global_dir / "tenant.corvin.yaml",
        "ldd":            global_dir / "ldd.json",
        "quality_layers": _forge_paths.corvin_home() / "global" / "quality-layers.json",
        "dialectic":      global_dir / "dialectic.json",
        "relay":          global_dir / "relay.json",
        "branding":       global_dir / "branding.yaml",
        "data_policy":    global_dir / "data_policy.yaml",
        "engines":        _forge_paths.voice_config_dir() / "service.env",
    }
    for ch in _BRIDGES:
        files[f"bridge.{ch}"] = _REPO / "operator" / "bridges" / ch / "settings.json"
    return files


def _snapshot(files: dict[str, Path]) -> dict[str, float]:
    result: dict[str, float] = {}
    for label, path in files.items():
        try:
            result[label] = path.stat().st_mtime
        except OSError:
            result[label] = 0.0
    return result


def _fmt(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


async def _stream(request: Request, tid: str) -> AsyncIterator[bytes]:
    files = _watched_files(tid)
    mtimes = _snapshot(files)
    deadline = time.monotonic() + _MAX_S
    tick = 0

    while time.monotonic() < deadline:
        if await request.is_disconnected():
            break
        await asyncio.sleep(_POLL_S)
        tick += 1

        changed: list[str] = []
        for label, path in files.items():
            try:
                mtime = path.stat().st_mtime
            except OSError:
                mtime = 0.0
            if mtime != mtimes[label]:
                mtimes[label] = mtime
                changed.append(label)

        if changed:
            yield _fmt("settings.changed", {"domains": changed, "ts": time.time()})

        if tick % _HEARTBEAT_TICKS == 0:
            yield _fmt("heartbeat", {"ts": time.time()})


@router.get("/settings/stream")
async def settings_stream(
    request: Request,
    _session: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> StreamingResponse:
    # Use the session's tenant_id — ignoring any caller-supplied ?tenant= query
    # param prevents cross-tenant info leakage (activity patterns of other tenants).
    return StreamingResponse(
        _stream(request, _session.tenant_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )

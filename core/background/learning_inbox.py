"""PLAN-0948 D2: the durable, cross-process inbox of the Data Hub learning daemon.

Producers live in different processes - the bridge adapter injects skills and sees the operator's
follow-up, the console receives ratings, the daemon runs in the console host - so they cannot share
an in-memory queue. Each producer appends ONE content-free JSON line to a per-day file under
``<tenant>/learning/daemon/inbox-YYYYMMDD.jsonl`` with a single ``O_APPEND`` write (atomic for a
line of this size; no lock, no coordination). The daemon host reads the files in order from a
persisted cursor, so a crash replays at most the unconfirmed tail and nothing is lost.

Content-free by construction: every event type has a closed field set; a field outside it is
dropped, a string is length-capped, and nothing carries user text, a path or a skill body.
Producers never raise - a learning side channel must not break a turn.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

EVENT_TYPES = ("skill_executed", "user_feedback")
_FIELDS: dict[str, tuple[str, ...]] = {
    "skill_executed": ("skill_id", "success", "execution_id", "data_sources", "execution_time_ms"),
    "user_feedback": ("skill_id", "signal", "dimension", "feedback_id", "data_sources", "user_id_masked"),
}
_DAY_FILE = re.compile(r"^inbox-(\d{8})\.jsonl$")
_MAX_LINE = 3500           # one write() stays well inside an atomic append
_MAX_STR = 128
_MAX_SOURCES = 8
#: Consumed day files older than this are removed by the host.
RETENTION_DAYS = 3


def process_tenant() -> str:
    """The tenant this process acts for (``CORVIN_TENANT_ID`` -> ``_default``) - the one tenant whose
    audit chain this process may write, hence the one tenant a learning host can run for."""
    return os.environ.get("CORVIN_TENANT_ID") or "_default"


def daemon_dir(tenant_id: str) -> Path:
    from core.paths.tenant import tenant_home  # honours CORVIN_HOME

    return tenant_home(tenant_id) / "learning" / "daemon"


def _clean(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in _FIELDS[event_type]:
        value = payload.get(key)
        if value is None:
            continue
        if key == "data_sources":
            if isinstance(value, (list, tuple)):
                out[key] = [str(v)[:_MAX_STR] for v in value if isinstance(v, str) and v][:_MAX_SOURCES]
        elif key in ("success",):
            out[key] = bool(value)
        elif key in ("signal", "execution_time_ms"):
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                out[key] = max(-1.0, min(1.0, float(value))) if key == "signal" else float(value)
        else:
            out[key] = str(value)[:_MAX_STR]
    return out


def sources_of(spec_or_meta: Any) -> list[str]:
    """The attribution ids a SkillSpec (or its ``meta``) carries - empty for every ordinary skill."""
    meta = getattr(spec_or_meta, "meta", spec_or_meta)
    value = meta.get("data_sources") if isinstance(meta, dict) else None
    if not isinstance(value, (list, tuple)):
        return []
    return [str(v)[:_MAX_STR] for v in value if isinstance(v, str) and v][:_MAX_SOURCES]


def signal_for_score(score: float) -> float:
    """Map a 0..1 grade (approval .9, rephrase .3, rejection .1) onto the daemon's -1..+1 signal."""
    return max(-1.0, min(1.0, (float(score) - 0.5) * 2.0))


def mask_user(user: str) -> str:
    """sha256(user)[:16] - the daemon never sees an identity, only a stable pseudonym."""
    return hashlib.sha256((user or "").encode("utf-8")).hexdigest()[:16]


def emit(tenant_id: str, event_type: str, payload: dict[str, Any]) -> bool:
    """Append one event; True when it reached the file. Never raises."""
    try:
        if event_type not in _FIELDS:
            return False
        clean = _clean(event_type, payload)
        if not clean.get("skill_id"):
            return False
        # Only the process tenant has a host (its audit writer refuses any other tenant's
        # decisions, ADR-0562), so an event for another tenant would sit unread forever and the
        # inbox would grow without bound (review 2026-10-12, DH-8).
        if tenant_id != process_tenant():
            return False
        now = datetime.now(timezone.utc)
        line = json.dumps({
            "event_type": event_type, "tenant_id": tenant_id,
            "timestamp": now.replace(tzinfo=None).isoformat(), "payload": clean,
        }, separators=(",", ":"), sort_keys=True) + "\n"
        data = line.encode("utf-8")
        if len(data) > _MAX_LINE:
            return False
        directory = daemon_dir(tenant_id)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"inbox-{now.strftime('%Y%m%d')}.jsonl"
        fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
        return True
    except Exception as exc:  # noqa: BLE001 - a side channel never breaks the producer
        logger.debug("learning inbox emit skipped (%s)", type(exc).__name__)
        return False


def day_files(tenant_id: str) -> list[Path]:
    directory = daemon_dir(tenant_id)
    try:
        return sorted(p for p in directory.iterdir() if _DAY_FILE.match(p.name))
    except OSError:
        return []


def _offsets(cursor: Optional[dict[str, Any]]) -> dict[str, int]:
    """Per-file read offsets. A legacy single ``{file, offset}`` cursor means: files before
    ``file`` are done, ``file`` is read up to ``offset``."""
    cursor = dict(cursor or {})
    if isinstance(cursor.get("files"), dict):
        return {str(k): int(v) for k, v in cursor["files"].items()}
    out: dict[str, int] = {}
    legacy = cursor.get("file")
    if legacy:
        out[str(legacy)] = int(cursor.get("offset", 0) or 0)
    return out


def read_from(tenant_id: str, cursor: Optional[dict[str, Any]], *, limit: int = 500
              ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Events after ``cursor``, at most ``limit``; and the advanced cursor ``{"files": {name: offset}}``.

    Every day file keeps its OWN offset. A single (file, offset) cursor lost a line a producer
    finished - or wrote - in yesterday's file after the reader had moved on to today's (review
    2026-10-12, DH-3). Only complete (newline-terminated) lines are consumed; a malformed line is
    skipped. ``foreign`` in the cursor counts lines tagged with another tenant (dropped).
    """
    offsets = _offsets(cursor)
    legacy_file = (cursor or {}).get("file") if not isinstance((cursor or {}).get("files"), dict) else None
    events: list[dict[str, Any]] = []
    foreign = 0
    for path in day_files(tenant_id):
        if legacy_file and path.name < legacy_file and path.name not in offsets:
            try:
                offsets[path.name] = path.stat().st_size   # legacy cursor: already consumed
            except OSError:
                pass
            continue
        offset = offsets.get(path.name, 0)
        try:
            with open(path, "rb") as f:
                f.seek(offset)
                while len(events) < limit:
                    raw = f.readline(_MAX_LINE + 2)
                    if not raw:
                        break
                    if not raw.endswith(b"\n"):
                        if len(raw) > _MAX_LINE:     # no producer writes this: skip the junk line
                            rest = f.readline()
                            offset += len(raw) + len(rest)
                            continue
                        break  # a partial tail: not yet complete, read again next time
                    offset += len(raw)
                    try:
                        ev = json.loads(raw)
                    except ValueError:
                        continue
                    if not isinstance(ev, dict) or ev.get("event_type") not in _FIELDS:
                        continue
                    if ev.get("tenant_id") != tenant_id:
                        foreign += 1
                        continue
                    events.append(ev)
        except OSError:
            continue
        offsets[path.name] = offset
        if len(events) >= limit:
            break
    return events, {"files": offsets, "foreign": foreign}


def prune(tenant_id: str, cursor: dict[str, Any], *, now: Optional[float] = None) -> int:
    """Remove day files that are fully consumed and older than ``RETENTION_DAYS``."""
    removed = 0
    cutoff = (now if now is not None else time.time()) - RETENTION_DAYS * 86400
    offsets = _offsets(cursor)
    for path in day_files(tenant_id):
        try:
            st = path.stat()
            if offsets.get(path.name, -1) >= st.st_size and st.st_mtime < cutoff:
                path.unlink()
                offsets.pop(path.name, None)
                removed += 1
        except OSError:
            continue
    if isinstance(cursor.get("files"), dict):
        cursor["files"] = offsets
    return removed


__all__ = ["EVENT_TYPES", "sources_of", "signal_for_score", "emit", "read_from", "prune", "day_files", "daemon_dir", "mask_user", "process_tenant"]

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


def read_from(tenant_id: str, cursor: Optional[dict[str, Any]], *, limit: int = 500
              ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Events after ``cursor`` ({file, offset}), at most ``limit``; and the advanced cursor.

    Only complete (newline-terminated) lines are consumed: a producer mid-write is read next time.
    A malformed line is skipped, not fatal.
    """
    cursor = dict(cursor or {})
    cur_file = cursor.get("file", "")
    cur_off = int(cursor.get("offset", 0) or 0)
    events: list[dict[str, Any]] = []
    for path in day_files(tenant_id):
        if cur_file and path.name < cur_file:
            continue
        offset = cur_off if path.name == cur_file else 0
        try:
            with open(path, "rb") as f:
                f.seek(offset)
                while len(events) < limit:
                    raw = f.readline()
                    if not raw:
                        break
                    if not raw.endswith(b"\n"):
                        break  # a partial tail: not yet complete
                    offset += len(raw)
                    try:
                        ev = json.loads(raw)
                    except ValueError:
                        continue
                    if isinstance(ev, dict) and ev.get("tenant_id") == tenant_id \
                            and ev.get("event_type") in _FIELDS:
                        events.append(ev)
        except OSError:
            continue
        cur_file, cur_off = path.name, offset
        if len(events) >= limit:
            break
    return events, {"file": cur_file, "offset": cur_off}


def prune(tenant_id: str, cursor: dict[str, Any], *, now: Optional[float] = None) -> int:
    """Remove day files that are fully consumed and older than ``RETENTION_DAYS``."""
    removed = 0
    cutoff = (now if now is not None else time.time()) - RETENTION_DAYS * 86400
    cur_file = cursor.get("file", "")
    for path in day_files(tenant_id):
        if not cur_file or path.name >= cur_file:
            continue  # unread or the file being read
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


__all__ = ["EVENT_TYPES", "sources_of", "signal_for_score", "emit", "read_from", "prune", "day_files", "daemon_dir", "mask_user"]

"""Tenant-scoped history of operator skill feedback (Stream 4 console surface).

One append-only JSONL per tenant under ``<tenant>/learning/skill_feedback/``. It holds
only structured fields (ids, rating, category) — the free-text ``reasoning`` of a
request is deliberately NOT persisted (it may carry PII; the audit chain carries the
outcome signal). Reads and writes share one ``flock`` so a status/history read never
sees a torn line.
"""
from __future__ import annotations

import fcntl
import json
import os
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from core.paths.tenant import tenant_home

_MAX_READ = 5000  # cap what read_all() returns; counts stream the whole append-only file


def _path(tenant_id: str) -> Path:
    return tenant_home(tenant_id) / "learning" / "skill_feedback" / "events.jsonl"


def append(
    tenant_id: str,
    *,
    feedback_id: str,
    timestamp: str,
    skill_id: str,
    subject_id: str,
    rating: int,
    category: str,
) -> None:
    p = _path(tenant_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(
        {
            "feedback_id": feedback_id,
            "timestamp": timestamp,
            "skill_id": skill_id,
            "subject_id": subject_id,
            "rating": rating,
            "category": category,
        },
        sort_keys=True,
    )
    fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT
                 | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0), 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, (line + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def _iter_records(tenant_id: str) -> Iterator[dict[str, Any]]:
    """Every stored event, oldest first, streamed line by line under the shared lock
    (torn or foreign lines are skipped). Memory is bounded by one line, not the file."""
    p = _path(tenant_id)
    if not p.is_file():
        return
    with open(p, "r", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_SH)
        for raw in fh:
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            if isinstance(rec, dict) and rec.get("feedback_id"):
                yield rec


def read_all(tenant_id: str) -> list[dict[str, Any]]:
    """Newest-last list of the last ``_MAX_READ`` stored events."""
    return list(deque(_iter_records(tenant_id), maxlen=_MAX_READ))


def _rating(rec: dict[str, Any]) -> int:
    try:
        return int(rec.get("rating", 0))
    except (TypeError, ValueError):
        return 0


def history(tenant_id: str, skill_id: str, limit: int) -> tuple[int, list[dict[str, Any]], float]:
    """``(total_count, newest-first events up to limit, avg_rating)`` for one skill.

    ``total_count`` and ``avg_rating`` cover EVERY stored event of the skill — a
    count capped at the read window reported 5 000 for a tenant that had more."""
    total, rating_sum = 0, 0
    newest: deque[dict[str, Any]] = deque(maxlen=max(int(limit), 0))
    for rec in _iter_records(tenant_id):
        if rec.get("skill_id") != skill_id:
            continue
        total += 1
        rating_sum += _rating(rec)
        newest.append(rec)
    return total, list(reversed(newest)), (rating_sum / total) if total else 0.0


def status(tenant_id: str) -> tuple[int, str | None]:
    """``(total_received, last_timestamp)`` over every stored event of the tenant."""
    total, last = 0, None
    for rec in _iter_records(tenant_id):
        total += 1
        last = rec
    return total, (last.get("timestamp") if last is not None else None)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

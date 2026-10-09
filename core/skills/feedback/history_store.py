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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.paths.tenant import tenant_home

_MAX_READ = 5000  # cap what a read walks; the file itself is append-only


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
    fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, (line + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def read_all(tenant_id: str) -> list[dict[str, Any]]:
    """Newest-last list of stored events (torn or foreign lines are skipped)."""
    p = _path(tenant_id)
    if not p.is_file():
        return []
    out: list[dict[str, Any]] = []
    with open(p, "r", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_SH)
        for raw in fh.readlines()[-_MAX_READ:]:
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            if isinstance(rec, dict) and rec.get("feedback_id"):
                out.append(rec)
    return out


def history(tenant_id: str, skill_id: str, limit: int) -> tuple[int, list[dict[str, Any]], float]:
    """``(total_count, newest-first events up to limit, avg_rating)`` for one skill."""
    mine = [r for r in read_all(tenant_id) if r.get("skill_id") == skill_id]
    avg = (sum(int(r.get("rating", 0)) for r in mine) / len(mine)) if mine else 0.0
    return len(mine), list(reversed(mine))[:limit], avg


def status(tenant_id: str) -> tuple[int, str | None]:
    """``(total_received, last_timestamp)`` over every skill of the tenant."""
    recs = read_all(tenant_id)
    return len(recs), (recs[-1].get("timestamp") if recs else None)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

"""One-time claim of a chat-staged pending record (send / friendship token).

``pop`` used to be read-then-unlink: two confirms racing (double click, two
tabs, a retry) both read the record before either unlinked it, and both
went on to send or mint. ``claim`` renames the file to a name unique to this
caller first — a rename of the same source can succeed exactly once — and
only the winner reads it.
"""
from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from typing import Any


def claim(path: Path, ttl_s: float) -> dict[str, Any] | None:
    """Atomically take ``path``; None if absent, already claimed, unreadable
    or older than ``ttl_s`` (an expired record is consumed, never returned)."""
    claimed = path.with_name(f"{path.stem}.{secrets.token_hex(8)}.claimed")
    try:
        os.rename(path, claimed)
    except OSError:
        return None
    try:
        rec = json.loads(claimed.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rec = None
    finally:
        try:
            claimed.unlink()
        except OSError:
            pass
    if not isinstance(rec, dict):
        return None
    created_at = rec.get("created_at", 0)
    if isinstance(created_at, bool) or not isinstance(created_at, (int, float)) \
            or time.time() - created_at > ttl_s:
        return None
    return rec


def sweep_stale_claims(directory: Path, older_than_s: float = 60.0) -> None:
    """A process killed between rename and unlink leaves a ``.claimed`` file;
    it holds a record that was already handed out, so it is only deleted."""
    now = time.time()
    for p in directory.glob("*.claimed"):
        try:
            if now - p.stat().st_mtime > older_than_s:
                p.unlink()
        except OSError:
            pass

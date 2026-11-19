"""Bounded, streaming tail reader for a tenant's audit chain (console READ side).

Console panels that list or aggregate recent chain records used to read the
whole file inside an ``async def`` handler (``read_text().splitlines()`` or a
full ``for line in f``). On a 500 MB chain that holds the event loop for
seconds and the console stops answering (CLAUDE.md "Console Event Loop Stays
Free"). This module is the one reader those panels share:

* it reads only the last ``max_scan_bytes`` of the file (a cut that lands
  mid-line drops that partial line),
* it streams line by line — the file is never held in memory as a whole,
* an optional byte-level ``needles`` prefilter skips ``json.loads`` for lines
  that cannot match.

A tail window is a PARTIAL view once the chain outgrows it. Every caller
reports that with :func:`scan_window` (``truncated`` / ``scanned_bytes`` /
``total_bytes`` / ``covers_since``) in its response - a panel that silently
shows the last 32 MiB of a 500 MB chain as "the" result is wrong without
looking wrong.

It is BLOCKING I/O. Call it through an awaited ``asyncio.to_thread`` from any
async handler. It is a recent-window view, never a verifier: the ADR-0232
tripwire and compliance reports read the full chain through their own path.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

#: Default tail window. Large enough for weeks of ordinary activity, small
#: enough that one pass stays well under a second off the loop.
DEFAULT_MAX_SCAN_BYTES = 32 * 1024 * 1024


def iter_chain_tail(
    chain_path: Path | str,
    *,
    max_scan_bytes: int = DEFAULT_MAX_SCAN_BYTES,
    needles: tuple[str, ...] | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield the dict records in the last ``max_scan_bytes`` of ``chain_path``,
    in file order (oldest first). Missing/unreadable file yields nothing;
    malformed lines are skipped."""
    if max_scan_bytes < 0:
        raise ValueError("max_scan_bytes must be >= 0")
    nd = None if needles is None else tuple(n.encode("utf-8") for n in needles)
    try:
        fh = Path(chain_path).open("rb")
    except OSError:
        return
    with fh:
        try:
            size = fh.seek(0, 2)
            start = max(0, size - max_scan_bytes)
            skip_partial = False
            if start > 0:
                fh.seek(start - 1)
                skip_partial = fh.read(1) != b"\n"
            fh.seek(start)
            for raw in fh:
                if skip_partial:
                    skip_partial = False
                    continue
                if nd is not None and not any(n in raw for n in nd):
                    continue
                line = raw.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line.decode("utf-8", errors="replace"))
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    yield rec
        except OSError:
            return


def scan_truncated(
    chain_path: Path | str, *, max_scan_bytes: int = DEFAULT_MAX_SCAN_BYTES,
) -> bool:
    """True when the chain is larger than the tail window, i.e. a result built
    from :func:`iter_chain_tail` does not cover the whole file."""
    try:
        return Path(chain_path).stat().st_size > max_scan_bytes
    except OSError:
        return False


def _record_iso(rec: dict[str, Any]) -> str | None:
    """ISO-8601 UTC time of a chain record (``ts`` epoch seconds, else its
    ``timestamp`` / ``timestamp_utc`` string), or None."""
    ts = rec.get("ts")
    if isinstance(ts, (int, float)) and not isinstance(ts, bool):
        try:
            return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat().replace("+00:00", "Z")
        except (OverflowError, OSError, ValueError):
            return None
    for key in ("timestamp", "timestamp_utc"):
        val = rec.get(key)
        if isinstance(val, str) and val:
            return val
    return None


def scan_window(
    chain_path: Path | str, *, max_scan_bytes: int = DEFAULT_MAX_SCAN_BYTES,
) -> dict[str, Any]:
    """What a tail scan of ``chain_path`` with this window covers.

    ``truncated``      the file is larger than the window (older records unseen)
    ``scanned_bytes``  bytes the scan reads
    ``total_bytes``    size of the chain file
    ``covers_since``   time of the first record inside the window when
                       truncated (the result covers "since then"), else None

    BLOCKING (a stat, plus one short read when truncated). A missing or
    unreadable file is an untruncated, empty window.
    """
    try:
        total = Path(chain_path).stat().st_size
    except OSError:
        total = 0
    truncated = total > max_scan_bytes
    covers_since = None
    if truncated:
        first = next(iter_chain_tail(chain_path, max_scan_bytes=max_scan_bytes), None)
        if first is not None:
            covers_since = _record_iso(first)
    return {
        "truncated": truncated,
        "scanned_bytes": min(total, max_scan_bytes),
        "total_bytes": total,
        "covers_since": covers_since,
    }


__all__ = ["DEFAULT_MAX_SCAN_BYTES", "iter_chain_tail", "scan_truncated", "scan_window"]

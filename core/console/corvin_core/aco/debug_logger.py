"""Shared session debug logger for ACO Layer 1.

A self-contained, thread-safe append-to-JSONL helper that any module can use
to write events to <session_workdir>/chat_debug.jsonl without importing the
full chat_runtime module.

Usage::

    from corvin_console.aco.debug_logger import write_event
    write_event(workdir, "acs.worker.spawned", worker_id="w1", model="claude-sonnet-4-6")
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
_MAX_BYTES = 2 * 1024 * 1024   # 2 MB → rotate


def write_event(workdir: Path | str, event: str, **fields: Any) -> None:
    """Append one debug event to <workdir>/chat_debug.jsonl.

    Thread-safe; silently discards on I/O error so it never breaks callers.
    Rotates log to .jsonl.1 / .jsonl.2 when file exceeds _MAX_BYTES.
    """
    workdir = Path(workdir)
    path = workdir / "chat_debug.jsonl"
    rec: dict = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event": event,
    }
    for k, v in fields.items():
        try:
            json.dumps(v)
            rec[k] = v
        except (TypeError, ValueError):
            rec[k] = str(v)
    line = json.dumps(rec, ensure_ascii=False) + "\n"
    try:
        with _LOCK:
            if path.exists() and path.stat().st_size > _MAX_BYTES:
                p1 = path.with_suffix(".jsonl.1")
                if p1.exists():
                    p1.replace(path.with_suffix(".jsonl.2"))
                path.replace(p1)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line)
    except Exception:  # noqa: BLE001
        pass

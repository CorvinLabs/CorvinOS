"""Universal Activity Hub (UAH) — shared writer.

MCP servers call ``write_chat_activity`` whenever CORVIN_CHAT_KEY is set,
meaning the tool call originated from a Chat subprocess (Chat turn as
Kommandozentrale).

Storage: <tenant_global>/chat_activity.jsonl (append-only, fcntl-locked).
Schema: {ts, action, panel, entity_id, chat_key, summary[, extra]}

Invariants:
  - MUST NOT import anthropic.
  - MUST NOT write PII, prompt text, or raw tool arguments.
  - Fail-open: any write failure is silently swallowed — the tool call
    must never be blocked by activity registration.
  - Called from MCP server threads: fcntl.flock provides cross-process
    mutual exclusion; the append is atomic at the OS level on ext4/XFS.
"""
from __future__ import annotations

from _compat_fcntl import fcntl  # portable: real fcntl on POSIX, no-op flock on Windows
import json
import os
import time
from pathlib import Path
from typing import Any

# Only these extra keys are safe to forward — no prompt text, no args payloads.
_SAFE_EXTRA_KEYS = frozenset({"strategy", "tool_name", "scope", "name", "runtime"})


def _tenant_global_dir() -> Path | None:
    try:
        from forge import paths as _fp  # forge is always on sys.path in MCP context
        return _fp.tenant_global_dir()
    except Exception:
        return None


def write_chat_activity(
    *,
    action: str,
    panel: str,
    entity_id: str,
    summary: str,
    extra: dict[str, Any] | None = None,
) -> None:
    """Append one activity entry when the call came from a Chat subprocess.

    Silently no-ops when CORVIN_CHAT_KEY is absent (console-direct calls
    already appear in their panel — no double-registration needed).
    """
    chat_key = os.environ.get("CORVIN_CHAT_KEY", "")
    if not chat_key:
        return

    global_dir = _tenant_global_dir()
    if global_dir is None:
        return

    entry: dict[str, Any] = {
        "ts":        time.time(),
        "action":    action,
        "panel":     panel,
        "entity_id": entity_id[:256],
        "chat_key":  chat_key,
        "summary":   summary[:300],
    }
    if extra:
        safe = {k: v for k, v in extra.items() if k in _SAFE_EXTRA_KEYS}
        if safe:
            entry["extra"] = safe

    path = global_dir / "chat_activity.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
    except Exception:
        pass  # fail-open: tracking never blocks the tool call

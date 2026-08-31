"""ADR-0087 M2: Buffered mid-stream injection transport for stateless engines.

When `/btw "text"` is called on Codex/OpenCode/Hermes, queue it here.
On next spawn, prepend queued text to user message.

Storage: <session_dir>/btw_queue.jsonl (append-only, one injection per line)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional


def _queue_path(session_dir: Path) -> Path:
    """Path to buffered injection queue."""
    return session_dir / "btw_queue.jsonl"


def enqueue_injection(session_dir: Path, text: str) -> None:
    """Queue a /btw injection for next spawn.

    Args:
        session_dir: <corvin_home>/tenants/<tid>/sessions/<bridge>:<chat>/
        text: The /btw text to queue
    """
    session_dir.mkdir(parents=True, exist_ok=True)
    path = _queue_path(session_dir)

    entry = {
        "timestamp": __import__("time").time(),
        "text": text,
        "queued_for_next_turn": True,
    }

    try:
        with open(path, "a", encoding="utf-8") as f:
            json.dump(entry, f, separators=(",", ":"))
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
    except Exception as e:
        print(f"[btw_queue] enqueue failed: {e}")


def dequeue_all_injections(session_dir: Path) -> str:
    """Load all queued injections and return as a single block.

    Also removes the queue file (consumed).

    Args:
        session_dir: session directory

    Returns:
        Concatenated block of all queued injections, or empty string if none.
    """
    path = _queue_path(session_dir)
    if not path.exists():
        return ""

    try:
        injections = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        obj = json.loads(line)
                        if obj.get("text"):
                            injections.append(obj["text"])
                    except json.JSONDecodeError:
                        pass

        # Delete queue after reading (consumed)
        path.unlink()

        if not injections:
            return ""

        # Format as a block for prepending
        return "## Buffered /btw injections from prior turns:\n\n" + "\n\n".join(
            injections
        )
    except Exception as e:
        print(f"[btw_queue] dequeue failed: {e}")
        return ""


def clear_queue(session_dir: Path) -> None:
    """Wipe the injection queue (e.g., on /reset)."""
    path = _queue_path(session_dir)
    if path.exists():
        path.unlink()

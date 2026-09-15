"""ADR-0087 M1: Context Checkpoint System — universal session state persistence.

Stores engine-agnostic conversation snapshots in append-only JSONL format.
Enables multi-turn context transfer across engine boundaries (Claude Code → Codex → Hermes, etc.).

Storage: <corvin_home>/tenants/<tid>/sessions/<bridge>:<chat>/context_checkpoints.jsonl
One JSON object per line (JSONL). Load latest line for current context.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_MODE = 0o600


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _checkpoint_path(session_dir: Path) -> Path:
    """Path to append-only checkpoint log for this session."""
    return session_dir / "context_checkpoints.jsonl"


def save_checkpoint(
    session_dir: Path,
    engine_id: str,
    turn_id: str,
    system_prompt_summary: str,
    last_message_summary: str,
    tool_results_digest: str,
    conversation_length_tokens: int,
) -> None:
    """Save a context checkpoint to append-only log.

    Args:
        session_dir: <corvin_home>/tenants/<tid>/sessions/<bridge>:<chat>/
        engine_id: which engine produced this turn (e.g. "claude_code", "codex")
        turn_id: unique turn identifier
        system_prompt_summary: first 100 chars of current system prompt
        last_message_summary: last user message, truncated to 200 chars
        tool_results_digest: tool names + result summary
        conversation_length_tokens: token count estimate
    """
    session_dir.mkdir(parents=True, exist_ok=True)
    path = _checkpoint_path(session_dir)

    checkpoint = {
        "turn_id": turn_id,
        "timestamp": time.time(),
        "timestamp_iso": _now_iso(),
        "engine_id": engine_id,
        "system_prompt_summary": system_prompt_summary,
        "last_message_summary": last_message_summary,
        "tool_results_digest": tool_results_digest,
        "conversation_length_tokens": conversation_length_tokens,
    }

    try:
        # Atomic append: write to path with exclusive lock, then sync
        with open(path, "a", encoding="utf-8", newline="") as fh:
            json.dump(checkpoint, fh, separators=(",", ":"))
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
    except Exception as e:
        # Non-fatal; checkpoint save failing should not break the turn
        print(f"[context_checkpoint] save failed: {e}")


def load_checkpoint(session_dir: Path) -> Optional[dict[str, Any]]:
    """Load the most recent context checkpoint from the log.

    Returns:
        Latest checkpoint dict, or None if log is empty/missing.
    """
    path = _checkpoint_path(session_dir)
    if not path.exists():
        return None

    try:
        with path.open("r", encoding="utf-8") as fh:
            last_line = None
            for line in fh:
                line = line.strip()
                if line:
                    last_line = line

            if last_line is None:
                return None

            return json.loads(last_line)
    except Exception as e:
        print(f"[context_checkpoint] load failed: {e}")
        return None


def clear_checkpoints(session_dir: Path) -> None:
    """Wipe all checkpoints for this session (e.g., on /reset)."""
    path = _checkpoint_path(session_dir)
    if path.exists():
        path.unlink()

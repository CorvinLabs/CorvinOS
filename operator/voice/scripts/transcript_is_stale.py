#!/usr/bin/env python3
"""transcript_is_stale.py — exit 0 if the Stop hook is "stale".

A Stop hook is **stale** when, by the time it runs, a NEW user message
already exists in the transcript AFTER the latest assistant turn. That means
the user has moved on (typed a new question, sent a new instruction) before
the TTS pipeline for the just-finished turn could speak it. Reading the old
turn aloud now would be confusing — at best out-of-context, at worst
mis-paired with the wrong question.

Why this happens:
  1. Pipeline lag — summarize+TTS for the previous turn takes 1-3 s.
  2. Multi-step turns — assistant emits thinking+text+tool_use as separate
     transcript entries; the Stop hook may fire mid-sequence, causing the
     "latest assistant" view to be older than the just-emitted text block.
  3. Heavy parallel I/O — transcript file may not be flushed yet when the
     hook reads it; we see the file as it WAS, not as it IS.

This guard is the same idea as `extract_last_user.py`'s anchor-on-assistant
logic, but at the hook level: skip TTS entirely if the assistant turn we'd
speak no longer matches the current state of the conversation.

Usage:
    transcript_is_stale.py <transcript_path>

Exit code:
    0  → STALE (caller should skip TTS)
    1  → fresh (safe to speak)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def _is_real_user_msg(evt: dict) -> bool:
    """A 'real' user message is human prose, not a tool_result wrapper."""
    msg = evt.get("message") if isinstance(evt.get("message"), dict) else evt
    if not isinstance(msg, dict):
        return False
    role = evt.get("role") or evt.get("type") or msg.get("role")
    if role not in ("user", "user_message"):
        return False
    content = msg.get("content")
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                if isinstance(b.get("text"), str) and b["text"].strip():
                    return True
        return False
    return False


def _is_assistant(evt: dict) -> bool:
    msg = evt.get("message") if isinstance(evt.get("message"), dict) else evt
    role = evt.get("role") or evt.get("type") or ""
    if role in ("assistant", "assistant_message"):
        return True
    return isinstance(msg, dict) and msg.get("role") == "assistant"


def is_stale(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False

    last_asst_idx = None
    last_user_idx = None
    for i, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue
        if _is_assistant(evt):
            last_asst_idx = i
        elif _is_real_user_msg(evt):
            last_user_idx = i

    if last_asst_idx is None or last_user_idx is None:
        return False
    return last_user_idx > last_asst_idx


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: transcript_is_stale.py <transcript_path>", file=sys.stderr)
        return 2
    return 0 if is_stale(Path(sys.argv[1])) else 1


if __name__ == "__main__":
    sys.exit(main())

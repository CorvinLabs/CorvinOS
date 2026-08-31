#!/usr/bin/env python3
"""Extract the last assistant text message from a Claude Code transcript file.

Usage:
    extract_last_assistant.py <transcript_path>

Transcripts are JSONL with one event per line. We walk from the end and
return the text of the last assistant turn (concatenating any text blocks).
Tool calls and tool results are skipped — we only want the prose.

Prints the message text to stdout. Empty stdout means nothing matched.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def extract(path: Path) -> str:
    """Return text of the LAST assistant message, or empty string.

    Robustness rule: once we identify the last assistant message in the
    transcript, we commit to it. If it has no text content (only tool_use
    blocks, or unrecognized shape), we return "" — we DO NOT fall back to
    older assistant messages, because that would surface stale content
    from earlier turns and the listener would hear "things that aren't in
    the answer". An empty return causes the stop_hook to skip TTS, which
    is the correct behaviour when the final assistant turn carries no
    prose to read aloud.
    """
    if not path.exists():
        return ""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""

    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue

        # Common shapes seen across Claude Code transcript versions:
        # {"type":"assistant","message":{"content":[{"type":"text","text":"..."}]}}
        # {"role":"assistant","content":[{"type":"text","text":"..."}]}
        # {"type":"assistant_message","text":"..."}
        msg = evt.get("message") if isinstance(evt.get("message"), dict) else evt
        role = evt.get("role") or evt.get("type") or ""
        if role not in ("assistant", "assistant_message"):
            inner_role = msg.get("role") if isinstance(msg, dict) else None
            if inner_role != "assistant":
                continue

        # First (i.e. last in chronological order) assistant message found.
        # Whatever shape it has, this is the one we commit to.
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and isinstance(block.get("text"), str):
                    parts.append(block["text"])
            return "\n".join(p for p in parts if p).strip()

        if isinstance(evt.get("text"), str):
            return evt["text"].strip()

        # Recognized as the last assistant turn but no text shape we know.
        # Return empty rather than scanning further back into older turns.
        return ""

    return ""


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: extract_last_assistant.py <transcript_path>", file=sys.stderr)
        return 2
    sys.stdout.write(extract(Path(sys.argv[1])))
    return 0


if __name__ == "__main__":
    sys.exit(main())

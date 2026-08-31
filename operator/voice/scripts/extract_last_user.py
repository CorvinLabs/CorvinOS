#!/usr/bin/env python3
"""Extract the last real user prompt from a Claude Code transcript.

Usage:
    extract_last_user.py <transcript_path>

A "real" user prompt is a JSONL event with role=user whose content is plain
prose typed by the human — NOT a tool_result, NOT a system-reminder XML
block, NOT a user-prompt-submit-hook payload, NOT an attachment marker.

Prints the prompt text to stdout. Empty stdout means nothing matched.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


# Wrappers Claude Code injects into the user content stream that are NOT
# the human's actual words. Strip them before deciding whether the
# remaining prose is a real prompt.
WRAPPER_RE = re.compile(
    r"<(system-reminder|command-name|command-message|command-args|"
    r"local-command-stdout|local-command-stderr|user-prompt-submit-hook|"
    r"bash-input|bash-stdout|bash-stderr|ide-selection|ide-opened-file|"
    r"file-attachment)\b[^>]*>[\s\S]*?</\1>",
    re.IGNORECASE,
)


def clean(s: str) -> str:
    s = WRAPPER_RE.sub("", s)
    return s.strip()


def _find_latest_assistant_idx(lines: list[str]) -> int | None:
    """Return the index of the LATEST assistant entry, or None if none.

    The Stop hook reads the transcript at hook-fire time. If the user has
    already typed a NEW prompt by then (race: hook lags behind transcript
    flushes), the newest user msg in the file is NOT the one that triggered
    the just-finished assistant turn. Anchoring on the latest assistant
    lets us walk back to the user msg that ACTUALLY belongs with this
    assistant turn — coherent TASK + BODY pairing in the spoken output.
    """
    idx = None
    for i, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = evt.get("message") if isinstance(evt.get("message"), dict) else evt
        role = evt.get("role") or evt.get("type") or ""
        if role in ("assistant", "assistant_message"):
            idx = i
            continue
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            idx = i
    return idx


def extract(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""

    # Anchor: walk back from the line BEFORE the latest assistant turn,
    # not from the end of the file. See _find_latest_assistant_idx for why.
    asst_idx = _find_latest_assistant_idx(lines)
    end_idx = (asst_idx - 1) if asst_idx is not None else (len(lines) - 1)
    if end_idx < 0:
        return ""

    for line in reversed(lines[: end_idx + 1]):
        line = line.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue

        msg = evt.get("message") if isinstance(evt.get("message"), dict) else evt
        role = evt.get("role") or evt.get("type") or ""
        if role not in ("user", "user_message"):
            inner_role = msg.get("role") if isinstance(msg, dict) else None
            if inner_role != "user":
                continue

        content = msg.get("content") if isinstance(msg, dict) else None

        # Strings are direct user prose — clean and return if non-empty.
        if isinstance(content, str):
            cleaned = clean(content)
            if cleaned:
                return cleaned
            continue

        # Lists may mix text blocks and tool_result blocks. tool_result
        # entries are NOT human prose; skip them entirely. If the only
        # content is tool_result, this turn is not a real user prompt
        # and we keep scanning backwards.
        if isinstance(content, list):
            parts = []
            saw_only_tool_result = True
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "tool_result":
                    continue
                saw_only_tool_result = False
                if btype == "text" and isinstance(block.get("text"), str):
                    parts.append(block["text"])
            if saw_only_tool_result:
                continue
            joined = clean("\n".join(p for p in parts if p))
            if joined:
                return joined

        # Some shapes put the prose directly on the event.
        if isinstance(evt.get("text"), str):
            cleaned = clean(evt["text"])
            if cleaned:
                return cleaned

    return ""


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: extract_last_user.py <transcript_path>", file=sys.stderr)
        return 2
    sys.stdout.write(extract(Path(sys.argv[1])))
    return 0


if __name__ == "__main__":
    sys.exit(main())

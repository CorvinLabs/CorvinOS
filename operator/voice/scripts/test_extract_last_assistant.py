#!/usr/bin/env python3
"""Tests for extract_last_assistant.py.

Most important guarantee: when the LAST assistant message in the transcript
has no readable text content (e.g. only tool_use blocks), the extractor
returns "" instead of falling back to an older assistant message. The
fallback path was the source of "the voice talks about older stuff that's
not in the answer". The stop_hook treats "" as "skip TTS", which is the
correct behaviour.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

# Make the script importable.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_last_assistant import extract  # noqa: E402


def write_transcript(events: list[dict]) -> Path:
    f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
    for evt in events:
        f.write(json.dumps(evt) + "\n")
    f.close()
    return Path(f.name)


def assistant_msg(blocks: list[dict] | str) -> dict:
    """Build a transcript event matching the most common Claude Code shape."""
    return {"type": "assistant", "message": {"role": "assistant", "content": blocks}}


def user_msg(blocks: list[dict] | str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": blocks}}


def text_block(s: str) -> dict:
    return {"type": "text", "text": s}


def tool_use_block(name: str = "Read") -> dict:
    return {"type": "tool_use", "id": "tu_x", "name": name, "input": {}}


def tool_result_block(s: str = "ok") -> dict:
    return {"type": "tool_result", "tool_use_id": "tu_x", "content": s}


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_simple_text_assistant_message() -> None:
    path = write_transcript([
        user_msg("Hi"),
        assistant_msg([text_block("Hallo, wie kann ich helfen?")]),
    ])
    assert extract(path) == "Hallo, wie kann ich helfen?"


def test_multiple_text_blocks_joined() -> None:
    path = write_transcript([
        user_msg("Wie geht's?"),
        assistant_msg([text_block("Erster Block."), text_block("Zweiter Block.")]),
    ])
    assert extract(path) == "Erster Block.\nZweiter Block."


def test_skips_user_messages_walks_back_to_assistant() -> None:
    path = write_transcript([
        assistant_msg([text_block("Antwort 1")]),
        user_msg("Frage 2"),
        assistant_msg([text_block("Antwort 2")]),
        user_msg([tool_result_block("file content")]),
    ])
    # User-with-tool_result is skipped; last assistant Text-block is "Antwort 2".
    assert extract(path) == "Antwort 2"


def test_string_content_shape() -> None:
    path = write_transcript([
        user_msg("Hi"),
        {"role": "assistant", "content": "Direkt-String-Antwort"},
    ])
    assert extract(path) == "Direkt-String-Antwort"


def test_returns_empty_on_missing_file() -> None:
    assert extract(Path("/tmp/does-not-exist-987654.jsonl")) == ""


# ---------------------------------------------------------------------------
# THE FIX: never fall back to older assistant messages once the last one is
# located. If it has no text content, return "" so stop_hook skips TTS rather
# than reading aloud stale content from earlier turns.
# ---------------------------------------------------------------------------


def test_last_assistant_only_tool_use_returns_empty_not_older_message() -> None:
    """Regression test: voice users reported 'it talks about older stuff'.

    Cause was: the last assistant turn ended on tool_use without text,
    and the extractor walked further back, returning "Antwort 1" — which
    belonged to a previous turn. New behaviour: return "" so the stop
    hook skips TTS.
    """
    path = write_transcript([
        user_msg("Frage 1"),
        assistant_msg([text_block("Antwort 1 (von früher!)")]),
        user_msg([tool_result_block()]),
        user_msg("Frage 2"),
        assistant_msg([tool_use_block("Read")]),  # last assistant, no text
    ])
    assert extract(path) == ""


def test_last_assistant_unknown_shape_returns_empty() -> None:
    path = write_transcript([
        assistant_msg([text_block("Antwort 1")]),
        user_msg("Frage 2"),
        # Last assistant message: content shape we don't recognise.
        {"type": "assistant", "message": {"role": "assistant", "content": 42}},
    ])
    assert extract(path) == ""


def test_last_assistant_text_plus_tool_use_keeps_text() -> None:
    """Common shape: assistant says something AND calls a tool in one turn.

    The text block must still be returned — it is the prose for this turn,
    not stale content from a previous one.
    """
    path = write_transcript([
        assistant_msg([text_block("Antwort 1")]),
        user_msg("Frage 2"),
        assistant_msg([
            text_block("Ich schaue rein."),
            tool_use_block("Read"),
        ]),
    ])
    assert extract(path) == "Ich schaue rein."


def test_malformed_jsonl_lines_are_skipped() -> None:
    path = write_transcript([
        user_msg("Hi"),
        assistant_msg([text_block("Gute Antwort")]),
    ])
    # Append garbage lines.
    with open(path, "a", encoding="utf-8") as f:
        f.write("not json at all\n")
        f.write("{broken json\n")
    assert extract(path) == "Gute Antwort"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main() -> int:
    tests = [
        test_simple_text_assistant_message,
        test_multiple_text_blocks_joined,
        test_skips_user_messages_walks_back_to_assistant,
        test_string_content_shape,
        test_returns_empty_on_missing_file,
        test_last_assistant_only_tool_use_returns_empty_not_older_message,
        test_last_assistant_unknown_shape_returns_empty,
        test_last_assistant_text_plus_tool_use_keeps_text,
        test_malformed_jsonl_lines_are_skipped,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL  {t.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"ERROR {t.__name__}: {type(exc).__name__}: {exc}")
    if failed:
        print(f"\n{failed} test(s) failed")
        return 1
    print(f"\nAll {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

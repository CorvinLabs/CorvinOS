"""Regression: chat_runtime.py's debug events must never log raw prompt/task
text (adversarial review finding).

`_dbg(sess.workdir, "turn.start", ...)` and `_dbg(sess.workdir,
"acs.run.start", ...)` used to include `prompt_preview=prompt[:120]` /
`task_preview=task_text[:120]` — the first 120 characters of the user's raw
message, persisted to `<workdir>/chat_debug.jsonl` and served verbatim,
un-redacted, by `GET /chat/sessions/{sid}/debug`
(routes/chat.py::get_session_debug_log, which does zero PII stripping).
Every other audit/debug surface in this file is scrupulously metadata-only
(`prompt_chars=len(prompt)`, allow-listed detail fields) — this was the one
sink that violated the project's own "don't leak PII into audit details or
log lines" rule (and its own `anomaly_detector.py` module independently
treats `_PII_FIELDS = {"prompt_preview", "task_preview"}` as PII needing
stripping before it reaches an API response — proof this was a recognized
category, just not scrubbed at the source).

A source-grep is the right regression guard here: the fields must never be
reintroduced as a `_dbg(...)` keyword argument, regardless of which debug
event name they're attached to.
"""
from __future__ import annotations

from pathlib import Path

_CHAT_RUNTIME = (
    Path(__file__).resolve().parents[1] / "corvin_console" / "chat_runtime.py"
)


def test_no_prompt_or_task_preview_keyword_in_chat_runtime_source():
    src = _CHAT_RUNTIME.read_text(encoding="utf-8")
    for banned in ("prompt_preview=", "task_preview="):
        assert banned not in src, (
            f"{banned!r} must not appear as a _dbg()/log keyword in "
            f"chat_runtime.py — it persists raw user text to "
            f"chat_debug.jsonl, served un-redacted by the debug endpoint"
        )

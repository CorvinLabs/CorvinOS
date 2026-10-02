"""With ``cel_cache_stable`` on, the console builds the system prompt with an
EMPTY ``task_text`` (the volatile blocks move into the user message). The
session ledger needs this turn's message anyway, or an earlier unanswered
message is dropped from history (review R5-6). Guard: every ``_build_args``
call that passes ``task_text=""`` also passes ``ledger_prompt=``.
"""
from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "corvin_console" / "chat_runtime.py"


def test_every_empty_task_text_build_passes_the_ledger_prompt():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    seen, offenders = 0, []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_build_args":
            kw = {k.arg: k.value for k in node.keywords}
            tt = kw.get("task_text")
            if isinstance(tt, ast.Constant) and tt.value == "" and kw.get("purpose") is None:
                seen += 1
                if "ledger_prompt" not in kw:
                    offenders.append(node.lineno)
    assert seen >= 1, "positive control: the cache-stable build was not found"
    assert not offenders, offenders


def test_the_ledger_renderer_keeps_an_unanswered_message_when_told_the_current_one():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "corvin_operator" / "bridges" / "shared"))
    import session_ledger as sl
    turns = [{"role": "user", "ts": 1, "parts": [{"kind": "text", "text": "A"}]},
             {"role": "assistant", "ts": 2, "parts": [{"kind": "text", "text": "a"}]},
             {"role": "user", "ts": 3, "parts": [{"kind": "text", "text": "B as I said"}]}]
    assert [r["user"] for r in sl.records_from_turn_log(turns, current_prompt="C")] == ["A", "B as I said"]

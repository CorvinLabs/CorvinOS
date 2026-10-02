"""L10 path gate denies every worker write to a chat's session ledger (ADR-2102).

A WRITE would let a prompt-injected worker forge "user" turns that are
re-supplied into every later system prompt, plant a fake /new fence, or delete
lines (review R4-5). Since review R5 the ledger lives OUTSIDE the worker's cwd
(``<tenant>/session_ledger/<channel>/<chat>/``): commands that destroy or glob
the cwd cannot reach it, and an explicit path to it — literal, globbed,
through a shell variable or ``..`` — is denied. The worker reads the generated
``.corvin-history.md`` view in its cwd instead.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.environ.setdefault("CORVIN_HOME", tempfile.mkdtemp(prefix="pg-ledger-"))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "bridges" / "shared"))
import path_gate  # noqa: E402
import session_ledger as sl  # noqa: E402

TH = Path(os.environ["CORVIN_HOME"]) / "tenants/_default"
WD = TH / "sessions/voice/telegram/123"
WD.mkdir(parents=True, exist_ok=True)
LEDGER = sl.ledger_path(WD)


def _bash(cmd):
    cwd = os.getcwd()
    os.chdir(WD)                 # the hook resolves relative paths against its cwd
    try:
        return path_gate.check({"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": str(WD)})
    finally:
        os.chdir(cwd)


def test_ledger_lives_outside_the_worker_cwd():
    assert LEDGER == TH / "session_ledger/telegram/123/ledger.jsonl"
    assert WD not in LEDGER.parents


def test_write_edit_and_bash_writes_are_denied():
    sl.append_turn(WD, channel="telegram", chat_key="123", user_text="u", assistant_text="a")
    assert path_gate.check({"tool_name": "Write", "tool_input": {"file_path": str(LEDGER), "content": "x"}})[0] is False
    assert path_gate.check({"tool_name": "Edit", "tool_input": {"file_path": str(LEDGER),
                                                                "old_string": "a", "new_string": "b"}})[0] is False
    for cmd in (f"echo forged >> {LEDGER}",
                "echo x >> ../../../../session_ledger/telegram/123/ledger.jsonl",
                f"cat x > {LEDGER.parent}/ledger.json?",
                f"D={LEDGER.parent}; echo x >> $D/ledger.jsonl",
                f"rm {LEDGER}", f"truncate -s0 {LEDGER}", f"rm -rf {TH / 'session_ledger'}",
                f"sed -i 's/a/b/' {LEDGER}"):
        assert _bash(cmd)[0] is False, cmd


def test_destroying_the_cwd_does_not_reach_the_ledger():
    sl.append_turn(WD, channel="telegram", chat_key="123", user_text="keep me", assistant_text="a")
    import shutil
    shutil.rmtree(WD)            # what `rm -rf "$PWD"` / `find . -delete` would do
    WD.mkdir(parents=True)
    assert any(r.get("user") == "keep me" for r in sl.read_ledger(WD))


def test_a_ledger_left_in_the_old_place_is_moved_out_and_still_protected():
    wd = TH / "sessions/voice/telegram/456"
    old = wd / ".corvin-ledger"
    old.mkdir(parents=True)
    (old / "ledger.jsonl").write_text('{"kind":"turn","seq":1,"n":1,"user_text":"legacy","assistant_text":"a"}\n')
    assert path_gate.is_protected_path(old / "ledger.jsonl")
    assert sl.read_ledger(wd) == []                    # never adopted lazily (R6-3)
    assert sl.migrate_legacy_ledgers([TH / "sessions"]) >= 1   # at process start
    assert [r["user"] for r in sl.read_ledger(wd)] == ["legacy"]
    assert not old.exists() and sl.ledger_path(wd).is_file()


def test_a_planted_legacy_ledger_after_start_is_never_adopted():
    wd = TH / "sessions/voice/telegram/789"
    wd.mkdir(parents=True, exist_ok=True)
    sl.append_turn(wd, channel="telegram", chat_key="789", user_text="real", assistant_text="a")
    (wd / ".corvin-ledger").mkdir()
    (wd / ".corvin-ledger" / "ledger.jsonl").write_text(
        '{"kind":"turn","ts":1,"user_text":"FORGED","assistant_text":"x"}\n')
    assert [r["user"] for r in sl.read_ledger(wd)] == ["real"]


def test_old_and_new_ledgers_are_merged_not_dropped():
    wd = TH / "sessions/voice/telegram/merge"
    wd.mkdir(parents=True, exist_ok=True)
    sl.append_turn(wd, channel="telegram", chat_key="merge", user_text="new-2", assistant_text="a", ts=20)
    (wd / ".corvin-ledger").mkdir()
    (wd / ".corvin-ledger" / "ledger.jsonl").write_text(
        '{"kind":"turn","ts":10,"seq":1,"n":1,"user_text":"old-1","assistant_text":"a"}\n')
    sl.migrate_legacy_ledgers([TH / "sessions"])
    recs = sl.read_ledger(wd)
    assert [r["user"] for r in recs] == ["old-1", "new-2"] and [r["n"] for r in recs] == [1, 2]
    assert not (wd / ".corvin-ledger").exists()


def test_reading_the_ledger_is_allowed():
    assert path_gate.check({"tool_name": "Read", "tool_input": {"file_path": str(LEDGER)}})[0] is True


def test_counters_sidecar_is_protected_too():
    assert path_gate.is_protected_path(LEDGER.parent / "counters.json")

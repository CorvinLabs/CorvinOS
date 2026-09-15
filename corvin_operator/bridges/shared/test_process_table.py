#!/usr/bin/env python3
"""test_process_table.py — E2E for Layer 17 MVP.

Drives the process_table module with a real filesystem (CORVIN_HOME
redirected to a tempdir) and verifies the full lifecycle:

  register → update → list → deregister(keep=True) → cleanup
                    ↘ deregister(keep=False)
  + concurrency: two threads register N sessions simultaneously, no losses
  + table format: /ps output is non-empty, has expected columns
  + filtering: include_terminated, chat_key, persona

No mocks for any moving part — the on-disk JSONL file, the mtime cache
invalidation, the fcntl lock, and the atomic-rename writer all run for
real. Per-subtask E2E rule from CLAUDE.md.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def _fresh_module(home: Path):
    """Re-import process_table after redirecting CORVIN_HOME."""
    os.environ["CORVIN_HOME"] = str(home)
    for mod in ("process_table", "paths"):
        sys.modules.pop(mod, None)
    import process_table  # type: ignore
    # Reset the in-module read cache so test isolation holds
    process_table._read_cache.update(mtime=-1.0, sessions=[])
    return process_table


# --------------------------------------------------------------------- cases

def case_register_creates_file_with_record(home: Path) -> None:
    _section("register creates sessions.jsonl with one record")
    pt = _fresh_module(home)

    rec = pt.register_session(
        "s_001", chat_key="discord:1234567890", persona="coder"
    )
    assert rec["session_id"] == "s_001", rec
    assert rec["status"] == "running"
    assert rec["tokens_total"] == 0
    assert rec["nice"] == 0
    assert rec["started_at"] == rec["last_activity"]
    print(f"  PASS register returned record with status={rec['status']}")

    f = pt.sessions_file()
    assert f.exists(), f"file should exist at {f}"
    lines = f.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1, f"expected 1 line, got {len(lines)}: {lines}"
    print(f"  PASS sessions.jsonl has 1 line at {f}")


def case_update_patches_record_and_bumps_activity(home: Path) -> None:
    _section("update_session patches fields and bumps last_activity")
    pt = _fresh_module(home)
    pt.register_session("s_002", chat_key="discord:42", persona="research")

    # bump time by 2s so last_activity changes detectably
    time.sleep(1.1)
    out = pt.update_session("s_002", tokens_total=12345, in_flight_tool="Read")
    assert out is not None
    assert out["tokens_total"] == 12345
    assert out["in_flight_tool"] == "Read"
    assert out["last_activity"] >= out["started_at"]
    print(
        f"  PASS update returned tokens={out['tokens_total']} "
        f"tool={out['in_flight_tool']}"
    )

    # Reading via list_sessions should reflect the patch
    sessions = pt.list_sessions()
    s = next(s for s in sessions if s["session_id"] == "s_002")
    assert s["tokens_total"] == 12345
    print("  PASS list_sessions reflects update")


def case_session_id_is_structurally_immutable(home: Path) -> None:
    _section("session_id can't be changed via update (Python catches duplicate)")
    pt = _fresh_module(home)
    pt.register_session("s_003", chat_key="x", persona="y")
    # Calling update_session with session_id= as a kwarg duplicates the
    # positional arg; Python raises TypeError before the function body
    # runs. This is the structural guarantee.
    try:
        pt.update_session("s_003", session_id="evil")
    except TypeError as exc:
        assert "session_id" in str(exc)
        print(f"  PASS Python rejected with: {exc}")
        # Verify the record wasn't mutated by the failed call
        rec = pt.get_session("s_003")
        assert rec is not None and rec["session_id"] == "s_003"
        print("  PASS record unchanged after failed call")
        return
    raise AssertionError("expected TypeError on duplicate session_id")


def case_update_validates_nice(home: Path) -> None:
    _section("update_session validates nice range")
    pt = _fresh_module(home)
    pt.register_session("s_004", chat_key="x", persona="y")
    for bad in (-21, 20, 99):
        try:
            pt.update_session("s_004", nice=bad)
        except ValueError:
            print(f"  PASS rejected nice={bad}")
        else:
            raise AssertionError(f"expected ValueError for nice={bad}")


def case_register_replaces_duplicate_id(home: Path) -> None:
    _section("re-register with same id replaces previous record")
    pt = _fresh_module(home)
    pt.register_session(
        "s_005", chat_key="a", persona="coder", tokens_total=999
    )
    pt.register_session("s_005", chat_key="a", persona="coder")
    sessions = pt.list_sessions()
    matches = [s for s in sessions if s["session_id"] == "s_005"]
    assert len(matches) == 1, f"expected 1 record, got {len(matches)}"
    # Fresh registration resets tokens_total to 0
    assert matches[0]["tokens_total"] == 0
    print("  PASS duplicate id replaced cleanly")


def case_deregister_keep_marks_exited(home: Path) -> None:
    _section("deregister(keep=True) marks status=exited, keeps record")
    pt = _fresh_module(home)
    pt.register_session("s_006", chat_key="a", persona="b")

    found = pt.deregister_session("s_006", exit_reason="ok", keep=True)
    assert found is True

    rec = pt.get_session("s_006")
    assert rec is not None, "record should still exist"
    assert rec["status"] == "exited"
    assert rec["exit_reason"] == "ok"
    assert rec["exit_at"] is not None
    print(f"  PASS status={rec['status']} exit_reason={rec['exit_reason']}")

    # default list_sessions excludes exited
    assert pt.list_sessions() == []
    # include_terminated=True surfaces it
    assert len(pt.list_sessions(include_terminated=True)) == 1
    print("  PASS list filtering distinguishes terminated")


def case_deregister_killed_marks_killed(home: Path) -> None:
    _section("deregister(exit_reason='killed') marks status=killed")
    pt = _fresh_module(home)
    pt.register_session("s_007", chat_key="a", persona="b")

    pt.deregister_session("s_007", exit_reason="killed", keep=True)
    rec = pt.get_session("s_007")
    assert rec["status"] == "killed", rec
    print(f"  PASS status={rec['status']}")


def case_deregister_remove(home: Path) -> None:
    _section("deregister(keep=False) removes record entirely")
    pt = _fresh_module(home)
    pt.register_session("s_008", chat_key="a", persona="b")
    found = pt.deregister_session("s_008", keep=False)
    assert found is True
    assert pt.get_session("s_008") is None
    print("  PASS record removed")


def case_deregister_missing_returns_false(home: Path) -> None:
    _section("deregister of unknown id returns False")
    pt = _fresh_module(home)
    found = pt.deregister_session("never_existed", keep=True)
    assert found is False
    print("  PASS returns False for missing")


def case_filter_by_chat_and_persona(home: Path) -> None:
    _section("list_sessions filters by chat_key and persona")
    pt = _fresh_module(home)
    pt.register_session("s_a", chat_key="discord:1", persona="coder")
    pt.register_session("s_b", chat_key="discord:1", persona="research")
    pt.register_session("s_c", chat_key="telegram:9", persona="coder")

    by_chat = pt.list_sessions(chat_key="discord:1")
    assert {s["session_id"] for s in by_chat} == {"s_a", "s_b"}
    by_persona = pt.list_sessions(persona="coder")
    assert {s["session_id"] for s in by_persona} == {"s_a", "s_c"}
    by_both = pt.list_sessions(chat_key="discord:1", persona="coder")
    assert [s["session_id"] for s in by_both] == ["s_a"]
    print("  PASS chat/persona filters compose correctly")


def case_concurrent_register_no_losses(home: Path) -> None:
    _section("concurrent register from 4 threads — no losses")
    pt = _fresh_module(home)

    N = 25
    THREADS = 4

    def worker(tid: int) -> None:
        for i in range(N):
            sid = f"s_t{tid}_{i:03d}"
            pt.register_session(sid, chat_key=f"discord:{tid}", persona="x")

    threads = [
        threading.Thread(target=worker, args=(t,)) for t in range(THREADS)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    sessions = pt.list_sessions()
    expected = THREADS * N
    assert len(sessions) == expected, (
        f"expected {expected} sessions, got {len(sessions)} — lock failed"
    )
    print(f"  PASS {expected} sessions persisted, no race losses")


def case_cleanup_terminated_respects_ttl(home: Path) -> None:
    _section("cleanup_terminated removes only old terminated entries")
    pt = _fresh_module(home)
    pt.register_session("s_old", chat_key="a", persona="b")
    pt.register_session("s_recent", chat_key="a", persona="b")
    pt.register_session("s_alive", chat_key="a", persona="b")

    # backdate s_old's exit_at by manually patching after deregister
    pt.deregister_session("s_old", keep=True)
    pt.deregister_session("s_recent", keep=True)

    # rewrite s_old.exit_at to 2 hours ago
    sessions = pt._read_all_unlocked()
    for s in sessions:
        if s["session_id"] == "s_old":
            s["exit_at"] = "2020-01-01T00:00:00Z"
    pt._write_all_unlocked(sessions)
    pt._read_cache["mtime"] = -1.0  # force reread

    removed = pt.cleanup_terminated(ttl_seconds=3600)
    assert removed == 1, f"expected 1 removed, got {removed}"
    assert pt.get_session("s_old") is None
    assert pt.get_session("s_recent") is not None  # too recent
    assert pt.get_session("s_alive") is not None  # never terminated
    print("  PASS cleanup respects TTL boundary")


def case_format_ps_table(home: Path) -> None:
    _section("format_ps_table renders a fixed-width chat-friendly table")
    pt = _fresh_module(home)
    pt.register_session(
        "s_t1", chat_key="discord:1501540900529246251", persona="coder"
    )
    pt.update_session("s_t1", tokens_total=12500, in_flight_tool="Read")
    pt.register_session("s_t2", chat_key="telegram:42", persona="research")

    sessions = pt.list_sessions()
    out = pt.format_ps_table(sessions)
    print(out)
    assert "ID" in out and "STATUS" in out, out
    assert "s_t1" in out and "s_t2" in out, out
    assert "coder" in out and "research" in out, out
    assert "Read" in out, "in_flight_tool should appear in table"
    # Truncated chat_key should appear (shortened with …)
    assert "…" in out, "long chat_key should be truncated"
    print("  PASS table contains expected columns and content")


def case_format_empty(home: Path) -> None:
    _section("format_ps_table on empty list returns explanatory string")
    pt = _fresh_module(home)
    out = pt.format_ps_table([])
    assert out == "(no active sessions)", out
    print(f"  PASS empty -> '{out}'")


def case_corrupt_line_skipped(home: Path) -> None:
    _section("corrupt line in sessions.jsonl is skipped, registry usable")
    pt = _fresh_module(home)
    pt.register_session("s_good", chat_key="a", persona="b")
    f = pt.sessions_file()
    # Append a malformed line
    with f.open("a", encoding="utf-8") as fh:
        fh.write("{not json at all\n")
    pt._read_cache["mtime"] = -1.0  # force reread

    sessions = pt.list_sessions()
    assert len(sessions) == 1 and sessions[0]["session_id"] == "s_good"
    print("  PASS corrupt line skipped, valid record preserved")


def case_register_validates_inputs(home: Path) -> None:
    _section("register_session validates session_id + nice")
    pt = _fresh_module(home)
    try:
        pt.register_session("", chat_key="a", persona="b")
    except ValueError:
        print("  PASS rejected empty session_id")
    else:
        raise AssertionError("expected ValueError on empty session_id")

    try:
        pt.register_session("s_x", chat_key="a", persona="b", nice=99)
    except ValueError:
        print("  PASS rejected nice=99")
    else:
        raise AssertionError("expected ValueError on out-of-range nice")


def case_sort_by_nice_then_activity(home: Path) -> None:
    _section("list_sessions sorts by nice asc, then last_activity desc")
    pt = _fresh_module(home)
    pt.register_session("s_low_prio", chat_key="a", persona="b", nice=10)
    time.sleep(0.05)
    pt.register_session("s_normal", chat_key="a", persona="b", nice=0)
    time.sleep(0.05)
    pt.register_session("s_high_prio", chat_key="a", persona="b", nice=-5)

    out = pt.list_sessions()
    ids = [s["session_id"] for s in out]
    assert ids[0] == "s_high_prio", ids
    assert ids[1] == "s_normal", ids
    assert ids[2] == "s_low_prio", ids
    print(f"  PASS sort order: {ids}")


# --------------------------------------------------------------------- driver

def main() -> None:
    saved_env = os.environ.get("CORVIN_HOME")
    cases = [
        case_register_creates_file_with_record,
        case_update_patches_record_and_bumps_activity,
        case_session_id_is_structurally_immutable,
        case_update_validates_nice,
        case_register_replaces_duplicate_id,
        case_deregister_keep_marks_exited,
        case_deregister_killed_marks_killed,
        case_deregister_remove,
        case_deregister_missing_returns_false,
        case_filter_by_chat_and_persona,
        case_concurrent_register_no_losses,
        case_cleanup_terminated_respects_ttl,
        case_format_ps_table,
        case_format_empty,
        case_corrupt_line_skipped,
        case_register_validates_inputs,
        case_sort_by_nice_then_activity,
    ]
    failures = 0
    for case in cases:
        home = Path(tempfile.mkdtemp(prefix="proctab-test-"))
        try:
            case(home)
        except Exception as exc:
            failures += 1
            print(f"  FAIL: {case.__name__}: {exc!r}")
            import traceback
            traceback.print_exc()
        finally:
            shutil.rmtree(home, ignore_errors=True)

    if saved_env is None:
        os.environ.pop("CORVIN_HOME", None)
    else:
        os.environ["CORVIN_HOME"] = saved_env

    print(f"\n=== {len(cases) - failures}/{len(cases)} cases passed ===")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""test_pipe_registry.py — E2E for Layer 18 pipe registry (Phase-1 MVP).

Drives every pipe mode against a real filesystem (CORVIN_HOME
redirected to a tempdir) and verifies the contract:

  - named FIFO: multi-write, FIFO read order, queue truncates on read,
    multiple consumers compete for next message
  - anonymous: single read consumes the pipe entirely (auto-remove)
  - broadcast: each subscriber sees every write exactly once,
    independent cursors, late subscribers only see new writes
  - concurrent writes from N threads: no message loss, monotonic seq
  - corrupt line in data file is skipped
  - validation: bad names rejected, double-create rejected, read of
    nonexistent pipe raises, broadcast-read without subscriber_id
    raises

Per-subtask E2E rule from CLAUDE.md: real filesystem, real fcntl
locks, no mocks for any moving part.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def _fresh_module(home: Path):
    os.environ["CORVIN_HOME"] = str(home)
    for mod in ("pipe_registry", "paths"):
        sys.modules.pop(mod, None)
    import pipe_registry  # type: ignore
    return pipe_registry


# --------------------------------------------------------------------- cases

def case_named_fifo_basic_lifecycle(home: Path) -> None:
    _section("named FIFO: create / write / read / remove")
    pr = _fresh_module(home)
    meta = pr.create_pipe("ch1", "named", owner="research")
    assert meta["type"] == "named"
    assert meta["next_seq"] == 0
    print(f"  PASS created pipe: name={meta['name']} type={meta['type']}")

    seq0 = pr.write("ch1", {"text": "first"})
    seq1 = pr.write("ch1", {"text": "second"})
    assert seq0 == 0 and seq1 == 1
    assert pr.queue_depth("ch1") == 2
    print(f"  PASS wrote two messages, queue depth=2")

    msgs = pr.read("ch1")
    assert len(msgs) == 2, msgs
    assert msgs[0]["payload"]["text"] == "first"
    assert msgs[1]["payload"]["text"] == "second"
    assert msgs[0]["seq"] == 0 and msgs[1]["seq"] == 1
    assert pr.queue_depth("ch1") == 0, "queue should be empty after read"
    print("  PASS read returned both messages in seq order, queue truncated")

    # second read sees nothing
    assert pr.read("ch1") == []
    print("  PASS empty pipe returns []")

    assert pr.remove_pipe("ch1") is True
    assert pr.get_meta("ch1") is None
    print("  PASS remove_pipe deleted everything")


def case_named_max_messages(home: Path) -> None:
    _section("named FIFO: max_messages caps the read")
    pr = _fresh_module(home)
    pr.create_pipe("ch2", "named")
    for i in range(5):
        pr.write("ch2", {"i": i})
    msgs = pr.read("ch2", max_messages=2)
    assert [m["payload"]["i"] for m in msgs] == [0, 1]
    # remaining 3 messages still queued
    assert pr.queue_depth("ch2") == 3
    rest = pr.read("ch2")
    assert [m["payload"]["i"] for m in rest] == [2, 3, 4]
    print("  PASS partial read leaves remainder in queue")


def case_anonymous_single_read_consumes(home: Path) -> None:
    _section("anonymous: read consumes pipe entirely")
    pr = _fresh_module(home)
    pr.create_pipe("anon1", "anonymous")
    pr.write("anon1", {"x": 1})
    pr.write("anon1", {"x": 2})

    msgs = pr.read("anon1")
    assert len(msgs) == 2, msgs

    # Pipe is auto-removed
    assert pr.get_meta("anon1") is None
    try:
        pr.read("anon1")
    except KeyError:
        print("  PASS anon pipe auto-removed; subsequent read raises KeyError")
        return
    raise AssertionError("expected KeyError after anonymous consume")


def case_anonymous_empty_read_does_not_remove(home: Path) -> None:
    _section("anonymous: read of empty pipe does NOT remove it")
    pr = _fresh_module(home)
    pr.create_pipe("anon2", "anonymous")
    msgs = pr.read("anon2")
    assert msgs == []
    # Pipe should still exist (writer hasn't written yet)
    assert pr.get_meta("anon2") is not None
    print("  PASS empty-read on anonymous keeps the pipe (no writer yet)")
    pr.write("anon2", "now-something")
    msgs = pr.read("anon2")
    assert len(msgs) == 1
    assert pr.get_meta("anon2") is None
    print("  PASS first non-empty read consumes and removes")


def case_broadcast_each_subscriber_sees_every_write(home: Path) -> None:
    _section("broadcast: each subscriber sees every write exactly once")
    pr = _fresh_module(home)
    pr.create_pipe("status", "broadcast")
    s1 = pr.subscribe("status")
    s2 = pr.subscribe("status")
    print(f"  registered subscribers: {s1}, {s2}")

    pr.write("status", {"event": "boot"})
    pr.write("status", {"event": "ready"})

    msgs1 = pr.read("status", subscriber_id=s1)
    msgs2 = pr.read("status", subscriber_id=s2)
    assert len(msgs1) == 2 and len(msgs2) == 2
    assert msgs1[0]["payload"]["event"] == "boot"
    assert msgs2[1]["payload"]["event"] == "ready"
    print("  PASS both subscribers got both events")

    # Second read from same subscriber gets nothing (cursor advanced)
    assert pr.read("status", subscriber_id=s1) == []
    print("  PASS cursor advances; repeat read empty until next write")

    pr.write("status", {"event": "shutdown"})
    new1 = pr.read("status", subscriber_id=s1)
    new2 = pr.read("status", subscriber_id=s2)
    assert len(new1) == 1 and new1[0]["payload"]["event"] == "shutdown"
    assert len(new2) == 1 and new2[0]["payload"]["event"] == "shutdown"
    print("  PASS new write reaches both cursors independently")


def case_broadcast_late_subscriber_misses_history(home: Path) -> None:
    _section("broadcast: late subscriber only sees writes AFTER subscribe")
    pr = _fresh_module(home)
    pr.create_pipe("logs", "broadcast")
    s_early = pr.subscribe("logs")
    pr.write("logs", "msg-1")
    pr.write("logs", "msg-2")

    s_late = pr.subscribe("logs")
    pr.write("logs", "msg-3")

    early = pr.read("logs", subscriber_id=s_early)
    late = pr.read("logs", subscriber_id=s_late)
    early_payloads = [m["payload"] for m in early]
    late_payloads = [m["payload"] for m in late]
    assert early_payloads == ["msg-1", "msg-2", "msg-3"], early_payloads
    assert late_payloads == ["msg-3"], late_payloads
    print(f"  PASS early sub got {early_payloads}, late sub got {late_payloads}")


def case_broadcast_unsubscribe(home: Path) -> None:
    _section("broadcast: unsubscribe drops the cursor and future reads")
    pr = _fresh_module(home)
    pr.create_pipe("ch3", "broadcast")
    s = pr.subscribe("ch3")
    pr.write("ch3", "hello")
    pr.read("ch3", subscriber_id=s)

    assert pr.unsubscribe("ch3", s) is True
    try:
        pr.read("ch3", subscriber_id=s)
    except KeyError:
        print("  PASS read after unsubscribe raises KeyError")
        return
    raise AssertionError("expected KeyError after unsubscribe")


def case_broadcast_read_requires_subscriber_id(home: Path) -> None:
    _section("broadcast read without subscriber_id raises ValueError")
    pr = _fresh_module(home)
    pr.create_pipe("noid", "broadcast")
    pr.write("noid", "x")
    try:
        pr.read("noid")  # missing subscriber_id
    except ValueError as exc:
        print(f"  PASS rejected: {exc}")
        return
    raise AssertionError("expected ValueError")


def case_concurrent_writes_no_loss(home: Path) -> None:
    _section("concurrent writes from 4 threads: no loss, monotonic seq")
    pr = _fresh_module(home)
    pr.create_pipe("hot", "named")

    N = 25
    THREADS = 4
    seqs: list[int] = []
    seqs_lock = threading.Lock()

    def worker(tid: int) -> None:
        for i in range(N):
            s = pr.write("hot", f"t{tid}_msg{i}")
            with seqs_lock:
                seqs.append(s)

    threads = [
        threading.Thread(target=worker, args=(t,)) for t in range(THREADS)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    expected = THREADS * N
    assert len(seqs) == expected, f"expected {expected}, got {len(seqs)}"
    # Seq numbers must be unique and form a contiguous range 0..expected-1
    assert sorted(seqs) == list(range(expected)), "non-contiguous seqs"
    msgs = pr.read("hot")
    assert len(msgs) == expected
    print(f"  PASS {expected} concurrent writes persisted, contiguous seq")


def case_corrupt_line_skipped(home: Path) -> None:
    _section("corrupt line in data file is skipped, valid messages preserved")
    pr = _fresh_module(home)
    pr.create_pipe("corrupt-test", "named")
    pr.write("corrupt-test", "good-1")
    p = pr._data_path("corrupt-test")
    with p.open("a", encoding="utf-8") as fh:
        fh.write("{not json at all\n")
    pr.write("corrupt-test", "good-2")
    msgs = pr.read("corrupt-test")
    payloads = [m["payload"] for m in msgs]
    assert payloads == ["good-1", "good-2"], payloads
    print("  PASS corrupt line skipped, both valid messages read")


def case_invalid_names_rejected(home: Path) -> None:
    _section("invalid pipe names are rejected")
    pr = _fresh_module(home)
    bad_names = ["", "a/b", "../x", ".hidden"]
    for n in bad_names:
        try:
            pr.create_pipe(n, "named")
        except ValueError:
            print(f"  PASS rejected name: {n!r}")
        else:
            raise AssertionError(f"expected ValueError for name {n!r}")


def case_double_create_rejected(home: Path) -> None:
    _section("create_pipe twice with same name raises FileExistsError")
    pr = _fresh_module(home)
    pr.create_pipe("dup", "named")
    try:
        pr.create_pipe("dup", "named")
    except FileExistsError as exc:
        print(f"  PASS rejected: {exc}")
        return
    raise AssertionError("expected FileExistsError")


def case_invalid_pipe_type_rejected(home: Path) -> None:
    _section("invalid pipe_type rejected")
    pr = _fresh_module(home)
    try:
        pr.create_pipe("x", "weirdpipe")
    except ValueError as exc:
        assert "pipe_type" in str(exc)
        print(f"  PASS rejected: {exc}")
        return
    raise AssertionError("expected ValueError")


def case_subscribe_only_for_broadcast(home: Path) -> None:
    _section("subscribe on a non-broadcast pipe raises ValueError")
    pr = _fresh_module(home)
    pr.create_pipe("namedish", "named")
    try:
        pr.subscribe("namedish")
    except ValueError as exc:
        print(f"  PASS rejected: {exc}")
        return
    raise AssertionError("expected ValueError")


def case_double_subscribe_rejected(home: Path) -> None:
    _section("re-subscribe with same id raises FileExistsError")
    pr = _fresh_module(home)
    pr.create_pipe("two", "broadcast")
    pr.subscribe("two", subscriber_id="alice")
    try:
        pr.subscribe("two", subscriber_id="alice")
    except FileExistsError as exc:
        print(f"  PASS rejected: {exc}")
        return
    raise AssertionError("expected FileExistsError")


def case_list_pipes_returns_metadata(home: Path) -> None:
    _section("list_pipes returns every pipe's metadata")
    pr = _fresh_module(home)
    pr.create_pipe("a", "named")
    pr.create_pipe("b", "anonymous")
    pr.create_pipe("c", "broadcast")
    pipes = {p["name"]: p["type"] for p in pr.list_pipes()}
    assert pipes == {"a": "named", "b": "anonymous", "c": "broadcast"}, pipes
    print(f"  PASS list returned all 3 with correct types")


def case_remove_nonexistent_returns_false(home: Path) -> None:
    _section("remove_pipe of nonexistent returns False")
    pr = _fresh_module(home)
    assert pr.remove_pipe("never-was") is False
    print("  PASS remove of nonexistent returns False")


# --------------------------------------------------------------------- driver

def main() -> None:
    saved = os.environ.get("CORVIN_HOME")
    cases = [
        case_named_fifo_basic_lifecycle,
        case_named_max_messages,
        case_anonymous_single_read_consumes,
        case_anonymous_empty_read_does_not_remove,
        case_broadcast_each_subscriber_sees_every_write,
        case_broadcast_late_subscriber_misses_history,
        case_broadcast_unsubscribe,
        case_broadcast_read_requires_subscriber_id,
        case_concurrent_writes_no_loss,
        case_corrupt_line_skipped,
        case_invalid_names_rejected,
        case_double_create_rejected,
        case_invalid_pipe_type_rejected,
        case_subscribe_only_for_broadcast,
        case_double_subscribe_rejected,
        case_list_pipes_returns_metadata,
        case_remove_nonexistent_returns_false,
    ]
    failures = 0
    for case in cases:
        home = Path(tempfile.mkdtemp(prefix="pipe-test-"))
        try:
            case(home)
        except Exception as exc:
            failures += 1
            print(f"  FAIL: {case.__name__}: {exc!r}")
            import traceback
            traceback.print_exc()
        finally:
            shutil.rmtree(home, ignore_errors=True)

    if saved is None:
        os.environ.pop("CORVIN_HOME", None)
    else:
        os.environ["CORVIN_HOME"] = saved

    print(f"\n=== {len(cases) - failures}/{len(cases)} cases passed ===")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()

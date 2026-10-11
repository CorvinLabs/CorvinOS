"""A write cut short (disk full, killed writer) must not swallow the NEXT audit record.

Live incident, 2026-10-04: the disk filled twice. A short write left an
``engine.span.end`` cut mid-record with no newline; the next writer appended
behind it, both landed on one unparseable line and an ``aco.integrity_alert`` was
lost with it (chain lines 180567 and 183691). ``write_event`` now starts the new
record on a fresh line when it finds an unterminated tail under its lock.

These drive the REAL ``write_event`` into a temp chain and judge the result with
the REAL ``verify_chain`` — no stand-in for the writer or the verifier.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "corvin_operator" / "forge"))


@pytest.fixture()
def se(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(tmp_path / "anchor.key"))
    monkeypatch.delenv("VOICE_AUDIT_PATH", raising=False)
    monkeypatch.delenv("FORGE_ROOT", raising=False)
    import forge.security_events as mod
    return mod


def _chain(se, tmp_path, n):
    path = tmp_path / "chain" / "audit.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        se.write_event(path, "tool.created", tool=f"t{i}", details={"count": i})
    return path


def _append_dead_writers_partial_record(path: Path, keep: int = 90) -> bytes:
    """Leave the file as a writer that died mid-record does: a partial line with no
    newline, appended AFTER the last complete record. The out-of-tree tail anchor is
    only advanced after a completed write, so it still points at that last record,
    exactly as in the live incident."""
    last = path.read_bytes().split(b"\n")[-2]
    torn = last[:keep]
    with path.open("ab") as fh:
        fh.write(torn)
    return torn


def _records(path: Path):
    out = []
    for raw in path.read_bytes().split(b"\n"):
        if not raw:
            continue
        try:
            out.append(json.loads(raw))
        except ValueError:
            out.append(None)  # an unparseable line
    return out


def test_record_written_after_a_torn_tail_survives_on_its_own_line(se, tmp_path):
    path = _chain(se, tmp_path, 3)
    torn = _append_dead_writers_partial_record(path)
    assert not path.read_bytes().endswith(b"\n")

    se.write_event(path, "tool.created", tool="after-1", details={"count": 98})
    se.write_event(path, "tool.created", tool="after-2", details={"count": 99})

    recs = _records(path)
    assert [r is None for r in recs] == [False, False, False, True, False, False], (
        "exactly the torn line is unparseable; both later records are intact")
    assert [r["tool"] for r in recs if r] == ["t0", "t1", "t2", "after-1", "after-2"]
    # The torn bytes are history: never rewritten, only left behind.
    assert torn in path.read_bytes().split(b"\n")


def test_verify_reports_only_the_historic_tear_and_nothing_new(se, tmp_path):
    path = _chain(se, tmp_path, 3)
    _append_dead_writers_partial_record(path)
    se.write_event(path, "tool.created", tool="after", details={"count": 98})

    ok, problems = se.verify_chain(path)
    assert not ok
    assert [(p.get("line"), p.get("issue")) for p in problems] == [(4, "invalid_json")], problems


def test_a_clean_tail_gets_no_extra_blank_line(se, tmp_path):
    path = _chain(se, tmp_path, 3)
    se.write_event(path, "tool.created", tool="next", details={"count": 3})
    raw = path.read_bytes()
    assert raw.endswith(b"\n") and b"\n\n" not in raw
    assert len(raw.split(b"\n")) - 1 == 4
    ok, problems = se.verify_chain(path)
    assert ok, problems


def test_tail_unterminated_is_false_for_missing_and_empty_files(se, tmp_path):
    assert se._tail_unterminated(tmp_path / "absent.jsonl") is False
    empty = tmp_path / "empty.jsonl"
    empty.write_bytes(b"")
    assert se._tail_unterminated(empty) is False

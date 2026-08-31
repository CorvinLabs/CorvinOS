"""Tests for ADR-0172 M1 — worker_trace module."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

_SHARED = Path(__file__).resolve().parent
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

import worker_trace as wt  # type: ignore[import-untyped]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _audit_line(event_type: str, details: dict, ts: float = 1.0) -> str:
    return json.dumps({"ts": ts, "event_type": event_type, "details": details})


def _write_audit(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# trace_path
# ---------------------------------------------------------------------------

def test_trace_path_canonical(tmp_path: Path) -> None:
    p = wt.trace_path(tmp_path / "run1", "w42")
    assert p == tmp_path / "run1" / "workers" / "w42.trace.jsonl"


# ---------------------------------------------------------------------------
# extract_claudecode_trace
# ---------------------------------------------------------------------------

def test_extract_claudecode_basic(tmp_path: Path) -> None:
    audit = tmp_path / "audit.jsonl"
    run_dir = tmp_path / "run1"
    lines = [
        _audit_line("forge.tool_executed",
                    {"run_id": "r1", "worker_id": "w1",
                     "tool_name": "Read", "decision": "allow"}, ts=1.0),
        _audit_line("forge.tool_executed",
                    {"run_id": "r1", "worker_id": "w1",
                     "tool_name": "Bash", "decision": "allow"}, ts=2.0),
        # Different worker — must not appear
        _audit_line("forge.tool_executed",
                    {"run_id": "r1", "worker_id": "w2",
                     "tool_name": "Edit", "decision": "allow"}, ts=3.0),
        # Different run — must not appear
        _audit_line("forge.tool_executed",
                    {"run_id": "r99", "worker_id": "w1",
                     "tool_name": "Write", "decision": "allow"}, ts=4.0),
        # Unrelated event — must be ignored
        _audit_line("acs.worker_spawned", {"run_id": "r1", "worker_id": "w1"}, ts=0.5),
    ]
    _write_audit(audit, lines)

    count = wt.extract_claudecode_trace(audit, "r1", "w1", "spn_abc", run_dir)
    assert count == 2

    events = wt.read_trace(wt.trace_path(run_dir, "w1"))
    assert len(events) == 2
    assert events[0]["tool_name"] == "Read"
    assert events[1]["tool_name"] == "Bash"
    assert events[0]["seq"] == 1
    assert events[1]["seq"] == 2
    assert all(e["span_id"] == "spn_abc" for e in events)
    assert all(e["worker_id"] == "w1" for e in events)
    assert all(e["run_id"] == "r1" for e in events)


def test_extract_claudecode_missing_audit(tmp_path: Path) -> None:
    count = wt.extract_claudecode_trace(
        tmp_path / "no_such.jsonl", "r1", "w1", "s1", tmp_path / "run"
    )
    assert count == 0
    assert not wt.trace_path(tmp_path / "run", "w1").exists()


def test_extract_claudecode_no_matching_events(tmp_path: Path) -> None:
    audit = tmp_path / "audit.jsonl"
    _write_audit(audit, [
        _audit_line("acs.run_start", {"run_id": "r1"}),
    ])
    count = wt.extract_claudecode_trace(audit, "r1", "w1", "s1", tmp_path / "run")
    assert count == 0


def test_extract_claudecode_denied_call(tmp_path: Path) -> None:
    audit = tmp_path / "audit.jsonl"
    run_dir = tmp_path / "run1"
    _write_audit(audit, [
        _audit_line("forge.tool_executed",
                    {"run_id": "r1", "worker_id": "w1",
                     "tool_name": "Bash", "decision": "deny"}, ts=1.0),
    ])
    count = wt.extract_claudecode_trace(audit, "r1", "w1", "spn1", run_dir)
    assert count == 1
    events = wt.read_trace(wt.trace_path(run_dir, "w1"))
    assert events[0]["decision"] == "deny"


# ---------------------------------------------------------------------------
# extract_hermes_trace
# ---------------------------------------------------------------------------

def test_extract_hermes_basic(tmp_path: Path) -> None:
    response = json.dumps({
        "result": "done",
        "tool_calls": [
            {"name": "search", "duration_ms": 100, "exit_code": 0},
            {"name": "summarize", "duration_ms": 200, "exit_code": 0},
        ],
    })
    count = wt.extract_hermes_trace(response, "w1", "r1", "spn1", tmp_path / "run")
    assert count == 2
    events = wt.read_trace(wt.trace_path(tmp_path / "run", "w1"))
    assert [e["tool_name"] for e in events] == ["search", "summarize"]
    assert events[0]["duration_ms"] == 100


def test_extract_hermes_no_tool_calls(tmp_path: Path) -> None:
    count = wt.extract_hermes_trace(
        json.dumps({"result": "plain text answer"}), "w1", "r1", "s1", tmp_path / "run"
    )
    assert count == 0


def test_extract_hermes_plain_text(tmp_path: Path) -> None:
    count = wt.extract_hermes_trace("just plain text", "w1", "r1", "s1", tmp_path)
    assert count == 0


def test_extract_hermes_empty_tool_calls(tmp_path: Path) -> None:
    count = wt.extract_hermes_trace(
        json.dumps({"tool_calls": []}), "w1", "r1", "s1", tmp_path / "run"
    )
    assert count == 0


# ---------------------------------------------------------------------------
# read_trace + summarize_trace
# ---------------------------------------------------------------------------

def test_read_trace_missing_file(tmp_path: Path) -> None:
    assert wt.read_trace(tmp_path / "no_such.jsonl") == []


def test_read_trace_corrupt_lines(tmp_path: Path) -> None:
    p = tmp_path / "trace.jsonl"
    p.write_text('{"seq":1,"event":"tool.called","tool_name":"Read"}\nNOT JSON\n', encoding="utf-8")
    events = wt.read_trace(p)
    assert len(events) == 1
    assert events[0]["tool_name"] == "Read"


def test_summarize_trace_basic() -> None:
    events = [
        {"event": "tool.called", "tool_name": "Read",  "decision": "allow"},
        {"event": "tool.called", "tool_name": "Read",  "decision": "allow"},
        {"event": "tool.called", "tool_name": "Bash",  "decision": "deny"},
        {"event": "tool.called", "tool_name": "Edit",  "decision": "allow"},
    ]
    s = wt.summarize_trace(events)
    assert s["total_tool_calls"] == 4
    assert s["denied_calls"] == 1
    assert s["tools_used"] == {"Read": 2, "Bash": 1, "Edit": 1}


def test_summarize_trace_empty() -> None:
    s = wt.summarize_trace([])
    assert s["total_tool_calls"] == 0
    assert s["tools_used"] == {}


# ---------------------------------------------------------------------------
# PII / content leak guard — trace events must never carry tool content
# ---------------------------------------------------------------------------

def test_extract_claudecode_no_content_fields(tmp_path: Path) -> None:
    """Trace events must not carry any content field, even if the audit
    source somehow had one (defence-in-depth against future audit changes)."""
    audit = tmp_path / "audit.jsonl"
    run_dir = tmp_path / "run1"
    _write_audit(audit, [
        _audit_line("forge.tool_executed", {
            "run_id": "r1", "worker_id": "w1",
            "tool_name": "Read",
            "decision": "allow",
            "input": "secret content that must never appear",  # hypothetical future field
        }, ts=1.0),
    ])
    wt.extract_claudecode_trace(audit, "r1", "w1", "spn1", run_dir)
    events = wt.read_trace(wt.trace_path(run_dir, "w1"))
    assert len(events) == 1
    assert "input" not in events[0]
    assert "secret" not in json.dumps(events[0])


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

"""E2E (reachability proof): a bridge turn records the provider quota windows.

Driven through the REAL entry point ``adapter.call_claude_streaming`` against a REAL
subprocess (``tests/fake_claude.py``) replaying a stream captured from the real CLI
(``core/console/tests/fixtures/chat_diff/real_file_tools.jsonl`` — the one capture that
carries a ``rate_limit_event``). Asserted on the file the console's Models page reads,
resolved the way the console resolves it.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
for _p in (HERE, HERE / "tests", HERE.parents[1] / "forge"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import bgscope_testkit as kit  # noqa: E402

FIXTURE = REPO / "core" / "console" / "tests" / "fixtures" / "chat_diff" / "real_file_tools.jsonl"

pytestmark = pytest.mark.skipif(sys.platform.startswith("win"),
                                reason="POSIX process semantics in the harness")


def _fresh_adapter():
    os.environ.pop("ADAPTER_FAKE_CLAUDE", None)
    for mod in list(sys.modules):
        if mod in ("adapter", "agents") or mod.startswith("agents."):
            del sys.modules[mod]
    import adapter  # type: ignore

    adapter._house_rules_classifier = lambda task, rules, auth, **_kw: ("", 1.0, "test-benign")
    return adapter


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    for k, v in {
        "CORVIN_HOME": tmp_path / "corvin", "XDG_CONFIG_HOME": tmp_path / "xdg",
        "FORGE_ROOT": tmp_path / "forge", "ADAPTER_INBOX": tmp_path / "inbox",
        "ADAPTER_OUTBOX": tmp_path / "outbox", "VOICE_AUDIT_PATH": tmp_path / "audit.jsonl",
    }.items():
        monkeypatch.setenv(k, str(v))
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(tmp_path / "anchor.key"))
    monkeypatch.setenv("ADAPTER_HEARTBEAT_INTERVAL", "0")
    monkeypatch.setenv("ADAPTER_STREAM_IDLE_TIMEOUT", "30")
    return tmp_path


def _turn(monkeypatch, sandbox, fixture):
    for k, v in kit.fake_env(sandbox, fixture, speedup=20).items():
        monkeypatch.setenv(k, v)
    adapter = _fresh_adapter()
    adapter.call_claude_streaming("irrelevant", channel="discord", chat_key="rlw",
                                  mode="unrestricted", profile=None)
    from corvin_console import rate_limit_windows as rlw  # noqa: PLC0415
    return rlw


def test_bridge_turn_writes_the_windows_the_models_page_reads(sandbox, monkeypatch):
    rlw = _turn(monkeypatch, sandbox, FIXTURE)
    path = rlw._path("_default")
    assert path is not None and str(path).startswith(str(sandbox / "corvin")), path
    rec = rlw.read("_default")
    assert rec["windows"]["five_hour"] == {"utilization": 0.08, "resets_at": 1791567000.0}
    assert rec["windows"]["seven_day"] == {"utilization": 0.16, "resets_at": 1792036800.0}
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    raw = path.read_text()
    # identifiers in the captured event never reach the store
    evt = next(json.loads(l) for l in FIXTURE.read_text().splitlines() if '"rate_limit_event"' in l)
    assert evt["session_id"] not in raw and evt["uuid"] not in raw


def test_stream_without_the_event_leaves_no_store(sandbox, monkeypatch):
    rlw = _turn(monkeypatch, sandbox, "plain_no_children")
    assert rlw.read("_default") == {}


def test_normaliser_surfaces_only_events_with_a_payload():
    from agents.claude_code import ClaudeCodeEngine  # noqa: PLC0415

    evs = ClaudeCodeEngine._normalise_all({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"}})
    assert [e.type for e in evs] == ["rate_limit"]
    assert ClaudeCodeEngine._normalise_all({"type": "rate_limit_event"}) == []

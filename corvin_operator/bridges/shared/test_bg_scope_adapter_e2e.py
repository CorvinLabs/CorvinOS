"""E2E (T-0071, reachability proof): a turn with background children, driven through
the REAL entry point ``adapter.call_claude_streaming`` against a REAL subprocess
(``tests/fake_claude.py`` replaying a stream captured from the real CLI), must reach
``ScopeTracker`` and leave ``bgscope.*`` records in the tenant's hash-chained audit
log — structured scalars only, chain intact.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE / "tests", HERE.parents[1] / "forge"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import bgscope_testkit as kit  # noqa: E402

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


def _run(monkeypatch, sandbox, fixture, chat="bgs"):
    for k, v in kit.fake_env(sandbox, fixture, speedup=4).items():
        monkeypatch.setenv(k, v)
    adapter = _fresh_adapter()
    ans = adapter.call_claude_streaming("irrelevant", channel="discord", chat_key=chat,
                                        mode="unrestricted", profile=None)
    from forge import paths, security_events as sec  # type: ignore  # noqa: PLC0415

    chain = paths.tenant_audit_chain("_default")
    recs = []
    if chain.exists():
        recs = [json.loads(l) for l in chain.read_text().splitlines() if l.strip()]
    return ans, [r for r in recs if str(r.get("event_type", "")).startswith("bgscope.")], chain, sec


def test_bash_child_reaches_the_tracker_and_the_audit_chain(sandbox, monkeypatch):
    _, recs, chain, sec = _run(monkeypatch, sandbox, "bash_bg_ok")
    # child records only here; waiting/completed are covered by test_bg_scope_completion.py
    recs = [r for r in recs if r["event_type"] in ("bgscope.child_started", "bgscope.child_finished")]
    assert [r["event_type"] for r in recs] == ["bgscope.child_started", "bgscope.child_finished"]
    start, fin = recs[0]["details"], recs[1]["details"]
    assert start["kind"] == "bash" and start["tenant_id"] == "_default"
    assert start["scope_id"].startswith("ot_") and start["children_open"] == 1
    assert (fin["state"], fin["exit_code"], fin["children_open"]) == ("completed", 0, 0)
    # the fixture child runs 8 s on the captured clock = ~2 s of wall time at 4x replay
    assert 1500 <= fin["duration_ms"] <= 4500, fin["duration_ms"]
    ok, problems = sec.verify_chain(chain)
    assert ok, problems


def test_audit_carries_no_free_text(sandbox, monkeypatch):
    """D9: neither the child's description nor the sub-agent prompt may reach the chain."""
    _, recs, _, _ = _run(monkeypatch, sandbox, "agent_bg")
    blob = json.dumps(recs, ensure_ascii=False)
    assert recs, "no bgscope records written"
    # the strings the CLI really sent, read from the fixture itself — never a guess at them
    started = next(e for e in kit.load_fixture("agent_bg") if e.get("subtype") == "task_started")
    secrets_ = [started["description"], started["prompt"], "Fertig"]
    assert all(len(x) > 3 for x in secrets_), secrets_
    for needle in secrets_:
        assert needle not in blob, f"{needle!r} reached the audit chain"
    allowed = {"scope_id", "child_id", "kind", "state", "exit_code", "duration_ms", "healed",
               "children_open", "tenant_id", "chain_dna", "wakeups", "children_total",
               "end_reason", "limit_s", "age_s", "limit"}
    for r in recs:
        assert set(r["details"]) <= allowed, set(r["details"]) - allowed


def test_monitor_is_audited_as_monitor_not_bash(sandbox, monkeypatch):
    _, recs, _, _ = _run(monkeypatch, sandbox, "monitor_3lines")
    assert {r["details"]["kind"] for r in recs if "kind" in r["details"]} == {"monitor"}
    assert [r["event_type"] for r in recs].count("bgscope.child_started") == 1


def test_mixed_children_are_each_audited_once(sandbox, monkeypatch):
    _, recs, _, _ = _run(monkeypatch, sandbox, "bash_and_agent_mixed")
    types = [r["event_type"] for r in recs]
    assert types.count("bgscope.child_started") == 2 and types.count("bgscope.child_finished") == 2
    by_kind = {r["details"]["kind"]: r["details"]["state"]
               for r in recs if r["event_type"] == "bgscope.child_finished"}
    assert by_kind == {"agent": "completed", "bash": "failed"}


def test_turn_without_children_writes_no_bgscope_records(sandbox, monkeypatch):
    ans, recs, _, _ = _run(monkeypatch, sandbox, "plain_no_children")
    assert ans.strip() == "ok" and recs == []

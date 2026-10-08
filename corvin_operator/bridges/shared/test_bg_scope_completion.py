"""E2E for T-0072 (ADR-2236 D2-D6): a turn is complete only when its background
children are, the user gets the first answer at once, interim messages while children
run and exactly ONE final message — driven through the REAL entry point
``adapter.process_one`` against a REAL subprocess (``tests/fake_claude.py`` replaying a
stream captured from the real CLI).

What is asserted is the outbox the messenger daemons poll, the session ledger, the audit
chain and process liveness — not internal state.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE / "tests", HERE.parents[1] / "forge"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import bgscope_testkit as kit  # noqa: E402

pytestmark = pytest.mark.skipif(sys.platform.startswith("win"),
                                reason="POSIX process semantics in the harness")


@pytest.fixture()
def box(tmp_path, monkeypatch):
    inbox, outbox, processed = tmp_path / "inbox", tmp_path / "outbox", tmp_path / "processed"
    for d in (inbox, outbox, processed, tmp_path / "corvin"):
        d.mkdir(parents=True)
    xdg = tmp_path / "xdg"
    (xdg / "corvin-voice").mkdir(parents=True)
    (xdg / "corvin-voice" / "profile.json").write_text("{}")
    for k, v in {
        "ADAPTER_INBOX": inbox, "ADAPTER_OUTBOX": outbox, "ADAPTER_PROCESSED": processed,
        "CORVIN_HOME": tmp_path / "corvin", "XDG_CONFIG_HOME": xdg,
        "FORGE_ROOT": tmp_path / "forge", "VOICE_AUDIT_PATH": tmp_path / "audit.jsonl",
    }.items():
        monkeypatch.setenv(k, str(v))
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(tmp_path / "anchor.key"))
    monkeypatch.setenv("CORVIN_OS_ENGINE", "claude_code")
    monkeypatch.setenv("ADAPTER_HEARTBEAT_INTERVAL", "0")
    monkeypatch.setenv("ADAPTER_STREAM_IDLE_TIMEOUT", "2")        # the OLD limit that used to kill a quiet child
    monkeypatch.setenv("BRIDGE_VOICE_SUMMARY", "0")
    return tmp_path


def _adapter():
    for mod in list(sys.modules):
        if mod in ("adapter", "agents", "profile") or mod.startswith("agents."):
            del sys.modules[mod]
    import adapter  # type: ignore

    adapter._house_rules_classifier = lambda task, rules, auth, **_kw: ("", 1.0, "test-benign")
    return adapter


def _turn(monkeypatch, box: Path, fixture: str, *, speedup: float = 4, silent_s: float = 0,
          settings: dict | None = None, env: dict | None = None, msg_id: str = "m1"):
    for k, v in kit.fake_env(box, fixture, speedup=speedup, silent_s=silent_s).items():
        monkeypatch.setenv(k, v)
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    adapter = _adapter()
    inbox = Path(os.environ["ADAPTER_INBOX"])
    f = inbox / f"{msg_id}.json"
    f.write_text(json.dumps({"id": msg_id, "channel": "sandbox-bg", "from": "u42",
                             "chat_id": "chan-1", "text": "please do it", "ts": 0}))
    t0 = time.time()
    adapter.process_one(f, settings={"whitelist": ["u42"], **(settings or {})})
    elapsed = time.time() - t0
    outbox = Path(os.environ["ADAPTER_OUTBOX"])
    files = sorted(outbox.glob(f"{msg_id}_*.json"))
    return [json.loads(p.read_text()) for p in files], [p.name for p in files], elapsed


def _split(msgs):
    # a voice-only envelope is also `_final` but carries no text — it is the spoken
    # version of the ONE closing message, not a second message
    final = [m for m in msgs if m.get("_final") and m.get("text")]
    interim = [m for m in msgs if not m.get("_final") and not m.get("_progress")
               and not m.get("_heartbeat") and m.get("text")]
    return interim, final


def _audit(box: Path, prefix="bgscope."):
    from forge import paths  # type: ignore  # noqa: PLC0415

    chain = paths.tenant_audit_chain("_default")
    if not chain.exists():
        return []
    recs = [json.loads(line) for line in chain.read_text().splitlines() if line.strip()]
    return [r for r in recs if str(r.get("event_type", "")).startswith(prefix)]


def _ledger_texts(box: Path):
    out = []
    for p in (box / "corvin").rglob("ledger.jsonl"):
        out += [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
    return out


# ------------------------------------------------------------ happy paths ---

@pytest.mark.parametrize("progress", [True, False], ids=["progress_on", "progress_off"])
def test_first_answer_at_once_then_exactly_one_final(box, monkeypatch, progress):
    msgs, names, _ = _turn(monkeypatch, box, "bash_bg_ok",
                           settings={"progress_updates": progress})
    interim, final = _split(msgs)
    assert len(final) == 1, [m.get("text") for m in msgs]
    assert len(interim) == 1
    first, last = interim[0], final[0]
    assert "gestartet" in first["text"]
    assert "1 background task still running" in first["text"]
    assert "abgeschlossen" in last["text"].lower()
    assert "still running" not in last["text"]
    # Art. 50 marking on both, interim is a normal message (not a sticky, not final)
    assert first["provenance"]["ai_generated"] is True and last["provenance"]["ai_generated"] is True
    assert "_final" not in first and "_progress" not in first
    assert first["msg_id"] != last["msg_id"] and first["chat_id"] == last["chat_id"] == "chan-1"
    # a backlog is still delivered in order: the interim file sorts before the final one
    assert names.index(next(n for n in names if "-000" in n)) < names.index(
        next(n for n in names if n.endswith("_00.json")))


def test_a_quiet_child_survives_longer_than_the_old_idle_limit(box, monkeypatch):
    """ADAPTER_STREAM_IDLE_TIMEOUT=2 would have killed it (T-0070 repro)."""
    msgs, _, elapsed = _turn(monkeypatch, box, "bash_bg_ok", speedup=4, silent_s=3)
    interim, final = _split(msgs)
    assert len(final) == 1 and "abgeschlossen" in final[0]["text"].lower()
    assert elapsed > 3
    assert kit.spawn_count(box) == 1, "the prompt was re-run"


def test_monitor_every_wakeup_reaches_the_user_and_the_last_one_is_final(box, monkeypatch):
    msgs, _, _ = _turn(monkeypatch, box, "monitor_3lines")
    interim, final = _split(msgs)
    texts = [m["text"] for m in interim]
    assert len(final) == 1 and len(interim) == 4, [m["text"][:50] for m in msgs]
    assert "monitor laeuft" in texts[0]
    for i, tick in enumerate(("Tick 1", "Tick 2", "Tick 3"), start=1):
        assert tick in texts[i]
    assert all("still running" in t for t in texts)
    assert "beendet" in final[0]["text"].lower() and "still running" not in final[0]["text"]


def test_child_ending_before_the_first_result_is_handled(box, monkeypatch):
    msgs, _, _ = _turn(monkeypatch, box, "bash_and_agent_mixed")
    interim, final = _split(msgs)
    assert len(final) == 1 and len(interim) == 2
    assert "bash" in final[0]["text"].lower()          # the failed bash child is the last one standing


def test_turn_without_children_is_unchanged(box, monkeypatch):
    msgs, names, _ = _turn(monkeypatch, box, "plain_no_children")
    interim, final = _split(msgs)
    assert interim == [] and len(final) == 1 and final[0]["text"].strip().startswith("ok")
    assert [n for n in names if "-0" in n] == [], "an interim file appeared for a plain turn"
    assert _audit(box) == []


def test_the_ledger_holds_everything_the_user_saw_in_order(box, monkeypatch):
    msgs, _, _ = _turn(monkeypatch, box, "monitor_3lines")
    turns = _ledger_texts(box)
    assert len(turns) == 1
    said = turns[0].get("assistant_text", "")
    order = [said.find(x) for x in ("monitor laeuft", "Tick 1", "Tick 2", "Tick 3", "Der Monitor ist beendet")]
    assert all(i >= 0 for i in order) and order == sorted(order), said


def test_scope_lifecycle_is_audited_and_the_chain_verifies(box, monkeypatch):
    _turn(monkeypatch, box, "bash_bg_ok")
    types = [r["event_type"] for r in _audit(box)]
    assert types == ["bgscope.child_started", "bgscope.waiting", "bgscope.child_finished",
                     "bgscope.completed"], types
    done = _audit(box)[-1]["details"]
    assert done["end_reason"] == "quiescent" and done["children_total"] == 1 and done["wakeups"] == 1
    from forge import paths, security_events as sec  # type: ignore  # noqa: PLC0415

    ok, problems = sec.verify_chain(paths.tenant_audit_chain("_default"))
    assert ok, problems


# -------------------------------------------------------------------- caps ---

def test_child_cap_ends_the_scope_honestly_and_kills_the_process(box, monkeypatch):
    msgs, _, elapsed = _turn(monkeypatch, box, "bash_bg_ok", speedup=1,
                             env={"CORVIN_BG_CHILD_MAX": "3"})
    interim, final = _split(msgs)
    assert len(final) == 1
    text = final[0]["text"]
    assert "Stopped after 3 s" in text and "1 background task" in text and "bash" in text
    # The cut must happen at the cap, not when the child would have ended on its own
    # (~8 s after it started). Wall time of process_one is no evidence: the voice summary
    # afterwards re-runs the fake CLI. The audit timestamps are.
    audit = {r["event_type"]: r for r in _audit(box)}
    waited = audit["bgscope.child_cap_exceeded"]["ts"] - audit["bgscope.child_started"]["ts"]
    assert 3 <= waited < 6.5, f"cap fired after {waited:.1f}s (cap=3s, child would run 8s)"
    assert "abgeschlossen" not in text.lower(), "the child's own wake-up text leaked in"
    assert not kit.pid_alive(box / "fake.pid"), "the CLI (and with it the child) is still alive"
    assert kit.spawn_count(box) == 1
    types = [r["event_type"] for r in _audit(box)]
    assert "bgscope.child_cap_exceeded" in types
    assert _audit(box)[-1]["details"]["end_reason"] == "child_cap"


def test_wakeup_cap_stops_a_chatty_monitor(box, monkeypatch):
    msgs, _, _ = _turn(monkeypatch, box, "monitor_3lines", speedup=2,
                       env={"CORVIN_BG_WAKEUP_MAX": "2"})
    interim, final = _split(msgs)
    assert len(final) == 1 and "more than 2 updates" in final[0]["text"]
    texts = [m["text"] for m in interim]
    assert any("Tick 3" in t for t in texts), "the update that tripped the cap was still delivered"
    assert "Tick 3" not in final[0]["text"], "an already delivered update must not be quoted again"
    assert not kit.pid_alive(box / "fake.pid")
    assert "bgscope.wakeup_cap_exceeded" in [r["event_type"] for r in _audit(box)]


def test_a_cap_of_zero_is_rejected_not_obeyed(box, monkeypatch):
    """A cap of 0 would disable the only bound (ADR-2236 D12)."""
    monkeypatch.setenv("CORVIN_BG_CHILD_MAX", "0")
    monkeypatch.setenv("CORVIN_BG_WAKEUP_MAX", "-5")
    import bg_scope  # type: ignore  # noqa: PLC0415

    assert bg_scope.child_max_s() == bg_scope.CHILD_MAX_DEFAULT_S
    assert bg_scope.wakeup_max() == bg_scope.WAKEUP_MAX_DEFAULT

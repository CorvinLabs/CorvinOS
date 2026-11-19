"""The per-chat debug log is content-free, and a turn that fails after its spawn is recorded.

1. ``chat_debug.jsonl`` sits in the worker's cwd and outside erasure's content scan. Driven
   through the real entry ``adapter.call_claude_streaming`` against a real subprocess (the
   launcher of ``test_bridge_sensitive_reroute_e2e``): the ``turn.start`` event carries neither
   the prompt text nor the raw chat key, the file is mode 0600, and a symlink planted under its
   name is never written through.

2. ``process_one`` records a turn in the session ledger only once its answer is final. A turn
   that RAISED after the CLI was started is in the CLI transcript but was in no record. Driven
   through ``adapter.process_one`` with a real inbox envelope; only the engine dispatch
   (``call_claude_streaming``) is replaced by one that reports the spawn and raises — the
   exception must still leave ``process_one`` (the runner's quarantine path is unchanged).
"""
from __future__ import annotations

import json
import stat
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import pytest  # noqa: E402

from test_bridge_sensitive_reroute_e2e import (  # noqa: E402,F401
    _fresh_adapter, _policy, env,
)

PROMPT = "tell me about the lighthouse MARKER-PROMPT-7731 please"
CHAT = "4915112345678"


def _events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_turn_start_carries_no_prompt_text_and_no_raw_chat_key(env):
    env.write(_policy(allow_default=True))
    env.turn(PROMPT, chat=CHAT)
    adapter = _fresh_adapter()
    path = adapter._session_dir("discord", CHAT) / "chat_debug.jsonl"
    raw = path.read_text(encoding="utf-8")
    starts = [e for e in _events(path) if e["event"] == "turn.start"]
    assert len(starts) == 1, raw                      # positive control: the event was written
    assert starts[0]["prompt_len"] == len(PROMPT)
    assert "prompt_preview" not in raw
    assert "MARKER-PROMPT-7731" not in raw and "lighthouse" not in raw
    assert CHAT not in raw, "the raw chat key is in the debug log"
    assert "chat_key" not in starts[0] and len(starts[0]["chat_fp"]) == 16
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    # The fingerprint is keyed: the bare hash of the chat key (reversible by enumerating
    # phone numbers / user ids) is not what is written, and the key is outside the cwd.
    import hashlib
    assert hashlib.sha256(CHAT.encode()).hexdigest()[:12] not in raw
    key = Path(adapter._session_dir("discord", CHAT)).parents[3] / "global" / "chat_debug_fp.key"
    assert key.is_file() and stat.S_IMODE(key.stat().st_mode) == 0o600
    assert path.parent not in key.parents


def test_the_fingerprint_is_stable_per_install_and_differs_across_keys(env, tmp_path, monkeypatch):
    adapter = _fresh_adapter()
    one = adapter._chat_debug_fp(CHAT)
    assert one and one == adapter._chat_debug_fp(CHAT)
    adapter2 = _fresh_adapter()                      # same install, new process state
    assert adapter2._chat_debug_fp(CHAT) == one
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "other-install"))
    adapter3 = _fresh_adapter()
    assert adapter3._chat_debug_fp(CHAT) not in ("", one)


def test_a_planted_key_symlink_yields_no_fingerprint(env, tmp_path):
    adapter = _fresh_adapter()
    import paths  # type: ignore
    gdir = Path(paths.tenant_global_dir("_default"))
    gdir.mkdir(parents=True, exist_ok=True)
    known = tmp_path / "known.key"
    known.write_bytes(b"A" * 32)
    (gdir / "chat_debug_fp.key").symlink_to(known)
    assert adapter._chat_debug_fp(CHAT) == ""
    wd = adapter._session_dir("discord", "nokey")
    wd.mkdir(parents=True, exist_ok=True)
    adapter._chat_debug_event(wd, "turn.start", chat_key=CHAT, channel="discord", prompt_len=1)
    [ev] = _events(wd / "chat_debug.jsonl")
    assert "chat_fp" not in ev and CHAT not in json.dumps(ev)


def test_legacy_debug_logs_lose_prompt_preview_and_raw_chat_key_on_first_write(env):
    adapter = _fresh_adapter()
    wd = adapter._session_dir("discord", "legacy")
    wd.mkdir(parents=True, exist_ok=True)
    old = json.dumps({"ts": "2026-01-01T00:00:00Z", "event": "turn.start", "chat_key": CHAT,
                      "prompt_preview": "my iban is DE00 MARKER-LEGACY", "prompt_len": 29})
    clean = json.dumps({"ts": "2026-01-01T00:00:01Z", "event": "turn.done", "status": "completed"})
    for name in ("chat_debug.jsonl", "chat_debug.jsonl.1", "chat_debug.jsonl.2"):
        (wd / name).write_text(old + "\nnot json\n" + clean + "\n", encoding="utf-8")
        (wd / name).chmod(0o644)
    adapter._chat_debug_event(wd, "turn.start", chat_key=CHAT, channel="discord", prompt_len=3)
    for name in ("chat_debug.jsonl", "chat_debug.jsonl.1", "chat_debug.jsonl.2"):
        raw = (wd / name).read_text(encoding="utf-8")
        assert "MARKER-LEGACY" not in raw and "prompt_preview" not in raw and CHAT not in raw, name
        evs = _events(wd / name)
        assert evs[0]["event"] == "turn.start" and evs[0]["prompt_len"] == 29   # facts are kept
        assert evs[0]["chat_fp"] == adapter._chat_debug_fp(CHAT)
        assert evs[1]["event"] == "turn.done"
        assert stat.S_IMODE((wd / name).stat().st_mode) == 0o600
    assert len(_events(wd / "chat_debug.jsonl")) == 3


def test_the_debug_log_is_never_written_through_a_planted_symlink(env, tmp_path):
    adapter = _fresh_adapter()
    wd = adapter._session_dir("discord", "dbg-link")
    wd.mkdir(parents=True, exist_ok=True)
    victim = tmp_path / "victim.jsonl"
    victim.write_text("UNTOUCHED\n", encoding="utf-8")
    (wd / "chat_debug.jsonl").symlink_to(victim)
    adapter._chat_debug_event(wd, "turn.start", chat_key="dbg-link", channel="discord", prompt_len=3)
    assert victim.read_text(encoding="utf-8") == "UNTOUCHED\n"
    path = wd / "chat_debug.jsonl"
    assert not path.is_symlink()
    assert [e["event"] for e in _events(path)] == ["turn.start"]


def test_an_older_world_readable_debug_log_is_tightened(env):
    adapter = _fresh_adapter()
    wd = adapter._session_dir("discord", "dbg-mode")
    wd.mkdir(parents=True, exist_ok=True)
    path = wd / "chat_debug.jsonl"
    path.write_text("", encoding="utf-8")
    path.chmod(0o644)
    adapter._chat_debug_event(wd, "turn.done", status="completed")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


# ── a turn that raises after the CLI spawn ──────────────────────────────────

@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    base = tmp_path
    for d in ("inbox", "outbox", "processed", "bridges", "home"):
        (base / d).mkdir()
    for k, v in {
        "CORVIN_HOME": base / "corvin", "XDG_CONFIG_HOME": base / "xdg", "HOME": base / "home",
        "CORVIN_TENANT_ID": "_default", "CORVIN_AUDIT_ANCHOR_KEY": base / "anchor.key",
        "ADAPTER_INBOX": base / "inbox", "ADAPTER_OUTBOX": base / "outbox",
        "ADAPTER_PROCESSED": base / "processed", "ADAPTER_BRIDGES_DIR": base / "bridges",
        "ADAPTER_FAKE_CLAUDE": "1", "ADAPTER_FAKE_DELAY": "0", "ADAPTER_DISABLE_VOICE": "1",
        "ADAPTER_ROUTING_MODE": "off", "CLAUDE_CONFIG_DIR": base / "claude-cfg",
    }.items():
        monkeypatch.setenv(k, str(v))
    monkeypatch.delenv("FORGE_ROOT", raising=False)
    sys.modules.pop("adapter", None)
    import adapter  # type: ignore
    adapter._house_rules_classifier = lambda task, rules, auth, **_kw: ("", 1.0, "test-benign")
    return adapter, base


def _envelope(base: Path, mid: str, text: str) -> Path:
    f = base / "inbox" / f"{mid}.json"
    f.write_text(json.dumps({"id": mid, "channel": "discord", "from": "424242", "chat_id": "fail-1",
                             "chat_type": "private", "text": text, "ts": time.time()}), encoding="utf-8")
    return f


def _turns(adapter, chat: str) -> list[dict]:
    import session_ledger as ledger  # type: ignore
    return [r for r in ledger.read_ledger(adapter._session_dir("discord", chat)) if r.get("kind") == "turn"]


def test_a_turn_that_raises_after_the_spawn_is_recorded_and_still_raises(sandbox, monkeypatch):
    adapter, base = sandbox

    def _boom(prompt, **_kw):
        adapter._TURN_OUTCOME.cli_spawned = True          # the CLI was started for this turn
        raise RuntimeError("stream broke at /home/someone/secret-path")
    monkeypatch.setattr(adapter, "call_claude_streaming", _boom)

    with pytest.raises(RuntimeError):
        adapter.process_one(_envelope(base, "f1", "what is the tide table for friday"),
                            settings={"whitelist": ["424242"]})
    [rec] = _turns(adapter, "fail-1")
    assert rec["user"] == "what is the tide table for friday"
    assert rec["spawned"] is True
    assert "RuntimeError" in rec["assistant"]
    assert "secret-path" not in json.dumps(rec), "the exception message went into the record"
    assert (base / "inbox" / "f1.json").exists(), "the envelope is the runner's to quarantine"


def test_a_turn_that_raises_before_any_spawn_is_recorded_as_a_fact_never_as_live(sandbox, monkeypatch):
    """R2-BR-5: such a turn was in no record at all. It is recorded with spawned=False and the
    exception type; its text is not kept (nothing proves which pre-spawn gates it passed)."""
    adapter, base = sandbox

    def _boom(prompt, **_kw):
        raise KeyError("failed before the engine was started at /home/someone/secret-path")
    monkeypatch.setattr(adapter, "call_claude_streaming", _boom)
    with pytest.raises(KeyError):
        adapter.process_one(_envelope(base, "f2", "second question"), settings={"whitelist": ["424242"]})
    [rec] = _turns(adapter, "fail-1")
    assert rec["spawned"] is False
    assert rec["refused"] == "failed_before_spawn"
    assert "KeyError" in rec["assistant"] and "before any engine" in rec["assistant"]
    assert rec["user"] == "" and rec["user_chars"] == len("second question")
    assert "secret-path" not in json.dumps(rec) and "second question" not in json.dumps(rec)
    # It can never count as live, and its text is not re-supplied.
    import session_ledger as ledger  # type: ignore
    block = ledger.render_context(adapter._session_dir("discord", "fail-1"),
                                  channel="discord", chat_key="fail-1")
    assert "second question" not in block and "failed before any engine was started" in block


def test_a_normal_turn_is_recorded_once(sandbox):
    """Positive control for the harness: the unchanged path still writes exactly one record."""
    adapter, base = sandbox
    adapter.process_one(_envelope(base, "f3", "third question"), settings={"whitelist": ["424242"]})
    [rec] = _turns(adapter, "fail-1")
    assert rec["user"] == "third question" and rec["spawned"] is True and "route" not in rec

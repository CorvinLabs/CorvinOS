#!/usr/bin/env python3
"""test_bg_task.py — the messenger-origin producer for background completions.

Two levels:
  1. bg_task_worker.py end-to-end: register → run the DETACHED worker (fake
     engine) → it calls mark_done → deliver_ready produces a routed outbox
     envelope carrying the real result. Proves the producer feeds the backbone.
  2. the `/task` command handler in adapter.process_one: registers a pending
     completion, spawns the worker with the correct spec, and ACKs — without
     actually running the engine (Popen captured).

Run: python3 operator/bridges/shared/test_bg_task.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKER = HERE / "bg_task_worker.py"
_BASE_PATH = "/usr/bin:/bin:/usr/local/bin:" + os.path.expanduser("~/.local/bin")

# adapter.subprocess IS the stdlib subprocess module (a process-wide singleton,
# not reloaded when "adapter" is popped from sys.modules) — a test that does
# `adapter.subprocess.Popen = _FakePopen` without restoring it permanently
# corrupts subprocess.Popen for every later test in this file. Restore it in
# a finally block after each test that mocks it.
_REAL_POPEN = subprocess.Popen


# ── Level 1: the detached worker actually produces a delivered completion ──


def test_worker_end_to_end() -> None:
    with tempfile.TemporaryDirectory() as td:
        home = Path(td) / "home"
        outbox = Path(td) / "outbox"
        env = os.environ.copy()
        env["PATH"] = env.get("PATH") or _BASE_PATH
        env["CORVIN_HOME"] = str(home)
        env["ADAPTER_OUTBOX"] = str(outbox)
        env["ADAPTER_FAKE_CLAUDE"] = "1"
        env["ADAPTER_FAKE_DELAY"] = "0"

        reg = (f"import sys; sys.path.insert(0, r'{HERE}'); "
               "import completion_notify as cn; "
               "cn.register('bgt_e2e', channel='signal', chat_id='+4915100000000', "
               "sender='+4915100000000', label='nightly job')")
        r = subprocess.run([sys.executable, "-c", reg], env=env,
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr

        spec_file = Path(td) / "spec.json"
        spec_file.write_text(json.dumps({
            "task_id": "bgt_e2e", "instruction": "summarise the logs",
            "channel": "signal", "chat_key": "+4915100000000",
        }))
        w = subprocess.run([sys.executable, str(WORKER), str(spec_file)], env=env,
                           capture_output=True, text=True, timeout=60)
        assert w.returncode == 0, f"worker failed: {w.stderr}"
        assert not spec_file.exists(), "worker must unlink the 0600 spec file"

        # deliver in a subprocess sharing the same CORVIN_HOME
        dl = (f"import sys; sys.path.insert(0, r'{HERE}'); "
              "import completion_notify as cn; "
              f"print(cn.deliver_ready(r'{outbox}'))")
        d = subprocess.run([sys.executable, "-c", dl], env=env,
                           capture_output=True, text=True)
        assert d.returncode == 0, d.stderr
        assert d.stdout.strip().endswith("1"), f"deliver_ready: {d.stdout!r}"

        files = list(outbox.glob("cn_*.json"))
        assert len(files) == 1, f"no delivered envelope: {list(outbox.iterdir())}"
        envelope = json.loads(files[0].read_text())
        assert envelope["channel"] == "signal"
        assert envelope["chat_id"] == "+4915100000000"
        # The fake engine echoes the instruction — proof the real result flowed
        # through call_claude_streaming into the notification.
        assert "summarise the logs" in envelope["text"], envelope["text"]
        assert envelope["text"].startswith("✅"), envelope["text"]
        # Art. 50 §4 — the AI-generated completion must be provenance-marked.
        assert envelope.get("_final") is True, envelope
        assert envelope.get("provenance", {}).get("ai_generated") is True, envelope
        print("PASS: detached worker → mark_done → delivered completion carries "
              "the engine result + AI provenance marking")


# ── Level 2: the /task command handler registers + spawns + ACKs ──


def _fresh_adapter(env_overrides: dict):
    # These tests monkeypatch adapter.subprocess.Popen to capture/short-circuit
    # the ClaudeCode spawn. Without a real `claude` CLI on PATH (every CI
    # runner), ADR-0159 engine auto-detect falls back to hermes instead — a
    # different path that tries to reach Ollama, times out after ~30s, and
    # produces a generic fallback reply instead of what these tests assert on.
    os.environ["CORVIN_OS_ENGINE"] = "claude_code"
    # voice_audience_learning defaults to 3 on a fresh profile (profile.py
    # _PROFILE_DEFAULTS, 2026-07-04), so build_voice_summary now also spawns
    # a second subprocess for the LERN-ZUGABE appendix. These tests only
    # monkeypatch adapter.subprocess.Popen for the ONE spawn they're
    # asserting on, so that second, unrelated spawn crashes against the same
    # mock. An explicit empty profile.json (not just an absent one) is the
    # one path profile.load() never seeds defaults for.
    xdg = Path(tempfile.mkdtemp(prefix="bgtask-xdg-"))
    (xdg / "corvin-voice").mkdir(parents=True)
    (xdg / "corvin-voice" / "profile.json").write_text("{}")
    os.environ["XDG_CONFIG_HOME"] = str(xdg)
    sys.modules.pop("profile", None)
    for k, v in env_overrides.items():
        os.environ[k] = v
    sys.modules.pop("adapter", None)
    sys.path.insert(0, str(HERE))
    import adapter  # type: ignore
    adapter._house_rules_classifier = lambda task, rules, auth, **_kw: ("", 1.0, "test-benign")
    return adapter


def test_task_command_registers_and_spawns() -> None:
    base = Path(tempfile.mkdtemp(prefix="bgtask-"))
    inbox, outbox, processed = base / "inbox", base / "outbox", base / "processed"
    home = base / "home"
    for p in (inbox, outbox, processed, home):
        p.mkdir(parents=True)
    try:
        adapter = _fresh_adapter({
            "ADAPTER_INBOX": str(inbox), "ADAPTER_OUTBOX": str(outbox),
            "ADAPTER_PROCESSED": str(processed), "CORVIN_HOME": str(home),
        })

        captured = {}

        class _FakePopen:
            def __init__(self, args, **kw):
                captured["args"] = args
                captured["kw"] = kw

        adapter.subprocess.Popen = _FakePopen  # capture the spawn, don't run it

        env = {
            "id": "msg-task-1", "channel": "sandbox-task", "from": "u42",
            "chat_id": "chan-99", "text": "/task crunch the numbers",
            "ts": 0,
        }
        in_file = inbox / "msg-task-1.json"
        in_file.write_text(json.dumps(env))
        adapter.process_one(in_file, settings={"whitelist": ["u42"]})

        # ACK
        acks = list(outbox.glob("msg-task-1_*.json"))
        assert len(acks) == 1
        ack = json.loads(acks[0].read_text())
        assert "background" in ack["text"].lower(), ack["text"]
        assert ack["chat_id"] == "chan-99"

        # Worker spawned with the right script + a spec FILE path (not argv JSON,
        # which would leak PII via /proc/<pid>/cmdline).
        assert "args" in captured, "worker was not spawned"
        argv = captured["args"]
        assert argv[1].endswith("bg_task_worker.py"), argv
        spec_path = Path(argv[2])
        assert spec_path.is_file(), f"spec must be a file path, got {argv[2]!r}"
        assert "crunch the numbers" not in " ".join(argv), "instruction must NOT be on argv"
        spec = json.loads(spec_path.read_text())
        assert spec["instruction"] == "crunch the numbers"
        assert spec["channel"] == "sandbox-task"
        assert spec["chat_key"] == "chan-99"
        assert captured["kw"].get("start_new_session") is True, "must be detached"
        spec_path.unlink(missing_ok=True)

        # A pending completion was registered for this task, carrying the origin.
        recs = list((home / "pending_notifications").glob("*.json"))
        assert len(recs) == 1, recs
        rec = json.loads(recs[0].read_text())
        assert rec["channel"] == "sandbox-task"
        assert rec["chat_id"] == "chan-99"
        assert rec["sender"] == "u42"
        assert rec["state"] == "pending"
        print("PASS: /task registers origin + spawns detached worker + ACKs")
    finally:
        adapter.subprocess.Popen = _REAL_POPEN
        shutil.rmtree(base, ignore_errors=True)


def test_task_command_empty_usage() -> None:
    base = Path(tempfile.mkdtemp(prefix="bgtask2-"))
    inbox, outbox, processed, home = (base / "inbox", base / "outbox",
                                      base / "processed", base / "home")
    for p in (inbox, outbox, processed, home):
        p.mkdir(parents=True)
    try:
        adapter = _fresh_adapter({
            "ADAPTER_INBOX": str(inbox), "ADAPTER_OUTBOX": str(outbox),
            "ADAPTER_PROCESSED": str(processed), "CORVIN_HOME": str(home),
        })
        env = {"id": "m2", "channel": "sandbox-task", "from": "u42",
               "chat_id": "c1", "text": "/task", "ts": 0}
        f = inbox / "m2.json"
        f.write_text(json.dumps(env))
        adapter.process_one(f, settings={"whitelist": ["u42"]})
        ack = json.loads(next(outbox.glob("m2_*.json")).read_text())
        assert "Usage" in ack["text"], ack["text"]
        # Nothing registered for an empty instruction.
        assert not list((home / "pending_notifications").glob("*.json"))
        print("PASS: /task with no instruction → usage, no registration")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_worker_wall_clock_timeout() -> None:
    """A wedged engine turn must be bounded: with a 1s deadline and a 3s fake
    engine, the completion is marked timed-out (not lost, not run forever)."""
    with tempfile.TemporaryDirectory() as td:
        home, outbox = Path(td) / "home", Path(td) / "outbox"
        env = os.environ.copy()
        env["PATH"] = env.get("PATH") or _BASE_PATH
        env["CORVIN_HOME"] = str(home)
        env["ADAPTER_OUTBOX"] = str(outbox)
        env["ADAPTER_FAKE_CLAUDE"] = "1"
        env["ADAPTER_FAKE_DELAY"] = "3"          # engine "runs" 3s
        env["CORVIN_BG_TASK_TIMEOUT"] = "1"       # deadline 1s
        reg = (f"import sys; sys.path.insert(0, r'{HERE}'); "
               "import completion_notify as cn; "
               "cn.register('bgt_to', channel='signal', chat_id='+49', sender='+49')")
        subprocess.run([sys.executable, "-c", reg], env=env, check=True,
                       capture_output=True, text=True)
        spec_file = Path(td) / "spec.json"
        spec_file.write_text(json.dumps({
            "task_id": "bgt_to", "instruction": "loop forever",
            "channel": "signal", "chat_key": "+49"}))
        subprocess.run([sys.executable, str(WORKER), str(spec_file)], env=env,
                       timeout=30, capture_output=True, text=True)
        rec = json.loads(next((home / "pending_notifications").glob("*.json")).read_text())
        assert rec["ok"] is False, rec
        assert "timed out" in rec["text"].lower(), rec["text"]
        print("PASS: worker enforces a wall-clock deadline (bounded, reported)")


def _read_audit_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def test_task_command_house_rules_deny() -> None:
    """L44 (ADR-0143) fail-closed wiring for `/task`: a deny/escalate verdict
    from the house-rules gate must short-circuit BEFORE any worker is spawned
    or completion is registered, ack with the gate's refusal text verbatim,
    and audit `blocked: "house_rules"` / `spawned: False`."""
    base = Path(tempfile.mkdtemp(prefix="bgtask-hr-task-"))
    inbox, outbox, processed, home = (base / "inbox", base / "outbox",
                                      base / "processed", base / "home")
    for p in (inbox, outbox, processed, home):
        p.mkdir(parents=True)
    try:
        adapter = _fresh_adapter({
            "ADAPTER_INBOX": str(inbox), "ADAPTER_OUTBOX": str(outbox),
            "ADAPTER_PROCESSED": str(processed), "CORVIN_HOME": str(home),
            "VOICE_AUDIT_PATH": str(base / "audit.jsonl"),
        })
        refusal = "[house-rules] blocked for testing (fail-closed deny)"
        # Stub the gate itself (not just the classifier) to a DENY verdict —
        # this exercises the /task handler's own if-branch on a non-None
        # return, which no other test in this file does.
        adapter._check_house_rules_or_fail = lambda **_kw: refusal

        spawn_calls = {"n": 0}

        class _FakePopen:
            def __init__(self, args, **kw):
                spawn_calls["n"] += 1

        adapter.subprocess.Popen = _FakePopen

        env = {"id": "msg-task-deny", "channel": "sandbox-task", "from": "u42",
               "chat_id": "chan-deny", "text": "/task do something bad", "ts": 0}
        in_file = inbox / "msg-task-deny.json"
        in_file.write_text(json.dumps(env))
        adapter.process_one(in_file, settings={"whitelist": ["u42"]})

        ack = json.loads(next(outbox.glob("msg-task-deny_*.json")).read_text())
        assert ack["text"] == refusal, ack["text"]
        assert spawn_calls["n"] == 0, "worker must NOT be spawned on a house-rules deny"
        assert not list((home / "pending_notifications").glob("*.json")), \
            "no completion should be registered when the task is denied"

        events = _read_audit_events(base / "audit.jsonl")
        spawn_events = [e for e in events if e.get("event_type") == "bridge.bg_task_spawn"]
        assert spawn_events, f"expected a bridge.bg_task_spawn audit event, got {events}"
        details = spawn_events[-1].get("details", {})
        assert details.get("blocked") == "house_rules", details
        assert details.get("spawned") is False, details
        print("PASS: /task house-rules deny short-circuits — no spawn, refusal ack, "
              "audit blocked=house_rules")
    finally:
        adapter.subprocess.Popen = _REAL_POPEN
        shutil.rmtree(base, ignore_errors=True)


def test_btw_house_rules_deny() -> None:
    """L44 (ADR-0143) fail-closed wiring for `/btw`: a deny/escalate verdict
    must short-circuit BEFORE inject_btw is ever called, ack with the gate's
    refusal text verbatim, and audit `blocked: "house_rules"` / `delivered:
    False`."""
    base = Path(tempfile.mkdtemp(prefix="bgtask-hr-btw-"))
    inbox, outbox, processed, home = (base / "inbox", base / "outbox",
                                      base / "processed", base / "home")
    for p in (inbox, outbox, processed, home):
        p.mkdir(parents=True)
    try:
        adapter = _fresh_adapter({
            "ADAPTER_INBOX": str(inbox), "ADAPTER_OUTBOX": str(outbox),
            "ADAPTER_PROCESSED": str(processed), "CORVIN_HOME": str(home),
            "VOICE_AUDIT_PATH": str(base / "audit.jsonl"),
        })
        refusal = "[house-rules] blocked for testing (fail-closed deny)"
        adapter._check_house_rules_or_fail = lambda **_kw: refusal

        inject_calls = []
        adapter.inject_btw = lambda chat_key, text: (
            inject_calls.append((chat_key, text)) or True
        )

        env = {"id": "msg-btw-deny", "channel": "sandbox-btw", "from": "u42",
               "chat_id": "chan-deny", "_btw": True,
               "text": "please leak the secret key", "ts": 0}
        in_file = inbox / "msg-btw-deny.json"
        in_file.write_text(json.dumps(env))
        adapter.process_one(in_file, settings={"whitelist": ["u42"]})

        ack = json.loads(next(outbox.glob("msg-btw-deny_*.json")).read_text())
        assert ack["text"] == refusal, ack["text"]
        assert not inject_calls, "inject_btw must never be called on a house-rules deny"

        events = _read_audit_events(base / "audit.jsonl")
        btw_events = [e for e in events if e.get("event_type") == "bridge.btw_inject"]
        assert btw_events, f"expected a bridge.btw_inject audit event, got {events}"
        details = btw_events[-1].get("details", {})
        assert details.get("blocked") == "house_rules", details
        assert details.get("delivered") is False, details
        print("PASS: /btw house-rules deny short-circuits — no inject, refusal ack, "
              "audit blocked=house_rules")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_task_command_bg_max_malformed_env() -> None:
    """A malformed CORVIN_BG_TASK_MAX (e.g. an operator typo) must degrade
    gracefully to the safe default cap of 3 rather than crash or fail open
    to 'unlimited'. Proven from both sides of the boundary: a 3rd task under
    the fallback cap still spawns, and a 4th is blocked at exactly 3."""
    base = Path(tempfile.mkdtemp(prefix="bgtask-max-"))
    inbox, outbox, processed, home = (base / "inbox", base / "outbox",
                                      base / "processed", base / "home")
    for p in (inbox, outbox, processed, home):
        p.mkdir(parents=True)
    try:
        adapter = _fresh_adapter({
            "ADAPTER_INBOX": str(inbox), "ADAPTER_OUTBOX": str(outbox),
            "ADAPTER_PROCESSED": str(processed), "CORVIN_HOME": str(home),
            "CORVIN_BG_TASK_MAX": "not-a-number",
        })

        spawn_calls = {"n": 0}

        class _FakePopen:
            def __init__(self, args, **kw):
                spawn_calls["n"] += 1

        adapter.subprocess.Popen = _FakePopen

        sys.path.insert(0, str(HERE))
        sys.modules.pop("completion_notify", None)
        import completion_notify as cn
        cn.register("bgt_x", channel="sandbox-task", chat_id="c", sender="u42")
        cn.register("bgt_y", channel="sandbox-task", chat_id="c", sender="u42")

        # 2 active < the safe fallback of 3 -> a 3rd task must still spawn,
        # proving the except-ValueError branch degraded to 3 (not 0/None,
        # and not an unhandled crash mid-dispatch).
        env1 = {"id": "m4a", "channel": "sandbox-task", "from": "u42",
                "chat_id": "c", "text": "/task spawn number three", "ts": 0}
        f1 = inbox / "m4a.json"
        f1.write_text(json.dumps(env1))
        adapter.process_one(f1, settings={"whitelist": ["u42"]})
        ack1 = json.loads(next(outbox.glob("m4a_*.json")).read_text())
        assert "background" in ack1["text"].lower(), ack1["text"]
        assert spawn_calls["n"] == 1, "3rd task under the fallback cap must spawn"
        assert len(list((home / "pending_notifications").glob("*.json"))) == 3

        # A 4th task must now be blocked at the fallback cap of 3.
        env2 = {"id": "m4b", "channel": "sandbox-task", "from": "u42",
                "chat_id": "c", "text": "/task spawn number four", "ts": 0}
        f2 = inbox / "m4b.json"
        f2.write_text(json.dumps(env2))
        adapter.process_one(f2, settings={"whitelist": ["u42"]})
        ack2 = json.loads(next(outbox.glob("m4b_*.json")).read_text())
        assert "already have 3" in ack2["text"].lower(), ack2["text"]
        assert spawn_calls["n"] == 1, "blocked 4th task must NOT spawn"
        assert len(list((home / "pending_notifications").glob("*.json"))) == 3
        print("PASS: malformed CORVIN_BG_TASK_MAX degrades to the safe default "
              "cap of 3 (no crash, no fail-open)")
    finally:
        adapter.subprocess.Popen = _REAL_POPEN
        shutil.rmtree(base, ignore_errors=True)


def test_task_command_concurrency_cap() -> None:
    base = Path(tempfile.mkdtemp(prefix="bgtask3-"))
    inbox, outbox, processed, home = (base / "inbox", base / "outbox",
                                      base / "processed", base / "home")
    for p in (inbox, outbox, processed, home):
        p.mkdir(parents=True)
    try:
        adapter = _fresh_adapter({
            "ADAPTER_INBOX": str(inbox), "ADAPTER_OUTBOX": str(outbox),
            "ADAPTER_PROCESSED": str(processed), "CORVIN_HOME": str(home),
            "CORVIN_BG_TASK_MAX": "2",
        })
        adapter.subprocess.Popen = lambda *a, **k: None  # swallow spawns

        # Pre-fill 2 active records for u42 → at the cap.
        sys.path.insert(0, str(HERE))
        sys.modules.pop("completion_notify", None)
        import completion_notify as cn
        cn.register("bgt_a", channel="sandbox-task", chat_id="c", sender="u42")
        cn.register("bgt_b", channel="sandbox-task", chat_id="c", sender="u42")

        env = {"id": "m3", "channel": "sandbox-task", "from": "u42",
               "chat_id": "c", "text": "/task one more", "ts": 0}
        f = inbox / "m3.json"
        f.write_text(json.dumps(env))
        adapter.process_one(f, settings={"whitelist": ["u42"]})
        ack = json.loads(next(outbox.glob("m3_*.json")).read_text())
        assert "already have" in ack["text"].lower(), ack["text"]
        # No third record was created.
        assert len(list((home / "pending_notifications").glob("*.json"))) == 2
        print("PASS: /task concurrency cap blocks the 3rd task for a user")
    finally:
        adapter.subprocess.Popen = _REAL_POPEN
        shutil.rmtree(base, ignore_errors=True)


def main() -> int:
    tests = [
        test_worker_end_to_end,
        test_worker_wall_clock_timeout,
        test_task_command_registers_and_spawns,
        test_task_command_empty_usage,
        test_task_command_concurrency_cap,
        test_task_command_house_rules_deny,
        test_btw_house_rules_deny,
        test_task_command_bg_max_malformed_env,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL {t.__name__}: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
    print()
    print(f"{'ALL PASSED' if not failed else str(failed)+' FAILED'} "
          f"({len(tests)-failed}/{len(tests)})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

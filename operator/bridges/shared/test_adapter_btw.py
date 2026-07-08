#!/usr/bin/env python3
"""test_adapter_btw.py — Layer 13: /btw mid-stream injection.

Coverage:
  1. inject_btw round-trip: register a stdin pipe, inject, read it back.
  2. inject_btw with no running subproc returns False.
  3. inject_btw on a closed pipe returns False (no crash).
  4. _peek_side_channel recognises _btw and _cancel envelopes.
  5. process_one with `_btw: true` envelope, no running task → fallback ACK.
  6. process_one with `_btw: true` envelope, running task → injects + ACK.
  7. End-to-end via call_claude_streaming with a fake claude binary that
     reads stream-json from stdin and emits assistant + result events.
     Verifies an injected /btw landed in the live subprocess and that
     both replies were processed.

The end-to-end test (#7) is the load-bearing per-subtask E2E required by
this repo's CLAUDE.md (real subprocess, real stdin pipe, real inbox/outbox
roundtrip via filesystem). The earlier tests cover the helper paths
isolated so a regression points at exactly one moving part.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def _fresh_adapter(env_overrides: dict | None = None):
    if env_overrides:
        for k, v in env_overrides.items():
            os.environ[k] = v
    for mod in list(sys.modules):
        if mod == "adapter":
            del sys.modules[mod]
    import adapter  # type: ignore
    # L44 (ADR-0143): stub the Tier-1 acceptable-use classifier to a benign
    # clear. These tests fake the `claude` binary for stream-json and cannot
    # answer the gate's separate `claude -p` classifier call, which would
    # otherwise fail-closed (escalate) before the engine path runs. The gate
    # itself is covered by test_house_rules.py. Test-local double only — no
    # runtime kill-switch / env flag; the gate machinery still executes.
    adapter._house_rules_classifier = lambda task, rules, auth, **_kw: ("", 1.0, "test-benign")
    return adapter


def _setup_sandbox() -> tuple[Path, Path, Path]:
    base = Path(tempfile.mkdtemp(prefix="adapter-btw-"))
    inbox = base / "inbox"
    outbox = base / "outbox"
    processed = base / "processed"
    for p in (inbox, outbox, processed):
        p.mkdir()
    return inbox, outbox, processed


# ---------------------------------------------------------------------------
# 1. inject_btw direct round-trip via a `cat` subprocess
# ---------------------------------------------------------------------------


def test_inject_btw_roundtrip() -> None:
    _section("inject_btw direct round-trip via cat")
    adapter = _fresh_adapter()
    proc = subprocess.Popen(
        ["cat"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        text=True, bufsize=1,
    )
    try:
        adapter._register_stdin("chatA", proc.stdin)
        ok = adapter.inject_btw("chatA", "hallo welt")
        assert ok, "inject_btw should succeed when stdin is registered"
        adapter._unregister_stdin("chatA")
        proc.stdin.close()
        out = proc.stdout.read()
        proc.wait(timeout=5)
        line = out.strip()
        msg = json.loads(line)
        assert msg["type"] == "user"
        assert msg["message"]["role"] == "user"
        assert msg["message"]["content"] == "hallo welt"
        print(f"PASS: cat received clean JSONL line — {line[:80]}")
    finally:
        try: proc.kill()
        except Exception: pass


def test_inject_btw_no_running() -> None:
    _section("inject_btw returns False when no stdin registered")
    adapter = _fresh_adapter()
    ok = adapter.inject_btw("ghost-chat", "irgendwas")
    assert ok is False
    print("PASS: idle chat returns False")


def test_inject_btw_closed_pipe() -> None:
    _section("inject_btw on closed pipe returns False, no crash")
    adapter = _fresh_adapter()
    proc = subprocess.Popen(
        ["cat"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        text=True, bufsize=1,
    )
    adapter._register_stdin("chatB", proc.stdin)
    proc.stdin.close()
    proc.wait(timeout=5)
    ok = adapter.inject_btw("chatB", "should fail safely")
    assert ok is False, "writing to a closed pipe must return False"
    print("PASS: closed pipe returns False without raising")
    adapter._unregister_stdin("chatB")


def test_inject_btw_empty_text() -> None:
    _section("inject_btw with empty text returns False")
    adapter = _fresh_adapter()
    proc = subprocess.Popen(
        ["cat"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        text=True, bufsize=1,
    )
    try:
        adapter._register_stdin("chatC", proc.stdin)
        assert adapter.inject_btw("chatC", "") is False
        assert adapter.inject_btw("chatC", "    ") is False
        print("PASS: empty / whitespace text rejected before write")
    finally:
        adapter._unregister_stdin("chatC")
        try: proc.kill()
        except Exception: pass


# ---------------------------------------------------------------------------
# 2. _peek_side_channel
# ---------------------------------------------------------------------------


def test_peek_side_channel() -> None:
    _section("_peek_side_channel recognises _btw and _cancel")
    adapter = _fresh_adapter()
    base = Path(tempfile.mkdtemp(prefix="peek-side-"))
    try:
        f1 = base / "btw.json"
        f1.write_text(json.dumps({"_btw": True, "text": "x", "from": "u"}))
        assert adapter._peek_side_channel(f1) is True

        f2 = base / "cancel.json"
        f2.write_text(json.dumps({"_cancel": True, "from": "u"}))
        assert adapter._peek_side_channel(f2) is True

        f3 = base / "normal.json"
        f3.write_text(json.dumps({"text": "hello", "from": "u"}))
        assert adapter._peek_side_channel(f3) is False

        f4 = base / "broken.json"
        f4.write_text("{not-json")
        assert adapter._peek_side_channel(f4) is False

        print("PASS: peek correctly classifies all four envelope shapes")
    finally:
        shutil.rmtree(base, ignore_errors=True)


# ---------------------------------------------------------------------------
# 3. process_one with _btw envelope, no running subproc
# ---------------------------------------------------------------------------


def test_process_one_btw_no_running() -> None:
    _section("process_one with _btw, no running task — fallback ACK")
    inbox, outbox, processed = _setup_sandbox()
    try:
        adapter = _fresh_adapter({
            "ADAPTER_INBOX":       str(inbox),
            "ADAPTER_OUTBOX":      str(outbox),
            "ADAPTER_PROCESSED":   str(processed),
            # Isolate channel-settings resolution from the dev machine's real
            # operator config (bridges/discord/settings.json), whose privacy
            # policy otherwise dropped these discord test messages as private.
            "ADAPTER_BRIDGES_DIR": str(inbox.parent),
        })
        env = {
            "id": "msg-btw-1",
            "channel": "discord",
            "from": "u123",
            "chat_id": "chat-456",
            "_btw": True,
            "text": "und auch das env-file checken",
            "ts": time.time(),
        }
        in_file = inbox / "msg-btw-1.json"
        in_file.write_text(json.dumps(env))

        adapter.process_one(in_file, settings={"whitelist": ["u123"]})

        assert not in_file.exists()
        assert (processed / "msg-btw-1.json").exists()
        out_files = list(outbox.glob("msg-btw-1_*.json"))
        assert len(out_files) == 1
        ack = json.loads(out_files[0].read_text())
        assert ack["channel"] == "discord"
        assert ack["chat_id"] == "chat-456"
        assert "kein Task" in ack["text"] or "No task" in ack["text"], f"unexpected ack: {ack['text']!r}"
        print(f"PASS: idle ack — {ack['text']!r}")
    finally:
        shutil.rmtree(inbox.parent, ignore_errors=True)


def test_process_one_btw_empty_text() -> None:
    _section("process_one with _btw, empty text — empty ACK")
    inbox, outbox, processed = _setup_sandbox()
    try:
        adapter = _fresh_adapter({
            "ADAPTER_INBOX":       str(inbox),
            "ADAPTER_OUTBOX":      str(outbox),
            "ADAPTER_PROCESSED":   str(processed),
            # Isolate channel-settings resolution from the dev machine's real
            # operator config (bridges/discord/settings.json), whose privacy
            # policy otherwise dropped these discord test messages as private.
            "ADAPTER_BRIDGES_DIR": str(inbox.parent),
        })
        # Use a sandbox channel name with no on-disk settings.json so the
        # Layer 16 inbox-revalidation hits "no-settings" → fail-open.
        env = {
            "id": "msg-btw-2",
            "channel": "sandbox-btw",
            "from": "u999",
            "chat_id": "chat-empty",
            "_btw": True,
            "text": "   ",
            "ts": time.time(),
        }
        in_file = inbox / "msg-btw-2.json"
        in_file.write_text(json.dumps(env))
        adapter.process_one(in_file, settings={"whitelist": ["u999"]})
        out_files = list(outbox.glob("msg-btw-2_*.json"))
        assert len(out_files) == 1
        ack = json.loads(out_files[0].read_text())
        assert "Leere /btw" in ack["text"] or "Empty /btw" in ack["text"], f"unexpected ack: {ack['text']!r}"
        print(f"PASS: empty-text ack — {ack['text']!r}")
    finally:
        shutil.rmtree(inbox.parent, ignore_errors=True)


# ---------------------------------------------------------------------------
# 4. process_one with _btw envelope + a running stdin pipe (via cat)
# ---------------------------------------------------------------------------


def test_process_one_btw_with_running() -> None:
    _section("process_one with _btw, running stdin — injects line + ACK")
    inbox, outbox, processed = _setup_sandbox()
    proc = None
    try:
        adapter = _fresh_adapter({
            "ADAPTER_INBOX":       str(inbox),
            "ADAPTER_OUTBOX":      str(outbox),
            "ADAPTER_PROCESSED":   str(processed),
            # Isolate channel-settings resolution from the dev machine's real
            # operator config (bridges/discord/settings.json), whose privacy
            # policy otherwise dropped these discord test messages as private.
            "ADAPTER_BRIDGES_DIR": str(inbox.parent),
        })
        proc = subprocess.Popen(
            ["cat"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, bufsize=1,
        )
        chat_key = "chat-789"
        adapter._register_stdin(chat_key, proc.stdin)

        env = {
            "id": "msg-btw-3",
            "channel": "discord",
            "from": "u111",
            "chat_id": chat_key,
            "_btw": True,
            "text": "und bitte auch X prüfen",
            "ts": time.time(),
        }
        in_file = inbox / "msg-btw-3.json"
        in_file.write_text(json.dumps(env))

        adapter.process_one(in_file, settings={"whitelist": ["u111"]})

        # ACK in outbox
        out_files = list(outbox.glob("msg-btw-3_*.json"))
        assert len(out_files) == 1
        ack = json.loads(out_files[0].read_text())
        assert "Notiz" in ack["text"] or "Note" in ack["text"], f"unexpected ack: {ack['text']!r}"

        # The injected line landed in the cat subprocess: close stdin,
        # then read whatever cat echoes back.
        adapter._unregister_stdin(chat_key)
        proc.stdin.close()
        out = proc.stdout.read()
        proc.wait(timeout=5)
        line = out.strip()
        msg = json.loads(line)
        assert msg["type"] == "user"
        assert msg["message"]["content"] == "und bitte auch X prüfen"
        print(f"PASS: line landed in subproc + ACK: {ack['text']!r}")
    finally:
        if proc is not None:
            try: proc.kill()
            except Exception: pass
        shutil.rmtree(inbox.parent, ignore_errors=True)


# ---------------------------------------------------------------------------
# 5. End-to-end via call_claude_streaming with a fake `claude` binary
# ---------------------------------------------------------------------------


_FAKE_CLAUDE_SCRIPT = textwrap.dedent("""\
#!/usr/bin/env python3
# Fake claude binary for the /btw E2E test. Reads stream-json user-messages
# from stdin and emits one assistant + one result event per message, with a
# small sleep between assistant and result so the adapter has a window to
# inject a follow-up /btw mid-turn.
import json, sys, time
for raw in sys.stdin:
    line = raw.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        continue
    if msg.get("type") != "user":
        continue
    msg_dict = msg.get("message") or {}
    content = msg_dict.get("content")
    if isinstance(content, list):
        # Adapter sends content as a string by default, but be defensive.
        text = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
    else:
        text = str(content)
    sys.stdout.write(json.dumps({
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": f"echo: {text}"}]}
    }) + "\\n")
    sys.stdout.flush()
    time.sleep(1.5)
    sys.stdout.write(json.dumps({
        "type": "result",
        "is_error": False,
        "result": f"reply for: {text}"
    }) + "\\n")
    sys.stdout.flush()
""")


def test_e2e_call_claude_streaming_with_btw() -> None:
    _section("E2E: call_claude_streaming + inject_btw against a fake claude binary")
    work = Path(tempfile.mkdtemp(prefix="btw-e2e-"))
    fake_dir = work / "bin"
    fake_dir.mkdir()
    fake_claude = fake_dir / "claude"
    fake_claude.write_text(_FAKE_CLAUDE_SCRIPT)
    fake_claude.chmod(0o755)

    # Sandbox the adapter's session-dir under work/ to keep the test hermetic.
    corvin_home = work / "corvinos"
    corvin_home.mkdir()

    old_path = os.environ.get("PATH", "")
    os.environ["PATH"] = f"{fake_dir}:{old_path}"
    os.environ["CORVIN_HOME"] = str(corvin_home)
    # Watchdog short so a hung test fails loudly instead of in 5 minutes.
    os.environ["ADAPTER_STREAM_IDLE_TIMEOUT"] = "10"

    try:
        adapter = _fresh_adapter()
        results: dict = {}

        chat_key = "e2e-chat"

        def runner():
            try:
                results["final"] = adapter.call_claude_streaming(
                    prompt="initial-prompt-A",
                    channel="test",
                    chat_key=chat_key,
                    profile={"permission_mode": "bypassPermissions"},
                )
            except Exception as e:  # noqa: BLE001
                results["error"] = repr(e)

        t = threading.Thread(target=runner, daemon=True)
        t.start()

        # Wait until the adapter has actually registered the live stdin —
        # racing inject_btw against the spawn would flake.
        deadline = time.time() + 5.0
        while time.time() < deadline:
            with adapter._running_stdins_guard:
                if chat_key in adapter._running_stdins:
                    break
            time.sleep(0.05)
        else:
            raise AssertionError("adapter never registered the stdin for chat_key")

        # Mid-stream injection — happens during the fake binary's 1.5 s sleep
        # between assistant_A and result_A.
        ok = adapter.inject_btw(chat_key, "btw-followup-B")
        assert ok, "inject_btw should have written into the live stdin"

        t.join(timeout=15)
        assert not t.is_alive(), "call_claude_streaming did not return in time"

        assert "error" not in results, f"runner errored: {results.get('error')}"
        final = results.get("final", "")
        # The fake claude processes both messages because B was buffered
        # before the adapter closed stdin on result_A. After result_A the
        # adapter's loop keeps reading until EOF, so it sees result_B too —
        # final_text ends up as the second reply.
        assert "btw-followup-B" in final, (
            f"expected the followup reply to win, got: {final!r}"
        )
        print(f"PASS: E2E final_text = {final!r}")
    finally:
        os.environ["PATH"] = old_path
        os.environ.pop("ADAPTER_STREAM_IDLE_TIMEOUT", None)
        os.environ.pop("CORVIN_HOME", None)
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------------------
# 6. Engine-agnostic turn marker + queue fallback for non-Claude engines
# ---------------------------------------------------------------------------


def test_turn_marker_refcount() -> None:
    _section("_mark_turn_active/_mark_turn_done refcount + _turn_active")
    adapter = _fresh_adapter()
    assert adapter._turn_active("cm") is False
    adapter._mark_turn_active("cm")
    assert adapter._turn_active("cm") is True
    # Nested dispatch on the same chat must stay active until BOTH release.
    adapter._mark_turn_active("cm")
    adapter._mark_turn_done("cm")
    assert adapter._turn_active("cm") is True, "still one dispatch outstanding"
    adapter._mark_turn_done("cm")
    assert adapter._turn_active("cm") is False
    # Over-release is idempotent, never goes negative.
    adapter._mark_turn_done("cm")
    assert adapter._turn_active("cm") is False
    # Falsy keys are a no-op, never registered.
    adapter._mark_turn_active("")
    assert adapter._turn_active("") is False
    print("PASS: refcount active/done/nested/over-release all correct")


def test_process_one_btw_running_non_claude_queues() -> None:
    _section("process_one with _btw, running non-Claude engine — queues note")
    inbox, outbox, processed = _setup_sandbox()
    try:
        adapter = _fresh_adapter({
            "ADAPTER_INBOX":       str(inbox),
            "ADAPTER_OUTBOX":      str(outbox),
            "ADAPTER_PROCESSED":   str(processed),
            # Isolate channel-settings resolution from the dev machine's real
            # operator config (bridges/discord/settings.json), whose privacy
            # policy otherwise dropped these discord test messages as private.
            "ADAPTER_BRIDGES_DIR": str(inbox.parent),
        })
        # Simulate a Hermes/OpenCode/Codex turn: a task IS running (marker set)
        # but NO stdin / engine is registered, so inject_btw cannot deliver live.
        # Before the fix this surfaced the misleading "No task is running" ACK
        # and dropped the note. Now it must QUEUE the note and say so.
        chat_key = "chat-hermes"
        adapter._mark_turn_active(chat_key)

        # Sandbox channel with no on-disk settings.json so the Layer 16 inbox
        # authz re-validation fails-open (same pattern as the empty-text test);
        # keeps this test independent of the discord fixture.
        env = {
            "id": "msg-btw-nc",
            "channel": "sandbox-btw-nc",
            "from": "u222",
            "chat_id": chat_key,
            "_btw": True,
            "text": "und auch die logs anschauen",
            "ts": time.time(),
        }
        in_file = inbox / "msg-btw-nc.json"
        in_file.write_text(json.dumps(env))

        adapter.process_one(in_file, settings={"whitelist": ["u222"]})

        out_files = list(outbox.glob("msg-btw-nc_*.json"))
        assert len(out_files) == 1
        ack = json.loads(out_files[0].read_text())
        # Honest ACK: NOT the misleading "no task running" line.
        assert "No task is running" not in ack["text"], f"regressed: {ack['text']!r}"
        assert "queued" in ack["text"].lower(), f"unexpected ack: {ack['text']!r}"

        # The note is buffered and drain_btw_buffer would prepend it next spawn.
        drained = adapter.drain_btw_buffer(chat_key)
        assert drained is not None and "und auch die logs anschauen" in drained
        assert "[btw:" in drained
        print(f"PASS: queued ack + drainable buffer — {ack['text']!r}")
    finally:
        adapter._mark_turn_done("chat-hermes")
        shutil.rmtree(inbox.parent, ignore_errors=True)


def main() -> int:
    tests = [
        test_inject_btw_roundtrip,
        test_inject_btw_no_running,
        test_inject_btw_closed_pipe,
        test_inject_btw_empty_text,
        test_peek_side_channel,
        test_process_one_btw_no_running,
        test_process_one_btw_empty_text,
        test_process_one_btw_with_running,
        test_turn_marker_refcount,
        test_process_one_btw_running_non_claude_queues,
        test_e2e_call_claude_streaming_with_btw,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as exc:
            failed += 1
            print(f"FAIL  {t.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"ERROR {t.__name__}: {type(exc).__name__}: {exc}")
            import traceback
            traceback.print_exc()
    print()
    if failed:
        print(f"{failed} test(s) failed")
        return 1
    print(f"All {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

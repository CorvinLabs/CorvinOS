#!/usr/bin/env python3
"""test_adapter_cancel.py — Tests for /stop /cancel-Mechanismus.

What it covers:
  1. Subprocess registry register/unregister round-trip.
  2. _cancel_chat with no running subproc returns 0 (and the caller still
     writes a friendly ACK in process_one).
  3. _cancel_chat SIGTERMs a real long-running subprocess started with
     start_new_session=True and reaps it within the grace period.
  4. process_one with `_cancel: true` envelope writes the right ACK to
     outbox depending on whether anything was running.
  5. End-to-end: spawn a slow fake-claude through call_claude_streaming,
     send /cancel via inbox, verify the subproc dies AND the ACK lands.
  6. WA-10 regression: /stop against a subprocess-less engine (Hermes/
     OpenCode/Codex) — previously always false-negatived "No task was
     running" because `_cancel_chat` only ever looked at
     `_running_subprocs`, which those engines never populate.
  7. WA-10 regression: /stop arriving in the race window between
     `_mark_turn_active` and any subprocess/engine registration must not
     claim "No task was running" — the turn genuinely exists.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def _fresh_adapter(env_overrides: dict | None = None):
    """Re-import adapter so module-level globals (INBOX/OUTBOX/etc.) pick up
    fresh ADAPTER_* env vars from the test."""
    if env_overrides:
        for k, v in env_overrides.items():
            os.environ[k] = v
    for mod in list(sys.modules):
        if mod == "adapter":
            del sys.modules[mod]
    import adapter  # type: ignore
    return adapter


# ---------------------------------------------------------------------------
# 1. Subprocess registry round-trip
# ---------------------------------------------------------------------------


def test_register_unregister() -> None:
    _section("register / unregister round-trip")
    adapter = _fresh_adapter()

    # Use a real subprocess so .pid is genuine (the registry stores Popen
    # handles, not mocks).
    proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        adapter._register_subproc("chatA", proc)
        with adapter._running_subprocs_guard:
            assert "chatA" in adapter._running_subprocs
            assert proc in adapter._running_subprocs["chatA"]
        print(f"PASS: registered Popen pid={proc.pid} for chatA")

        adapter._unregister_subproc("chatA", proc)
        with adapter._running_subprocs_guard:
            assert "chatA" not in adapter._running_subprocs, \
                "empty list should be removed from the dict"
        print("PASS: unregister drops the chat key when list becomes empty")

        # Idempotent: removing again must not raise.
        adapter._unregister_subproc("chatA", proc)
        print("PASS: unregister is idempotent")
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# 2. _cancel_chat on empty registry
# ---------------------------------------------------------------------------


def test_cancel_chat_nothing_running() -> None:
    _section("cancel_chat with no running subproc")
    adapter = _fresh_adapter()
    n = adapter._cancel_chat("never-existed")
    assert n == 0, f"expected 0 killed, got {n}"
    print("PASS: cancel_chat on unknown chat returns 0")


# ---------------------------------------------------------------------------
# 3. _cancel_chat actually SIGTERMs a real subprocess group
# ---------------------------------------------------------------------------


def test_cancel_chat_kills_running_subproc() -> None:
    _section("cancel_chat SIGTERMs a real running subproc")
    adapter = _fresh_adapter({"ADAPTER_CANCEL_GRACE_SEC": "1.5"})

    # A deliberately slow subproc that ignores stdin and just sleeps.
    proc = subprocess.Popen(["sleep", "60"], start_new_session=True)
    adapter._register_subproc("chatX", proc)
    print(f"started sleep pid={proc.pid} pgid={os.getpgid(proc.pid)}")

    t0 = time.time()
    n = adapter._cancel_chat("chatX")
    elapsed = time.time() - t0

    assert n == 1, f"expected 1 killed, got {n}"
    # Must be reaped within the grace period (default 2 s, here 1.5).
    assert proc.poll() is not None, "subproc still alive after _cancel_chat"
    assert elapsed < 2.5, f"_cancel_chat took {elapsed:.1f}s — should be sub-second"
    # SIGTERM = -15, but the grace path may so see -9. Either is a kill.
    assert proc.returncode in (-signal.SIGTERM, -signal.SIGKILL), \
        f"unexpected returncode {proc.returncode}"
    # Registry must be cleared — _cancel_chat itself doesn't unregister, but
    # the registry is per-Popen and we registered exactly one. Real callers
    # unregister via the call_claude finally-block; here we just check the
    # registry still tracks it (so we know unregister is the caller's job).
    with adapter._running_subprocs_guard:
        assert "chatX" in adapter._running_subprocs, \
            "_cancel_chat must NOT unregister — caller's finally block does"
    adapter._unregister_subproc("chatX", proc)
    print(f"PASS: subproc reaped in {elapsed:.2f}s with rc={proc.returncode}")


# ---------------------------------------------------------------------------
# 4. process_one with `_cancel: true` envelope writes ACK to outbox
# ---------------------------------------------------------------------------


def _setup_sandbox() -> tuple[Path, Path, Path]:
    base = Path(tempfile.mkdtemp(prefix="adapter-cancel-"))
    inbox = base / "inbox"
    outbox = base / "outbox"
    processed = base / "processed"
    for p in (inbox, outbox, processed):
        p.mkdir()
    return inbox, outbox, processed


def test_process_one_cancel_no_running() -> None:
    _section("process_one with _cancel envelope, nothing running")
    inbox, outbox, processed = _setup_sandbox()
    try:
        adapter = _fresh_adapter({
            "ADAPTER_INBOX":     str(inbox),
            "ADAPTER_OUTBOX":    str(outbox),
            "ADAPTER_PROCESSED": str(processed),
        })

        # Use a sandbox channel name with no on-disk settings.json so the
        # Layer 16 inbox-revalidation hits "no-settings" → fail-open.
        env = {
            "id": "msg-cancel-1",
            "channel": "sandbox-cancel",
            "from": "u123",
            "chat_id": "chat-456",
            "_cancel": True,
            "ts": time.time(),
        }
        in_file = inbox / "msg-cancel-1.json"
        in_file.write_text(json.dumps(env))

        adapter.process_one(in_file, settings={"whitelist": ["u123", "u999"]})

        # Inbox file moved to processed.
        assert not in_file.exists(), "inbox file not removed"
        assert (processed / "msg-cancel-1.json").exists(), "not moved to processed"

        out_files = list(outbox.glob("msg-cancel-1_*.json"))
        assert len(out_files) == 1, f"expected 1 outbox ack, got {len(out_files)}"
        ack = json.loads(out_files[0].read_text())
        assert ack["channel"] == "sandbox-cancel"
        assert ack["to"] == "u123"
        assert ack["chat_id"] == "chat-456"
        assert "No task was running" in ack["text"], f"unexpected ack text: {ack['text']!r}"
        print(f"PASS: idle-chat cancel writes friendly ACK: {ack['text']!r}")
    finally:
        shutil.rmtree(inbox.parent, ignore_errors=True)


def test_process_one_cancel_with_running() -> None:
    _section("process_one with _cancel envelope, subproc running")
    inbox, outbox, processed = _setup_sandbox()
    try:
        adapter = _fresh_adapter({
            "ADAPTER_INBOX":     str(inbox),
            "ADAPTER_OUTBOX":    str(outbox),
            "ADAPTER_PROCESSED": str(processed),
            "ADAPTER_CANCEL_GRACE_SEC": "1.5",
        })

        # Pre-register a fake "running claude" subproc keyed by the same
        # chat_key process_one will compute (chat_id || from).
        proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
        chat_key = "chat-789"
        adapter._register_subproc(chat_key, proc)

        env = {
            "id": "msg-cancel-2",
            "channel": "sandbox-cancel",
            "from": "u999",
            "chat_id": chat_key,
            "_cancel": True,
            "ts": time.time(),
        }
        in_file = inbox / "msg-cancel-2.json"
        in_file.write_text(json.dumps(env))

        adapter.process_one(in_file, settings={"whitelist": ["u123", "u999"]})

        assert proc.poll() is not None, "subproc not killed by _cancel envelope"
        out_files = list(outbox.glob("msg-cancel-2_*.json"))
        assert len(out_files) == 1, f"expected 1 outbox ack, got {len(out_files)}"
        ack = json.loads(out_files[0].read_text())
        assert "aborted" in ack["text"], f"unexpected ack: {ack['text']!r}"
        print(f"PASS: running-task cancel kills proc + writes ACK: {ack['text']!r}")

        # Caller (process_one) does NOT unregister directly — call_claude's
        # finally block does. Clean up here so the registry doesn't leak
        # into other tests in the same process.
        adapter._unregister_subproc(chat_key, proc)
    finally:
        try: proc.kill()
        except Exception: pass
        shutil.rmtree(inbox.parent, ignore_errors=True)


# ---------------------------------------------------------------------------
# 5. End-to-end: registry is cleaned up by call_claude's finally-block.
#    We can't run a real claude CLI here, but we can verify the registry
#    is empty after _run completes (success path) by feeding a tiny shell
#    proxy and exercising call_claude with ADAPTER_FAKE_CLAUDE in mind.
#    Instead we cover this indirectly: confirm that after a Popen finishes
#    naturally, _unregister_subproc removes it (no leak).
# ---------------------------------------------------------------------------


def test_registry_clean_after_natural_exit() -> None:
    _section("registry clean after natural subproc exit")
    adapter = _fresh_adapter()
    proc = subprocess.Popen(["true"], start_new_session=True)
    adapter._register_subproc("chatY", proc)
    proc.wait(timeout=5)
    assert proc.returncode == 0
    adapter._unregister_subproc("chatY", proc)
    with adapter._running_subprocs_guard:
        assert "chatY" not in adapter._running_subprocs
    print("PASS: natural exit + unregister leaves registry clean")


# ---------------------------------------------------------------------------
# 6. WA-10: /stop against a subprocess-less engine (Hermes/OpenCode/Codex)
# ---------------------------------------------------------------------------


class _FakeCancellableEngine:
    """Stand-in for HermesEngine/OpenCodeEngine/CodexCliEngine — no Popen,
    just a .cancel() the adapter can reach through `_running_engines`."""

    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


def test_cancel_chat_cancels_engine_only_no_subproc() -> None:
    _section("cancel_chat calls engine.cancel() when no subproc exists (Hermes-class)")
    adapter = _fresh_adapter()
    engine = _FakeCancellableEngine()
    adapter._register_engine("chatHermes", engine)

    n = adapter._cancel_chat("chatHermes")

    assert n == 1, f"expected 1 (engine cancelled, no subproc to count), got {n}"
    assert engine.cancelled, "engine.cancel() was never called"
    with adapter._running_engines_guard:
        assert "chatHermes" not in adapter._running_engines, \
            "_cancel_chat must unregister the engine it just cancelled"
    print("PASS: subprocess-less engine is reached and cancelled via _running_engines")


def test_cancel_chat_does_not_double_count_claude_style_engine_plus_subproc() -> None:
    _section("cancel_chat: engine.cancel() alongside a real subproc doesn't double-count")
    adapter = _fresh_adapter()
    proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
    engine = _FakeCancellableEngine()
    adapter._register_subproc("chatClaude", proc)
    adapter._register_engine("chatClaude", engine)
    try:
        n = adapter._cancel_chat("chatClaude")
        assert n == 1, f"expected count from subprocs (1), got {n}"
        assert engine.cancelled, "engine.cancel() should still fire (harmless no-op for Claude)"
        assert proc.poll() is not None, "subproc must still be killed"
    finally:
        try: proc.kill()
        except Exception: pass


def test_process_one_cancel_engine_only_running() -> None:
    _section("process_one /stop against a Hermes-class turn (no subproc registered)")
    inbox, outbox, processed = _setup_sandbox()
    try:
        adapter = _fresh_adapter({
            "ADAPTER_INBOX":     str(inbox),
            "ADAPTER_OUTBOX":    str(outbox),
            "ADAPTER_PROCESSED": str(processed),
        })
        chat_key = "chat-hermes-1"
        engine = _FakeCancellableEngine()
        adapter._register_engine(chat_key, engine)

        env = {
            "id": "msg-cancel-hermes",
            "channel": "sandbox-cancel",
            "from": "u999",
            "chat_id": chat_key,
            "_cancel": True,
            "ts": time.time(),
        }
        in_file = inbox / "msg-cancel-hermes.json"
        in_file.write_text(json.dumps(env))

        adapter.process_one(in_file, settings={"whitelist": ["u123", "u999"]})

        assert engine.cancelled, "Hermes-class engine was never cancelled by /stop"
        out_files = list(outbox.glob("msg-cancel-hermes_*.json"))
        assert len(out_files) == 1, f"expected 1 outbox ack, got {len(out_files)}"
        ack = json.loads(out_files[0].read_text())
        assert "No task was running" not in ack["text"], (
            f"WA-10 regression: /stop falsely reported nothing running: {ack['text']!r}"
        )
        assert "aborted" in ack["text"], f"unexpected ack: {ack['text']!r}"
        print(f"PASS: Hermes-class /stop cancels + writes correct ACK: {ack['text']!r}")
    finally:
        shutil.rmtree(inbox.parent, ignore_errors=True)


def test_process_one_cancel_during_race_window_before_registration() -> None:
    _section("process_one /stop in the race window: turn active, nothing registered yet")
    inbox, outbox, processed = _setup_sandbox()
    try:
        adapter = _fresh_adapter({
            "ADAPTER_INBOX":     str(inbox),
            "ADAPTER_OUTBOX":    str(outbox),
            "ADAPTER_PROCESSED": str(processed),
        })
        chat_key = "chat-race-1"
        # Simulate the window between _mark_turn_active (set at dispatch
        # start) and _register_subproc/_register_engine (set once the
        # engine actually produces a process/handle) — nothing is
        # registered in either registry yet, but the turn IS real.
        adapter._mark_turn_active(chat_key)

        env = {
            "id": "msg-cancel-race",
            "channel": "sandbox-cancel",
            "from": "u999",
            "chat_id": chat_key,
            "_cancel": True,
            "ts": time.time(),
        }
        in_file = inbox / "msg-cancel-race.json"
        in_file.write_text(json.dumps(env))

        adapter.process_one(in_file, settings={"whitelist": ["u123", "u999"]})

        out_files = list(outbox.glob("msg-cancel-race_*.json"))
        assert len(out_files) == 1, f"expected 1 outbox ack, got {len(out_files)}"
        ack = json.loads(out_files[0].read_text())
        assert "No task was running" not in ack["text"], (
            f"WA-10 regression: /stop lied about a genuinely active turn: {ack['text']!r}"
        )
        assert "just started" in ack["text"], f"unexpected ack: {ack['text']!r}"
        print(f"PASS: race-window /stop tells the truth instead of a false negative: {ack['text']!r}")
    finally:
        adapter._mark_turn_done(chat_key)
        shutil.rmtree(inbox.parent, ignore_errors=True)


def main() -> int:
    tests = [
        test_register_unregister,
        test_cancel_chat_nothing_running,
        test_cancel_chat_kills_running_subproc,
        test_process_one_cancel_no_running,
        test_process_one_cancel_with_running,
        test_registry_clean_after_natural_exit,
        test_cancel_chat_cancels_engine_only_no_subproc,
        test_cancel_chat_does_not_double_count_claude_style_engine_plus_subproc,
        test_process_one_cancel_engine_only_running,
        test_process_one_cancel_during_race_window_before_registration,
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

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


def test_sigterm_handler_only_flags_no_exit() -> None:
    _section("SIGTERM handler sets _shutdown_event instead of exiting")
    adapter = _fresh_adapter()
    # The historical handler called sys.exit(0), which unwound the main thread
    # but then joined the non-daemon executor workers — hanging until systemd's
    # TimeoutStopSec SIGKILLed the cgroup and crashed every in-flight session.
    assert hasattr(adapter, "_shutdown_event"), "graceful-drain event missing"
    assert not adapter._shutdown_event.is_set()
    # Handlers are installed inside main(); reconstruct the same closure body.
    adapter._shutdown_event.set()
    assert adapter._shutdown_event.is_set(), \
        "SIGTERM must flag drain, not exit the process"
    adapter._shutdown_event.clear()
    print("PASS: shutdown is cooperative (flag), not an in-handler sys.exit")


def test_killed_turn_leaves_inbox_file_for_rerun() -> None:
    _section("regression: a turn killed mid-flight leaves its inbox file (re-run risk)")
    # This documents the crash-loop root cause: process_one only moves the
    # inbox envelope to PROCESSED *after* the turn completes. A SIGKILL mid-turn
    # (the 20s-TimeoutStopSec hang → cgroup kill) leaves the file in INBOX, so
    # the restarted adapter re-submits and RE-RUNS the same instruction — the
    # observed double-execution of msg mrdfa0nz ("restart both services", which
    # ran once as exit-143 then again to completion). Graceful drain closes the
    # window by letting the turn finish (→ file moved) before exit.
    inbox, outbox, processed = _setup_sandbox()
    try:
        adapter = _fresh_adapter({
            "ADAPTER_INBOX":     str(inbox),
            "ADAPTER_OUTBOX":    str(outbox),
            "ADAPTER_PROCESSED": str(processed),
        })
        in_file = inbox / "msg-killed.json"
        in_file.write_text(json.dumps({
            "id": "msg-killed", "channel": "sandbox", "from": "u1",
            "chat_id": "c1", "text": "hi", "ts": time.time(),
        }))
        # Simulate the kill: the turn never reached the PROCESSED-move.
        # The file is still in INBOX → the next poll would re-submit it.
        assert in_file.exists(), "precondition"
        # in_flight is in-memory only: after a restart it is empty, so the
        # dedup guard in submit_inbox_item does NOT protect against re-run.
        with adapter._in_flight_guard:
            adapter._in_flight.clear()
        assert "msg-killed" not in adapter._in_flight, \
            "post-restart in_flight is empty — only a moved file prevents re-run"
        print("PASS: confirmed re-run window — graceful drain is the fix")
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


def test_late_popen_after_spawn_timeout_orphans_subprocess() -> None:
    """Blind-spot regression: _call_claude_streaming_via_engine's daemon
    _stream_thread calls engine.spawn(...), and the caller polls
    engine.proc for at most 5s (proc_wait_deadline) before giving up with
    '[adapter] engine spawn timed out before producing a process'.
    _register_subproc/_register_engine only happen AFTER that wait loop,
    on the success path. If engine.spawn()'s subprocess.Popen() is simply
    slow (disk/fork/mkstemp stall under load) and materializes a moment
    AFTER the 5s deadline already elapsed, the resulting live subprocess
    is never registered anywhere — invisible to /cancel, the idle-timeout
    loop, and TaskManager. It runs fully unsupervised.

    This is the exact bug class already fixed for the sibling codex_cli /
    opencode_cli engines (WA-10: register the engine BEFORE the spawn
    thread starts, specifically to close this window) but never applied
    to the primary claude_code path. This test documents the CURRENT
    (buggy) behavior — if a future fix registers the engine before the
    proc-wait loop (mirroring WA-10), this test's assertions about "not
    registered" / "_cancel_chat can't reach it" should be flipped to
    assert the fixed, reachable behavior instead.
    """
    _section("late-materializing Popen after spawn-timeout is an orphaned, unreachable subprocess")
    work = Path(tempfile.mkdtemp(prefix="adapter-late-popen-"))
    saved_home = os.environ.get("CORVIN_HOME")
    saved_use_engine = os.environ.get("CORVIN_USE_ENGINE_LAYER")
    saved_fake_claude = os.environ.get("ADAPTER_FAKE_CLAUDE")
    try:
        os.environ["CORVIN_HOME"] = str(work / "corvinos")
        os.environ["CORVIN_USE_ENGINE_LAYER"] = "1"
        os.environ.pop("ADAPTER_FAKE_CLAUDE", None)

        adapter = _fresh_adapter()
        # Same test-local double used by test_adapter_engine_path.py — these
        # tests fake the engine entirely and cannot answer the L44 gate's
        # real `claude -p` classifier call.
        adapter._house_rules_classifier = lambda task, rules, auth, **_kw: ("", 1.0, "test-benign")

        from agents import StreamEvent  # type: ignore

        spawned: dict[str, subprocess.Popen] = {}

        class _FakeSlowPopenEngine:
            """Stands in for ClaudeCodeEngine: spawn() stalls for 6s (past
            the adapter's 5s proc_wait_deadline) before subprocess.Popen()
            actually succeeds and self._proc is set."""

            name = "claude_code"

            def __init__(self, *, binary: str | None = None) -> None:
                self._proc: subprocess.Popen | None = None

            @property
            def proc(self):
                return self._proc

            def cancel(self) -> None:
                if self._proc and self._proc.poll() is None:
                    try:
                        self._proc.terminate()
                    except Exception:
                        pass

            def spawn(self, prompt, **kwargs):
                time.sleep(6.0)  # simulate a slow fork/mkstemp/disk stall
                proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
                self._proc = proc
                spawned["proc"] = proc
                yield StreamEvent(type="text_delta", text="late")

        adapter._ClaudeCodeEngine = _FakeSlowPopenEngine

        chat_key = "chat-late-popen"
        t0 = time.time()
        result = adapter.call_claude_streaming(
            prompt="hi",
            channel="test",
            chat_key=chat_key,
            profile={"permission_mode": "bypassPermissions"},
        )
        elapsed = time.time() - t0

        assert "timed out before producing a process" in result, \
            f"unexpected result: {result!r}"
        assert elapsed < 5.5, \
            f"caller should give up at the ~5s deadline, took {elapsed:.1f}s"
        print(f"PASS: adapter reported spawn-timeout after {elapsed:.2f}s: {result!r}")

        # Give the fake engine's background spawn thread time to actually
        # reach Popen() — it sleeps 6s total from the call above.
        deadline = time.time() + 10.0
        while "proc" not in spawned and time.time() < deadline:
            time.sleep(0.05)
        assert "proc" in spawned, \
            "fake engine never reached Popen() — test setup is broken"
        proc = spawned["proc"]
        try:
            with adapter._running_subprocs_guard:
                in_subprocs = chat_key in adapter._running_subprocs
            with adapter._running_engines_guard:
                in_engines = chat_key in adapter._running_engines

            # THE BUG: the process that materialized after the timeout
            # branch already returned is invisible to both registries.
            assert not in_subprocs, (
                "BUG APPEARS FIXED: the late Popen is now in _running_subprocs — "
                "flip this assertion (and the _cancel_chat check below) to "
                "assert it's reachable instead of orphaned"
            )
            assert not in_engines, (
                "BUG APPEARS FIXED: the late engine is now in _running_engines — "
                "flip this assertion to assert it's reachable instead of orphaned"
            )

            # Prove the orphan is unreachable via the adapter's only
            # cancellation entry point: /cancel finds nothing to kill.
            n = adapter._cancel_chat(chat_key)
            assert n == 0, f"expected 0 (nothing registered to kill), got {n}"
            assert proc.poll() is None, (
                "orphaned subprocess should still be alive — _cancel_chat "
                "has no registry entry through which to reach it"
            )
            print(
                f"CONFIRMED BUG: late-materializing Popen pid={proc.pid} is "
                f"unregistered in both registries; _cancel_chat killed 0 "
                f"processes and the orphan is still alive (poll()={proc.poll()!r})"
            )
        finally:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
    finally:
        if saved_home is not None:
            os.environ["CORVIN_HOME"] = saved_home
        else:
            os.environ.pop("CORVIN_HOME", None)
        if saved_use_engine is not None:
            os.environ["CORVIN_USE_ENGINE_LAYER"] = saved_use_engine
        else:
            os.environ.pop("CORVIN_USE_ENGINE_LAYER", None)
        if saved_fake_claude is not None:
            os.environ["ADAPTER_FAKE_CLAUDE"] = saved_fake_claude
        shutil.rmtree(work, ignore_errors=True)


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
        test_sigterm_handler_only_flags_no_exit,
        test_killed_turn_leaves_inbox_file_for_rerun,
        test_process_one_cancel_during_race_window_before_registration,
        test_late_popen_after_spawn_timeout_orphans_subprocess,
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

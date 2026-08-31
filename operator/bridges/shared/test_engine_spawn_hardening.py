"""Regression tests for engine spawn hardening (release-blocking H1/H7/H9).

Three confirmed findings, fixed by porting ClaudeCodeEngine's patterns to
the codex/opencode siblings:

  H1 — /stop kills the adapter's own process group.
       codex_cli / opencode_cli spawned WITHOUT start_new_session=True, yet
       adapter._cancel_chat does os.killpg(os.getpgid(proc.pid), SIGTERM).
       Without a fresh session the child shares the bridge's pgid, so /stop
       killed the bridge + every concurrent turn. Fix: start_new_session=True
       on both spawns (mirror of claude_code.py).

  H9 — codex/opencode stderr pipe drained only AFTER stdout EOF, so a child
       writing >~64KiB to stderr mid-run blocked forever. Fix: background
       stderr-drain thread + ring buffer (mirror of ClaudeCodeEngine).

  H7 — ClaudeCodeEngine ignored the canonical CORVIN_CLAUDE_BIN pin that
       bridge.sh resolves + exports. Fix: resolve CORVIN_CLAUDE_BIN first,
       then CLAUDE_BIN, then "claude".

Run:
    python -m pytest operator/bridges/shared/test_engine_spawn_hardening.py -q
"""

from __future__ import annotations

import os
import signal
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from agents import collect  # noqa: E402
from agents import claude_code as claude_code_mod  # noqa: E402
from agents import codex_cli as codex_mod  # noqa: E402
from agents import opencode_cli as opencode_mod  # noqa: E402
from agents.claude_code import (  # noqa: E402
    ClaudeCodeEngine,
    _configured_claude_bin,
    _resolve_claude_bin,
)
from agents.codex_cli import CodexCliEngine  # noqa: E402
from agents.copilot_cli import CopilotCliEngine  # noqa: E402
from agents.opencode_cli import OpenCodeEngine  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _FakeStderr:
    """readline()-driven fake: yields queued chunks then EOF (b'')."""

    def __init__(self, chunks: list[bytes] | None = None) -> None:
        self._chunks = list(chunks or [])

    def readline(self):
        if self._chunks:
            return self._chunks.pop(0)
        return b""

    def read(self):
        # Should NOT be the source of truth anymore (drain thread owns the
        # pipe). Kept so any stray caller doesn't explode.
        return b""

    def close(self):
        pass


class _FakeProc:
    """Minimal subprocess.Popen stand-in for the streaming loop."""

    def __init__(self, stdout_lines: list[bytes], stderr: _FakeStderr,
                 returncode: int = 0) -> None:
        self.stdout = iter(stdout_lines)
        self.stderr = stderr
        self._returncode = returncode
        self.pid = 4242
        self.stdin = None

    def poll(self):
        return self._returncode

    def terminate(self):
        pass

    def wait(self, timeout=None):
        return self._returncode

    def kill(self):
        pass


def _patch_popen(module, monkey_store, *, stdout_lines, stderr_chunks=None,
                 returncode=0):
    """Replace module.subprocess.Popen with a capturing fake factory."""
    captured: dict = {}

    def fake_popen(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _FakeProc(stdout_lines, _FakeStderr(stderr_chunks), returncode)

    monkey_store.append((module.subprocess, module.subprocess.Popen))
    module.subprocess.Popen = fake_popen  # type: ignore[assignment]
    return captured


# ---------------------------------------------------------------------------
# H1 — start_new_session=True on codex / opencode spawns
# ---------------------------------------------------------------------------

class StartNewSessionTests(unittest.TestCase):
    def setUp(self):
        self._restore: list = []

    def tearDown(self):
        for subproc_mod, orig in self._restore:
            subproc_mod.Popen = orig

    def test_codex_spawn_uses_start_new_session(self):
        cap = _patch_popen(
            codex_mod, self._restore,
            stdout_lines=[b'{"type":"turn.completed","usage":{}}\n'],
        )
        eng = CodexCliEngine(binary="/fake/codex")
        collect(eng.spawn("hi", timeout=5))
        self.assertTrue(
            cap["kwargs"].get("start_new_session") is True,
            "codex_cli must spawn with start_new_session=True (H1)",
        )

    def test_opencode_spawn_uses_start_new_session(self):
        cap = _patch_popen(
            opencode_mod, self._restore,
            stdout_lines=[],  # EOF → synthesized turn_completed
        )
        eng = OpenCodeEngine(binary="/fake/opencode")
        collect(eng.spawn("hi", timeout=5))
        self.assertTrue(
            cap["kwargs"].get("start_new_session") is True,
            "opencode_cli must spawn with start_new_session=True (H1)",
        )

    def test_claude_spawn_still_uses_start_new_session(self):
        # Guard the reference engine so it can't silently regress.
        cap = _patch_popen(
            claude_code_mod, self._restore,
            stdout_lines=[
                b'{"type":"system","subtype":"init","session_id":"x"}\n',
                b'{"type":"result","subtype":"success","is_error":false,'
                b'"result":"ok"}\n',
            ],
        )
        eng = ClaudeCodeEngine(binary="/fake/claude")
        collect(eng.spawn("hi", timeout=5, prompt_via_stdin=False))
        self.assertTrue(cap["kwargs"].get("start_new_session") is True)


# ---------------------------------------------------------------------------
# H9 — concurrent stderr drain thread
# ---------------------------------------------------------------------------

class StderrDrainTests(unittest.TestCase):
    def setUp(self):
        self._restore: list = []

    def tearDown(self):
        for subproc_mod, orig in self._restore:
            subproc_mod.Popen = orig

    def test_codex_starts_stderr_drain_thread(self):
        _patch_popen(
            codex_mod, self._restore,
            stdout_lines=[b'{"type":"turn.completed","usage":{}}\n'],
        )
        eng = CodexCliEngine(binary="/fake/codex")
        collect(eng.spawn("hi", timeout=5))
        self.assertIsNotNone(
            eng._stderr_thread,
            "codex_cli must start a concurrent stderr-drain thread (H9)",
        )

    def test_opencode_starts_stderr_drain_thread(self):
        _patch_popen(
            opencode_mod, self._restore,
            stdout_lines=[],
        )
        eng = OpenCodeEngine(binary="/fake/opencode")
        collect(eng.spawn("hi", timeout=5))
        self.assertIsNotNone(
            eng._stderr_thread,
            "opencode_cli must start a concurrent stderr-drain thread (H9)",
        )

    def test_codex_stderr_ring_buffer_is_capped(self):
        # A child writing a large stderr body must NOT grow the buffer
        # without bound — the ring cap is what makes the concurrent drain
        # deadlock-safe. Drive the drain loop directly against a fat body.
        eng = CodexCliEngine(binary="/fake/codex")
        # Many realistic-sized lines totalling ~200KiB (>> the 64KiB pipe
        # buffer that would deadlock a non-drained child).
        lines = [(f"stderr line {i:05d} " + "x" * 80 + "\n").encode()
                 for i in range(2000)]
        eng._proc = _FakeProc([], _FakeStderr(lines))  # type: ignore[assignment]
        eng._start_stderr_drain()
        eng._stderr_thread.join(timeout=5)  # type: ignore[union-attr]
        self.assertLessEqual(
            eng._stderr_buf_chars, codex_mod._STDERR_TAIL_CHARS,
            "stderr ring buffer must stay capped at _STDERR_TAIL_CHARS",
        )
        # Tail retains the MOST RECENT output (last line drained).
        self.assertIn("line 01999", eng.stderr_tail(max_chars=200))

    def test_codex_error_path_uses_drained_tail(self):
        # No turn.completed + a stderr body → error must carry the drained
        # tail, not an empty proc.stderr.read().
        eng = CodexCliEngine(binary="/fake/codex")
        eng._proc = _FakeProc(  # type: ignore[assignment]
            [], _FakeStderr([b"boom: model exploded\n"]), returncode=1,
        )
        eng._start_stderr_drain()
        eng._stderr_thread.join(timeout=5)  # type: ignore[union-attr]
        events = list(eng._iter_stream(start_time=0.0, timeout=5))
        self.assertEqual(events[-1].type, "error")
        self.assertIn("boom", events[-1].error or "")


# ---------------------------------------------------------------------------
# H7 — CORVIN_CLAUDE_BIN pin
# ---------------------------------------------------------------------------

class CorvinClaudeBinTests(unittest.TestCase):
    def setUp(self):
        self._saved = {
            k: os.environ.get(k)
            for k in ("CORVIN_CLAUDE_BIN", "CLAUDE_BIN")
        }
        for k in self._saved:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_corvin_claude_bin_wins_over_claude_bin(self):
        os.environ["CORVIN_CLAUDE_BIN"] = "/pinned/bin/claude"
        os.environ["CLAUDE_BIN"] = "/legacy/bin/claude"
        self.assertEqual(_configured_claude_bin(), "/pinned/bin/claude")
        # Path-form pins are honoured as-is by _resolve_claude_bin, so the
        # constructed engine binds to the pin.
        eng = ClaudeCodeEngine()
        self.assertEqual(eng.binary, "/pinned/bin/claude")

    def test_falls_back_to_claude_bin_then_default(self):
        os.environ["CLAUDE_BIN"] = "/legacy/bin/claude"
        self.assertEqual(_configured_claude_bin(), "/legacy/bin/claude")
        os.environ.pop("CLAUDE_BIN")
        self.assertEqual(_configured_claude_bin(), "claude")

    def test_empty_env_values_are_treated_as_unset(self):
        os.environ["CORVIN_CLAUDE_BIN"] = ""
        os.environ["CLAUDE_BIN"] = "/legacy/bin/claude"
        self.assertEqual(_configured_claude_bin(), "/legacy/bin/claude")

    def test_explicit_binary_arg_still_wins(self):
        os.environ["CORVIN_CLAUDE_BIN"] = "/pinned/bin/claude"
        eng = ClaudeCodeEngine(binary="/explicit/claude")
        self.assertEqual(eng.binary, "/explicit/claude")

    def test_whitespace_only_env_values_are_treated_as_unset(self):
        # A stray-space typo in a service.env / systemd EnvironmentFile is
        # a plausible real-world misconfiguration. bash's own `[[ -z ]]`
        # guard in bridge.sh does NOT catch whitespace-only values either,
        # so this must be handled at the Python layer — same contract as
        # the literal empty-string case above (docstring: "Empty-string
        # AND whitespace-only env values ... are treated as unset").
        os.environ["CORVIN_CLAUDE_BIN"] = "   "
        os.environ["CLAUDE_BIN"] = "/legacy/bin/claude"
        self.assertEqual(_configured_claude_bin(), "/legacy/bin/claude")

        os.environ.pop("CLAUDE_BIN")
        self.assertEqual(_configured_claude_bin(), "claude")

    def test_whitespace_only_configured_bin_resolves_like_bare_default(self):
        # End-to-end: with CORVIN_CLAUDE_BIN whitespace-only and no
        # CLAUDE_BIN set, _configured_claude_bin() must fall through to
        # the bare "claude" default, and _resolve_claude_bin() must then
        # run the normal PATH / fallback search for it — NOT hand a
        # literal " " to Popen (which would raise a confusing
        # FileNotFoundError naming binary ' ').
        os.environ["CORVIN_CLAUDE_BIN"] = " "
        configured = _configured_claude_bin()
        self.assertEqual(configured, "claude")
        self.assertEqual(
            _resolve_claude_bin(configured),
            _resolve_claude_bin("claude"),
        )


# ---------------------------------------------------------------------------
# Fixed blind spot — cancel()/_cleanup_proc() used to leak tool-use
# grandchild processes
#
# ClaudeCodeEngine spawns with start_new_session=True (see
# StartNewSessionTests above) specifically so the WHOLE process tree — the
# CLI plus any Bash/MCP-server/curl child it forks for tool use — can be
# reaped as one process group. adapter.py's user-triggered _cancel_chat()
# does exactly that: os.killpg(os.getpgid(pid), SIGTERM). But
# ClaudeCodeEngine.cancel() and _cleanup_proc() — the two call sites hit by
# the internal per-turn timeout and the streaming idle-timeout — used to
# only call proc.terminate()/proc.kill() on the single DIRECT child. If a
# tool-use grandchild was alive at that moment, it was orphaned (reparented,
# never reaped) and kept running.
#
# These tests exercise a REAL process tree (a tiny fake-`claude` shell
# script that forks a detached grandchild) — not the mocked-Popen fixtures
# above — so the leak is observed exactly as it would occur on a live
# bridge host.
#
# Fixed: cancel()/_cleanup_proc() (and the equivalent methods in
# codex_cli.py / opencode_cli.py / copilot_cli.py) now route through the
# shared agents/__init__.py::terminate_process_tree() helper, which
# killpg's the whole process group the same way adapter.py._cancel_chat()
# already does. Both tests below pass against the current implementation —
# they now guard against a regression, not document an open bug.
# ---------------------------------------------------------------------------

_FAKE_CLAUDE_WITH_GRANDCHILD = """\
#!/usr/bin/env bash
echo '{"type":"system","subtype":"init","session_id":"x"}'
sleep 300 </dev/null >/dev/null 2>&1 &
echo $! > "$GRANDCHILD_PID_FILE"
sleep 300
"""


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class GrandchildLeakTests(unittest.TestCase):
    """Regression guard: cancel()/_cleanup_proc() must killpg the whole
    process group, or a live tool-use grandchild spawned by the CLI would
    outlive its parent's cancellation instead of being reaped with it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="engine-grandchild-")
        self._grandchild_pid: int | None = None

    def tearDown(self):
        if self._grandchild_pid is not None:
            try:
                os.kill(self._grandchild_pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        self._tmp.cleanup()

    def _make_fake_claude(self) -> str:
        script_path = os.path.join(self._tmp.name, "fake_claude.sh")
        with open(script_path, "w") as fh:
            fh.write(_FAKE_CLAUDE_WITH_GRANDCHILD)
        os.chmod(script_path, 0o755)
        return script_path

    def _wait_for_grandchild_pid(self, pidfile: str, timeout: float = 5.0) -> int:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if os.path.exists(pidfile):
                try:
                    content = open(pidfile).read().strip()
                except OSError:
                    content = ""
                if content:
                    return int(content)
            time.sleep(0.05)
        raise AssertionError(f"grandchild pidfile {pidfile} never appeared")

    def _drain_quietly(self, gen) -> None:
        try:
            for _ in gen:
                pass
        except Exception:
            pass

    def test_cancel_reaps_tool_use_grandchild(self):
        """cancel() must kill the WHOLE process group (killpg), not just
        the direct child, or a live tool-use grandchild (Bash, MCP
        server, curl, ...) is orphaned and keeps running after /stop /
        the per-turn timeout fires."""
        script = self._make_fake_claude()
        pidfile = os.path.join(self._tmp.name, "grandchild.pid")
        eng = ClaudeCodeEngine(binary=script)

        gen = eng.spawn(
            "hi", timeout=30, prompt_via_stdin=False,
            env={"GRANDCHILD_PID_FILE": pidfile},
        )
        try:
            first = next(gen)  # drives the real Popen + first stdout line
            self.assertEqual(first.type, "session_started")

            grandchild_pid = self._wait_for_grandchild_pid(pidfile)
            self._grandchild_pid = grandchild_pid
            self.assertTrue(
                _process_alive(grandchild_pid),
                "test setup broken: grandchild not alive before cancel()",
            )

            eng.cancel()
            deadline = time.time() + 2.0
            while eng._proc.poll() is None and time.time() < deadline:
                time.sleep(0.05)
            self.assertIsNotNone(
                eng._proc.poll(),
                "direct child should be dead after cancel()",
            )

            time.sleep(0.3)  # let the OS deliver/process the signal
            self.assertFalse(
                _process_alive(grandchild_pid),
                "BUG: cancel() only proc.terminate()s the direct child "
                "(no os.killpg) — a live tool-use grandchild survives "
                "its parent's cancellation and keeps running (leaked).",
            )
        finally:
            self._drain_quietly(gen)

    def test_cleanup_proc_reaps_tool_use_grandchild_on_generator_close(self):
        """The streaming idle-timeout path (adapter.py) stops iterating
        and drops the generator, which drives spawn()'s
        `finally: self._cleanup_proc()`. That must ALSO killpg the whole
        tree, not just terminate()/kill() the direct child."""
        script = self._make_fake_claude()
        pidfile = os.path.join(self._tmp.name, "grandchild2.pid")
        eng = ClaudeCodeEngine(binary=script)

        gen = eng.spawn(
            "hi", timeout=30, prompt_via_stdin=False,
            env={"GRANDCHILD_PID_FILE": pidfile},
        )
        first = next(gen)
        self.assertEqual(first.type, "session_started")

        grandchild_pid = self._wait_for_grandchild_pid(pidfile)
        self._grandchild_pid = grandchild_pid
        self.assertTrue(_process_alive(grandchild_pid))

        gen.close()  # GeneratorExit -> spawn()'s finally -> _cleanup_proc()

        deadline = time.time() + 2.0
        while eng._proc.poll() is None and time.time() < deadline:
            time.sleep(0.05)
        self.assertIsNotNone(
            eng._proc.poll(), "direct child should be dead after close()",
        )

        time.sleep(0.3)
        self.assertFalse(
            _process_alive(grandchild_pid),
            "BUG: _cleanup_proc() only terminate()/kill()s the direct "
            "child (no os.killpg) — a live tool-use grandchild survives "
            "generator close / idle-timeout cleanup and keeps running.",
        )


# ---------------------------------------------------------------------------
# copilot_cli.py — the TimeoutExpired path in spawn() had the SAME blind
# spot as GrandchildLeakTests above, but via a different code path.
#
# copilot_cli.py's spawn() uses a single blocking proc.communicate(timeout=
# ...) instead of a streaming read loop. Before the fix, its
# `except subprocess.TimeoutExpired` handler called a bare proc.kill() on
# only the direct child, and — worse — spawn() also sets
# start_new_session=True, so the child sits in its own detached session.
# A grandchild leaked via THIS path no longer even shares the bridge's own
# session/pgid, making it strictly harder to clean up after the fact than
# before start_new_session=True was added (adversarial review finding).
# Fixed by routing through the same terminate_process_tree() helper
# cancel() already uses.
# ---------------------------------------------------------------------------

_FAKE_COPILOT_WITH_GRANDCHILD = """\
#!/usr/bin/env bash
sleep 300 </dev/null >/dev/null 2>&1 &
echo $! > "$GRANDCHILD_PID_FILE"
sleep 300
"""


class CopilotTimeoutGrandchildLeakTests(unittest.TestCase):
    """Regression guard: the `except subprocess.TimeoutExpired` branch in
    CopilotCliEngine.spawn() must killpg the whole process group, or a live
    tool-use grandchild forked by the `copilot` binary outlives the
    timeout cleanup instead of being reaped with it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="copilot-grandchild-")
        self._grandchild_pid: int | None = None
        self._saved_bin = os.environ.get("CORVIN_COPILOT_BIN")

    def tearDown(self):
        if self._grandchild_pid is not None:
            try:
                os.kill(self._grandchild_pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        if self._saved_bin is None:
            os.environ.pop("CORVIN_COPILOT_BIN", None)
        else:
            os.environ["CORVIN_COPILOT_BIN"] = self._saved_bin
        self._tmp.cleanup()

    def _make_fake_copilot(self) -> str:
        script_path = os.path.join(self._tmp.name, "fake_copilot.sh")
        with open(script_path, "w") as fh:
            fh.write(_FAKE_COPILOT_WITH_GRANDCHILD)
        os.chmod(script_path, 0o755)
        return script_path

    def _wait_for_grandchild_pid(self, pidfile: str, timeout: float = 5.0) -> int:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if os.path.exists(pidfile):
                try:
                    content = open(pidfile).read().strip()
                except OSError:
                    content = ""
                if content:
                    return int(content)
            time.sleep(0.05)
        raise AssertionError(f"grandchild pidfile {pidfile} never appeared")

    def test_timeout_path_reaps_tool_use_grandchild(self):
        """spawn()'s TimeoutExpired handler must kill the WHOLE process
        group (killpg), not just the direct child via a bare proc.kill(),
        or a live tool-use grandchild the `copilot` binary forked is
        orphaned — and, because spawn() uses start_new_session=True, fully
        detached from the bridge's own session as well."""
        script = self._make_fake_copilot()
        os.environ["CORVIN_COPILOT_BIN"] = script
        pidfile = os.path.join(self._tmp.name, "grandchild.pid")

        eng = CopilotCliEngine(timeout_s=10)
        gen = eng.spawn(
            "hi", timeout=1.0, env={"GRANDCHILD_PID_FILE": pidfile},
        )

        first = next(gen)  # yielded before Popen() runs
        self.assertEqual(first.type, "session_started")

        # The second next() call synchronously runs Popen() + blocks on
        # communicate(timeout=1.0) until TimeoutExpired fires and the
        # cleanup path runs — drive it on a background thread so this test
        # can poll for the grandchild pidfile concurrently.
        result: dict = {}

        def _drive() -> None:
            try:
                result["event"] = next(gen)
            except StopIteration:
                result["event"] = None

        t = threading.Thread(target=_drive)
        t.start()

        grandchild_pid = self._wait_for_grandchild_pid(pidfile)
        self._grandchild_pid = grandchild_pid
        self.assertTrue(
            _process_alive(grandchild_pid),
            "test setup broken: grandchild not alive before the timeout fires",
        )

        t.join(timeout=5)
        self.assertFalse(t.is_alive(), "spawn() timeout path did not return in time")

        ev = result.get("event")
        self.assertIsNotNone(ev, "spawn() must yield an error event on timeout")
        self.assertEqual(ev.type, "error")
        self.assertIn("timed out", ev.error or "")

        time.sleep(0.3)  # let the OS deliver/process the signal
        self.assertFalse(
            _process_alive(grandchild_pid),
            "BUG: the TimeoutExpired handler only proc.kill()s the direct "
            "child (no killpg) — a live tool-use grandchild survives the "
            "timeout cleanup and keeps running, fully detached from the "
            "bridge's own session (start_new_session=True) and effectively "
            "unkillable short of a cgroup-level kill.",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

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
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from agents import collect  # noqa: E402
from agents import claude_code as claude_code_mod  # noqa: E402
from agents import codex_cli as codex_mod  # noqa: E402
from agents import opencode_cli as opencode_mod  # noqa: E402
from agents.claude_code import ClaudeCodeEngine, _configured_claude_bin  # noqa: E402
from agents.codex_cli import CodexCliEngine  # noqa: E402
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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

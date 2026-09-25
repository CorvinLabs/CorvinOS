"""ClaudeCodeEngine wall-clock watchdog (A2A review r4, finding 1).

Before the fix `_iter_stream` compared the clock against `timeout` only when a
NEW stdout line arrived, so a child that went silent (hung tool call, stalled
API) ran past its deadline indefinitely. These tests drive the real engine
against a FAKE `claude` binary (a python script) — never the real CLI.
"""
from __future__ import annotations

import os
import stat
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path

_SHARED = Path(__file__).resolve().parents[1]
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

from agents import collect  # noqa: E402
from agents.claude_code import ClaudeCodeEngine, STREAM_TIMEOUT_ERROR  # noqa: E402

_FAKE = textwrap.dedent('''\
    #!{py}
    import json, os, sys, time
    pidfile = os.environ.get("FAKE_PIDFILE")
    if pidfile:
        open(pidfile, "w").write(str(os.getpid()))
    print(json.dumps({{"type": "system", "subtype": "init", "session_id": "s1",
                       "model": "claude-x"}}), flush=True)
    time.sleep(float(os.environ.get("FAKE_SLEEP", "8")))
    print(json.dumps({{"type": "assistant", "message": {{"model": "claude-x",
        "content": [{{"type": "text", "text": "late answer"}}]}}}}), flush=True)
    print(json.dumps({{"type": "result", "subtype": "success",
        "result": "late answer", "usage": {{"input_tokens": 1,
        "output_tokens": 1}}}}), flush=True)
''')


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie still answers kill(0); treat it as dead.
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().split()[2] != "Z"
    except OSError:
        return True


@unittest.skipIf(sys.platform.startswith("win"), "POSIX fake binary")
class WatchdogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="cc-watchdog-"))
        self.fake = self.tmp / "fakeclaude"
        self.fake.write_text(_FAKE.format(py=sys.executable))
        self.fake.chmod(self.fake.stat().st_mode | stat.S_IEXEC)
        self.pidfile = self.tmp / "pid"
        self._env = {k: os.environ.get(k) for k in ("FAKE_SLEEP", "FAKE_PIDFILE", "ADAPTER_FAKE_CLAUDE")}
        os.environ.pop("ADAPTER_FAKE_CLAUDE", None)
        os.environ["FAKE_PIDFILE"] = str(self.pidfile)

    def tearDown(self) -> None:
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_silent_child_is_killed_at_the_deadline(self) -> None:
        os.environ["FAKE_SLEEP"] = "30"
        eng = ClaudeCodeEngine(binary=str(self.fake))
        t0 = time.monotonic()
        res = collect(eng.spawn("hi", timeout=1.5))
        elapsed = time.monotonic() - t0
        # Deadline 1.5 s + SIGTERM delivery; far below the 30 s silence.
        self.assertLess(elapsed, 6.0, f"watchdog did not fire (elapsed {elapsed:.1f}s)")
        self.assertGreaterEqual(elapsed, 1.4)
        self.assertEqual(res.error, STREAM_TIMEOUT_ERROR)
        self.assertTrue(eng.timed_out)
        pid = int(self.pidfile.read_text())
        self.assertFalse(_alive(pid), "child process survived the timeout")

    def test_infinite_timeout_arms_no_watchdog(self) -> None:
        # Adapter / console chat pass timeout=float("inf") and own the idle
        # watchdog themselves — the engine must not kill those turns.
        os.environ["FAKE_SLEEP"] = "1.2"
        eng = ClaudeCodeEngine(binary=str(self.fake))
        before = {t.name for t in threading.enumerate()}
        gen = eng.spawn("hi", timeout=float("inf"))
        first = next(gen)
        names = {t.name for t in threading.enumerate()} - before
        self.assertNotIn("claude-timeout-watchdog", names)
        res = collect(gen)
        self.assertIsNone(res.error)
        self.assertEqual(res.final_text, "late answer")
        self.assertFalse(eng.timed_out)
        self.assertEqual(first.type, "session_started")

    def test_turn_finishing_inside_timeout_is_untouched(self) -> None:
        os.environ["FAKE_SLEEP"] = "0.3"
        before = {t for t in threading.enumerate() if t.name == "claude-timeout-watchdog"}
        eng = ClaudeCodeEngine(binary=str(self.fake))
        res = collect(eng.spawn("hi", timeout=20))
        self.assertIsNone(res.error)
        self.assertEqual(res.final_text, "late answer")
        self.assertFalse(eng.timed_out)
        # Watchdog timer was cancelled, not left running for 20 s. Timer.cancel()
        # does not join, so give THIS test's timer a moment to exit; timers of
        # other tests in a full run are not this test's business.
        deadline = time.monotonic() + 2.0
        mine = []
        while time.monotonic() < deadline:
            mine = [t for t in threading.enumerate()
                    if t.name == "claude-timeout-watchdog" and t.is_alive() and t not in before]
            if not mine:
                break
            time.sleep(0.05)
        self.assertEqual(mine, [], "the watchdog kept running after the turn finished")


if __name__ == "__main__":
    unittest.main()

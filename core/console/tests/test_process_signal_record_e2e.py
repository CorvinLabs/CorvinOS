"""Every signal sent to an agent CLI leaves a record in the tenant audit chain, before it is sent.

PLAN bg-task robustness, P0. Measured 2026-10-18: tasks ended ``exit 143`` with no restart,
cancel or supervisor action in the same second, and nothing named the sender. The real
call sites are exercised here against real subprocesses: ``chat_runtime._stop_proc`` (the
console's cap/wakeup/abort path) and ``agents.terminate_process_tree`` (the bridge engines).
The assertion reads the tenant chain file, not a mock.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]
for _p in (_REPO / "core" / "console", _REPO / "corvin_operator" / "bridges" / "shared",
           _REPO / "corvin_operator" / "forge"):
    sys.path.insert(0, str(_p))


def _records(chain: Path) -> list[dict]:
    if not chain.exists():
        return []
    out = []
    for line in chain.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _signal_records(chain: Path) -> list[dict]:
    return [r for r in _records(chain)
            if r.get("event_type") == "console.action_performed"
            and str((r.get("details") or {}).get("action")) == "process.signal_sent"]


@unittest.skipIf(sys.platform.startswith("win"), "POSIX process groups")
class ProcessSignalRecordE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._env_before = dict(os.environ)
        os.environ.update({
            "CORVIN_HOME": str(self.home / "corvin"), "XDG_CONFIG_HOME": str(self.home / "xdg"),
            "FORGE_ROOT": str(self.home / "forge"), "CORVIN_AUDIT_ANCHOR_KEY": str(self.home / "anchor.key"),
            "CORVIN_TENANT_ID": "_default",
        })
        os.environ.pop("VOICE_AUDIT_PATH", None)
        (self.home / "corvin").mkdir()
        from forge import paths as fp  # type: ignore[import]
        self.chain = fp.tenant_audit_chain("_default")

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env_before)
        self.tmp.cleanup()

    def _sleeper(self) -> subprocess.Popen:
        # own session: the same shape the engines spawn with (start_new_session=True)
        return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                start_new_session=True)

    def test_console_stop_proc_records_reason_before_sigterm(self):
        from corvin_console import chat_runtime

        proc = self._sleeper()

        class _AsyncLike:
            """The subset of asyncio.subprocess.Process that _stop_proc uses."""
            pid = proc.pid
            returncode = None

            def terminate(self) -> None:
                proc.terminate()

            def kill(self) -> None:
                proc.kill()

        wrapped = _AsyncLike()
        chat_runtime._stop_proc(wrapped, grace_s=0.0, reason="child_cap", tenant_id="_default")
        proc.wait(timeout=10)

        sigs = [r for r in _signal_records(self.chain) if r["details"]["target_id"] == str(proc.pid)]
        self.assertTrue(sigs, "no record for the SIGTERM sent by _stop_proc")
        self.assertEqual(sigs[0]["details"]["trigger"], "SIGTERM:child_cap")
        self.assertEqual(sigs[0]["details"]["target_kind"], "cli_process")

    def test_bridge_terminate_process_tree_names_the_engine_caller(self):
        from agents import terminate_process_tree  # type: ignore[import]

        proc = self._sleeper()

        def _cleanup_proc() -> None:          # the real engines call the helper from their cleanup
            terminate_process_tree(proc, grace=2.0)

        _cleanup_proc()
        proc.wait(timeout=10)

        sigs = [r for r in _signal_records(self.chain) if r["details"]["target_id"] == str(proc.pid)]
        self.assertTrue(sigs, "terminate_process_tree did not record its SIGTERM")
        self.assertEqual(sigs[0]["details"]["trigger"], "SIGTERM:_cleanup_proc")

    def test_a_failing_audit_write_never_blocks_the_kill(self):
        from corvin_core import process_signals
        from forge import security_events  # type: ignore[import]

        proc = self._sleeper()
        original = security_events.write_event

        def _down(*_a, **_k):
            raise RuntimeError("chain unreachable")

        security_events.write_event = _down
        try:
            process_signals.send_signal(proc.pid, 15, reason="test")
            proc.wait(timeout=10)
        finally:
            security_events.write_event = original
        self.assertIsNotNone(proc.returncode)
        self.assertEqual(proc.returncode, -15)

if __name__ == "__main__":
    unittest.main()

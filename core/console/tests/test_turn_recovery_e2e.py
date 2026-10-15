"""A console turn cut off by a host restart is run again and must end with output.

Reproduces the 2026-10-15 incident: ``systemctl --user restart corvin-webui`` sent SIGTERM
to the running ``claude`` child; the task ended ``failed`` / exit 143 with no output and
nothing ran it again. Here the real ``chat_runtime.stream_turn`` runs against a real
subprocess (the fake CLI of the bg-scope harness), the child gets a real SIGTERM, and
``turn_recovery`` - reached through the real host lifespans - re-runs the request.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import signal
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]
for _p in (_REPO / "core" / "console", _REPO / "corvin_operator" / "bridges" / "shared",
           _REPO / "corvin_operator" / "bridges" / "shared" / "tests", _REPO / "corvin_operator" / "forge"):
    sys.path.insert(0, str(_p))

import bgscope_testkit as kit  # noqa: E402


@unittest.skipIf(sys.platform.startswith("win"), "POSIX signal semantics")
class TurnRecoveryE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._env_before = dict(os.environ)
        os.environ.update({
            "CORVIN_HOME": str(self.home / "corvin"), "XDG_CONFIG_HOME": str(self.home / "xdg"),
            "FORGE_ROOT": str(self.home / "forge"), "CORVIN_AUDIT_ANCHOR_KEY": str(self.home / "anchor.key"),
            "CORVIN_TENANT_ID": "_default", "CORVIN_OS_ENGINE": "claude_code",
        })
        os.environ.pop("VOICE_AUDIT_PATH", None)
        (self.home / "corvin").mkdir()
        from corvin_console import chat_runtime
        importlib.reload(chat_runtime)
        try:
            import forge.paths as fp  # type: ignore[import]
            importlib.reload(fp)
            importlib.reload(chat_runtime)
        except ImportError:
            pass
        self.cr = chat_runtime
        import house_rules as _hr  # type: ignore
        self._hr, self._hr_orig = _hr, _hr._house_rules_classifier
        _hr._house_rules_classifier = lambda task, rules, auth, **kw: ("", 1.0, "test-benign")
        from corvin_console import turn_recovery
        importlib.reload(turn_recovery)
        self.tr = turn_recovery
        self.sess = self.cr.create_session("_default")

    def tearDown(self) -> None:
        self._hr._house_rules_classifier = self._hr_orig
        self.tmp.cleanup()
        os.environ.clear()
        os.environ.update(self._env_before)

    # -- helpers ------------------------------------------------------------
    def _tasks(self) -> list[dict]:
        d = Path(self.sess.workdir) / "tasks"
        return sorted((json.loads(p.read_text()) for p in d.glob("*.json") if p.name != "recovery_attempts.json"),
                      key=lambda m: m["created_at"])

    async def _interrupted_turn(self, prompt: str) -> None:
        """A real turn whose CLI is SIGTERMed mid-run, exactly like a service restart."""
        os.environ.update(kit.fake_env(self.home, "bash_bg_ok", speedup=4.0, silent_s=30.0))
        pidfile = self.home / "fake.pid"
        pidfile.unlink(missing_ok=True)

        async def drain() -> None:
            async for _ in self.cr.stream_turn(self.sess, prompt):
                pass
        t = asyncio.create_task(drain())
        for _ in range(200):
            if pidfile.exists() and pidfile.read_text().strip():
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("fake CLI never started")
        os.kill(int(pidfile.read_text().strip()), signal.SIGTERM)
        await asyncio.wait_for(t, 30)

    # -- tests --------------------------------------------------------------
    def test_sigtermed_turn_is_found_and_rerun_to_an_answer(self):
        async def go():
            await self._interrupted_turn("please do it")
            failed = self._tasks()[-1]
            self.assertEqual((failed["status"], failed["result_summary"]), ("failed", ""))
            self.assertIn(failed["exit_code"], (-15, 143))
            self.tr.note_shutdown("_default")       # what the host lifespan does on stop

            self.assertEqual([c.task_id for c in self.tr.find_interrupted("_default")], [failed["task_id"]])
            os.environ.update(kit.fake_env(self.home, "bash_bg_ok", speedup=4.0))   # the CLI works again
            done = await self.tr.recover_all("_default", delay_s=0)
            self.assertEqual(done, [(self.sess.sid, "recovered")])

            newest = self._tasks()[-1]
            self.assertNotEqual(newest["task_id"], failed["task_id"])
            self.assertEqual(newest["status"], "completed")
            self.assertIn("please do it", newest["input"]["instruction"])
            self.assertTrue(newest["input"]["instruction"].startswith("[Automatic restart"))
            # the request is now answered: nothing left to recover, and it is not retried forever
            self.assertEqual(self.tr.find_interrupted("_default"), [])
        asyncio.run(go())

    def test_a_cancelled_or_unrelated_failure_is_not_rerun(self):
        async def go():
            await self._interrupted_turn("please do it")
            # no shutdown note and not reaped: a plain SIGTERM of a child is NOT a host restart
            self.assertEqual(self.tr.find_interrupted("_default"), [])
            self.tr.note_shutdown("_default")
            self.assertEqual(len(self.tr.find_interrupted("_default")), 1)
            self.assertEqual(self.tr.find_interrupted("_default", now=time.time() + self.tr.WINDOW_S + 60), [])
        asyncio.run(go())

    def test_no_output_is_retried_but_bounded(self):
        async def go():
            await self._interrupted_turn("please do it")
            self.tr.note_shutdown("_default")
            cand = self.tr.find_interrupted("_default")[0]
            calls = []

            async def no_output(c):
                calls.append(c.task_id)
                return False
            with patch.object(self.tr, "_run_once", no_output):
                self.assertEqual(await self.tr.recover_candidate(cand), "exhausted")
                self.assertEqual(len(calls), self.tr.MAX_ATTEMPTS)
                # persisted: a second boot does not start over
                self.assertEqual(await self.tr.recover_candidate(cand), "exhausted")
                self.assertEqual(len(calls), self.tr.MAX_ATTEMPTS)
        asyncio.run(go())


class HostLifespansReachTheMechanism(unittest.TestCase):
    def test_gateway_lifespan_schedules_recovery_and_notes_the_shutdown(self):
        from fastapi.testclient import TestClient
        from corvin_console import turn_recovery as tr
        from corvin_gateway.app import app
        with patch.object(tr, "schedule_recovery") as sched, patch.object(tr, "note_shutdown") as note:
            with TestClient(app):
                pass
        self.assertEqual((sched.call_count, note.call_count), (1, 1))

    def test_console_lifespan_schedules_recovery_and_notes_the_shutdown(self):
        from fastapi.testclient import TestClient
        from corvin_console import turn_recovery as tr
        from corvin_console.app import create_app
        with patch.object(tr, "schedule_recovery") as sched, patch.object(tr, "note_shutdown") as note:
            with TestClient(create_app()):
                pass
        self.assertEqual((sched.call_count, note.call_count), (1, 1))


if __name__ == "__main__":
    unittest.main()

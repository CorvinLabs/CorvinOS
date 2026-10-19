"""A console turn cut off by a host restart is run again and must end with output.

Reproduces the 2026-10-15 incident: ``systemctl --user restart corvin-webui`` sent SIGTERM
to the running ``claude`` child; the task ended ``failed`` / exit 143 with no output and
nothing ran it again. Here the real ``chat_runtime.stream_turn`` runs against a real
subprocess (the fake CLI of the bg-scope harness), the child gets a real SIGTERM, and
``turn_recovery`` - reached through the real host lifespans - re-runs the request.
"""
from __future__ import annotations

import asyncio
import contextlib
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

    def test_reruns_that_die_are_not_recovered_and_the_attempt_budget_holds(self):
        """PLAN bg-task P1 (measured 2026-10-18): a rerun killed with a child open used to be
        announced as "recovered" (its failure notice is a result), and the attempt key grew with
        every prefix, so MAX_ATTEMPTS never applied. Every rerun here is killed as soon as it
        starts; the budget must end the chain as exhausted, with the request stored once."""
        pidfile = self.home / "fake.pid"

        def _is_fake_cli(pid: int) -> bool:
            # The fake CLI writes its pid non-atomically: a read can see a prefix of the number.
            # Only signal a pid whose command line really is the fake CLI.
            try:
                return "fake_claude" in Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace")
            except OSError:
                return False

        async def killer(seen: set[str]) -> None:
            # Runs in the test's own event loop, so a failure surfaces as a task exception
            # instead of silently ending a helper thread.
            while True:
                try:
                    txt = pidfile.read_text().strip()
                except OSError:
                    txt = ""
                if txt.isdigit() and txt not in seen and _is_fake_cli(int(txt)):
                    seen.add(txt)
                    with contextlib.suppress(ProcessLookupError):
                        os.kill(int(txt), signal.SIGTERM)
                await asyncio.sleep(0.01)

        async def go():
            await self._interrupted_turn("summarise the board minutes")
            self.tr.note_shutdown("_default")
            # Reruns that hang until the killer arrives: a fast CLI can finish before it does.
            os.environ.update(kit.fake_env(self.home, "bash_bg_ok", speedup=1.0, silent_s=600.0))
            pidfile.unlink(missing_ok=True)
            seen: set[str] = set()
            watcher = asyncio.create_task(killer(seen))
            try:
                return await self.tr.recover_all("_default", delay_s=0)
            finally:
                watcher.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await watcher

        done = asyncio.run(go())
        self.assertEqual(done, [(self.sess.sid, "exhausted")])
        notices = [t["notice"] for t in self.cr.read_turns("_default", self.sess.sid) if t.get("notice")]
        self.assertNotIn("recovered", notices)
        self.assertEqual(notices[-1], "gave_up")
        reruns = [t for t in self._tasks() if t["input"]["instruction"].startswith("[Automatic restart")]
        self.assertEqual(len(reruns), self.tr.MAX_ATTEMPTS)
        for t in reruns:   # the request is stored once, not with one more prefix per attempt
            self.assertEqual(t["input"]["instruction"].count("[Automatic restart"), 1)
        # an exhausted chain stays visible but is never picked up again at the next boot
        self.assertTrue(all(c.attempts >= self.tr.MAX_ATTEMPTS for c in self.tr.find_interrupted("_default")))
        self.assertEqual(asyncio.run(self.tr.recover_all("_default", delay_s=0)), [])

    def test_user_is_told_in_the_chat_and_can_read_the_timeline(self):
        """PLAN-0958 P2: one persisted notice per step, no ids, kept out of the worker's history,
        and the per-task timeline route returns the same story over HTTP."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from corvin_console import deps as console_deps
        from corvin_console.routes import chat as chat_route
        from test_update_button_e2e import _fake_record
        import session_ledger  # type: ignore

        async def go():
            await self._interrupted_turn("write the quarterly report for the board")
            failed = self._tasks()[-1]
            self.tr.note_shutdown("_default")
            os.environ.update(kit.fake_env(self.home, "bash_bg_ok", speedup=4.0))
            self.assertEqual(await self.tr.recover_all("_default", delay_s=0), [(self.sess.sid, "recovered")])
            return failed
        failed = asyncio.run(go())

        notices = [t for t in self.cr.read_turns("_default", self.sess.sid) if t.get("notice")]
        self.assertEqual([t["notice"] for t in notices], ["interrupted_resuming", "recovered"])
        text = notices[0]["parts"][0]["text"]
        self.assertIn("service restart", text)
        self.assertIn("attempt 1 of 2", text)
        self.assertIn("1 left", text)
        self.assertIn("write the quarterly report", text)           # the user's own words as the label
        import re
        self.assertIsNone(re.search(r"[0-9a-f]{8}-[0-9a-f]{4}|\\d{10,}", " ".join(
            t["parts"][0]["text"] for t in notices)), "a notice carried an id-shaped token")
        # the worker's view of the chat never contains a notice
        recs = session_ledger.records_from_turn_log(self.cr.read_turns("_default", self.sess.sid))
        self.assertFalse([r for r in recs if "Your task '" in (r.get("assistant") or "") + (r.get("user") or "")])
        self.assertTrue(recs, "control: the ledger view must still contain the real turns")

        chat_route.chat_runtime = self.cr
        app = FastAPI()
        app.include_router(chat_route.router)
        rec = _fake_record()
        app.dependency_overrides[console_deps.require_session] = lambda: rec
        app.dependency_overrides[console_deps.require_csrf] = lambda: rec
        with TestClient(app) as c:
            r = c.get(f"/chat/sessions/{self.sess.sid}/tasks/{failed['task_id']}/timeline")
            self.assertEqual(r.status_code, 200, r.text)
            rows = r.json()["rows"]
            self.assertIn("interrupted_resuming", [x["kind"] for x in rows])
            self.assertEqual(rows[0]["state"], "pending")
            self.assertEqual(c.get(f"/chat/sessions/{self.sess.sid}/tasks/nope/timeline").status_code, 404)

    def test_an_outbound_effect_is_never_repeated_silently(self):
        """PLAN-0958 P4: the interrupted run had already called a send tool - the user is asked,
        the request is NOT re-run (no second CLI spawn), and a later boot does not ask again."""
        from corvin_core.task_manager import TaskManager

        async def go():
            await self._interrupted_turn("mail the report to the board")
            failed = self._tasks()[-1]
            TaskManager(Path(self.sess.workdir) / "tasks").record_event(
                failed["task_id"], {"event": "tool_use", "tool_name": "mcp__claude_ai_Gmail__send_message"})
            self.tr.note_shutdown("_default")
            cand = self.tr.find_interrupted("_default")[0]
            self.assertEqual(cand.outbound, ("mcp__claude_ai_Gmail__send_message",))
            os.environ.update(kit.fake_env(self.home, "bash_bg_ok", speedup=4.0))
            spawns_before = kit.spawn_count(self.home)
            self.assertEqual(await self.tr.recover_all("_default", delay_s=0), [(self.sess.sid, "needs_user")])
            self.assertEqual(kit.spawn_count(self.home), spawns_before, "the request was re-run")
            notices = [t["notice"] for t in self.cr.read_turns("_default", self.sess.sid) if t.get("notice")]
            self.assertEqual(notices, ["needs_user"])
            self.assertEqual(await self.tr.recover_all("_default", delay_s=0), [])      # not asked twice
        asyncio.run(go())

    def test_a_read_only_tool_is_not_outbound(self):
        names = ("Bash", "Read", "Write", "Edit", "mcp__forge__forge_list", "mcp__claude_ai_Gmail__search_threads")
        tasks = Path(self.sess.workdir) / "tasks"
        tasks.mkdir(exist_ok=True)
        (tasks / "t1.events.jsonl").write_text("\n".join(
            json.dumps({"event": "tool_use", "tool_name": n}) for n in names) + "\n")
        self.assertEqual(self.tr.outbound_tools(tasks, "t1"), ())

    def test_operator_cancel_ends_the_child_and_is_final(self):
        """PLAN-0958 P5 / CONCEPT-0112 Q2: a stop from another tab (cancel_live_turn) must terminate the
        real claude child - not only the asyncio task - end the task as cancelled, tell the user
        nothing, and never be picked up by recovery."""
        async def go():
            os.environ.update(kit.fake_env(self.home, "bash_bg_ok", speedup=4.0, silent_s=30.0))
            pidfile = self.home / "fake.pid"
            pidfile.unlink(missing_ok=True)
            loop = asyncio.get_running_loop()

            async def drain():
                async for _ in self.cr.stream_turn(self.sess, "stop me"):
                    pass
            t = asyncio.create_task(drain())
            self.cr.register_live_turn(self.sess.tenant_id, self.sess.sid, loop, t)
            for _ in range(200):
                if pidfile.exists() and pidfile.read_text().strip():
                    break
                await asyncio.sleep(0.05)
            else:
                raise AssertionError("fake CLI never started")
            pid = int(pidfile.read_text().strip())
            self.assertTrue(self.cr.cancel_live_turn(self.sess.tenant_id, self.sess.sid))
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(t, 30)
            for _ in range(100):
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                await asyncio.sleep(0.05)
            else:
                raise AssertionError("the claude child outlived the cancel (orphaned process)")
            meta = self._tasks()[-1]
            self.assertEqual(meta["status"], "cancelled")
            self.assertFalse([x for x in self.cr.read_turns("_default", self.sess.sid) if x.get("notice")])
            self.tr.note_shutdown("_default")
            self.assertEqual(self.tr.find_interrupted("_default"), [])      # final: never resumed
        asyncio.run(go())

    def test_a_reaped_orphan_is_rerun_too(self):
        """PLAN-0958 P1 made the boot reaper write ``interrupted`` (no ended_at, not ``failed``).
        The 14:03 task of 2026-10-15 stayed interrupted forever because recovery only looked
        at ``failed``. A real reap of a dead-pid task must lead to a re-run."""
        from corvin_core.task_manager import TaskManager

        async def go():
            tasks_dir = Path(self.sess.workdir) / "tasks"
            tm = TaskManager(tasks_dir)
            tid = tm.create_task(self.sess.chat_key, "please finish the report", tenant_id="_default")
            tm.record_event(tid, {"event": "task.started", "engine": "claude", "pid": 2 ** 22 + 12345})
            self.assertEqual(TaskManager(tasks_dir).reap_stale_running(), [tid])
            meta = json.loads((tasks_dir / f"{tid}.json").read_text())
            self.assertEqual((meta["status"], meta["exit_code"]), ("interrupted", -1))
            self.assertEqual([c.task_id for c in self.tr.find_interrupted("_default")], [tid])
            os.environ.update(kit.fake_env(self.home, "bash_bg_ok", speedup=4.0))
            self.assertEqual(await self.tr.recover_all("_default", delay_s=0), [(self.sess.sid, "recovered")])
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

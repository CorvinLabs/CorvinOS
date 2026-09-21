"""The stale-task reaper must run on EVERY host, not just the messenger bridge.

``TaskManager.reap_stale_running()`` is sound, liveness-gated and covered by six
unit tests in ``core/console/tests/test_task_manager.py``. None of that mattered,
because until 2026-09-21 it had exactly one caller: ``adapter.py``'s boot. A
console-only install — ``corvinos-serve`` / ``corvin-service`` with no bridge ever
started — therefore never reaped anything.

The consequence is not a slow leak, it is a permanently dead chat. Nothing moves a
task out of ``running`` except a terminal event or this sweep, so every console
restart mid-turn leaks one; the fifth leak reaches ``max_concurrent=5`` and every
further turn raises ``QuotaExceededError`` forever. No restart, cache clear or
waiting recovers it. Measured on a console-only Windows install: 5/5 orphans on
one web chat, oldest 9.7 h, chat dead, surfacing to the user only as "The turn
failed unexpectedly (QuotaExceededError)".

So the assertions here are about the CALLER and about the GLOB — the two things a
unit test of the reaper cannot see. Same shape, and the same file layout, as
``test_boot_platform_call_site.py``.
"""
from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parents[3]


def _boot_platform_calls() -> list[str]:
    """Names called inside ``boot_platform``, ranked by source line.

    An AST walk, not a substring search: ``"_reap_stale_tasks" in source`` is
    also true of the comment that explains it, and a wiring gate a comment can
    satisfy is exactly the kind of guard that survives deleting what it guards.
    """
    from corvin_plugins import bootstrap

    tree = ast.parse(Path(bootstrap.__file__).read_text(encoding="utf-8"))
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "boot_platform"
    )
    ranked = sorted(
        (node.lineno,
         node.func.id if isinstance(node.func, ast.Name) else node.func.attr)
        for node in ast.walk(fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, (ast.Name, ast.Attribute))
    )
    return [name for _, name in ranked]


class TestTheSharedSequenceReaps(unittest.TestCase):
    def test_boot_platform_calls_the_reaper(self):
        """The whole defect: a mechanism with one caller and two hosts."""
        self.assertIn(
            "_reap_stale_tasks", _boot_platform_calls(),
            "boot_platform does not call _reap_stale_tasks. It is the ONE "
            "sequence both shipped hosts run (see test_boot_platform_call_site), "
            "so removing this call means a console-only install never reaps and "
            "any chat dies permanently on its fifth interrupted turn.",
        )

    def test_the_reaper_runs_after_the_compliance_tripwire(self):
        """Ordering: nothing may precede the fail-closed tripwires, and a
        best-effort convenience sweep least of all."""
        names = _boot_platform_calls()
        self.assertLess(
            names.index("assert_compliance"), names.index("_reap_stale_tasks"),
            "the compliance tripwires must run before the task reaper",
        )

    def test_the_reaper_is_not_behind_a_feature_flag(self):
        """A default-off switch here reinstates the permanently-dead chat."""
        from corvin_plugins import bootstrap

        src = Path(bootstrap.__file__).read_text(encoding="utf-8")
        body = src[src.index("def _reap_stale_tasks("):]
        body = body[:body.index("\ndef ")]
        for forbidden in ("is_enabled(", "CORVIN_NO_REAP", "CORVIN_SKIP_REAP"):
            self.assertNotIn(
                forbidden, body,
                f"_reap_stale_tasks must carry no off switch (found {forbidden!r})",
            )


class TestTheSweepReachesRealConsoleTaskDirs(unittest.TestCase):
    """The glob is the other half of the wiring, and it is silent when wrong.

    ``_reap_stale_tasks`` logs "no orphaned running tasks" just as happily when it
    swept the wrong subtree as when there was genuinely nothing to do, so a
    mismatched pattern reads exactly like success. These tests build a task dir at
    the path the console really uses and assert the sweep finds it.
    """

    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        # The real layout, as observed on a live install:
        #   <home>/tenants/_default/sessions/web_<sid>/tasks
        self.tasks_dir = (self.home / "tenants" / "_default" / "sessions"
                          / "web_q4gHyf08tuXkldGzcVN7qA" / "tasks")
        self.tasks_dir.mkdir(parents=True)
        self.addCleanup(self._tmp.cleanup)

    def _task_manager(self):
        from corvin_core.task_manager import TaskManager

        return TaskManager(self.tasks_dir)

    def _sweep(self):
        """Run the real sweep against this fake home."""
        from corvin_plugins import bootstrap

        with mock.patch(
            "corvin_operator.forge.forge.paths.corvin_home",
            return_value=self.home,
        ):
            return bootstrap._reap_stale_tasks()

    def test_an_orphan_in_a_real_console_task_dir_is_reaped(self):
        from corvin_core.task_manager import TaskStatus

        tm = self._task_manager()
        chat_key = "web:q4gHyf08tuXkldGzcVN7qA"
        tid = tm.create_task(chat_key=chat_key, instruction="interrupted turn")
        # No pid: the delegation path records none, and a console killed before
        # task.started leaves none either — both are genuine orphans.
        tm.record_event(tid, {"event": "task.started"})
        self.assertEqual(tm._count_running_tasks(chat_key), 1)

        self.assertEqual(self._sweep(), 1)

        self.assertEqual(tm.get_task(tid).status, TaskStatus.FAILED)
        self.assertEqual(
            tm._count_running_tasks(chat_key), 0,
            "the quota counter must be freed — that is the only reason to reap",
        )

    def test_a_full_quota_is_freed_end_to_end(self):
        """Reproduce the reported failure exactly, then clear it.

        Five orphans is not an arbitrary number: it is ``max_concurrent``, the
        point at which ``check_quota`` starts refusing every new turn.
        """
        from corvin_core.task_manager import QuotaExceededError

        tm = self._task_manager()
        chat_key = "web:q4gHyf08tuXkldGzcVN7qA"
        for i in range(5):
            tid = tm.create_task(chat_key=chat_key, instruction=f"turn {i}")
            tm.record_event(tid, {"event": "task.started"})

        # Red: this is the user-visible bug.
        with self.assertRaises(QuotaExceededError):
            tm.check_quota(chat_key, max_concurrent=5)

        self.assertEqual(self._sweep(), 5)

        # Green: the chat accepts turns again.
        tm.check_quota(chat_key, max_concurrent=5)

    def test_a_live_task_survives_the_boot_sweep(self):
        """The boot sweep must inherit the reaper's liveness gate, not bypass it.

        An engine subprocess can be reparented and outlive the host that spawned
        it, so "at boot nothing is running" is false (incident 2026-06-17). This
        is the check that fails if the sweep ever grows its own idea of staleness
        — an age cutoff, say — on top of the pid probe.
        """
        from corvin_core.task_manager import TaskStatus

        tm = self._task_manager()
        tid = tm.create_task(chat_key="web:live", instruction="still streaming")
        tm.record_event(tid, {"event": "task.started", "pid": 4242})

        with mock.patch(
            "corvin_core.task_manager.TaskManager._task_pid_alive",
            return_value=True,
        ):
            self.assertEqual(self._sweep(), 0)

        self.assertEqual(tm.get_task(tid).status, TaskStatus.RUNNING)

    def test_the_sweep_honours_corvin_home(self):
        """Hard-wiring ~/.corvin would make a non-default install sweep the wrong
        tree and report success while its own chats stayed starved (ADR-0007)."""
        tm = self._task_manager()
        tid = tm.create_task(chat_key="web:x", instruction="orphan")
        tm.record_event(tid, {"event": "task.started"})

        from corvin_plugins import bootstrap

        elsewhere = self.home / "not-the-home"
        elsewhere.mkdir()
        with mock.patch(
            "corvin_operator.forge.forge.paths.corvin_home",
            return_value=elsewhere,
        ):
            self.assertEqual(
                bootstrap._reap_stale_tasks(), 0,
                "a sweep of an empty home must find nothing — if this reaped, "
                "the function is not using the home it was given",
            )
        # Positive control: the orphan really was reapable, so the 0 above is a
        # statement about the path and not about the task.
        self.assertEqual(self._sweep(), 1)

    def test_a_corrupt_task_file_does_not_break_the_boot(self):
        """Best-effort by contract: the cost of a failed sweep is a blocked
        quota, never an install that will not serve."""
        (self.tasks_dir / "garbage.json").write_text("{not json", encoding="utf-8")
        tm = self._task_manager()
        tid = tm.create_task(chat_key="web:x", instruction="orphan")
        tm.record_event(tid, {"event": "task.started"})

        self.assertEqual(self._sweep(), 1, "one bad file must not abort the sweep")

    def test_meta_status_is_what_the_quota_reads(self):
        """Guards the assumption the whole fix rests on: reaping writes a
        terminal status into the META file, because ``_count_running_tasks``
        reads meta and never replays the event log."""
        tm = self._task_manager()
        tid = tm.create_task(chat_key="web:x", instruction="orphan")
        tm.record_event(tid, {"event": "task.started"})
        self._sweep()

        meta = json.loads((self.tasks_dir / f"{tid}.json").read_text(encoding="utf-8"))
        self.assertNotEqual(meta["status"], "running")


class TestTheConsoleStillWritesWhereTheSweepLooks(unittest.TestCase):
    def test_chat_runtime_builds_its_tasks_dir_under_the_session_workdir(self):
        """Source-coupling guard. The sweep's glob and the console's write path
        are in different packages with nothing but a convention between them; if
        the console ever moves its task dir out of ``sess.workdir / "tasks"``,
        the sweep goes quietly back to finding nothing.
        """
        src = (_REPO / "core" / "console" / "corvin_console"
               / "chat_runtime.py").read_text(encoding="utf-8")
        self.assertIn(
            'sess.workdir / "tasks"', src,
            "chat_runtime no longer writes tasks under sess.workdir — the boot "
            "sweep's tenants/*/sessions/**/tasks glob must be updated with it",
        )


if __name__ == "__main__":
    unittest.main()

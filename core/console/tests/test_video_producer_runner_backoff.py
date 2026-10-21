"""A plugin load without a job runner is retried with exponential backoff, not on every request (PLG-10).

Until 2026-10-21 ``_plugin_needs_reload`` was true on EVERY request once 30 s had passed after a load whose
runner never imported (no ``async_runner`` / no ``job_main.py``): a warning and a full re-import per request,
for ever. Now a load that is complete is only reloaded when its source vanished; an incomplete one is retried
after 30 s, then 60, 120 ... capped at 10 min, and the state change is logged once. The retired-runner list
is capped.

A fake plugin directory (models + storage, no runner), the real loader, a controlled clock.
"""
from __future__ import annotations

import logging
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_FAKE_STORAGE = "_stores = []\n\ndef get_storage(base, mark_interrupted=True):\n    return object()\n"
_FAKE_MODELS = "class VideoJob:\n    def __init__(self, **kw):\n        self.__dict__.update(kw)\n"
_FAKE_RUNNER = (
    "def get_runner():\n    return 'thread-runner'\n\n"
    "def get_process_runner(max_workers=3):\n    return 'process-runner'\n"
)


class RunnerBackoff(unittest.TestCase):
    def setUp(self):
        from corvin_console.routes import video_producer_api as vpa

        self.vpa = vpa
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.src = self.tmp / "src"
        self.src.mkdir()
        (self.src / "__init__.py").write_text("")
        (self.src / "storage.py").write_text(_FAKE_STORAGE)
        (self.src / "models.py").write_text(_FAKE_MODELS)
        keys = ("VideoJob", "_get_storage", "get_runner", "PLUGIN_SOURCE", "_PLUGIN_SRC", "_LOADED_AT",
                "_LOADED_COMPLETE", "_runner_retry_s", "_runner_missing_warned")
        saved = {k: getattr(vpa, k) for k in keys if hasattr(vpa, k)}
        saved_modules = {n: m for n, m in sys.modules.items() if n.split(".")[0] == vpa._PLUGIN_PKG}
        retired = list(vpa._RETIRED_RUNNER_GETTERS)

        def restore():
            for n in [n for n in sys.modules if n.split(".")[0] == vpa._PLUGIN_PKG]:
                sys.modules.pop(n)
            sys.modules.update(saved_modules)
            for k, v in saved.items():
                setattr(vpa, k, v)
            vpa._RETIRED_RUNNER_GETTERS.clear()
            vpa._RETIRED_RUNNER_GETTERS.extend(retired)

        self.addCleanup(restore)
        for k in ("VideoJob", "_get_storage", "get_runner", "PLUGIN_SOURCE", "_PLUGIN_SRC"):
            setattr(vpa, k, None)
        vpa._RETIRED_RUNNER_GETTERS.clear()
        for n in [n for n in sys.modules if n.split(".")[0] == vpa._PLUGIN_PKG]:
            sys.modules.pop(n)
        self.now = 1000.0
        self.load_calls = 0
        real_load = vpa._load_plugin

        def counting_load():
            self.load_calls += 1
            return real_load()

        for p in (mock.patch.object(vpa, "_enabled_for", lambda t: True),
                  mock.patch.object(vpa, "_plugin_locations", lambda: [(self.src, "fake")]),
                  mock.patch.object(vpa.time, "monotonic", lambda: self.now),
                  mock.patch.object(vpa, "_load_plugin", counting_load),
                  mock.patch.object(vpa, "_runner_retry_s", vpa._RUNNER_RETRY_S),
                  mock.patch.object(vpa, "_runner_missing_warned", False)):
            p.start()
            self.addCleanup(p.stop)

    def _loads(self) -> int:
        """Drive ``_ensure_plugin`` once; the number of loader runs it caused (0 or 1)."""
        before = self.load_calls
        self.assertTrue(self.vpa._ensure_plugin("t"))
        return self.load_calls - before

    def test_a_load_without_a_runner_backs_off_exponentially_and_warns_once(self):
        vpa = self.vpa
        with self.assertLogs(vpa.logger, level="WARNING") as first:
            self.assertEqual(self._loads(), 1)
        self.assertIsNone(vpa.get_runner)
        self.assertEqual(sum("runner" in m for m in first.output), 1)
        # under the first wait: nothing
        self.now += 10
        self.assertEqual(self._loads(), 0)
        # after 30 s: one retry, no second warning, and the wait doubles
        self.now += 21
        with mock.patch.object(vpa.logger, "warning") as warn:
            self.assertEqual(self._loads(), 1)
        self.assertFalse(warn.called, "the same missing-runner state was warned about again")
        self.assertEqual(list(vpa._RETIRED_RUNNER_GETTERS), [], "a None getter was retired")
        self.now += 45                                  # 45 s < 60 s: no retry
        self.assertEqual(self._loads(), 0)
        self.now += 16                                  # 61 s: retry
        self.assertEqual(self._loads(), 1)
        waits = []
        for _ in range(8):                              # 120, 240, 480, 600, 600 ...
            waits.append(vpa._runner_retry_s)
            self.now += vpa._runner_retry_s + 1
            self.assertEqual(self._loads(), 1)
        self.assertEqual(waits[:4], [120.0, 240.0, 480.0, 600.0])
        self.assertTrue(all(w == 600.0 for w in waits[4:]), waits)
        self.assertLessEqual(vpa._runner_retry_s, 600.0)

    def test_the_runner_appearing_ends_the_retries_and_resets_the_wait(self):
        vpa = self.vpa
        self._loads()
        self.now += 31
        self._loads()                                   # one failed retry: wait is 60 now
        self.assertEqual(vpa._runner_retry_s, 60.0)
        (self.src / "async_runner.py").write_text(_FAKE_RUNNER)
        (self.src / "job_main.py").write_text("")
        self.now += 30
        self.assertEqual(self._loads(), 0)              # not before the wait is over
        self.now += 31
        with self.assertLogs(vpa.logger, level="INFO") as back:
            self.assertEqual(self._loads(), 1)
        self.assertTrue(any("runner" in m and "available" in m for m in back.output), back.output)
        self.assertEqual(vpa.get_runner(), "process-runner")
        self.assertEqual(vpa._runner_retry_s, vpa._RUNNER_RETRY_S)
        self.now += 10_000
        self.assertEqual(self._loads(), 0, "a complete load must not be reloaded by time")

    def test_a_complete_load_whose_source_vanishes_reloads_at_once_with_a_warning(self):
        vpa = self.vpa
        (self.src / "async_runner.py").write_text(_FAKE_RUNNER)
        (self.src / "job_main.py").write_text("")
        self._loads()
        self.assertEqual(vpa.get_runner(), "process-runner")
        other = self.tmp / "other" / "src"
        shutil.copytree(self.src, other)
        with mock.patch.object(vpa, "_plugin_locations", lambda: [(self.src, "fake"), (other, "other")]):
            shutil.rmtree(self.src)
            with self.assertLogs(vpa.logger, level="WARNING") as gone:
                self.assertEqual(self._loads(), 1)
        self.assertTrue(any("gone" in m for m in gone.output), gone.output)
        self.assertEqual(vpa.PLUGIN_SOURCE, "other")
        self.assertEqual(len(vpa._RETIRED_RUNNER_GETTERS), 1)

    def test_a_source_without_the_job_entry_point_has_no_process_runner_and_is_retried_not_relooped(self):
        vpa = self.vpa
        (self.src / "async_runner.py").write_text(_FAKE_RUNNER)   # importable, but no job_main.py to spawn
        self.assertEqual(self._loads(), 1)
        self.assertIsNone(vpa.get_runner, "a process runner for a missing job_main.py spawns a dead path")
        self.now += 5
        self.assertEqual(self._loads(), 0, "reloaded on the next request")

    def test_the_retired_runner_list_is_capped(self):
        vpa = self.vpa
        for i in range(vpa._MAX_RETIRED_RUNNERS + 4):
            vpa._RETIRED_RUNNER_GETTERS.append(i)
        kept = list(vpa._RETIRED_RUNNER_GETTERS)
        self.assertEqual(len(kept), vpa._MAX_RETIRED_RUNNERS)
        self.assertEqual(kept[-1], vpa._MAX_RETIRED_RUNNERS + 3, "the newest must be kept")
        self.assertEqual(kept[0], 4, "the oldest must be dropped")


if __name__ == "__main__":
    logging.basicConfig()
    unittest.main()

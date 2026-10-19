"""CorvinTV: the plugin source moving under a running console (2026-10-19).

The console loaded the plugin from <home>/marketplace-cache/.../contributor/media/video_producer/src at
14:27; at 20:59 a marketplace resync replaced the cache (CorvinTV had moved to buildin/media/corvintv), and
every job after that started ``python <gone>/job_main.py`` and ended "The job process ended unexpectedly
(exit code 2)". ``_ensure_plugin`` now notices that the loaded source is gone and loads it from where it is
now; jobs of the retired load stay cancellable through its runner.

Real plugin source (the sibling Corvin-Marketplace checkout, copied twice into a temp dir), real loader.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parents[3]
_SRC = next((p for p in (_REPO.parent / "Corvin-Marketplace" / "plugins" / "buildin" / "media" / "corvintv" / "src",
                         _REPO.parent / "Corvin-Marketplace" / "plugins" / "contributor" / "media" / "video_producer" / "src")
             if (p / "job_main.py").is_file()), None)


@unittest.skipUnless(_SRC, "needs the sibling Corvin-Marketplace checkout")
class PluginSourceMoved(unittest.TestCase):
    def setUp(self):
        from corvin_console.routes import video_producer_api as vpa

        self.vpa = vpa
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.old, self.new = self.tmp / "old" / "src", self.tmp / "new" / "src"
        for d in (self.old, self.new):
            shutil.copytree(_SRC, d, ignore=shutil.ignore_patterns("__pycache__"))
        saved = {k: getattr(vpa, k) for k in ("VideoJob", "_get_storage", "get_runner", "PLUGIN_SOURCE", "_PLUGIN_SRC", "_LOADED_AT")}
        saved_modules = {n: m for n, m in sys.modules.items() if n.split(".")[0] == vpa._PLUGIN_PKG}

        def restore():
            for n in [n for n in sys.modules if n.split(".")[0] == vpa._PLUGIN_PKG]:
                sys.modules.pop(n)
            sys.modules.update(saved_modules)
            for k, v in saved.items():
                setattr(vpa, k, v)
            vpa._RETIRED_RUNNER_GETTERS.clear()

        self.addCleanup(restore)
        for k in ("VideoJob", "_get_storage", "get_runner", "PLUGIN_SOURCE", "_PLUGIN_SRC"):
            setattr(vpa, k, None)
        for n in [n for n in sys.modules if n.split(".")[0] == vpa._PLUGIN_PKG]:
            sys.modules.pop(n)
        stack = [mock.patch.object(vpa, "_enabled_for", lambda t: True),
                 mock.patch.object(vpa, "_plugin_locations", lambda: [(self.old, "old"), (self.new, "new")])]
        for p in stack:
            p.start()
            self.addCleanup(p.stop)

    def test_a_vanished_source_is_reloaded_from_where_it_is_now(self):
        vpa = self.vpa
        self.assertTrue(vpa._ensure_plugin("t"))
        self.assertEqual((vpa.PLUGIN_SOURCE, vpa._PLUGIN_SRC), ("old", self.old))
        old_getter = vpa.get_runner
        runner_mod = sys.modules[f"{vpa._PLUGIN_PKG}.async_runner"]
        self.assertEqual(runner_mod._JOB_MAIN, (self.old / "job_main.py").resolve())

        self.assertTrue(vpa._ensure_plugin("t"))  # still there: no reload
        self.assertIs(vpa.get_runner, old_getter)

        shutil.rmtree(self.old.parent)            # the resync
        self.assertTrue(vpa._ensure_plugin("t"))
        self.assertEqual((vpa.PLUGIN_SOURCE, vpa._PLUGIN_SRC), ("new", self.new))
        runner_mod = sys.modules[f"{vpa._PLUGIN_PKG}.async_runner"]
        self.assertEqual(runner_mod._JOB_MAIN, (self.new / "job_main.py").resolve())
        self.assertTrue(runner_mod._JOB_MAIN.is_file())
        self.assertEqual(vpa._RETIRED_RUNNER_GETTERS, [old_getter])

    def test_the_reload_does_not_close_the_jobs_this_console_is_running(self):
        # the fresh load's first open of a store sweeps non-terminal jobs as "interrupted" (a console restart);
        # without a job lock (Windows) that closed the live jobs of THIS console (adversarial review 2026-10-19)
        vpa = self.vpa
        self.assertTrue(vpa._ensure_plugin("t"))
        base = str(self.tmp / "tenant" / "video_producer")
        store = vpa._get_storage(base)
        store.save_job(vpa.VideoJob(id="job_0000live", task="t", status="skills_running"))
        shutil.rmtree(self.old.parent)
        self.assertTrue(vpa._ensure_plugin("t"))
        self.assertEqual(vpa.PLUGIN_SOURCE, "new")
        self.assertEqual(vpa._get_storage(base).get_job("job_0000live").status, "skills_running")

    def test_a_load_without_a_runner_is_retried(self):
        vpa = self.vpa
        self.assertTrue(vpa._ensure_plugin("t"))
        vpa.get_runner = None                     # the runner import failed: caught mid-resync
        self.assertTrue(vpa._ensure_plugin("t"))
        self.assertIsNone(vpa.get_runner, "retried before the back-off")
        vpa._LOADED_AT -= vpa._RUNNER_RETRY_S + 1
        self.assertTrue(vpa._ensure_plugin("t"))
        self.assertIsNotNone(vpa.get_runner)

    def test_a_tenant_that_did_not_enable_the_plugin_never_triggers_the_reload(self):
        vpa = self.vpa
        self.assertTrue(vpa._ensure_plugin("t"))
        shutil.rmtree(self.old.parent)
        with mock.patch.object(vpa, "_enabled_for", lambda t: False):
            self.assertFalse(vpa._ensure_plugin("other"))
        self.assertEqual(vpa.PLUGIN_SOURCE, "old")

    def test_cancel_reaches_a_job_of_the_retired_runner(self):
        vpa = self.vpa

        class R:
            def __init__(self, ids):
                self.ids = ids

            def cancel_job(self, job_id):
                return job_id in self.ids

        old, cur = R({"job_00000001"}), R({"job_00000002"})
        vpa._RETIRED_RUNNER_GETTERS.append(lambda: old)
        vpa.get_runner = lambda: cur
        self.assertTrue(vpa._cancel_in_any_runner("job_00000001"))
        self.assertTrue(vpa._cancel_in_any_runner("job_00000002"))
        self.assertFalse(vpa._cancel_in_any_runner("job_00000003"))
        vpa._RETIRED_RUNNER_GETTERS.clear()
        vpa.get_runner = lambda: object()  # a runner that cannot cancel
        self.assertIsNone(vpa._cancel_in_any_runner("job_00000001"))


if __name__ == "__main__":
    unittest.main()

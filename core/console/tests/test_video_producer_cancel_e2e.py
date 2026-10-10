"""HTTP E2E for POST /v1/console/video/jobs/{id}/cancel (ADR-2247, PLAN-0946 R1b).

The real console router (session cookie + CSRF, real gates, two tenants, a temp CORVIN_HOME); the runner is
the plugin's REAL ProcessJobRunner starting the REAL job_main as a child process. Substituted, outside the
code under test: the operator storyboard (no model call), the child's TTS chain (offline mock tier, via the
runner's ``entry``), and - in the hang test - an ``ffmpeg`` on the child's PATH that ignores SIGTERM and
keeps a grandchild alive, so the job is stuck in exactly the call the old code could not leave.

Proof that nothing is left is read from /proc, not from the API's word."""
from __future__ import annotations

import importlib
import os
import stat
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_video_producer_routes_e2e as _routes  # noqa: E402
from test_video_producer_process_e2e import (  # noqa: E402
    STORYBOARD, _alive, _chromium_of, _procs, _wait,
)
from test_video_producer_routes_e2e import _chain_text, _finish_job, _route_module, _sandbox  # noqa: E402
from test_video_producer_styles_e2e import H, V  # noqa: E402


def _use_plugin_src_from_env(mod) -> None:
    """Verification before a plugin change reaches the sibling marketplace checkout: with
    VP_TEST_PLUGIN_SRC=<.../video_producer/src> the route module loads the plugin from there."""
    src = os.environ.get("VP_TEST_PLUGIN_SRC")
    if not src:
        return
    mod._plugin_locations = lambda: [(Path(src), "test-override")]
    for name in [n for n in sys.modules if n == mod._PLUGIN_PKG or n.startswith(mod._PLUGIN_PKG + ".")]:
        sys.modules.pop(name, None)
    mod.VideoJob, mod._get_storage, mod.get_runner, mod.PLUGIN_SOURCE = mod._load_plugin()


def _hanging_ffmpeg_dir(root: Path) -> str:
    d = root / "bin"
    d.mkdir()
    p = d / "ffmpeg"
    p.write_text('#!/bin/sh\ntrap "" TERM\nsleep 600 &\nsleep 600\n')
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return f"{d}{os.pathsep}{os.environ['PATH']}"


@unittest.skipUnless(os.path.isdir("/proc"), "needs /proc (Linux)")
class CancelRouteHttpE2E(unittest.TestCase):
    _base = _routes.VideoProducerRoutesE2E
    setUp, tearDown, _benign_l44 = _base.setUp, _base.tearDown, _base._benign_l44

    def _runner(self, mod, env=None):
        ar = importlib.import_module(f"{mod._PLUGIN_PKG}.async_runner")
        entry = Path(ar.__file__).resolve().parents[1] / "tests" / "_vp_job_main_mock_tts.py"
        if not entry.is_file():
            self.skipTest("the loaded plugin copy has no tests/_vp_job_main_mock_tts.py")
        inner = ar.ProcessJobRunner(entry=[sys.executable, str(entry)], env=env, grace_s=1.0)

        class Injecting:
            running_job_ids = staticmethod(inner.running_job_ids)
            cancel_job = staticmethod(inner.cancel_job)

            async def start_job(self, job_id, task, config):
                return await inner.start_job(job_id, task, dict(config, storyboard=STORYBOARD, tts_engine="auto"))

        mod.get_runner = lambda: Injecting()
        self.addCleanup(lambda: [inner.cancel_job(j) for j in list(inner.running_job_ids())])
        return inner

    def _start(self, client, csrf, task="Explain the quarterly review in two scenes."):
        self._benign_l44()
        r = client.post(f"{V}/jobs", json={"task": task}, headers=H(csrf))
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["job_id"]

    def test_cancel_a_job_hung_in_ffmpeg_ends_cancelled_and_leaves_no_process(self):
        tmp = Path(tempfile.mkdtemp())
        env = dict(os.environ, PATH=_hanging_ffmpeg_dir(tmp))
        with _sandbox(tmp / "sbx", tenants=("tenant_a",)) as (_c, _t, home, clients):
            mod = _route_module()
            _use_plugin_src_from_env(mod)
            client, csrf = clients["tenant_a"]
            inner = self._runner(mod, env)
            job_id = self._start(client, csrf)
            # the job is stuck: the stub ffmpeg and its grandchildren are running in the job's group
            self.assertTrue(_wait(lambda: any(n == "sleep" for _, n in _procs(job_id)), 90), _procs(job_id))
            running = client.get(f"{V}/jobs/{job_id}").json()
            self.assertIn(running["status"], ("pending", "storyboard_generating", "skills_running"))
            n_before = len(_procs(job_id))
            t0 = time.monotonic()
            r = client.post(f"{V}/jobs/{job_id}/cancel", headers=H(csrf))
            elapsed = time.monotonic() - t0
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json(), {"job_id": job_id, "status": "cancelled"})
            self.assertEqual(client.get(f"{V}/jobs/{job_id}").json()["status"], "cancelled")
            self.assertTrue(_wait(lambda: not _procs(job_id), 10), _procs(job_id))
            self.assertTrue(_wait(lambda: not inner.is_job_running(job_id), 10))
            self.assertEqual(list((home / "tenants" / "tenant_a" / "video_producer" / "jobs").glob("*.run")), [])
            # the operator's action is on the tenant's audit chain, ids and enums only
            chain = [ln for ln in _chain_text(home).splitlines() if "video_producer.job_cancelled" in ln]
            self.assertEqual(len(chain), 1, chain)
            self.assertIn(job_id, chain[0])
            self.assertNotIn("quarterly review", chain[0])
            # a second cancel finds a finished job
            self.assertEqual(client.post(f"{V}/jobs/{job_id}/cancel", headers=H(csrf)).status_code, 409)
            print(f"\nMEASURE http cancel of a job hung in ffmpeg: {n_before} processes -> 0, answered 'cancelled' in {elapsed:.2f}s")

    def test_another_tenant_cannot_cancel_it_and_a_missing_csrf_is_refused(self):
        tmp = Path(tempfile.mkdtemp())
        env = dict(os.environ, PATH=_hanging_ffmpeg_dir(tmp))
        with _sandbox(tmp / "sbx", tenants=("tenant_a", "tenant_b")) as (_c, _t, home, clients):
            mod = _route_module()
            _use_plugin_src_from_env(mod)
            (ca, csrf_a), (cb, csrf_b) = clients["tenant_a"], clients["tenant_b"]
            self._runner(mod, env)
            job_id = self._start(ca, csrf_a)
            self.assertTrue(_wait(lambda: any(n == "sleep" for _, n in _procs(job_id)), 90), _procs(job_id))
            # B knows A's job id: for B there is no such job
            self.assertEqual(cb.post(f"{V}/jobs/{job_id}/cancel", headers=H(csrf_b)).status_code, 404)
            self.assertTrue(_procs(job_id), "tenant B's request killed tenant A's job")
            # CSRF is required for the mutation
            self.assertIn(ca.post(f"{V}/jobs/{job_id}/cancel").status_code, (401, 403))
            self.assertTrue(_procs(job_id), "a request without CSRF killed the job")
            # a malformed id is a 404, not a path
            self.assertEqual(ca.post(f"{V}/jobs/..%2f..%2fx/cancel", headers=H(csrf_a)).status_code, 404)
            self.assertEqual(ca.post(f"{V}/jobs/{job_id}/cancel", headers=H(csrf_a)).status_code, 200)
            self.assertTrue(_wait(lambda: not _procs(job_id), 10), _procs(job_id))

    def test_a_finished_job_and_an_unknown_job_cannot_be_cancelled(self):
        with _sandbox(Path(tempfile.mkdtemp()), tenants=("tenant_a",)) as (_c, _t, home, clients):
            mod = _route_module()
            _use_plugin_src_from_env(mod)
            client, csrf = clients["tenant_a"]
            mod.get_runner = lambda: _routes._RecordingRunner()
            self._benign_l44()
            job_id = client.post(f"{V}/jobs", json={"task": "Explain the audit chain."}, headers=H(csrf)).json()["job_id"]
            # the recorder runner has no cancel_job: honest 501, not a fake success
            self.assertEqual(client.post(f"{V}/jobs/{job_id}/cancel", headers=H(csrf)).status_code, 501)
            _finish_job(home, "tenant_a", job_id, "complete")
            r = client.post(f"{V}/jobs/{job_id}/cancel", headers=H(csrf))
            self.assertEqual(r.status_code, 409, r.text)
            self.assertEqual(client.post(f"{V}/jobs/job_deadbeef/cancel", headers=H(csrf)).status_code, 404)

    def test_cancel_mid_render_through_the_route_leaves_no_chromium(self):
        with _sandbox(Path(tempfile.mkdtemp()), tenants=("tenant_a",)) as (_c, _t, home, clients):
            mod = _route_module()
            _use_plugin_src_from_env(mod)
            client, csrf = clients["tenant_a"]
            inner = self._runner(mod)
            job_id = self._start(client, csrf)
            leader = _wait(lambda: getattr(inner._active.get(job_id), "proc", None), 30).pid
            chrome = _wait(lambda: _chromium_of(leader), 120)
            self.assertTrue(chrome, _procs(job_id))
            r = client.post(f"{V}/jobs/{job_id}/cancel", headers=H(csrf))
            self.assertEqual((r.status_code, r.json()["status"]), (200, "cancelled"), r.text)
            self.assertTrue(_wait(lambda: not _procs(job_id), 20), _procs(job_id))
            self.assertTrue(_wait(lambda: not _alive(chrome), 20), _alive(chrome))


if __name__ == "__main__":
    unittest.main()

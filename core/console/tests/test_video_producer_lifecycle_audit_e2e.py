"""HTTP E2E for the host-side Video Producer job lifecycle audit (ADR-2247 R2b, T-0090).

Driven through the REAL console router (TestClient, session cookie + CSRF, two tenants, a temp
CORVIN_HOME). Real: the route handlers, the plugin's ProcessJobRunner (one OS process per job, its
reaper, its cancel), the plugin's storage and ProductionReport, the host's watcher, and the tenant's
hash-chained audit file (verified with forge.security_events.verify_chain). Replaced: only the job
process itself - ``_vp_lifecycle_stub_job.py`` stands in for job_main (a real job calls an LLM, a TTS
provider and ffmpeg), writing its record and report through the plugin's own code.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_video_producer_routes_e2e import _route_module, _sandbox  # noqa: E402

_HERE = Path(__file__).resolve().parent
_STUB = _HERE / "_vp_lifecycle_stub_job.py"
_DRIVER = _HERE / "_vp_lifecycle_console_driver.py"
_PKG = "corvin_video_producer_plugin"


def _chain(home: Path, tenant: str) -> Path:
    return home / "tenants" / tenant / "global" / "forge" / "audit.jsonl"


def _records(home: Path, tenant: str) -> list:
    p = _chain(home, tenant)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def _lifecycle(home: Path, tenant: str, job_id: str = None) -> list:
    out = []
    for r in _records(home, tenant):
        ev = r.get("event") or r.get("event_type") or r.get("type") or ""
        if not str(ev).startswith("video_producer.job_"):
            continue
        d = r.get("details") or {}
        if job_id is None or d.get("job_id") == job_id:
            out.append((ev, d, r))
    return out


def _wait(pred, timeout=25.0, what="condition"):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = pred()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


def _verify(testcase, home: Path, tenant: str) -> None:
    import forge.security_events as se  # type: ignore[import-not-found]

    ok, problems = se.verify_chain(_chain(home, tenant))
    testcase.assertTrue(ok, problems)
    recs = _records(home, tenant)
    for prev, cur in zip(recs, recs[1:]):
        testcase.assertEqual(cur.get("prev_hash"), prev.get("hash"), "prev_hash must link to the previous record")


def _procs_with(token: str) -> list:
    pids = []
    for d in Path("/proc").iterdir():
        if d.name.isdigit():
            try:
                if token in (d / "cmdline").read_bytes().decode("utf-8", "replace"):
                    pids.append(int(d.name))
            except OSError:
                pass
    return pids


class VideoProducerLifecycleAuditE2E(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())
        self._restore: list = []
        self._runners: list = []

    def tearDown(self):
        for r in self._runners:
            try:
                r.shutdown(wait=False)
            except Exception:  # noqa: BLE001
                pass
        for obj, attr, orig in reversed(self._restore):
            setattr(obj, attr, orig)

    def _benign_l44(self):
        import house_rules as _hr  # type: ignore
        self._restore.append((_hr, "_house_rules_classifier", _hr._house_rules_classifier))
        _hr._house_rules_classifier = lambda task, rules, auth, **kw: ("", 0.99, "benign (test stub)")

    def _process_runner(self, mod):
        runner_mod = sys.modules[f"{_PKG}.async_runner"]
        runner = runner_mod.ProcessJobRunner(entry=[sys.executable, str(_STUB)])
        self._runners.append(runner)
        mod.get_runner = lambda: runner
        return runner

    def _post(self, client, csrf, task):
        return client.post("/v1/console/video/jobs", json={"task": task}, headers={"X-CSRF-Token": csrf})

    # ── started / completed / degraded; two simultaneous jobs of one tenant ──────────────────
    def test_two_simultaneous_jobs_are_audited_with_independent_reports(self):
        with _sandbox(self._tmp, tenants=("tenant_a", "tenant_b")) as (_c, _t, home, clients):
            mod = _route_module()
            self._benign_l44()
            self._process_runner(mod)
            (ca, csrf_a) = clients["tenant_a"]
            tasks = ["Explain the audit chain in two scenes [pair] [complete]",
                     "Explain the consent gate in three scenes [pair] [degraded]"]
            results: dict = {}

            def post(i):
                results[i] = self._post(ca, csrf_a, tasks[i])

            threads = [threading.Thread(target=post, args=(i,)) for i in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            for i in range(2):
                self.assertEqual(results[i].status_code, 200, results[i].text)
            clean, degraded = results[0].json()["job_id"], results[1].json()["job_id"]

            _wait(lambda: len([e for e in _lifecycle(home, "tenant_a") if e[0] == "video_producer.job_completed"]) == 2,
                  what="two job_completed records")
            _wait(lambda: any(e[0] == "video_producer.job_degraded" for e in _lifecycle(home, "tenant_a", degraded)),
                  what="job_degraded")
            time.sleep(0.5)  # nothing more may arrive

            for jid in (clean, degraded):
                evs = [e[0] for e in _lifecycle(home, "tenant_a", jid)]
                self.assertEqual(evs.count("video_producer.job_started"), 1, evs)
                self.assertEqual(evs.count("video_producer.job_completed"), 1, evs)
                self.assertEqual(evs[0], "video_producer.job_started")
                for _ev, d, _r in _lifecycle(home, "tenant_a", jid):
                    self.assertEqual(d.get("tenant_id"), "tenant_a")
                    self.assertNotIn("_dropped_fields", d, d)
            self.assertEqual([e[0] for e in _lifecycle(home, "tenant_a", clean)].count("video_producer.job_degraded"), 0)

            done = {e[1]["job_id"]: e[1] for e in _lifecycle(home, "tenant_a") if e[0] == "video_producer.job_completed"}
            self.assertEqual(done[clean]["scene_count"], 2)
            self.assertEqual(done[clean]["degradation_count"], 0)
            self.assertEqual(done[degraded]["scene_count"], 3)
            self.assertEqual((done[degraded]["web_scenes"], done[degraded]["pillow_scenes"]), (2, 1))
            self.assertEqual(done[degraded]["degradation_count"], 1)
            self.assertEqual(done[degraded]["tts_tiers"], "openai")
            self.assertEqual(done[degraded]["observed_by"], "watcher")
            deg = [e[1] for e in _lifecycle(home, "tenant_a", degraded) if e[0] == "video_producer.job_degraded"][0]
            self.assertEqual((deg["degradation_count"], deg["degradation_kinds"]), (1, "web_to_pillow"))

            # the two jobs ran at the same time and kept independent reports in an intact store
            base = home / "tenants" / "tenant_a" / "video_producer"
            for jid, n in ((clean, 0), (degraded, 1)):
                rep = json.loads((base / "videos" / jid / "report.json").read_text())
                self.assertEqual(rep["job_id"], jid)
                self.assertEqual(len(rep["degradations"]), n)
            for f in (base / "jobs").glob("*.json"):
                self.assertEqual(json.loads(f.read_text())["status"], "complete", f.name)
            self.assertEqual(list((base / "jobs").glob("*.run")), [])
            self.assertEqual(list((base / "jobs").glob("*.lock")), [])

            # no task text on the chain, nothing in the other tenant's chain, the chain verifies
            text = _chain(home, "tenant_a").read_text()
            for t in tasks:
                self.assertNotIn(t[:24], text)
            self.assertEqual(_lifecycle(home, "tenant_b"), [])
            # cross-tenant: tenant_b cannot see or cancel tenant_a's job, and writes no record for it
            (cb, csrf_b) = clients["tenant_b"]
            self.assertEqual(cb.get(f"/v1/console/video/jobs/{clean}").status_code, 404)
            r = cb.post(f"/v1/console/video/jobs/{clean}/cancel", headers={"X-CSRF-Token": csrf_b})
            self.assertEqual(r.status_code, 404, r.text)
            self.assertEqual(_lifecycle(home, "tenant_b"), [])
            self.assertEqual(len(_lifecycle(home, "tenant_a", clean)), 2)  # started + completed, nothing added
            _verify(self, home, "tenant_a")

    # ── cancelled (one record, by the route) and child_exit ───────────────────────────────────
    def test_cancel_and_child_exit_each_yield_exactly_one_terminal_record(self):
        with _sandbox(self._tmp, tenants=("tenant_a",)) as (client, csrf, home, _clients):
            mod = _route_module()
            self._benign_l44()
            self._process_runner(mod)
            hang = self._post(client, csrf, "Explain the path gate slowly [hang]").json()["job_id"]
            die = self._post(client, csrf, "Explain the erasure layer [die]").json()["job_id"]
            base = home / "tenants" / "tenant_a" / "video_producer"
            _wait(lambda: (base / "stub_marks" / hang).exists(), what="the hang job to run")

            r = client.post(f"/v1/console/video/jobs/{hang}/cancel", headers={"X-CSRF-Token": csrf})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["status"], "cancelled")
            _wait(lambda: any(e[0] == "video_producer.job_failed" for e in _lifecycle(home, "tenant_a", die)),
                  what="job_failed for the dying job")
            time.sleep(1.0)  # the watcher has seen the cancelled record by now; it must not write again

            hang_end = [e for e in _lifecycle(home, "tenant_a", hang) if e[0] != "video_producer.job_started"]
            self.assertEqual([e[0] for e in hang_end], ["video_producer.job_cancelled"], hang_end)
            self.assertEqual(hang_end[0][1]["reason"], "cancelled")
            # whoever saw the end first wrote it - the route (outcome) or the watcher - never both
            self.assertIn(hang_end[0][1]["observed_by"], ("route", "watcher"))
            die_end = [e for e in _lifecycle(home, "tenant_a", die) if e[0] != "video_producer.job_started"]
            self.assertEqual([e[0] for e in die_end], ["video_producer.job_failed"], die_end)
            self.assertEqual(die_end[0][1]["reason"], "child_exit")
            self.assertEqual(_procs_with(hang), [], "the cancelled job left a process")
            _verify(self, home, "tenant_a")

    # ── a console killed mid-job: exactly one job_failed reason=interrupted at the next start ──
    def test_console_killed_mid_job_yields_one_interrupted_record_at_next_start(self):
        with _sandbox(self._tmp, tenants=("tenant_a",)) as (client, _csrf, home, _clients):
            mod = _route_module()
            src = Path(sys.modules[_PKG].__path__[0])
            base = home / "tenants" / "tenant_a" / "video_producer"
            job_id = "job_c0ffee01"
            drv = subprocess.Popen([sys.executable, str(_DRIVER), str(src), str(base), job_id],
                                   stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(drv.stdout.readline().strip(), "READY")
                _wait(lambda: json.loads((base / "jobs" / f"{job_id}.json").read_text())["status"] == "skills_running",
                      what="the job to be mid-flight")
                os.kill(drv.pid, signal.SIGKILL)  # the console dies; its job process is left behind
                drv.wait(timeout=10)

                # the next console start: a fresh process has no store cached
                sys.modules[f"{_PKG}.storage"].reset_storage()
                self.assertEqual(mod.audit_interrupted_jobs(), 1)
                self.assertEqual(mod.audit_interrupted_jobs(), 0)      # a second pass in the same process
                sys.modules[f"{_PKG}.storage"].reset_storage()
                self.assertEqual(mod.audit_interrupted_jobs(), 0)      # and in "another" start: already closed

                r = client.get(f"/v1/console/video/jobs/{job_id}")
                self.assertEqual(r.status_code, 200, r.text)
                self.assertEqual(r.json()["status"], "error")
                evs = _lifecycle(home, "tenant_a", job_id)
                self.assertEqual([e[0] for e in evs], ["video_producer.job_failed"], evs)
                self.assertEqual((evs[0][1]["reason"], evs[0][1]["observed_by"], evs[0][1]["tenant_id"]),
                                 ("interrupted", "boot", "tenant_a"))
                _verify(self, home, "tenant_a")
            finally:
                if drv.poll() is None:
                    drv.kill()
                for pid in _procs_with(job_id):
                    try:
                        os.killpg(pid, signal.SIGKILL)
                    except OSError:
                        pass

    # ── best-effort: a chain write that fails is counted; the video is still produced ──────────
    def test_failed_chain_write_is_counted_and_the_video_is_still_produced(self):
        with _sandbox(self._tmp, tenants=("tenant_a",)) as (client, csrf, home, _clients):
            mod = _route_module()
            self._benign_l44()
            self._process_runner(mod)
            import forge.security_events as se  # type: ignore[import-not-found]

            real = se.write_event

            def failing(path, event_type, **kw):
                if str(event_type).startswith("video_producer.job_"):
                    raise OSError("disk full (test)")
                return real(path, event_type, **kw)

            before = client.get("/v1/console/video/overview").json()["lifecycle_audit_failures"]
            with mock.patch.object(se, "write_event", failing):
                r = self._post(client, csrf, "Explain the audit chain [complete]")
                self.assertEqual(r.status_code, 200, r.text)
                job_id = r.json()["job_id"]
                _wait(lambda: client.get(f"/v1/console/video/jobs/{job_id}").json()["status"] == "complete",
                      what="the job to complete")
                _wait(lambda: client.get("/v1/console/video/overview").json()["lifecycle_audit_failures"] >= before + 2,
                      what="two counted lifecycle failures")
            self.assertEqual(_lifecycle(home, "tenant_a", job_id), [])
            self.assertTrue((home / "tenants" / "tenant_a" / "video_producer" / "videos" / job_id / "report.json").exists())
            _verify(self, home, "tenant_a")


class VideoProducerBootAuditCallSites(unittest.TestCase):
    """Both shipped hosts start the boot audit (a function nothing calls would be dead)."""

    def test_both_hosts_call_start_boot_audit(self):
        root = Path(__file__).resolve().parents[3]
        for rel in ("core/gateway/corvin_gateway/app.py", "core/console/corvin_console/standalone.py"):
            text = (root / rel).read_text()
            self.assertIn("video_producer_api as _vpa", text, rel)
            self.assertIn("_vpa.start_boot_audit()", text, rel)


if __name__ == "__main__":
    unittest.main()

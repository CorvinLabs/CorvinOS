"""Playwright's Chromium is never installed from a GET; the install is an explicit operator action (PLG-11).

Until 2026-10-21 ``_web_slides_available()`` - called by every ``GET /video/settings`` - started a
``playwright install`` thread (a ~150 MB download; offline an 1800 s subprocess per console restart) on any
host missing the build. Now the probe only reports ``web_slides: {available, reason}`` and
``POST /v1/console/video/web-slides/install`` (session + CSRF, audited, network pre-check) starts it.

Real: the console router, the audit chain under the sandbox home. Replaced: the package manager subprocess
and the network pre-check.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_video_producer_routes_e2e import VideoProducerRoutesE2E, _route_module, _sandbox  # noqa: E402

WANTED = ["chromium_headless_shell-1248", "chromium-1248"]
_URL = "/v1/console/video/web-slides/install"


def _events(home: Path, prefix: str = "video_producer.web_slides_install") -> list:
    out = []
    for f in home.rglob("audit.jsonl"):
        for line in f.read_text().splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if str(rec.get("event_type", "")).startswith(prefix):
                out.append((rec["event_type"], rec.get("details") or {}))
    return out


class WebSlidesInstallE2E(unittest.TestCase):
    setUp, tearDown = VideoProducerRoutesE2E.setUp, VideoProducerRoutesE2E.tearDown

    def _host(self, browsers: Path, *, package: bool = True, reachable: bool = True):
        """A host with the playwright package and ``browsers`` as its browser dir; the download host
        reachable or not; the package manager replaced by a recorder."""
        mod = _route_module()
        ran: list = []
        real_find = mod.importlib.util.find_spec

        def run(cmd, **kw):
            ran.append(cmd)
            (browsers / WANTED[0]).mkdir(parents=True, exist_ok=True)   # what the real install leaves behind
            return subprocess.CompletedProcess(cmd, 0, b"", b"")

        patches = [
            mock.patch.dict(mod.os.environ, {"PLAYWRIGHT_BROWSERS_PATH": str(browsers)}),
            mock.patch.object(mod.importlib.util, "find_spec",
                              lambda n, *a, **k: ((object() if package else None) if n == "playwright"
                                                  else real_find(n, *a, **k))),
            mock.patch.object(mod, "_playwright_chromium_dirs", lambda: list(WANTED)),
            mock.patch.object(mod, "_download_host_reachable", lambda: reachable),
            mock.patch.object(mod.subprocess, "run", run),
            mock.patch.object(mod, "_chromium_install", dict(mod._chromium_install, running=False)),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return ran

    @staticmethod
    def _wait_idle(mod, timeout: float = 5.0) -> None:
        end = time.monotonic() + timeout
        while mod._chromium_install.get("running") and time.monotonic() < end:
            time.sleep(0.02)

    def test_a_get_reports_the_missing_browser_and_installs_nothing(self):
        with _sandbox(self._tmp) as (client, _csrf, home, _c):
            ran = self._host(self._tmp / "browsers")
            st = client.get("/v1/console/video/settings").json()
            self.assertEqual(st["web_slides"], {"available": False, "reason": "browser_missing"})
            self.assertFalse(st["web_slides_available"])
            self.assertEqual(ran, [], "a GET started the Chromium download")
            self.assertEqual(_events(home), [])
            self.assertNotIn("vp-chromium-install", {t.name for t in threading.enumerate()})

    def test_the_install_is_an_explicit_audited_operator_action(self):
        with _sandbox(self._tmp) as (client, csrf, home, _c):
            mod = _route_module()
            ran = self._host(self._tmp / "browsers")
            self.assertEqual(client.post(_URL).status_code, 403, "CSRF gate")   # no token: refused
            r = client.post(_URL, headers={"X-CSRF-Token": csrf})
            self.assertEqual(r.status_code, 202, r.text)
            self.assertEqual(r.json()["status"], "started")
            self._wait_idle(mod)
            self.assertEqual(len(ran), 1)
            self.assertEqual(ran[0][1:], ["-m", "playwright", "install", "chromium", "chromium-headless-shell"])
            st = client.get("/v1/console/video/settings").json()
            self.assertEqual(st["web_slides"], {"available": True, "reason": None})
            names = [e for e, _ in _events(home)]
            self.assertEqual(names, ["video_producer.web_slides_install_requested",
                                     "video_producer.web_slides_install_finished"])
            details = dict(_events(home))
            self.assertEqual(details["video_producer.web_slides_install_requested"]["outcome"], "started")
            self.assertEqual(details["video_producer.web_slides_install_finished"]["outcome"], "ok")
            self.assertEqual(details["video_producer.web_slides_install_finished"]["tenant_id"], "_default")
            # done: a second request has nothing to do and starts nothing
            r = client.post(_URL, headers={"X-CSRF-Token": csrf})
            self.assertEqual((r.status_code, r.json()["status"]), (200, "already_available"))
            self.assertEqual(len(ran), 1)

    def test_offline_the_install_is_refused_before_any_subprocess(self):
        with _sandbox(self._tmp) as (client, csrf, home, _c):
            ran = self._host(self._tmp / "browsers", reachable=False)
            r = client.post(_URL, headers={"X-CSRF-Token": csrf})
            self.assertEqual(r.status_code, 503, r.text)
            self.assertIn("reachable", r.text)
            self.assertEqual(ran, [])
            self.assertEqual([(e, d["outcome"]) for e, d in _events(home)],
                             [("video_producer.web_slides_install_requested", "refused_offline")])
            self.assertEqual(client.get("/v1/console/video/settings").json()["web_slides"],
                             {"available": False, "reason": "browser_missing"})

    def test_a_running_install_is_reported_and_not_started_twice(self):
        with _sandbox(self._tmp) as (client, csrf, home, _c):
            mod = _route_module()
            ran = self._host(self._tmp / "browsers")
            gate = threading.Event()

            def slow_run(cmd, **kw):
                ran.append(cmd)
                gate.wait(5)
                return subprocess.CompletedProcess(cmd, 0, b"", b"")

            with mock.patch.object(mod.subprocess, "run", slow_run):
                self.assertEqual(client.post(_URL, headers={"X-CSRF-Token": csrf}).status_code, 202)
                st = client.get("/v1/console/video/settings").json()
                self.assertEqual(st["web_slides"], {"available": False, "reason": "install_running"})
                r = client.post(_URL, headers={"X-CSRF-Token": csrf})
                self.assertEqual((r.status_code, r.json()["status"]), (202, "running"))
                gate.set()
                self._wait_idle(mod)
            self.assertEqual(len(ran), 1)
            outcomes = [d["outcome"] for _, d in _events(home)]
            self.assertEqual(outcomes, ["started", "already_running", "ok"])

    def test_a_failed_install_is_recorded_and_can_be_retried(self):
        with _sandbox(self._tmp) as (client, csrf, home, _c):
            mod = _route_module()
            self._host(self._tmp / "browsers")

            def failing(cmd, **kw):
                raise subprocess.CalledProcessError(1, cmd)

            with mock.patch.object(mod.subprocess, "run", failing):
                self.assertEqual(client.post(_URL, headers={"X-CSRF-Token": csrf}).status_code, 202)
                self._wait_idle(mod)
            fin = [d for e, d in _events(home) if e.endswith("finished")]
            self.assertEqual((fin[0]["outcome"], fin[0]["error_class"]), ("failed", "CalledProcessError"))
            self.assertEqual(client.get("/v1/console/video/settings").json()["web_slides"],
                             {"available": False, "reason": "browser_missing"})
            self.assertEqual(client.post(_URL, headers={"X-CSRF-Token": csrf}).status_code, 202)

    def test_without_the_package_there_is_nothing_to_install(self):
        with _sandbox(self._tmp) as (client, csrf, home, _c):
            ran = self._host(self._tmp / "browsers", package=False)
            self.assertEqual(client.get("/v1/console/video/settings").json()["web_slides"],
                             {"available": False, "reason": "package_missing"})
            r = client.post(_URL, headers={"X-CSRF-Token": csrf})
            self.assertEqual(r.status_code, 409, r.text)
            self.assertEqual(ran, [])
            self.assertEqual([d["outcome"] for _, d in _events(home)], ["package_missing"])


if __name__ == "__main__":
    unittest.main()

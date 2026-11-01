"""HTTP E2E proof: CorvinTV cinematic mode is reachable from the console's job endpoint.

Same harness as test_video_producer_routes_e2e (real console router, real session + CSRF, the real L44/L34/L35
gates; only the production runner is a recorder). The job's own pipeline (real job_main, Chromium, ffmpeg) is proven in
the plugin's tests/test_cinematic_job_e2e.py; this file proves that a request with ``mode: "cinematic"`` hands exactly
that mode, the length, the languages and the user's designs to the runner, and that a plain request is unchanged.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_video_producer_routes_e2e as _routes  # noqa: E402  (module import: pytest must not collect its TestCase again)
from test_video_producer_routes_e2e import _RecordingRunner, _route_module, _sandbox  # noqa: E402

H = lambda csrf: {"X-CSRF-Token": csrf}  # noqa: E731
SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="200" viewBox="0 0 400 200"><rect width="400" height="200" fill="#fff"/></svg>'


class CinematicModeE2E(unittest.TestCase):
    _base = _routes.VideoProducerRoutesE2E
    setUp, tearDown, _benign_l44 = _base.setUp, _base.tearDown, _base._benign_l44

    def _post(self, client, csrf, body):
        self._benign_l44()
        return client.post("/v1/console/video/jobs", json=body, headers=H(csrf))

    def test_a_cinematic_request_reaches_the_runner_with_its_mode_length_languages_and_designs(self):
        with _sandbox(self._tmp) as (client, csrf, home, _clients):
            mod = _route_module()
            runner = _RecordingRunner()
            mod.get_runner = lambda: runner
            r = self._post(client, csrf, {"task": "Show how our product works.", "mode": "cinematic", "cinematic_minutes": 3,
                                          "cinematic_langs": ["de", "en"], "designs": [{"name": "home", "svg": SVG}]})
            self.assertEqual(r.status_code, 200, r.text)
            _job, _task, cfg = runner.calls[-1]
            self.assertEqual(cfg["mode"], "cinematic")
            self.assertEqual(cfg["cinematic_minutes"], 3)
            self.assertEqual(cfg["cinematic_langs"], ["de", "en"])
            self.assertEqual(cfg["cinematic_designs"], {"home": SVG})

    def test_a_plain_request_carries_no_cinematic_key(self):
        with _sandbox(self._tmp) as (client, csrf, home, _clients):
            mod = _route_module()
            runner = _RecordingRunner()
            mod.get_runner = lambda: runner
            r = self._post(client, csrf, {"task": "Explain caching."})
            self.assertEqual(r.status_code, 200, r.text)
            cfg = runner.calls[-1][2]
            self.assertFalse([k for k in cfg if k == "mode" or k.startswith("cinematic")], cfg)

    def test_a_film_is_never_a_revision_and_no_job_is_left_behind(self):
        with _sandbox(self._tmp) as (client, csrf, home, _clients):
            mod = _route_module()
            runner = _RecordingRunner()
            mod.get_runner = lambda: runner
            before = client.get("/v1/console/video/jobs").json()["total"]
            r = self._post(client, csrf, {"task": "change it", "mode": "cinematic", "base_job_id": "job_aaaaaaaa"})
            self.assertEqual(r.status_code, 409, r.text)
            self.assertEqual(runner.calls, [])
            self.assertEqual(client.get("/v1/console/video/jobs").json()["total"], before)

    def test_bad_designs_and_modes_are_refused_before_a_job_exists(self):
        with _sandbox(self._tmp) as (client, csrf, home, _clients):
            mod = _route_module()
            runner = _RecordingRunner()
            mod.get_runner = lambda: runner
            bad = [
                {"task": "x", "mode": "movie"},
                {"task": "x", "mode": "cinematic", "cinematic_minutes": 99},
                {"task": "x", "mode": "cinematic", "cinematic_langs": ["de", "en", "fr"]},
                {"task": "x", "mode": "cinematic", "designs": [{"name": "../evil", "svg": SVG}]},
                {"task": "x", "mode": "cinematic", "designs": [{"name": "a", "svg": ""}]},
                {"task": "x", "mode": "cinematic", "designs": [{"name": f"d{i}", "svg": SVG} for i in range(13)]},
            ]
            for body in bad:
                self.assertEqual(self._post(client, csrf, body).status_code, 422, body)
            self.assertEqual(runner.calls, [])


if __name__ == "__main__":
    unittest.main()

"""HTTP E2E: GET /video/jobs/{id}/quality-metrics merges the production report with ffprobe facts (T-0091).

Driven through the REAL console router (TestClient, real session cookie + CSRF, two tenants, a temp
CORVIN_HOME) - the same harness as test_video_producer_routes_e2e. The report is the one a REAL job wrote
(``fixtures/video_producer/report_real_job_2026-10-11.json``: the plugin's ``tests/test_report_e2e.py``
run on 2026-10-11 - real job process, Chromium, ffmpeg, mock TTS); only its ``job_id`` is remapped to the
job this test creates. The video is a real MP4 encoded by ffmpeg, so the ffprobe half is real too.

Covered: the merge (signals with value, threshold, verdict, basis), ``timing``/``runtime`` gone and
``plan_estimate_error`` present as information, an old job without a report says ``missing``, a refused
write says ``not_written``, a broken / foreign / symlinked report is ``unreadable`` and never read, and a
second tenant cannot read the first tenant's job (404) - nor can an anonymous caller (401).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_video_producer_routes_e2e as _routes  # noqa: E402
from test_video_producer_revision_sources_e2e import _complete_job_with_storyboard  # noqa: E402
from test_video_producer_routes_e2e import _route_module, _sandbox  # noqa: E402

V = "/v1/console/video"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "video_producer" / "report_real_job_2026-10-11.json"


def _store(home: Path, tenant: str):
    return _route_module()._get_storage(str(home / "tenants" / tenant / "video_producer"))


def _encode_mp4(path: Path) -> bool:
    """A real 2-second H.264/AAC MP4 (ffprobe needs something real to measure)."""
    if not shutil.which("ffmpeg"):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30:duration=2",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-shortest", str(path)], capture_output=True, timeout=60, check=False)
    return r.returncode == 0 and path.is_file()


def _job_with_output(home: Path, tenant: str, job_id: str, *, metadata: dict, video: bool = True) -> Path:
    """A finished job of ``tenant`` with its stored output record; returns the job directory."""
    _complete_job_with_storyboard(home / "tenants" / tenant, job_id, "Explain the audit chain.")
    storage = _store(home, tenant)
    job_dir = Path(storage.videos_dir) / job_id
    out = job_dir / "output.mp4"
    if video:
        _encode_mp4(out)
    models = sys.modules[_route_module().VideoJob.__module__]
    storage.save_video_output(models.VideoOutput(job_id=job_id, video_path=str(out), metadata=metadata,
                                                 created_at=datetime.now()))
    return job_dir


def _place_report(job_dir: Path, job_id: str) -> dict:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    data["job_id"] = job_id
    (job_dir / "report.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
    return data


class QualityReportE2E(unittest.TestCase):
    setUp, tearDown = _routes.VideoProducerRoutesE2E.setUp, _routes.VideoProducerRoutesE2E.tearDown

    def test_the_report_is_merged_with_ffprobe_and_the_timing_checks_are_gone(self):
        with _sandbox(self._tmp, tenants=("tenant_a", "tenant_b")) as (_c, _t, home, clients):
            (ca, _), (cb, _) = clients["tenant_a"], clients["tenant_b"]
            job_id = "job_9a1b2c3d"
            job_dir = _job_with_output(home, "tenant_a", job_id, metadata={"report": "report.json", "tts_provider_used": "mock"})
            written = _place_report(job_dir, job_id)

            r = ca.get(f"{V}/jobs/{job_id}/quality-metrics")
            self.assertEqual(r.status_code, 200, r.text)
            q = r.json()
            rep = q["report"]
            self.assertEqual(rep["state"], "present")
            self.assertEqual((rep["schema"], rep["status"], rep["plugin_version"]), (1, "complete", written["plugin_version"]))
            sig = {s["id"]: s for s in rep["signals"]}
            # every signal the job wrote reaches the panel, as written - nothing recomputed, nothing added
            self.assertEqual(list(sig), [s["id"] for s in written["signals"]])
            for s in written["signals"]:
                got = sig[s["id"]]
                self.assertEqual((got["value"], got["verdict"], got["basis"], got["valid"], got["unit"]),
                                 (s["value"], s["verdict"], s["basis"], s["valid"], s["unit"]), s["id"])
            dr = sig["degradation_rate"]
            self.assertEqual((dr["verdict"], dr["basis"], dr["threshold"]["max_share"]), ("fail", {"n": 2, "of": 2}, 0.1))
            self.assertEqual((sig["layout_clean"]["valid"], sig["layout_clean"]["invalidated_by"]), (False, "degradation_rate"))
            pe = sig["plan_estimate_error"]
            self.assertEqual((pe["verdict"], pe["threshold"]), ("info", None))
            self.assertEqual(pe["detail"], {"narrated_s": 9.2, "plan_s": 16.0})
            self.assertEqual(rep["degraded_scenes"], {"n": 2, "of": 2})
            self.assertEqual(rep["degradations_by_kind"], {"tts_tier_below": 2})
            self.assertEqual(sum(rep["verdicts"].values()), len(written["signals"]))
            # the estimate-vs-voice checks are gone; the other checks still run
            ids = {c["id"] for c in q["checks"]}
            self.assertFalse(ids & {"timing", "runtime"}, ids)
            self.assertIn("voice", ids)
            if shutil.which("ffprobe") and (job_dir / "output.mp4").is_file():
                self.assertEqual((q["video"]["codec"], q["video"]["width"], q["video"]["height"]), ("h264", 1280, 720))
                self.assertGreater(q["container"]["duration_s"], 1.5)
                self.assertEqual(q["checks"][0], {"id": "playable", "label": "Container readable", "status": "pass",
                                                  "detail": q["checks"][0]["detail"]})

            # tenant isolation: tenant B asking for tenant A's job id gets nothing
            rb = cb.get(f"{V}/jobs/{job_id}/quality-metrics")
            self.assertEqual(rb.status_code, 404, rb.text)
            self.assertNotIn("degradation_rate", rb.text)
            # and tenant B's own job of the same id does not see A's report
            _job_with_output(home, "tenant_b", job_id, metadata={}, video=False)
            rb2 = cb.get(f"{V}/jobs/{job_id}/quality-metrics")
            self.assertEqual(rb2.status_code, 200, rb2.text)
            self.assertEqual(rb2.json()["report"], {"state": "missing", "signals": []})

            # no session, no data
            anon = ca.__class__(ca.app, raise_server_exceptions=False)
            self.assertEqual(anon.get(f"{V}/jobs/{job_id}/quality-metrics").status_code, 401)

    def test_old_refused_and_broken_reports_are_named_never_filled_in(self):
        with _sandbox(self._tmp, tenants=("tenant_a",)) as (ca, _csrf, home, _clients):
            def report(job_id):
                r = ca.get(f"{V}/jobs/{job_id}/quality-metrics")
                self.assertEqual(r.status_code, 200, r.text)
                return r.json()["report"]

            # a job produced before reports existed: no file, no `report` key in its metadata
            _job_with_output(home, "tenant_a", "job_00000001", metadata={"tts_provider_used": "openai"}, video=False)
            self.assertEqual(report("job_00000001"), {"state": "missing", "signals": []})
            # the job ran with reports but its report was refused on write
            _job_with_output(home, "tenant_a", "job_00000002", metadata={"report": None}, video=False)
            self.assertEqual(report("job_00000002"), {"state": "not_written", "signals": []})
            # not JSON
            d3 = _job_with_output(home, "tenant_a", "job_00000003", metadata={"report": "report.json"}, video=False)
            (d3 / "report.json").write_text("{not json", encoding="utf-8")
            self.assertEqual(report("job_00000003"), {"state": "unreadable", "reason": "not_json", "signals": []})
            # another job's report copied in
            d4 = _job_with_output(home, "tenant_a", "job_00000004", metadata={"report": "report.json"}, video=False)
            _place_report(d4, "job_somebody")
            self.assertEqual(report("job_00000004")["reason"], "other_job")
            # a symlink to a report outside the store is never followed
            outside = Path(tempfile.mkdtemp()) / "report.json"
            outside.write_text(json.dumps({**json.loads(FIXTURE.read_text()), "job_id": "job_00000005"}))
            d5 = _job_with_output(home, "tenant_a", "job_00000005", metadata={"report": "report.json"}, video=False)
            (d5 / "report.json").symlink_to(outside)
            self.assertEqual(report("job_00000005"), {"state": "unreadable", "reason": "unreadable", "signals": []})
            # too large
            d6 = _job_with_output(home, "tenant_a", "job_00000006", metadata={"report": "report.json"}, video=False)
            (d6 / "report.json").write_text(" " * (64 * 1024 + 1))
            self.assertEqual(report("job_00000006")["reason"], "too_large")
            # an unknown verdict is shown as unknown, never as a pass
            d7 = _job_with_output(home, "tenant_a", "job_00000007", metadata={"report": "report.json"}, video=False)
            data = _place_report(d7, "job_00000007")
            data["signals"][0]["verdict"] = "great"
            (d7 / "report.json").write_text(json.dumps(data))
            rep = report("job_00000007")
            self.assertEqual(rep["signals"][0]["verdict"], "unknown")
            self.assertEqual(rep["verdicts"]["unknown"], 1)


if __name__ == "__main__":
    unittest.main()

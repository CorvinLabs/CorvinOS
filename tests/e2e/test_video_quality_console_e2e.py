"""Video quality is MEASURED from the produced artifacts (2026-09-20).

Over HTTP against the real router with a fake storage that returns a job
whose output is a small MP4 rendered by ffmpeg in the test (2 s, 1280×720,
30 fps, with an audio track), an SRT with a repeated cue, two scene clips,
a storyboard that planned 1 s per scene. Until 2026-09-20 the endpoint
returned one hard-coded record (three scenes, "h264 7200k") for any id.

1. the stream facts come from ffprobe, the checklist from those facts, the
   score names its denominator, the repeated caption is flagged;
2. a job without output measures nothing and says so (skips, not passes);
3. the overview counts produced videos and averages only measured ones;
4. the plugin loader now finds the sibling marketplace checkout.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg/ffprobe not installed")


@dataclass
class _Job:
    id: str
    task: str
    status: str
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    video_output_path: Optional[str] = None
    storyboard: Optional[str] = None
    percent: int = 100
    current_step: Optional[str] = None
    current_scene: Optional[int] = None
    total_scenes: Optional[int] = None


@dataclass
class _Output:
    job_id: str
    video_path: str
    srt_path: Optional[str]
    thumbnail_path: Optional[str] = None
    metadata: dict = field(default_factory=dict)


class _Storage:
    def __init__(self, jobs, outputs):
        self._jobs, self._outputs = {j.id: j for j in jobs}, outputs

    def get_job(self, job_id):
        return self._jobs.get(job_id)

    def list_jobs(self, limit=20, offset=0):
        return list(self._jobs.values())[offset:offset + limit]

    def get_job_count(self):
        return len(self._jobs)

    def get_video_output(self, job_id):
        return self._outputs.get(job_id)


def _render(tmp: Path) -> Path:
    out = tmp / "videos" / "job_test"
    (out / "scenes").mkdir(parents=True)
    ff = shutil.which("ffmpeg")
    for i in (1, 2):
        subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=24000",
                        "-t", "1.5" if i == 1 else "0.5", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out / "scenes" / f"scene_{i:03d}.mp4")], check=True)
        subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=1", "-frames:v", "1", str(out / "scenes" / f"scene_{i:03d}.png")], check=True)
    subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=24000",
                    "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out / "output.mp4")], check=True)
    (out / "output.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n\n2\n00:00:01,000 --> 00:00:02,000\nHello\n")
    return out


@pytest.fixture
def client(tmp_path, monkeypatch):
    from core.console.corvin_console.routes import video_producer_api as vp

    out = _render(tmp_path)
    storyboard = json.dumps({"id": "sb1", "scenes": [{"id": "s1", "kind": "title", "duration_ms": 1000, "narration_text": "Hello there"},
                                                    {"id": "s2", "kind": "narration", "duration_ms": 1000, "narration_text": "Hello again friend"}]})
    t0 = datetime(2026, 9, 20, 10, 0, 0)
    jobs = [
        _Job("job_test", "Say hello", "complete", t0, t0, datetime(2026, 9, 20, 10, 0, 30), video_output_path=str(out / "output.mp4"), storyboard=storyboard, total_scenes=2),
        _Job("job_empty", "Never rendered", "complete", t0, t0, t0, storyboard=storyboard),
        _Job("job_running", "Still rendering", "skills_running", t0, t0, percent=40),
    ]
    outputs = {"job_test": _Output("job_test", str(out / "output.mp4"), str(out / "output.srt"), metadata={"scenes": 2})}
    storage = _Storage(jobs, outputs)
    monkeypatch.setattr(vp, "get_storage", lambda: storage)
    app = FastAPI()
    app.include_router(vp.router, prefix="/v1/console")
    with TestClient(app) as c:
        yield c


def test_quality_is_measured_from_the_artifacts(client):
    r = client.get("/v1/console/video/jobs/job_test/quality-metrics")
    assert r.status_code == 200, r.text
    q = r.json()
    assert q["ffprobe_available"] is True
    assert q["video"]["width"] == 1280 and q["video"]["height"] == 720 and q["video"]["fps"] == 30.0 and q["video"]["codec"] == "h264"
    assert q["audio"]["codec"] == "aac" and q["audio"]["sample_rate_hz"] == 24000
    assert 1.8 <= q["container"]["duration_s"] <= 2.3 and q["container"]["size_bytes"] > 0
    assert q["captions"]["cues"] == 2 and q["captions"]["duplicate_consecutive"] == 1 and q["captions"]["coverage"] >= 0.85
    assert q["summary"] == {"scenes_planned": 2, "scenes_rendered": 2, "planned_s": 2.0, "rendered_s": q["summary"]["rendered_s"], "size_bytes": q["container"]["size_bytes"]}
    scenes = {s["id"]: s for s in q["scenes"]}
    assert scenes["s1"]["planned_s"] == 1.0 and 1.3 <= scenes["s1"]["actual_s"] <= 1.7 and scenes["s1"]["has_slide"] and scenes["s1"]["narration_words"] == 2
    assert scenes["s1"]["drift_pct"] > 25 and scenes["s2"]["drift_pct"] < -25
    checks = {c["id"]: c["status"] for c in q["checks"]}
    assert checks["playable"] == "pass" and checks["resolution"] == "pass" and checks["fps"] == "pass" and checks["audio"] == "pass"
    assert checks["captions_dupes"] == "warn" and checks["timing"] == "fail" and checks["scenes"] == "pass" and checks["voice"] == "warn"
    sc = q["score"]
    assert sc["total"] == len([c for c in q["checks"] if c["status"] != "skip"]) and sc["passed"] + sc["warned"] + sc["failed"] == sc["total"]
    assert sc["share"] == round(sc["passed"] / sc["total"], 3)
    assert q["production"]["seconds"] == 30.0


def test_a_job_without_output_measures_nothing_and_says_so(client):
    q = client.get("/v1/console/video/jobs/job_empty/quality-metrics").json()
    assert q["container"] is None and q["video"] is None and q["captions"] is None
    assert q["source"]["video"] is None
    statuses = {c["status"] for c in q["checks"]}
    assert "pass" not in statuses, q["checks"]
    assert q["score"]["share"] is None or q["score"]["passed"] == 0
    assert client.get("/v1/console/video/jobs/nope/quality-metrics").status_code == 404


def test_overview_counts_and_averages_only_measured_videos(client):
    o = client.get("/v1/console/video/overview").json()
    assert o["jobs_total"] == 3 and o["videos"] == 2 and o["by_status"]["skills_running"] == 1
    assert o["measured_videos"] == 1 and 0 < o["mean_score_share"] < 1
    assert 1.8 <= o["runtime_s"] <= 2.3 and o["size_bytes"] > 0


def test_poster_and_scene_slides_are_served(client):
    assert client.get("/v1/console/video/videos/job_test/poster").headers["content-type"] == "image/png"
    assert client.get("/v1/console/video/videos/job_test/scenes/2/slide").status_code == 200
    assert client.get("/v1/console/video/videos/job_test/scenes/3/slide").status_code == 404
    assert client.get("/v1/console/video/videos/job_empty/poster").status_code == 404


def test_plugin_loader_finds_the_sibling_marketplace_checkout():
    from core.console.corvin_console.routes import video_producer_api as vp

    marketplace = Path(vp._repo_root).parent / "Corvin-Marketplace" / "plugins" / "contributor" / "media" / "video_producer" / "src"
    if not marketplace.is_dir():
        pytest.skip("no sibling marketplace checkout")
    assert vp.PLUGIN_SOURCE is not None and Path(vp.PLUGIN_SOURCE).resolve() == marketplace.resolve()

"""Loop A pipeline — end to end through the real tools (Blender, espeak-ng, ffmpeg).

A two-scene mini concept runs through the real runner CLI against a sandbox
board: TTS (the offline espeak-ng provider — OpenAI is forced off so the test
never spends money or leaves the host), render in time slices that resume,
compose to an MP4 whose streams and duration are checked with ffprobe, and the
human tasks marked blocked. Skipped when a tool is missing.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
import loop_a_pipeline as pipe  # noqa: E402

pytestmark = pytest.mark.skipif(
    not all(shutil.which(t) for t in ("blender", "ffmpeg", "ffprobe", "espeak-ng")),
    reason="needs blender, ffmpeg, ffprobe, espeak-ng")

MINI = """name: mini_chain
fps: 10
render_settings: {samples: 2, resolution: [320, 180], use_denoiser: false}
scenes:
  - name: chain
    template: linked_chain
    template_params: {num_items: 3}
    duration_s: 0.5
    camera_angle: 30
    narration: Blocks link to the block before them.
  - name: marks
    template: checkmark_sequence
    template_params: {num_marks: 2, interval_frames: 3}
    duration_s: 0.5
"""

BOARD = {"version": 1, "initiatives": [{
    "id": "loop-a", "label": "Loop A", "title": "3D PoC",
    "tasks": [{"id": str(i), "title": t, "status": "pending", "progress": 0}
              for i, t in [(1, "Blender setup"), (2, "YAML + TTS"), (3, "Render"), (4, "Compose"),
                           (5, "Learning study"), (6, "Analysis")]]}]}


def _task(home: Path, tid: str) -> dict:
    b = json.loads((home / "tenants/_default/global/initiatives.json").read_text())
    return next(t for t in b["initiatives"][0]["tasks"] if t["id"] == tid)


def _run(home: Path, concept: Path, out: Path, budget: int) -> dict:
    env = {**os.environ, "CORVIN_HOME": str(home), "CORVIN_TENANT_ID": "_default",
           "OPENAI_API_KEY": "", "VOICE_AUDIT_PATH": str(home / "audit.jsonl"),
           "PYTHONPATH": os.pathsep.join([str(REPO / "core/console"), str(REPO / "corvin_operator/forge"),
                                          str(REPO), os.environ.get("PYTHONPATH", "")])}
    p = subprocess.run([sys.executable, str(REPO / "tools/loop_a_pipeline.py"), "--concept", str(concept),
                        "--out", str(out), "--budget-s", str(budget), "--threads", "2"],
                       capture_output=True, text=True, env=env, timeout=900)
    assert p.stdout.strip(), p.stderr[-2000:]
    return json.loads(p.stdout.strip().splitlines()[-1])


def test_mini_concept_runs_to_a_video_on_its_own(tmp_path):
    home = tmp_path / "home"
    (home / "tenants/_default/global").mkdir(parents=True)
    (home / "tenants/_default/global/initiatives.json").write_text(json.dumps(BOARD))
    concept = tmp_path / "mini.yaml"
    concept.write_text(MINI)
    out = tmp_path / "out"

    # Slice 1: zero render budget — TTS completes, rendering is left pending.
    r1 = _run(home, concept, out, budget=0)
    assert r1["state"] == "rendering"
    assert _task(home, "2")["status"] == "done"
    assert "espeak-ng" in _task(home, "2")["note"]
    manifest = json.loads((out / "audio/manifest.json").read_text())
    assert manifest["chain"]["provider"] == "espeak-ng" and manifest["chain"]["duration_s"] > 1
    resolved = pipe.gen.load_concept(out / "concept.resolved.yaml")
    assert resolved["scenes"][0]["duration_s"] >= manifest["chain"]["duration_s"]  # fitted, never cut
    assert resolved["scenes"][1]["duration_s"] == 0.5                             # no narration: unchanged

    # Slice 2: enough budget — render resumes, compose, human tasks blocked.
    r2 = _run(home, concept, out, budget=600)
    assert r2["state"] == "waiting_for_humans", r2
    assert _task(home, "3")["status"] == "done"
    assert _task(home, "4")["status"] == "done"
    video = out / "mini_chain.mp4"
    assert pipe.ffprobe_streams(video) == {"video", "audio"}
    expected = sum(round(s["duration_s"] * 10) for s in resolved["scenes"]) / 10
    assert abs(pipe.ffprobe_duration(video) - expected) < 0.5
    for tid in ("5", "6"):
        assert _task(home, tid)["status"] == "blocked"
    assert "45 human participants" in _task(home, "5")["note"]

    # Slice 3: nothing left — idempotent, nothing re-rendered.
    before = {p: p.stat().st_mtime_ns for p in (out / "frames").rglob("*.exr")}
    r3 = _run(home, concept, out, budget=600)
    assert r3["state"] == "waiting_for_humans"
    assert before == {p: p.stat().st_mtime_ns for p in (out / "frames").rglob("*.exr")}


def test_render_plan_resumes_only_missing_frames(tmp_path):
    concept = pipe.gen.validate_concept({"name": "x", "fps": 10, "scenes": [
        {"name": "a", "template": "linked_chain", "duration_s": 1.0}]})
    d = tmp_path / "frames" / "a"
    d.mkdir(parents=True)
    for f in (1, 2, 3, 7):
        (d / f"frame_{f:04d}.exr").write_bytes(pipe.gen.EXR_MAGIC + b"\0" * 2000)
    (d / "frame_0004.exr").write_bytes(b"garbage")  # corrupt → re-render
    assert pipe.render_plan(concept, tmp_path) == [("a", 4, 6), ("a", 8, 10)]

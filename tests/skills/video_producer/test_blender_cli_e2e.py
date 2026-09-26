"""Reachability / E2E-wiring-proof for the Blender render CLI.

Invokes `python3 -m core.skills.os_skills.video_producer.blender_cli` as a
REAL subprocess -- the actual CLI boundary an operator would use -- rather
than importing `main()` and calling it directly. This is what closes Phase 3
of the video_producer Blender plan: BlenderHeadlessOrchestrator.
render_enhancement() now has a real call site outside its own definition
file and outside any test file.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURE_BLEND = Path(__file__).resolve().parents[2] / "fixtures" / "video_producer" / "simple_scene.blend"
REPO_ROOT = Path(__file__).resolve().parents[3]

pytestmark = pytest.mark.skipif(
    shutil.which("blender") is None,
    reason="blender binary not on PATH",
)


@pytest.mark.timeout(120)
def test_cli_renders_a_real_video_as_a_subprocess(tmp_path, monkeypatch):
    assert FIXTURE_BLEND.exists(), f"fixture missing: {FIXTURE_BLEND}"

    output = tmp_path / "cli_e2e.mp4"
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "corvin_home"))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")

    proc = subprocess.run(
        [
            sys.executable, "-m", "core.skills.os_skills.video_producer.blender_cli",
            "--blend", str(FIXTURE_BLEND),
            "--output", str(output),
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=100,
    )

    assert proc.returncode == 0, f"CLI failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    assert "RENDER OK" in proc.stdout
    assert output.exists(), "CLI reported success but the output file is missing"

    import ffmpeg

    probe = ffmpeg.probe(str(output))
    video_stream = next(s for s in probe["streams"] if s["codec_type"] == "video")
    audio_stream = next((s for s in probe["streams"] if s["codec_type"] == "audio"), None)
    assert video_stream["codec_name"] == "h264"
    assert float(probe["format"]["duration"]) == pytest.approx(2.0, abs=0.2)
    assert audio_stream is not None, "fixture has baked narration but no audio stream in the render"
    assert audio_stream["codec_name"] == "aac"


@pytest.mark.timeout(30)
def test_cli_fails_closed_on_missing_blend_file(tmp_path):
    proc = subprocess.run(
        [
            sys.executable, "-m", "core.skills.os_skills.video_producer.blender_cli",
            "--blend", str(tmp_path / "does_not_exist.blend"),
            "--output", str(tmp_path / "out.mp4"),
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert proc.returncode == 1
    assert "not found" in proc.stderr

"""E2E proof for the CorvinOS demo video: 3D Blender content + real spoken
narration, rendered through the real (fixed) Blender pipeline.

Narration (tests/fixtures/video_producer/corvinos_demo/narration_script.txt)
was synthesized once via real edge-tts (generate_narration.py) and its
REAL measured duration (ffprobe, not a guess) used to calibrate the Blender
scene's frame_end (build_scene.py) -- both committed as fixture artifacts
(narration.mp3, corvinos_demo.blend) so this test renders deterministically
without a network call on every run. To regenerate them from scratch:

    cd tests/fixtures/video_producer/corvinos_demo
    python3 generate_narration.py   # real edge-tts call, needs network
    blender --background --factory-startup --python build_scene.py

This test drives the same blender_cli.py entry point
tests/skills/video_producer/test_blender_cli_e2e.py already proves reachable
-- the assertions here are specific to THIS scene's real content (real
narration duration, real frame count for an 8-module orbiting scene).
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "video_producer" / "corvinos_demo"
BLEND_FILE = FIXTURE_DIR / "corvinos_demo.blend"
DURATION_CONFIG = FIXTURE_DIR / "narration_duration.json"
REPO_ROOT = Path(__file__).resolve().parents[3]

pytestmark = pytest.mark.skipif(
    shutil.which("blender") is None,
    reason="blender binary not on PATH",
)


@pytest.mark.timeout(420)
def test_corvinos_demo_renders_with_real_voice_and_3d_content(tmp_path):
    assert BLEND_FILE.exists(), (
        f"fixture missing: {BLEND_FILE} -- run generate_narration.py + "
        "build_scene.py in tests/fixtures/video_producer/corvinos_demo/ first"
    )

    import json

    config = json.loads(DURATION_CONFIG.read_text())
    expected_duration_s = config["duration_s"]
    expected_frame_end = config["frame_end"]

    output = tmp_path / "corvinos_demo_render.mp4"
    proc = subprocess.run(
        [
            sys.executable, "-m", "core.skills.os_skills.video_producer.blender_cli",
            "--blend", str(BLEND_FILE),
            "--output", str(output),
            "--bitrate-kbps", "2500",
            "--timeout-sec", "400",
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=410,
    )

    assert proc.returncode == 0, f"CLI failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    assert output.exists(), "CLI reported success but the output file is missing"

    import ffmpeg

    probe = ffmpeg.probe(str(output))
    video_stream = next(s for s in probe["streams"] if s["codec_type"] == "video")
    audio_stream = next((s for s in probe["streams"] if s["codec_type"] == "audio"), None)
    real_duration = float(probe["format"]["duration"])

    assert video_stream["codec_name"] == "h264"
    assert audio_stream is not None, "the real narration audio did not survive the render"
    assert audio_stream["codec_name"] == "aac"

    # Matches the REAL edge-tts-measured narration duration this scene was
    # calibrated to (see narration_duration.json), not a hardcoded 30s.
    assert real_duration == pytest.approx(expected_duration_s, abs=0.5)

    frame_count = int(video_stream.get("nb_frames", 0))
    assert frame_count == pytest.approx(expected_frame_end, abs=2)

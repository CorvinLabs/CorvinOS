"""E2E proof for the "audit chain" 1-minute learn video: 3 real segments
(Blender 3D chain, a PowerPoint-sourced slide, an SVG diagram) with real
per-segment edge-tts narration, assembled into one video.

Per operator instruction (2026-09-26), this video's SOURCES live outside
CorvinOS, under /home/shumway/projects/Corvin-Videos/audit_chain_learn_video/
(narration scripts, generate_narration.py, build_blender_scene.py,
build_ppt_slide.py, build_svg_diagram.py, assemble_video.py, and the
committed fixture artifacts they produce). This test only POINTS at that
directory; nothing from it is copied into the CorvinOS repo.

Full regeneration (if the assets are missing or narration text changes):

    cd /home/shumway/projects/Corvin-Videos/audit_chain_learn_video
    python3 generate_narration.py                                   # real edge-tts, needs network
    blender --background --factory-startup --python build_blender_scene.py
    python3 -m core.skills.os_skills.video_producer.blender_cli \\
        --blend seg1_blender.blend --output seg1_blender.mp4 --timeout-sec 260
    python3 build_ppt_slide.py
    python3 build_svg_diagram.py
    python3 assemble_video.py

This test itself only re-runs the fast, deterministic last step
(assemble_video.py, pure ffmpeg -- no Blender render, no network) to prove
the pipeline is reproducible, then verifies the real output.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path("/home/shumway/projects/Corvin-Videos/audit_chain_learn_video")

pytestmark = pytest.mark.skipif(
    not PROJECT_DIR.exists() or shutil.which("ffmpeg") is None,
    reason="Corvin-Videos audit_chain_learn_video project or ffmpeg not available",
)


@pytest.mark.timeout(60)
def test_audit_chain_learn_video_assembles_and_verifies(tmp_path):
    manifest_path = PROJECT_DIR / "narration_manifest.json"
    seg1 = PROJECT_DIR / "seg1_blender.mp4"
    ppt_png = PROJECT_DIR / "compliance_slide.png"
    svg_png = PROJECT_DIR / "hash_chain_diagram.png"

    for required in (manifest_path, seg1, ppt_png, svg_png):
        assert required.exists(), (
            f"missing {required} -- see this file's module docstring to regenerate"
        )

    manifest = json.loads(manifest_path.read_text())
    segs = manifest["segments"]
    expected_total = manifest["total_duration_s"]

    # Re-run the real assembly step into an isolated output so this test
    # never depends on (or clobbers) whatever audit_chain_learn_video.mp4
    # already sits in the project directory -- proves assemble_video.py is
    # genuinely reproducible, not just "ran once and was eyeballed."
    out_path = tmp_path / "audit_chain_learn_video.mp4"
    script = f"""
import sys
sys.path.insert(0, {str(PROJECT_DIR)!r})
import assemble_video as av

av.HERE = __import__("pathlib").Path({str(PROJECT_DIR)!r})
seg2 = av.HERE / "seg2_ppt_test.mp4"
seg3 = av.HERE / "seg3_svg_test.mp4"
segs = {segs!r}
av.make_still_clip(av.HERE / "compliance_slide.png", av.HERE / segs["seg2_ppt"]["audio_file"], segs["seg2_ppt"]["duration_s"], seg2)
av.make_still_clip(av.HERE / "hash_chain_diagram.png", av.HERE / segs["seg3_svg"]["audio_file"], segs["seg3_svg"]["duration_s"], seg3)
av.concat_segments([av.HERE / "seg1_blender.mp4", seg2, seg3], {str(out_path)!r})
seg2.unlink()
seg3.unlink()
"""
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, timeout=50,
    )
    assert proc.returncode == 0, f"assembly failed:\nSTDOUT:{proc.stdout}\nSTDERR:{proc.stderr}"
    assert out_path.exists()

    import ffmpeg

    probe = ffmpeg.probe(str(out_path))
    video_stream = next(s for s in probe["streams"] if s["codec_type"] == "video")
    audio_stream = next((s for s in probe["streams"] if s["codec_type"] == "audio"), None)
    real_duration = float(probe["format"]["duration"])

    assert video_stream["codec_name"] == "h264"
    assert video_stream["width"] == 960
    assert video_stream["height"] == 540
    assert audio_stream is not None
    assert audio_stream["codec_name"] == "aac"

    # Real measured sum of the 3 real edge-tts segments (~59-60s for the
    # requested "1min" video), not a hardcoded assumption.
    assert real_duration == pytest.approx(expected_total, abs=1.0)

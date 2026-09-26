"""Real end-to-end proof for BlenderHeadlessOrchestrator.render_enhancement().

Drives the REAL transport boundary: a real `blender` subprocess rendering the
real fixture scene (tests/fixtures/video_producer/simple_scene.blend), read
back with a real `ffprobe` via ffmpeg-python. Nothing here mocks subprocess,
bpy, or ffmpeg -- this is the proof that closes Phase 2 of the video_producer
Blender plan (fixing the tempdir-deletion, "-o" no-op, hardcoded frame/duration,
and in-memory-only-audit bugs).

Requires: a `blender` binary on PATH, and `ffmpeg`/`ffprobe` on PATH plus
`ffmpeg-python` installed (see pyproject.toml's `video` extra).
"""

import json
import shutil
from pathlib import Path

import pytest

from core.skills.os_skills.video_producer.blender_orchestrator import (
    BlenderHeadlessOrchestrator,
    RenderConfig,
)

FIXTURE_BLEND = Path(__file__).resolve().parents[2] / "fixtures" / "video_producer" / "simple_scene.blend"

pytestmark = pytest.mark.skipif(
    shutil.which("blender") is None,
    reason="blender binary not on PATH",
)


@pytest.fixture
def isolated_tenant(tmp_path, monkeypatch):
    """Real CORVIN_HOME + CORVIN_TENANT_ID, isolated to a temp dir per test.

    write_event() fail-closes (AuditTenantMismatch) when a record's tenant_id
    doesn't match the process's CORVIN_TENANT_ID (ADR-0007 isolation) -- so a
    caller writing audit events for a given tenant must actually run scoped
    to that tenant, not just pass the id as a constructor argument.
    """
    tenant_id = "blender_e2e_test"
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "corvin_home"))
    monkeypatch.setenv("CORVIN_TENANT_ID", tenant_id)
    return tenant_id


@pytest.mark.asyncio
@pytest.mark.timeout(120)
async def test_render_enhancement_produces_a_real_playable_video(tmp_path, isolated_tenant):
    assert FIXTURE_BLEND.exists(), (
        f"fixture missing: {FIXTURE_BLEND} -- run "
        "tests/fixtures/video_producer/build_fixture_scene.py first"
    )

    destination = tmp_path / "e2e_render.mp4"

    orchestrator = BlenderHeadlessOrchestrator(
        blend_file=str(FIXTURE_BLEND),
        tenant_id=isolated_tenant,
    )
    config = RenderConfig(
        resolution="320x240",
        fps=25,
        frame_count=50,  # matches the fixture's authored frame_end
        output_codec="h264",
        bitrate_kbps=800,
        timeout_sec=90,
    )

    result = await orchestrator.render_enhancement(
        input_video="",  # unused by the Blender path (no compositing input yet)
        config=config,
        output_path=str(destination),
    )

    assert result.success, f"render failed: {result.errors}"
    assert result.output_file == str(destination)

    # The file must exist NOW, after render_enhancement() returned -- proves
    # the tempdir-deletion bug (Bug A) is fixed: the durable copy happened
    # before the `with tempfile.TemporaryDirectory()` block exited.
    assert destination.exists(), "reported success but the durable file is missing"
    assert destination.stat().st_size > 0

    # Real ffprobe of the durable file -- independent of whatever
    # RenderResult claims, closing the loop on Bug B (wrong render target)
    # and Bug C (hardcoded frame_count/duration_sec).
    import ffmpeg

    probe = ffmpeg.probe(str(destination))
    video_stream = next(s for s in probe["streams"] if s["codec_type"] == "video")
    audio_stream = next((s for s in probe["streams"] if s["codec_type"] == "audio"), None)
    real_duration = float(probe["format"]["duration"])

    assert video_stream["codec_name"] == "h264"
    assert real_duration == pytest.approx(2.0, abs=0.2)  # 50 frames @ 25fps

    # The fixture bakes a 2s sine-tone VSE strip (Phase 4) -- a real audio
    # stream must survive the render, not just the video.
    assert audio_stream is not None, "fixture has baked narration but no audio stream in the render"
    assert audio_stream["codec_name"] == "aac"

    # RenderResult's own numbers must match the independently-probed reality,
    # not the previous hardcoded frame_count=1/duration_sec=1 placeholders.
    assert result.frame_count > 1
    assert result.duration_sec == pytest.approx(real_duration, abs=0.2)


@pytest.mark.asyncio
@pytest.mark.timeout(120)
async def test_render_enhancement_writes_a_durable_audit_trail(tmp_path, isolated_tenant):
    """Bug D: audit events must land in the real per-tenant hash chain, not
    just self.audit_events (an in-memory list discarded with the object)."""
    from core.paths import tenant_audit_chain

    destination = tmp_path / "audit_render.mp4"
    orchestrator = BlenderHeadlessOrchestrator(
        blend_file=str(FIXTURE_BLEND),
        tenant_id=isolated_tenant,
    )
    config = RenderConfig(
        resolution="320x240",
        fps=25,
        frame_count=50,
        output_codec="h264",
        bitrate_kbps=800,
        timeout_sec=90,
    )

    result = await orchestrator.render_enhancement(
        input_video="",
        config=config,
        output_path=str(destination),
    )
    assert result.success, f"render failed: {result.errors}"

    chain_path = tenant_audit_chain(isolated_tenant)
    assert chain_path.exists(), f"no durable audit chain written at {chain_path}"

    records = [json.loads(line) for line in chain_path.read_text().splitlines() if line.strip()]
    event_types = {r["event_type"] for r in records}
    assert {
        "blender.render_start",
        "blender.bpy_script_generated",
        "blender.render_complete",
    } <= event_types

    complete = next(r for r in records if r["event_type"] == "blender.render_complete")
    assert complete["details"]["frame_count"] == result.frame_count

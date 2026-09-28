"""
E2E Tests for Autonomous Video Processor

Tests the complete end-to-end pipeline:
- Format detection
- Pipeline auto-detection
- Processing (simple and enhanced)
- Output validation
- Audit trail + learning events
- Tenant isolation

Compliance:
- ADR-0692: Video Producer Orchestration
- ADR-0720: Fail-closed hardening
- ADR-0232: Audit trail
- ADR-0314: Learning events
- ADR-0007: Tenant isolation
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(
    not _HAVE_FFMPEG, reason="external dependency: ffmpeg/ffprobe binaries not on PATH"
)


def _make_video(path: Path, *, vcodec: str = "libx264", audio: bool = True) -> Path:
    """Synthesize a real 1-second clip with the ffmpeg binary."""
    cmd = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25"]
    if audio:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440", "-c:a", "aac"]
    cmd += ["-t", "1", "-c:v", vcodec, "-y", str(path)]
    subprocess.run(cmd, check=True, capture_output=True, timeout=60)
    return path


def _chain_stages(tenant_id: str) -> list[dict]:
    from core.deployment import audit_sink

    _, fp = audit_sink._forge()
    chain = fp.tenant_audit_chain(tenant_id)
    if not chain.exists():
        return []
    recs = [json.loads(l) for l in chain.read_text().splitlines() if l.strip()]
    return [r for r in recs if r.get("event_type") == "video.autonomous_stage"]


@pytest.fixture(autouse=True)
def _process_tenant(monkeypatch):
    # The forge writer refuses a record for a tenant other than the process
    # tenant (ADR-0007 chokepoint), exactly as in production.
    monkeypatch.setenv("CORVIN_TENANT_ID", "test_tenant")


@pytest.mark.asyncio
class TestAutonomousVideoProcessorE2E:
    """End-to-end autonomous video processing — real ffmpeg, real audit chain."""

    @needs_ffmpeg
    async def test_e2e_simple_video_process(self, tmp_path):
        """Simple pipeline (H.264 + AAC in): remux to MP4, validated by ffprobe."""
        from core.skills.os_skills.video_producer.autonomous_processor import (
            AutonomousVideoProcessor,
        )

        processor = AutonomousVideoProcessor(tenant_id="test_tenant", output_dir=str(tmp_path / "out"))
        src = _make_video(tmp_path / "input.mp4")

        result = await processor.process_video(str(src))

        assert result.success, result.process_stages
        assert result.input_file == str(src)
        assert Path(result.output_file).is_file()
        assert result.validation.video_codec == "h264"
        assert result.validation.audio_codec == "aac"
        assert 0.5 < result.validation.duration_sec < 2.0
        assert result.tenant_id == "test_tenant"
        stages = [e["event_type"] for e in result.audit_events]
        assert "simple_processing_complete" in stages and stages[-1] == "processor_complete"

    @needs_ffmpeg
    async def test_e2e_enhanced_video_process_transcodes(self, tmp_path):
        """VP9 without audio → enhanced pipeline → H.264 output (no audio track)."""
        from core.skills.os_skills.video_producer.autonomous_processor import (
            AutonomousVideoProcessor,
        )

        processor = AutonomousVideoProcessor(tenant_id="test_tenant", output_dir=str(tmp_path / "out"))
        src = _make_video(tmp_path / "input.webm", vcodec="libvpx-vp9", audio=False)

        result = await processor.process_video(str(src))

        assert result.process_stages["processing"].success
        assert result.validation.video_codec == "h264"
        # No audio track came in and none was synthesised: validation says so.
        assert not result.success
        assert "No audio stream in output" in result.validation.errors

    async def test_e2e_format_detection_rejects_invalid_input(self, tmp_path):
        """Format detection rejects invalid input (fail-closed)."""
        from core.skills.os_skills.video_producer.autonomous_processor import (
            AutonomousVideoProcessor,
        )

        processor = AutonomousVideoProcessor(tenant_id="test_tenant", output_dir=str(tmp_path))

        result = await processor.process_video(str(tmp_path / "nonexistent_file_12345.mp4"))

        assert not result.success
        assert result.output_file is None
        assert len(result.audit_events) > 0

    async def test_e2e_audit_trail_hash_chained(self, tmp_path):
        """Stage records are on the REAL tenant chain, linked and content-free."""
        from core.deployment import audit_sink
        from core.skills.os_skills.video_producer.autonomous_processor import (
            AutonomousVideoProcessor,
        )

        before = len(_chain_stages("test_tenant"))
        processor = AutonomousVideoProcessor(tenant_id="test_tenant", output_dir=str(tmp_path))
        processor._emit_audit_event("test_event_1", {"input_file": "/home/alice/secret.mp4",
                                                     "video_codec": "h264"})
        processor._emit_audit_event("test_event_2", {"error": "boom at /home/alice"})

        recs = _chain_stages("test_tenant")[before:]
        assert [r["details"]["stage"] for r in recs] == [
            "processor_initialized", "test_event_1", "test_event_2"]
        assert recs[1]["details"]["video_codec"] == "h264"
        assert "alice" not in json.dumps(recs)
        # The in-memory mirror names the committed records.
        assert [e["hash"] for e in processor.audit_events] == [r["hash"] for r in recs]
        for prev, cur in zip(recs, recs[1:]):
            assert cur["prev_hash"] == prev["hash"]
        se, fp = audit_sink._forge()
        ok, problems = se.verify_chain(fp.tenant_audit_chain("test_tenant"))
        assert ok, problems

    async def test_e2e_learning_events_emitted(self, tmp_path):
        """Learning events are recorded in memory (not emitted to ADR-0314)."""
        from core.skills.os_skills.video_producer.autonomous_processor import (
            AutonomousVideoProcessor,
        )

        processor = AutonomousVideoProcessor(tenant_id="test_tenant", output_dir=str(tmp_path))

        processor._emit_learning_event("video_processed", {
            "codec": "h264",
            "success": True,
        })

        assert len(processor.learning_events) == 1
        event = processor.learning_events[0]
        assert event["event_type"] == "video_processed"
        assert event["tenant_id"] == "test_tenant"

    async def test_e2e_tenant_isolation(self, tmp_path, monkeypatch):
        """Each processor writes only into its own tenant's chain."""
        from core.deployment import audit_sink
        from core.skills.os_skills.video_producer.autonomous_processor import (
            AutonomousVideoProcessor,
        )

        b1, b2 = len(_chain_stages("tenant_1")), len(_chain_stages("tenant_2"))
        monkeypatch.setenv("CORVIN_TENANT_ID", "tenant_1")
        processor_t1 = AutonomousVideoProcessor(tenant_id="tenant_1", output_dir=str(tmp_path))
        processor_t1._emit_audit_event("test", {"video_codec": "h264"})
        # A tenant_2 processor inside a tenant_1 process is refused, not
        # written into anybody's chain.
        with pytest.raises(audit_sink.AuditWriteFailed):
            AutonomousVideoProcessor(tenant_id="tenant_2", output_dir=str(tmp_path))
        monkeypatch.setenv("CORVIN_TENANT_ID", "tenant_2")
        processor_t2 = AutonomousVideoProcessor(tenant_id="tenant_2", output_dir=str(tmp_path))
        processor_t2._emit_audit_event("test", {"video_codec": "vp9"})

        t1, t2 = _chain_stages("tenant_1")[b1:], _chain_stages("tenant_2")[b2:]
        assert len(t1) == len(t2) == 2
        assert all(r["details"]["tenant_id"] == "tenant_1" for r in t1)
        assert all(r["details"]["tenant_id"] == "tenant_2" for r in t2)
        assert t1[-1]["details"]["video_codec"] == "h264"
        assert t2[-1]["details"]["video_codec"] == "vp9"
        assert all(e["tenant_id"] == "tenant_1" for e in processor_t1.audit_events)
        assert all(e["tenant_id"] == "tenant_2" for e in processor_t2.audit_events)

    async def test_e2e_audit_failure_fails_the_run(self, tmp_path):
        """Fail-closed: a stage record that does not commit aborts the run.

        The error record itself cannot be written either, so the audit
        failure propagates instead of a silently unaudited result.
        """
        from unittest.mock import patch

        from core.deployment import audit_sink
        from core.skills.os_skills.video_producer.autonomous_processor import (
            AutonomousVideoProcessor,
        )

        processor = AutonomousVideoProcessor(tenant_id="test_tenant", output_dir=str(tmp_path))
        with patch.object(audit_sink, "emit", side_effect=audit_sink.AuditWriteFailed("x")):
            with pytest.raises(audit_sink.AuditWriteFailed):
                await processor.process_video(str(tmp_path / "x.mp4"))

    async def test_e2e_pipeline_detection_simple_vs_enhanced(self, tmp_path):
        """Test pipeline auto-detection (simple vs. enhanced)."""
        from core.skills.os_skills.video_producer.autonomous_processor import (
            AutonomousVideoProcessor,
            InputMetadata,
        )

        processor = AutonomousVideoProcessor(tenant_id="test_tenant", output_dir=str(tmp_path))

        # Simple case: H.264 + AAC
        metadata_simple = InputMetadata(
            file_path="/tmp/video.mp4",
            format_name="mov,mp4",
            duration_sec=60.0,
            resolution="1920x1080",
            width=1920,
            height=1080,
            fps=30.0,
            video_codec="h264",
            audio_codec="aac",
            bitrate_kbps=5000,
            has_audio=True,
            is_valid=True,
            errors=[],
        )

        pipeline = await processor._determine_pipeline(metadata_simple)
        assert pipeline.pipeline_type == "simple"
        assert not pipeline.needs_blender

        # Enhanced case: VP9 + no audio
        metadata_enhanced = InputMetadata(
            file_path="/tmp/video.webm",
            format_name="webm",
            duration_sec=60.0,
            resolution="1920x1080",
            width=1920,
            height=1080,
            fps=30.0,
            video_codec="vp9",
            audio_codec=None,
            bitrate_kbps=5000,
            has_audio=False,
            is_valid=True,
            errors=[],
        )

        pipeline = await processor._determine_pipeline(metadata_enhanced)
        assert pipeline.pipeline_type == "enhanced"

    async def test_bitrate_calculation_various_resolutions(self, tmp_path):
        """Bitrate grows with resolution (it used to hit the cap at 1080p)."""
        from core.skills.os_skills.video_producer.autonomous_processor import (
            AutonomousVideoProcessor,
        )

        processor = AutonomousVideoProcessor(tenant_id="test_tenant", output_dir=str(tmp_path))

        cases = [
            ("640x480", 30, 500, 2_500),
            ("1280x720", 30, 1_500, 4_000),
            ("1920x1080", 30, 3_000, 8_000),
            ("3840x2160", 30, 12_000, 45_000),
        ]
        rates = []
        for resolution, fps, lo, hi in cases:
            bitrate = processor._calculate_optimal_bitrate(resolution, fps)
            assert lo <= bitrate <= hi, (resolution, bitrate)
            rates.append(bitrate)
        assert rates == sorted(rates) and len(set(rates)) == len(rates)

    @needs_ffmpeg
    async def test_output_validation_checks_codec_and_duration(self, tmp_path):
        """Validation reads the real file: missing → invalid; real clip → valid."""
        from core.skills.os_skills.video_producer.autonomous_processor import (
            AutonomousVideoProcessor,
        )

        processor = AutonomousVideoProcessor(tenant_id="test_tenant", output_dir=str(tmp_path))

        validation = await processor._validate_output(str(tmp_path / "nonexistent.mp4"))
        assert not validation.is_valid
        assert len(validation.errors) > 0

        ok = await processor._validate_output(str(_make_video(tmp_path / "v.mp4")))
        assert ok.is_valid, ok.errors
        assert ok.video_codec == "h264" and ok.audio_codec == "aac"

    async def test_ffmpeg_command_generation(self, tmp_path):
        """Test ffmpeg command auto-generation."""
        from core.skills.os_skills.video_producer.autonomous_processor import (
            AutonomousVideoProcessor,
            ConversionParams,
        )

        processor = AutonomousVideoProcessor(tenant_id="test_tenant", output_dir=str(tmp_path))

        params = ConversionParams(
            video_codec="h264",
            bitrate_kbps=5000,
            resolution="1920x1080",
            audio_codec="aac",
            audio_bitrate_kbps=256,
            preset="medium",
        )

        cmd = processor._generate_ffmpeg_command(
            "/tmp/input.mp4",
            "/tmp/output.mp4",
            params,
        )

        assert "ffmpeg" in cmd[0]
        assert "/tmp/input.mp4" in cmd
        assert "/tmp/output.mp4" in cmd
        assert "-b:v" in cmd
        assert "5000k" in cmd


@pytest.mark.asyncio
class TestAutoFormatConverter:
    """Tests for AutoFormatConverter."""

    async def test_bitrate_calculation_precision(self):
        """Test bitrate calculation precision."""
        from core.skills.os_skills.video_producer.auto_format_converter import (
            AutoFormatConverter,
        )

        converter = AutoFormatConverter()

        # Target H.264 rates: ~0.9 Mbps at 480p30, ~5 Mbps at 1080p30,
        # ~17 Mbps at 2160p30 — and strictly increasing with resolution
        # (the old formula capped 1080p and 4K at the same 20 Mbps).
        bitrate_sd = converter._calculate_optimal_bitrate("640x480", 30)
        assert 500 <= bitrate_sd < 2500

        bitrate_fhd = converter._calculate_optimal_bitrate("1920x1080", 30)
        assert 3000 < bitrate_fhd < 8000

        bitrate_4k = converter._calculate_optimal_bitrate("3840x2160", 30)
        assert 12000 < bitrate_4k < 45000
        assert bitrate_sd < bitrate_fhd < bitrate_4k

    async def test_codec_selection_h264_preferred(self):
        """Test H.264 is preferred codec."""
        from core.skills.os_skills.video_producer.auto_format_converter import (
            AutoFormatConverter,
        )

        converter = AutoFormatConverter()
        params = converter._auto_select_codec_and_bitrate("1920x1080", 30)

        assert params.video_codec == "h264"
        assert params.audio_codec == "aac"

    async def test_ffmpeg_command_generation(self):
        """Test ffmpeg command generation."""
        from core.skills.os_skills.video_producer.auto_format_converter import (
            AutoFormatConverter,
            ConversionParams,
        )

        converter = AutoFormatConverter()

        params = ConversionParams(
            video_codec="h264",
            bitrate_kbps=5000,
            resolution="1920x1080",
            audio_codec="aac",
            audio_bitrate_kbps=256,
            preset="medium",
        )

        cmd = converter._generate_ffmpeg_command(
            "/tmp/input.mp4",
            "/tmp/output.mp4",
            params,
        )

        assert "ffmpeg" in cmd[0]
        assert "libx264" in cmd
        assert "-b:v" in cmd
        assert "5000k" in cmd


@pytest.mark.asyncio
class TestBlenderHeadlessOrchestrator:
    """Tests for BlenderHeadlessOrchestrator."""

    async def test_orchestrator_initialization(self):
        """Test orchestrator initialization."""
        from core.skills.os_skills.video_producer.blender_orchestrator import (
            BlenderHeadlessOrchestrator,
        )

        orchestrator = BlenderHeadlessOrchestrator(
            blend_file="/tmp/test.blend",
            tenant_id="test_tenant",
        )

        assert orchestrator.blend_file == "/tmp/test.blend"
        assert orchestrator.tenant_id == "test_tenant"

    async def test_bpy_script_generation(self):
        """Test Python bpy script auto-generation."""
        from core.skills.os_skills.video_producer.blender_orchestrator import (
            BlenderHeadlessOrchestrator,
            RenderConfig,
        )

        orchestrator = BlenderHeadlessOrchestrator(
            blend_file="/tmp/test.blend",
        )

        config = RenderConfig(
            resolution="1920x1080",
            fps=30,
            frame_count=90,
            output_codec="h264",
            bitrate_kbps=5000,
        )

        from pathlib import Path

        script = await orchestrator._auto_generate_bpy_script(
            "/tmp/input.mp4",
            config,
            Path("/tmp/output.mp4"),
        )

        assert "import bpy" in script
        assert "1920" in script
        assert "1080" in script
        assert "90" in script  # frame_count
        assert "scene.render.filepath" in script
        assert "/tmp/output.mp4" in script

    async def test_render_output_validation(self):
        """Test render output validation."""
        from core.skills.os_skills.video_producer.blender_orchestrator import (
            BlenderHeadlessOrchestrator,
        )

        orchestrator = BlenderHeadlessOrchestrator(
            blend_file="/tmp/test.blend",
        )

        # Non-existent file should fail validation
        result = await orchestrator._validate_render_output("/tmp/nonexistent.mp4")
        assert not result


# Run tests
if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])

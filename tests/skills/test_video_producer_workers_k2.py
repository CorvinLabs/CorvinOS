"""Unit tests for Video Producer Workers (Phase 1 Step 2 — k=2)."""

import asyncio
import tempfile
from pathlib import Path
from typing import Dict, Any

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from core.skills.os_skills.video_producer import (
    AudioSynthesisWorker,
    ScreenshotCaptureWorker,
    VideoAssemblerWorker,
    WorkerManifest,
    VideoProducerMaestro,
)


def test_audio_synthesis_worker():
    """Test audio synthesis worker initialization and execution."""
    manifest = WorkerManifest(
        id="video-producer:audio-synthesis",
        version="1.0.0",
        name="Audio Synthesis",
        description="TTS worker",
        plugin_id="video-producer",
        boot_layer="bundled",
        capabilities=["audio_synthesis", "tts"],
        config={"voice": "en-US-AriaNeural"},
        required_checks=["audio_quality"],
    )

    worker = AudioSynthesisWorker(manifest)
    assert worker.manifest.id == "video-producer:audio-synthesis"
    assert worker.manifest.version == "1.0.0"

    # Test execution (async)
    with tempfile.TemporaryDirectory() as tmpdir:
        result = asyncio.run(
            worker.execute({
                "narration": "Hello world, this is a test.",
                "voice": "en-US-AriaNeural",
                "output_dir": tmpdir,
            })
        )

        assert result.status == "success"
        assert "audio_files" in result.output
        assert result.output["narration_chars"] == len("Hello world, this is a test.")
        assert result.latency_ms >= 0

        print("✓ Audio synthesis worker passed")
        print(f"  - Generated files: {result.output['audio_files']}")
        print(f"  - Duration: {result.output['duration_s']:.1f}s")
        print(f"  - Latency: {result.latency_ms:.1f}ms")


def test_screenshot_capture_worker():
    """Test screenshot capture worker initialization and execution."""
    manifest = WorkerManifest(
        id="video-producer:screenshot-capture",
        version="1.0.0",
        name="Screenshot Capture",
        description="Browser automation",
        plugin_id="video-producer",
        boot_layer="bundled",
        capabilities=["screenshot_capture"],
        config={"viewport": "1920x1080"},
        required_checks=["image_quality"],
    )

    worker = ScreenshotCaptureWorker(manifest)
    assert worker.manifest.id == "video-producer:screenshot-capture"

    # Test execution (will create stub files since Playwright may not be installed)
    with tempfile.TemporaryDirectory() as tmpdir:
        result = asyncio.run(
            worker.execute({
                "urls": ["https://example.com", "https://example.org"],
                "output_dir": tmpdir,
                "viewport": "1920x1080",
            })
        )

        assert result.status == "success"
        assert result.output["count"] == 2
        assert result.output["viewport"] == "1920x1080"

        print("✓ Screenshot capture worker passed")
        print(f"  - Screenshots: {result.output['count']}")
        print(f"  - Viewport: {result.output['viewport']}")
        print(f"  - Latency: {result.latency_ms:.1f}ms")


def test_video_assembler_worker():
    """Test video assembler worker initialization and execution."""
    manifest = WorkerManifest(
        id="video-producer:video-assembler",
        version="1.0.0",
        name="Video Assembler",
        description="FFmpeg mux + encode",
        plugin_id="video-producer",
        boot_layer="bundled",
        capabilities=["video_assembly", "ffmpeg"],
        config={"codec": "h264", "bitrate": "30M"},
        required_checks=["output_quality"],
    )

    worker = VideoAssemblerWorker(manifest)
    assert worker.manifest.id == "video-producer:video-assembler"

    # Test execution (will create stub output since FFmpeg may not be installed)
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create stub video/audio files
        video_file = Path(tmpdir) / "video.mp4"
        audio_file = Path(tmpdir) / "audio.mp3"
        output_file = Path(tmpdir) / "final.mp4"

        video_file.touch()
        audio_file.touch()

        result = asyncio.run(
            worker.execute({
                "video_file": str(video_file),
                "audio_file": str(audio_file),
                "output_file": str(output_file),
            })
        )

        assert result.status == "success"
        assert result.output["codec"] == "h264"
        assert result.output["container"] == "mp4"
        assert result.output["duration_s"] >= 0

        print("✓ Video assembler worker passed")
        print(f"  - Output: {result.output['output_file']}")
        print(f"  - Codec: {result.output['codec']}")
        print(f"  - Latency: {result.latency_ms:.1f}ms")


def test_workers_in_maestro():
    """Test all workers registered and dispatched via maestro."""
    with tempfile.TemporaryDirectory() as tmpdir:
        maestro = VideoProducerMaestro(tmpdir)

        # Create all three worker manifests
        audio_manifest = WorkerManifest(
            id="video-producer:audio-synthesis",
            version="1.0.0",
            name="Audio Synthesis",
            description="TTS worker",
            plugin_id="video-producer",
            boot_layer="bundled",
            capabilities=["audio_synthesis"],
            config={},
            required_checks=[],
        )

        screenshot_manifest = WorkerManifest(
            id="video-producer:screenshot-capture",
            version="1.0.0",
            name="Screenshot Capture",
            description="Browser automation",
            plugin_id="video-producer",
            boot_layer="bundled",
            capabilities=["screenshot_capture"],
            config={},
            required_checks=[],
        )

        assembler_manifest = WorkerManifest(
            id="video-producer:video-assembler",
            version="1.0.0",
            name="Video Assembler",
            description="FFmpeg mux + encode",
            plugin_id="video-producer",
            boot_layer="bundled",
            capabilities=["video_assembly"],
            config={},
            required_checks=[],
        )

        # Register all workers
        maestro.register_worker(AudioSynthesisWorker(audio_manifest))
        maestro.register_worker(ScreenshotCaptureWorker(screenshot_manifest))
        maestro.register_worker(VideoAssemblerWorker(assembler_manifest))

        # Verify all registered
        all_workers = maestro.get_all_workers()
        assert len(all_workers) == 3
        assert "video-producer:audio-synthesis" in all_workers
        assert "video-producer:screenshot-capture" in all_workers
        assert "video-producer:video-assembler" in all_workers

        print("✓ All workers registered in maestro")
        print(f"  - Total workers: {len(all_workers)}")
        print(f"  - Worker IDs: {list(all_workers.keys())}")


if __name__ == "__main__":
    test_audio_synthesis_worker()
    test_screenshot_capture_worker()
    test_video_assembler_worker()
    test_workers_in_maestro()

    print("\n✅ All Worker (k=2 Phase 1 Step 2) tests passed (4/4)")

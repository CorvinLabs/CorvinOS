"""
Unit tests for Video Producer Maestro (Orchestrator) — Phase 1 Skeleton.

Tests:
- Maestro initialization
- Worker registry (register, get, validate)
- Phases 1-3 execution
- Error handling
"""

import asyncio
import tempfile
from pathlib import Path
from typing import Dict, Any

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from core.skills.os_skills.video_producer import (
    VideoProducerMaestro,
    WorkerSkillBase,
    WorkerManifest,
    WorkerResult,
)


class MockAudioWorker(WorkerSkillBase):
    """Mock audio synthesis worker for testing."""

    async def execute(self, input_data: Dict[str, Any], **config_overrides) -> WorkerResult:
        return WorkerResult(
            worker_id="video-producer:audio-synthesis",
            status="success",
            output={"audio_files": ["/tmp/narration.mp3"], "duration_s": 60.0},
        )


class MockScreenshotWorker(WorkerSkillBase):
    """Mock screenshot capture worker for testing."""

    async def execute(self, input_data: Dict[str, Any], **config_overrides) -> WorkerResult:
        return WorkerResult(
            worker_id="video-producer:screenshot-capture",
            status="success",
            output={"screenshots": ["/tmp/screenshot1.png"], "count": 1},
        )


def test_maestro_initialization():
    """Test maestro initializes correctly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        maestro = VideoProducerMaestro(tmpdir)

        assert maestro.project_dir == Path(tmpdir)
        assert maestro.assets_dir.exists()
        assert maestro.audio_dir.exists()
        assert maestro.screenshots_dir.exists()
        assert maestro.output_dir.exists()
        print("✓ Maestro initialization passed")


def test_worker_registration():
    """Test worker registry (register, get, list)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        maestro = VideoProducerMaestro(tmpdir)

        # Create mock workers
        audio_manifest = WorkerManifest(
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
        audio_worker = MockAudioWorker(audio_manifest)

        screenshot_manifest = WorkerManifest(
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
        screenshot_worker = MockScreenshotWorker(screenshot_manifest)

        # Register
        maestro.register_worker(audio_worker)
        maestro.register_worker(screenshot_worker)

        # Verify
        assert maestro.get_worker("video-producer:audio-synthesis") is not None
        assert maestro.get_worker("video-producer:screenshot-capture") is not None
        assert len(maestro.get_all_workers()) == 2

        print("✓ Worker registration passed")


def test_maestro_phases_1_3():
    """Test Phases 1-3 execution."""
    with tempfile.TemporaryDirectory() as tmpdir:
        maestro = VideoProducerMaestro(tmpdir)

        # Register mock workers
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
        maestro.register_worker(MockAudioWorker(audio_manifest))

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
        maestro.register_worker(MockScreenshotWorker(screenshot_manifest))

        # Run orchestration
        result = asyncio.run(
            maestro.orchestrate(
                asset_paths=[tmpdir],
                instructions={"title": "Test Video"},
            )
        )

        # Verify
        assert result["status"] in ["success", "partial"]
        assert result["analysis"] is not None
        assert result["storyboard"] is not None
        assert "workers_ready" in result

        print("✓ Phases 1-3 orchestration passed")
        print(f"  - Status: {result['status']}")
        print(f"  - Workers ready: {result['workers_ready']}")


def test_phase_skip():
    """Test skipping phases."""
    with tempfile.TemporaryDirectory() as tmpdir:
        maestro = VideoProducerMaestro(tmpdir)

        result = asyncio.run(
            maestro.orchestrate(
                asset_paths=[tmpdir],
                skip_phases=[3],  # Skip Phase 3
            )
        )

        assert result["status"] == "partial"
        assert result["workers_ready"] is False

        print("✓ Phase skip passed")


def test_worker_manifest_validation():
    """Test manifest compliance validation."""
    with tempfile.TemporaryDirectory() as tmpdir:
        maestro = VideoProducerMaestro(tmpdir)

        # Register a compliant worker
        manifest = WorkerManifest(
            id="video-producer:test",
            version="1.0.0",
            name="Test",
            description="Test worker",
            plugin_id="video-producer",
            boot_layer="bundled",
            capabilities=["test"],
            config={},
            required_checks=[],
        )
        worker = MockAudioWorker(manifest)
        maestro.register_worker(worker)

        # Validate
        errors = maestro.validate_registry()
        assert len(errors) == 0

        print("✓ Manifest validation passed")


if __name__ == "__main__":
    test_maestro_initialization()
    test_worker_registration()
    test_maestro_phases_1_3()
    test_phase_skip()
    test_worker_manifest_validation()

    print("\n✅ All Phase 1 Orchestrator tests passed (5/5)")

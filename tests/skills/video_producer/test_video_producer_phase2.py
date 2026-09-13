"""Tests for Video Producer Skill 2.0 — Phase 2: Voice + Screenshots

Phase 2 Focus:
- Voice Synthesizer Worker
- Screenshot Capturer Worker
- Worker registration in pipeline
- Result integration
"""

import pytest
import json
import os
from video_producer.maestro import VideoJob, VideoJobPhase
from video_producer.workers.voice_synthesizer import VoiceSynthesizerWorker, VoiceResult
from video_producer.workers.screenshot_capturer import (
    ScreenshotCapturerWorker,
    ScreenshotResult,
)
from video_producer.worker_manager import VideoProductionPipeline, WorkerRegistration


class TestVoiceSynthesizerWorker:
    """Test Voice Synthesizer Worker"""

    def test_voice_synthesizer_executes(self):
        """Voice Synthesizer Worker executes"""
        worker = VoiceSynthesizerWorker()
        job = VideoJob(
            job_id="test",
            topic="Test",
            duration=60,
            audience="test",
            narration=["Scene one.", "Scene two."],
        )

        result = worker.execute(job)
        assert result.success is True
        assert len(result.audio_files) == 2
        assert result.loudness_lufs == -23.0

    def test_voice_synthesizer_creates_audio_files(self):
        """Voice Synthesizer creates audio files"""
        worker = VoiceSynthesizerWorker()
        job = VideoJob(
            job_id="test",
            topic="Test",
            duration=60,
            audience="test",
            narration=["This is the first scene."],
        )

        result = worker.execute(job)
        assert len(result.audio_files) == 1

        # Check file exists
        audio_file = result.audio_files[0]
        assert os.path.exists(audio_file)

    def test_voice_synthesizer_calculates_duration(self):
        """Voice Synthesizer calculates total duration"""
        worker = VoiceSynthesizerWorker()
        job = VideoJob(
            job_id="test",
            topic="Test",
            duration=60,
            audience="test",
            narration=[
                "This is a short scene.",
                "This is another scene.",
                "And one more.",
            ],
        )

        result = worker.execute(job)
        assert result.total_duration_seconds > 0

    def test_voice_synthesizer_normalizes_loudness(self):
        """Voice Synthesizer normalizes to -23 LUFS (broadcast)"""
        worker = VoiceSynthesizerWorker()
        job = VideoJob(
            job_id="test",
            topic="Test",
            duration=60,
            audience="test",
            narration=["Scene 1", "Scene 2"],
        )

        result = worker.execute(job)
        assert result.loudness_lufs == -23.0

    def test_voice_synthesizer_confidence_score(self):
        """Voice Synthesizer provides confidence score"""
        worker = VoiceSynthesizerWorker()
        job = VideoJob(
            job_id="test",
            topic="Test",
            duration=60,
            audience="test",
            narration=["Scene 1"],
        )

        result = worker.execute(job)
        assert 0.0 <= result.confidence <= 1.0
        assert result.confidence > 0.85

    def test_voice_synthesizer_provider_selection(self):
        """Voice Synthesizer accepts provider selection"""
        worker_google = VoiceSynthesizerWorker(tts_provider="google")
        worker_azure = VoiceSynthesizerWorker(tts_provider="azure")

        assert worker_google.tts_provider == "google"
        assert worker_azure.tts_provider == "azure"

    def test_voice_synthesizer_audio_file_structure(self):
        """Voice Synthesizer creates properly structured audio files"""
        worker = VoiceSynthesizerWorker()
        job = VideoJob(
            job_id="test",
            topic="Test",
            duration=60,
            audience="test",
            narration=["Sample narration."],
        )

        result = worker.execute(job)
        audio_file = result.audio_files[0]

        # Read metadata
        with open(audio_file, "r") as f:
            metadata = json.load(f)

        assert "narration" in metadata
        assert "sample_rate" in metadata
        assert "bitrate" in metadata
        assert metadata["sample_rate"] == 48000


class TestScreenshotCapturerWorker:
    """Test Screenshot Capturer Worker"""

    def test_screenshot_capturer_executes(self):
        """Screenshot Capturer Worker executes"""
        worker = ScreenshotCapturerWorker()
        job = VideoJob(
            job_id="test",
            topic="Test",
            duration=60,
            audience="test",
            narration=["Check the console.", "Look at the dashboard."],
        )

        result = worker.execute(job)
        assert result.success is True
        assert result.num_captured >= 0

    def test_screenshot_capturer_parses_console_target(self):
        """Screenshot Capturer parses console URL from narration"""
        worker = ScreenshotCapturerWorker()

        url = worker._parse_target_url("The console shows the main interface.")
        assert url == "http://localhost:8765/console/"

    def test_screenshot_capturer_parses_dashboard_target(self):
        """Screenshot Capturer parses dashboard URL"""
        worker = ScreenshotCapturerWorker()

        url = worker._parse_target_url("Open the dashboard to see metrics.")
        assert url == "http://localhost:8765/console/dashboard"

    def test_screenshot_capturer_parses_vibe_target(self):
        """Screenshot Capturer parses Vibe Engineering URL"""
        worker = ScreenshotCapturerWorker()

        url = worker._parse_target_url("Navigate to the Vibe panel.")
        assert url == "http://localhost:8765/console/vibe"

    def test_screenshot_capturer_returns_none_for_unmapped_target(self):
        """Screenshot Capturer returns None for unmapped narration"""
        worker = ScreenshotCapturerWorker()

        url = worker._parse_target_url("This is generic narration without targets.")
        assert url is None

    def test_screenshot_capturer_creates_screenshot_files(self):
        """Screenshot Capturer creates screenshot files"""
        worker = ScreenshotCapturerWorker()
        job = VideoJob(
            job_id="test",
            topic="Test",
            duration=60,
            audience="test",
            narration=["Check the console.", "Look at the settings."],
        )

        result = worker.execute(job)
        for screenshot_path in result.screenshots:
            assert os.path.exists(screenshot_path)

    def test_screenshot_capturer_multiple_targets(self):
        """Screenshot Capturer handles multiple targets"""
        worker = ScreenshotCapturerWorker()
        job = VideoJob(
            job_id="test",
            topic="Test",
            duration=60,
            audience="test",
            narration=[
                "Open the console.",
                "Go to the dashboard.",
                "Check the vibe panel.",
            ],
        )

        result = worker.execute(job)
        assert result.num_captured == 3

    def test_screenshot_capturer_confidence_score(self):
        """Screenshot Capturer provides confidence score"""
        worker = ScreenshotCapturerWorker()
        job = VideoJob(
            job_id="test",
            topic="Test",
            duration=60,
            audience="test",
            narration=["Scene 1"],
        )

        result = worker.execute(job)
        assert 0.0 <= result.confidence <= 1.0
        assert result.confidence > 0.80

    def test_screenshot_capturer_metadata(self):
        """Screenshot Capturer includes metadata in files"""
        worker = ScreenshotCapturerWorker()
        job = VideoJob(
            job_id="test",
            topic="Test",
            duration=60,
            audience="test",
            narration=["Check the console."],
        )

        result = worker.execute(job)
        if result.screenshots:
            screenshot_file = result.screenshots[0]
            with open(screenshot_file, "r") as f:
                metadata = json.load(f)

            assert "url" in metadata
            assert "scene_index" in metadata


class TestWorkerRegistration:
    """Test worker registration in pipeline"""

    def test_worker_registration_register(self):
        """WorkerRegistration registers workers"""
        registry = WorkerRegistration()
        voice_worker = VoiceSynthesizerWorker()
        registry.register("VOICE", voice_worker)

        assert registry.get_worker("VOICE") is not None

    def test_worker_registration_list(self):
        """WorkerRegistration lists all workers"""
        registry = WorkerRegistration()
        registry.register("VOICE", VoiceSynthesizerWorker())
        registry.register("SCREENSHOTS", ScreenshotCapturerWorker())

        workers = registry.list_workers()
        assert len(workers) == 2
        assert "VOICE" in workers
        assert "SCREENSHOTS" in workers

    def test_worker_registration_enable_disable(self):
        """WorkerRegistration enables/disables workers"""
        registry = WorkerRegistration()
        registry.register("VOICE", VoiceSynthesizerWorker())

        assert registry.is_enabled("VOICE") is True

        registry.disable_worker("VOICE")
        assert registry.is_enabled("VOICE") is False

        registry.enable_worker("VOICE")
        assert registry.is_enabled("VOICE") is True


class TestVideoProductionPipeline:
    """Test VideoProductionPipeline integration"""

    def test_pipeline_creates_job(self, pipeline):
        """Pipeline creates a video job"""
        job_id = pipeline.create_and_process_job(
            topic="Test",
            duration=60,
            audience="test",
            narration=["Scene 1"],
            auto_execute=False,
        )
        assert job_id is not None
        assert job_id in pipeline.maestro.list_jobs()

    def test_pipeline_registers_worker(self, pipeline):
        """Pipeline registers workers"""
        pipeline.register_worker(VideoJobPhase.VOICE, VoiceSynthesizerWorker)
        pipeline.register_worker(VideoJobPhase.SCREENSHOTS, ScreenshotCapturerWorker)

        status = pipeline.get_workers_status()
        assert status["total_registered"] == 2

    def test_pipeline_get_job_status(self, pipeline):
        """Pipeline provides job status"""
        job_id = pipeline.create_and_process_job(
            topic="Test",
            duration=60,
            audience="test",
            narration=["Scene 1"],
            auto_execute=False,
        )

        status = pipeline.get_job_status(job_id)
        assert status["job_id"] == job_id
        assert status["current_phase"] == "ANALYSIS"
        assert status["num_scenes"] == 1

    def test_pipeline_get_workers_status(self, pipeline):
        """Pipeline provides worker status"""
        pipeline.register_worker(VideoJobPhase.VOICE, VoiceSynthesizerWorker)

        status = pipeline.get_workers_status()
        assert "registered_workers" in status
        assert "total_registered" in status
        assert status["total_registered"] >= 1

    def test_pipeline_job_not_found(self, pipeline):
        """Pipeline handles missing jobs gracefully"""
        status = pipeline.get_job_status("nonexistent_job")
        assert "error" in status


class TestPhase2Integration:
    """Test Phase 2 integration between workers"""

    def test_voice_and_screenshot_results_compatible(self):
        """Voice and Screenshot results are compatible"""
        voice_worker = VoiceSynthesizerWorker()
        screenshot_worker = ScreenshotCapturerWorker()

        job = VideoJob(
            job_id="test",
            topic="Test",
            duration=60,
            audience="test",
            narration=["Scene 1: Check the console."],
        )

        voice_result = voice_worker.execute(job)
        screenshot_result = screenshot_worker.execute(job)

        # Both should succeed
        assert voice_result.success is True
        assert screenshot_result.success is True

        # Results should be compatible for Phase 3 assembly
        assert len(voice_result.audio_files) > 0
        assert len(screenshot_result.screenshots) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

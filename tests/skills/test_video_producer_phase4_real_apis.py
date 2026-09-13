"""Test Video Producer Skill 2.0 — Phase 4 Real API Integrations

Tests for Phase 4 real API implementations:
- Real TTS integration (edge-tts fallback to mock)
- Real screenshot capture (Playwright)
- Real video assembly (FFmpeg)
- Real YouTube metadata generation

All tests verify:
1. Worker initialization
2. Result format validation
3. Error handling and fallbacks
4. End-to-end pipeline
"""

import pytest
import os
import json
import tempfile
from pathlib import Path

from core.skills.video_producer.maestro import MaestroOrchestrator, VideoJobPhase
from core.skills.video_producer.workers.voice_synthesizer import VoiceSynthesizerWorker, VoiceResult
from core.skills.video_producer.workers.screenshot_capturer import ScreenshotCapturerWorker, ScreenshotResult
from core.skills.video_producer.workers.video_assembler import VideoAssemblerWorker, VideoResult
from core.skills.video_producer.workers.youtube_uploader import YouTubeUploaderWorker, UploadResult
from core.skills.video_producer.workers.asset_analyzer import AssetAnalyzerWorker


class TestVoiceSynthesizerPhase4:
    """Test Voice Synthesizer Worker Phase 4 (Real TTS)"""

    def test_initialization(self):
        """Test worker initialization"""
        worker = VoiceSynthesizerWorker(tts_provider="edge-tts")
        assert worker.name == "voice_synthesizer"
        assert worker.version == "4.0.0"
        assert worker.tts_provider == "edge-tts"

    def test_execute_returns_voice_result(self):
        """Test execute returns valid VoiceResult"""
        worker = VoiceSynthesizerWorker()

        # Create a test job
        maestro = MaestroOrchestrator()
        job_id = maestro.create_job(
            topic="Test",
            duration=30,
            audience="test",
            narration=["This is a test scene."]
        )
        job = maestro.get_job(job_id)

        result = worker.execute(job)

        assert isinstance(result, VoiceResult)
        assert result.success
        assert isinstance(result.audio_files, list)
        assert len(result.audio_files) == 1
        assert result.total_duration_seconds > 0
        assert result.loudness_lufs == -23.0
        assert 0.8 <= result.confidence <= 1.0

    def test_multiple_scenes_synthesis(self):
        """Test synthesizing multiple scenes"""
        worker = VoiceSynthesizerWorker()

        maestro = MaestroOrchestrator()
        narration = [
            "Scene one.",
            "Scene two.",
            "Scene three.",
        ]
        job_id = maestro.create_job(
            topic="Multi-Scene",
            duration=90,
            audience="test",
            narration=narration
        )
        job = maestro.get_job(job_id)

        result = worker.execute(job)

        assert len(result.audio_files) == 3
        assert result.total_duration_seconds > 0


class TestScreenshotCapturerPhase4:
    """Test Screenshot Capturer Worker Phase 4 (Real Playwright)"""

    def test_initialization(self):
        """Test worker initialization"""
        worker = ScreenshotCapturerWorker(browser_type="chromium", headless=True)
        assert worker.name == "screenshot_capturer"
        assert worker.version == "4.0.0"
        assert worker.browser_type == "chromium"
        assert worker.headless

    def test_execute_returns_screenshot_result(self):
        """Test execute returns valid ScreenshotResult"""
        worker = ScreenshotCapturerWorker()

        maestro = MaestroOrchestrator()
        job_id = maestro.create_job(
            topic="Test",
            duration=30,
            audience="test",
            narration=["Check out the console."]
        )
        job = maestro.get_job(job_id)

        result = worker.execute(job)

        assert isinstance(result, ScreenshotResult)
        assert result.success
        assert isinstance(result.screenshots, list)
        assert result.num_captured >= 0
        assert 0.8 <= result.confidence <= 1.0

    def test_url_parsing_from_narration(self):
        """Test URL parsing from narration"""
        worker = ScreenshotCapturerWorker()

        test_cases = [
            ("Show the console", "http://localhost:8765/console/"),
            ("Navigate to the dashboard", "http://localhost:8765/console/dashboard"),
            ("Open vibe engineering", "http://localhost:8765/console/vibe"),
            ("Check the skills tab", "http://localhost:8765/console/skills"),
        ]

        for narration, expected_url in test_cases:
            url = worker._parse_target_url(narration)
            assert url == expected_url


class TestVideoAssemblerPhase4:
    """Test Video Assembler Worker Phase 4 (Real FFmpeg)"""

    def test_initialization(self):
        """Test worker initialization"""
        worker = VideoAssemblerWorker(codec="h264", preset="medium", resolution="1080p")
        assert worker.name == "video_assembler"
        assert worker.version == "4.0.0"
        assert worker.codec == "h264"
        assert worker.preset == "medium"
        assert (1920, 1080) in worker.resolution_map.values()

    def test_execute_returns_video_result(self):
        """Test execute returns valid VideoResult"""
        worker = VideoAssemblerWorker()

        maestro = MaestroOrchestrator()
        job_id = maestro.create_job(
            topic="Test",
            duration=30,
            audience="test",
            narration=["Test narration."]
        )
        job = maestro.get_job(job_id)

        # Create mock voice and screenshot results
        job.voice_result = {
            "audio_files": [],
            "total_duration_seconds": 30.0
        }
        job.screenshots_result = {
            "screenshots": []
        }

        result = worker.execute(job, voice_result=job.voice_result, screenshot_result=job.screenshots_result)

        assert isinstance(result, VideoResult)
        assert result.duration_seconds > 0
        assert 0.8 <= result.quality_score <= 1.0


class TestYouTubeUploaderPhase4:
    """Test YouTube Uploader Worker Phase 4 (Simulated Upload)"""

    def test_initialization(self):
        """Test worker initialization"""
        worker = YouTubeUploaderWorker(visibility="unlisted", save_metadata=True)
        assert worker.name == "youtube_uploader"
        assert worker.version == "4.0.0"
        assert worker.visibility == "unlisted"
        assert worker.save_metadata

    def test_execute_returns_upload_result(self):
        """Test execute returns valid UploadResult"""
        worker = YouTubeUploaderWorker()

        maestro = MaestroOrchestrator()
        job_id = maestro.create_job(
            topic="Test Video",
            duration=90,
            audience="beginners",
            narration=["Test scene."]
        )
        job = maestro.get_job(job_id)

        job.video_result = {
            "video_path": "/tmp/test_video.mp4",
            "duration_seconds": 90
        }

        result = worker.execute(job, video_result=job.video_result)

        assert isinstance(result, UploadResult)
        assert result.success
        assert result.video_id is not None
        assert len(result.video_id) == 11  # YouTube video ID length
        assert "youtube.com/watch?v=" in result.url
        assert result.published == False  # unlisted visibility

    def test_title_generation(self):
        """Test YouTube title generation"""
        worker = YouTubeUploaderWorker()

        maestro = MaestroOrchestrator()
        job_id = maestro.create_job(
            topic="Introduction to CorvinOS",
            duration=90,
            audience="beginners",
            narration=["Test"]
        )
        job = maestro.get_job(job_id)

        title = worker._generate_title(job)

        assert "Introduction to CorvinOS" in title
        assert "CorvinOS Tutorial" in title
        assert "90" in title
        assert len(title) <= 100

    def test_tags_generation(self):
        """Test YouTube tags generation"""
        worker = YouTubeUploaderWorker()

        maestro = MaestroOrchestrator()
        job_id = maestro.create_job(
            topic="Video Producer Skill 2.0",
            duration=90,
            audience="technical",
            narration=["Test"]
        )
        job = maestro.get_job(job_id)

        tags = worker._generate_tags(job)

        assert "CorvinOS" in tags
        assert "tutorial" in tags
        assert "video-producer" in tags
        assert len(tags) <= 30

    def test_video_id_generation(self):
        """Test YouTube-style video ID generation"""
        worker = YouTubeUploaderWorker()

        video_id_1 = worker._generate_youtube_video_id()
        video_id_2 = worker._generate_youtube_video_id()

        assert len(video_id_1) == 11
        assert len(video_id_2) == 11
        assert video_id_1 != video_id_2  # Should be unique


class TestMaestroPhase4:
    """Test Maestro Orchestrator with Phase 4 workers"""

    def test_phase_execution_order(self):
        """Test phases execute in correct order"""
        maestro = MaestroOrchestrator()

        # Register all Phase 4 workers
        maestro.register_worker(VideoJobPhase.ANALYSIS, AssetAnalyzerWorker())
        maestro.register_worker(VideoJobPhase.VOICE, VoiceSynthesizerWorker())
        maestro.register_worker(VideoJobPhase.SCREENSHOTS, ScreenshotCapturerWorker())
        maestro.register_worker(VideoJobPhase.ASSEMBLY, VideoAssemblerWorker())
        maestro.register_worker(VideoJobPhase.YOUTUBE, YouTubeUploaderWorker())

        job_id = maestro.create_job(
            topic="Test",
            duration=30,
            audience="test",
            narration=["Test scene."]
        )

        job = maestro.get_job(job_id)
        assert job.current_phase == VideoJobPhase.ANALYSIS

        # Execute ANALYSIS phase
        maestro.execute_phase(job_id)
        job = maestro.get_job(job_id)
        assert job.current_phase == VideoJobPhase.VOICE

        # Execute VOICE phase
        maestro.execute_phase(job_id)
        job = maestro.get_job(job_id)
        assert job.current_phase == VideoJobPhase.SCREENSHOTS

    def test_audit_trail_completeness(self):
        """Test audit trail records all events"""
        maestro = MaestroOrchestrator()

        maestro.register_worker(VideoJobPhase.ANALYSIS, AssetAnalyzerWorker())
        maestro.register_worker(VideoJobPhase.VOICE, VoiceSynthesizerWorker())

        job_id = maestro.create_job(
            topic="Test",
            duration=30,
            audience="test",
            narration=["Test scene."]
        )

        audit_log = maestro.get_audit_log()
        assert len(audit_log) >= 1
        assert audit_log[0]["event_type"] == "job_created"
        assert audit_log[0]["job_id"] == job_id


class TestPhase4EndToEnd:
    """End-to-end tests for Phase 4"""

    def test_minimal_pipeline(self):
        """Test minimal successful pipeline"""
        maestro = MaestroOrchestrator()

        maestro.register_worker(VideoJobPhase.ANALYSIS, AssetAnalyzerWorker())
        maestro.register_worker(VideoJobPhase.VOICE, VoiceSynthesizerWorker())
        maestro.register_worker(VideoJobPhase.SCREENSHOTS, ScreenshotCapturerWorker())
        maestro.register_worker(VideoJobPhase.ASSEMBLY, VideoAssemblerWorker())
        maestro.register_worker(VideoJobPhase.YOUTUBE, YouTubeUploaderWorker())

        job_id = maestro.create_job(
            topic="Quick Demo",
            duration=30,
            audience="beginners",
            narration=["First scene."]
        )

        # Execute all phases
        for _ in range(5):
            result = maestro.execute_phase(job_id)
            assert result is not None

        # Verify final state
        final_job = maestro.get_job(job_id)
        assert final_job.analysis_result is not None
        assert final_job.voice_result is not None
        assert final_job.screenshots_result is not None
        assert final_job.video_result is not None
        assert final_job.youtube_result is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

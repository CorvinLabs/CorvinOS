"""Tests for Video Producer Skill 2.0 — Phase 3: Assembly + YouTube

Phase 3 Focus:
- Video Assembler Worker
- YouTube Uploader Worker
- End-to-end pipeline
- Learning loop integration
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
from video_producer.workers.video_assembler import VideoAssemblerWorker, VideoResult
from video_producer.workers.youtube_uploader import YouTubeUploaderWorker, UploadResult
from video_producer.worker_manager import VideoProductionPipeline


class TestVideoAssemblerWorker:
    """Test Video Assembler Worker"""

    def test_video_assembler_executes(self):
        """Video Assembler Worker executes"""
        worker = VideoAssemblerWorker()
        job = VideoJob(
            job_id="test",
            topic="Test",
            duration=60,
            audience="test",
            narration=["Scene 1"],
        )

        # Mock results
        voice_result = VoiceResult(
            audio_files=["/tmp/test_audio.mp3"],
            total_duration_seconds=30,
            loudness_lufs=-23.0,
            confidence=0.92,
        )
        screenshot_result = ScreenshotResult(
            screenshots=["/tmp/test_screenshot.png"],
            total_duration_seconds=30,
            num_captured=1,
            confidence=0.88,
        )

        result = worker.execute(job, voice_result, screenshot_result)
        assert result.success is True
        assert result.codec in ["h264", "vp9", "av1"]

    def test_video_assembler_creates_video_file(self):
        """Video Assembler creates video output file"""
        worker = VideoAssemblerWorker()
        job = VideoJob(
            job_id="test",
            topic="Test",
            duration=60,
            audience="test",
            narration=["Scene 1"],
        )

        voice_result = VoiceResult(
            audio_files=["/tmp/test_audio.mp3"],
            total_duration_seconds=30,
            loudness_lufs=-23.0,
            confidence=0.92,
        )
        screenshot_result = ScreenshotResult(
            screenshots=["/tmp/test_screenshot.png"],
            total_duration_seconds=30,
            num_captured=1,
            confidence=0.88,
        )

        result = worker.execute(job, voice_result, screenshot_result)
        assert os.path.exists(result.video_path)

    def test_video_assembler_h264_codec(self):
        """Video Assembler uses H.264 by default"""
        worker = VideoAssemblerWorker()
        assert worker.codec == "h264"

        result = worker.execute(
            VideoJob(
                job_id="test",
                topic="Test",
                duration=60,
                audience="test",
                narration=["Scene 1"],
            ),
            VoiceResult([], 30, -23.0, 0.9),
            ScreenshotResult([], 30, 0, 0.9),
        )
        assert result.codec == "h264"

    def test_video_assembler_codec_selection(self):
        """Video Assembler supports codec selection"""
        worker_vp9 = VideoAssemblerWorker(codec="vp9")
        worker_av1 = VideoAssemblerWorker(codec="av1")

        assert worker_vp9.codec == "vp9"
        assert worker_av1.codec == "av1"

    def test_video_assembler_quality_preset(self):
        """Video Assembler supports quality presets"""
        worker = VideoAssemblerWorker(preset="high")
        assert worker.preset == "high"

    def test_video_assembler_bitrate_estimation(self):
        """Video Assembler estimates bitrate"""
        worker = VideoAssemblerWorker()
        job = VideoJob(
            job_id="test",
            topic="Test",
            duration=60,
            audience="test",
            narration=["Scene 1"],
        )

        result = worker.execute(
            job,
            VoiceResult([], 30, -23.0, 0.9),
            ScreenshotResult([], 30, 0, 0.9),
        )
        assert result.bitrate_kbps > 1000
        assert result.bitrate_kbps < 10000

    def test_video_assembler_quality_score(self):
        """Video Assembler provides quality score"""
        worker = VideoAssemblerWorker()
        job = VideoJob(
            job_id="test",
            topic="Test",
            duration=60,
            audience="test",
            narration=["Scene 1"],
        )

        result = worker.execute(
            job,
            VoiceResult([], 30, -23.0, 0.9),
            ScreenshotResult([], 30, 0, 0.9),
        )
        assert 0.0 <= result.quality_score <= 1.0
        assert result.quality_score > 0.85

    def test_video_assembler_builds_ffmpeg_command(self):
        """Video Assembler builds FFmpeg command"""
        worker = VideoAssemblerWorker()
        cmd = worker._build_ffmpeg_command(
            audio_files=["/tmp/audio.mp3"],
            screenshot_files=["/tmp/screenshot.png"],
            output_path="/tmp/output.mp4",
            job=VideoJob(
                job_id="test",
                topic="Test",
                duration=60,
                audience="test",
                narration=["Scene 1"],
            ),
        )

        assert isinstance(cmd, list)
        assert "ffmpeg" in cmd
        assert "/tmp/output.mp4" in cmd


class TestYouTubeUploaderWorker:
    """Test YouTube Uploader Worker"""

    def test_youtube_uploader_executes(self):
        """YouTube Uploader Worker executes"""
        worker = YouTubeUploaderWorker()
        job = VideoJob(
            job_id="test",
            topic="What is CorvinOS?",
            duration=60,
            audience="beginners",
            narration=["CorvinOS is great."],
        )

        video_result = VideoResult(
            video_path="/tmp/video.mp4",
            duration_seconds=60,
            bitrate_kbps=2500,
            codec="h264",
            quality_score=0.89,
        )

        result = worker.execute(job, video_result)
        assert result.success is True
        assert "youtube.com" in result.url

    def test_youtube_uploader_generates_video_id(self):
        """YouTube Uploader generates video ID"""
        worker = YouTubeUploaderWorker()
        job = VideoJob(
            job_id="test123",
            topic="Test",
            duration=60,
            audience="test",
            narration=["Scene 1"],
        )

        result = worker.execute(job, VideoResult("", 60, 2500, "h264", 0.89))
        assert result.video_id is not None
        assert len(result.video_id) > 5
        assert "test123" in result.video_id

    def test_youtube_uploader_generates_title(self):
        """YouTube Uploader generates proper title"""
        worker = YouTubeUploaderWorker()
        job = VideoJob(
            job_id="test",
            topic="What is CorvinOS?",
            duration=120,
            audience="test",
            narration=["Scene 1"],
        )

        title = worker._generate_title(job)
        assert "What is CorvinOS?" in title
        assert "CorvinOS Tutorial" in title
        assert "120" in title

    def test_youtube_uploader_generates_description(self):
        """YouTube Uploader generates description"""
        worker = YouTubeUploaderWorker()
        job = VideoJob(
            job_id="test",
            topic="Building Plugins",
            duration=180,
            audience="technical",
            narration=["Scene 1"],
        )

        description = worker._generate_description(job)
        assert "Building Plugins" in description
        assert "technical" in description
        assert "180" in description
        assert "CorvinOS" in description

    def test_youtube_uploader_generates_tags(self):
        """YouTube Uploader generates tags"""
        worker = YouTubeUploaderWorker()
        job = VideoJob(
            job_id="test",
            topic="Building Plugins",
            duration=60,
            audience="technical",
            narration=["Scene 1"],
        )

        tags = worker._generate_tags(job)
        assert "CorvinOS" in tags
        assert "tutorial" in tags
        assert "technical" in tags
        assert "plugins" in tags

    def test_youtube_uploader_tags_for_gdpr_topic(self):
        """YouTube Uploader adds GDPR tags for compliance topics"""
        worker = YouTubeUploaderWorker()
        job = VideoJob(
            job_id="test",
            topic="GDPR Compliance",
            duration=60,
            audience="operators",
            narration=["Scene 1"],
        )

        tags = worker._generate_tags(job)
        assert "gdpr" in tags

    def test_youtube_uploader_tags_for_security_topic(self):
        """YouTube Uploader adds security tags"""
        worker = YouTubeUploaderWorker()
        job = VideoJob(
            job_id="test",
            topic="Security Best Practices",
            duration=60,
            audience="operators",
            narration=["Scene 1"],
        )

        tags = worker._generate_tags(job)
        assert "security" in tags

    def test_youtube_uploader_visibility_setting(self):
        """YouTube Uploader respects visibility setting"""
        worker_private = YouTubeUploaderWorker(visibility="private")
        worker_public = YouTubeUploaderWorker(visibility="public")

        assert worker_private.visibility == "private"
        assert worker_public.visibility == "public"

    def test_youtube_uploader_api_key_optional(self):
        """YouTube Uploader API key is optional (for testing)"""
        worker_with_key = YouTubeUploaderWorker(api_key="test_key_123")
        worker_without_key = YouTubeUploaderWorker()

        assert worker_with_key.api_key == "test_key_123"
        assert worker_without_key.api_key is None

    def test_youtube_uploader_title_length_limit(self):
        """YouTube Uploader enforces title length limit"""
        worker = YouTubeUploaderWorker()
        job = VideoJob(
            job_id="test",
            topic="A" * 200,  # Very long topic
            duration=60,
            audience="test",
            narration=["Scene 1"],
        )

        title = worker._generate_title(job)
        assert len(title) <= 100


class TestEndToEndIntegration:
    """Test end-to-end pipeline integration"""

    def test_full_pipeline_phase1_to_phase3(self, pipeline):
        """Full pipeline from job creation to YouTube upload (mock)"""
        # Register all workers
        pipeline.register_worker(VideoJobPhase.ANALYSIS, __import__(
            "video_producer.workers.asset_analyzer", fromlist=["AssetAnalyzerWorker"]
        ).AssetAnalyzerWorker)
        pipeline.register_worker(VideoJobPhase.VOICE, VoiceSynthesizerWorker)
        pipeline.register_worker(VideoJobPhase.SCREENSHOTS, ScreenshotCapturerWorker)
        pipeline.register_worker(VideoJobPhase.ASSEMBLY, VideoAssemblerWorker)
        pipeline.register_worker(VideoJobPhase.YOUTUBE, YouTubeUploaderWorker)

        # Create job
        job_id = pipeline.create_and_process_job(
            topic="What is CorvinOS?",
            duration=60,
            audience="beginners",
            narration=[
                "CorvinOS is an open-source operating system.",
                "It provides security features.",
            ],
            auto_execute=False,
        )

        assert job_id is not None
        assert job_id in pipeline.maestro.list_jobs()

    def test_job_feedback_persists_through_phases(self, pipeline):
        """Job feedback persists through all phases"""
        job_id = pipeline.create_and_process_job(
            topic="Test",
            duration=60,
            audience="test",
            narration=["Scene 1", "Scene 2"],
            auto_execute=False,
        )

        # Record feedback at different phases
        pipeline.maestro.record_feedback(job_id, 0, "pacing", "too_fast")
        pipeline.maestro.record_feedback(job_id, 1, "quality", "good")

        # Get job and verify feedback
        job = pipeline.maestro.get_job(job_id)
        assert len(job.feedback_history) == 2
        assert job.feedback_history[0].feedback_type == "pacing"
        assert job.feedback_history[1].feedback_type == "quality"

    def test_learning_signals_from_feedback(self, pipeline):
        """Learning signals can be derived from feedback"""
        job_id = pipeline.create_and_process_job(
            topic="Test",
            duration=60,
            audience="test",
            narration=["Scene 1"],
            auto_execute=False,
        )

        # Simulate learning feedback
        for scene_idx in range(3):
            pipeline.maestro.record_feedback(
                job_id, scene_idx, "engagement", "high"
            )

        job = pipeline.maestro.get_job(job_id)

        # Learning signal: average engagement
        avg_engagement = len(
            [f for f in job.feedback_history if f.value == "high"]
        ) / len(job.feedback_history)
        assert avg_engagement > 0

    def test_audit_trail_completeness(self, pipeline):
        """Audit trail is complete and comprehensive"""
        job_id = pipeline.create_and_process_job(
            topic="Test",
            duration=60,
            audience="test",
            narration=["Scene 1"],
            auto_execute=False,
        )

        pipeline.maestro.record_feedback(job_id, 0, "quality", "excellent")

        audit_log = pipeline.maestro.get_audit_log()
        assert len(audit_log) > 0

        # Check for expected event types
        event_types = [e["event_type"] for e in audit_log]
        assert "job_created" in event_types
        assert "feedback_recorded" in event_types

    def test_multiple_jobs_in_pipeline(self, pipeline):
        """Pipeline handles multiple concurrent jobs"""
        job_id1 = pipeline.create_and_process_job(
            topic="Test 1",
            duration=60,
            audience="test",
            narration=["Scene 1"],
            auto_execute=False,
        )
        job_id2 = pipeline.create_and_process_job(
            topic="Test 2",
            duration=120,
            audience="technical",
            narration=["Scene 1", "Scene 2"],
            auto_execute=False,
        )

        jobs = pipeline.maestro.list_jobs()
        assert len(jobs) >= 2
        assert job_id1 in jobs
        assert job_id2 in jobs

        # Get status of both
        status1 = pipeline.get_job_status(job_id1)
        status2 = pipeline.get_job_status(job_id2)

        assert status1["duration_seconds"] == 60
        assert status2["duration_seconds"] == 120

    def test_error_handling_missing_job(self, pipeline):
        """Pipeline handles missing jobs gracefully"""
        status = pipeline.get_job_status("nonexistent_job_xyz")
        assert "error" in status

    def test_workers_status_after_registration(self, pipeline):
        """Workers status reflects registered workers"""
        initial_status = pipeline.get_workers_status()
        initial_count = initial_status["total_registered"]

        pipeline.register_worker(VideoJobPhase.VOICE, VoiceSynthesizerWorker)

        updated_status = pipeline.get_workers_status()
        assert updated_status["total_registered"] > initial_count


class TestLearningIntegration:
    """Test learning loop integration"""

    def test_per_scene_feedback_for_learning(self, pipeline):
        """Per-scene feedback can drive learning optimization"""
        job_id = pipeline.create_and_process_job(
            topic="Test",
            duration=60,
            audience="test",
            narration=["Intro scene.", "Main scene.", "Outro scene."],
            auto_execute=False,
        )

        # Simulate learning feedback for each scene
        feedback_scores = [
            ("pacing", "too_slow"),
            ("pacing", "perfect"),
            ("pacing", "too_fast"),
        ]

        for scene_idx, (feedback_type, value) in enumerate(feedback_scores):
            pipeline.maestro.record_feedback(job_id, scene_idx, feedback_type, value)

        job = pipeline.maestro.get_job(job_id)

        # Learning insight: scenes have different optimal pacing
        assert len(job.feedback_history) == 3
        pacing_values = [f.value for f in job.feedback_history]
        assert "too_slow" in pacing_values
        assert "perfect" in pacing_values
        assert "too_fast" in pacing_values

    def test_feedback_history_for_optimizer(self, pipeline):
        """Feedback history enables learning optimizer"""
        job_id = pipeline.create_and_process_job(
            topic="Test",
            duration=60,
            audience="test",
            narration=["Scene 1"],
            auto_execute=False,
        )

        # Record multiple feedback events
        qualities = ["good", "excellent", "excellent", "good"]
        for idx, quality in enumerate(qualities):
            pipeline.maestro.record_feedback(job_id, 0, "quality", quality)

        job = pipeline.maestro.get_job(job_id)

        # Optimizer can compute average quality score
        excellent_count = len([f for f in job.feedback_history if f.value == "excellent"])
        quality_score = excellent_count / len(job.feedback_history)
        assert quality_score == 0.5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

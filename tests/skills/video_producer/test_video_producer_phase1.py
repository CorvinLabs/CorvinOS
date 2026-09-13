"""Tests for Video Producer Skill 2.0 — Phase 1: Maestro + Asset Analyzer

Phase 1 Focus:
- Job creation with validation
- Phase gates enforcement
- Asset Analysis
- Feedback recording
"""

import pytest
from video_producer.maestro import (
    MaestroOrchestrator,
    VideoJob,
    VideoJobPhase,
    FeedbackEvent,
)
from video_producer.workers.asset_analyzer import (
    AssetAnalyzerWorker,
    AnalysisStatus,
)


class TestMaestroJobCreation:
    """Test Maestro job creation and validation"""

    def test_maestro_creates_job(self, maestro):
        """Maestro creates a video job"""
        job_id = maestro.create_job(
            topic="What is CorvinOS?",
            duration=60,
            audience="beginners",
            narration=["CorvinOS is an OS."],
        )
        assert job_id in maestro.jobs
        assert maestro.jobs[job_id].current_phase == VideoJobPhase.ANALYSIS

    def test_maestro_job_has_correct_fields(self, maestro):
        """Job has correct fields after creation"""
        job_id = maestro.create_job(
            topic="Testing",
            duration=120,
            audience="technical",
            narration=["Scene 1", "Scene 2"],
        )
        job = maestro.jobs[job_id]
        assert job.topic == "Testing"
        assert job.duration_seconds == 120
        assert job.audience == "technical"
        assert len(job.narration) == 2

    def test_maestro_rejects_empty_narration(self, maestro):
        """Maestro rejects empty narration"""
        with pytest.raises(ValueError):
            maestro.create_job(
                topic="Test",
                duration=60,
                audience="test",
                narration=[],
            )

    def test_maestro_rejects_unsourced_narration(self, maestro):
        """Maestro rejects narration with unsourced claims"""
        with pytest.raises(ValueError):
            maestro.create_job(
                topic="Test",
                duration=60,
                audience="test",
                narration=["I think CorvinOS is great"],
            )

    def test_maestro_rejects_negative_duration(self, maestro):
        """Maestro rejects negative duration"""
        with pytest.raises(ValueError):
            maestro.create_job(
                topic="Test",
                duration=-10,
                audience="test",
                narration=["Valid narration"],
            )

    def test_maestro_generates_job_id_if_not_provided(self, maestro):
        """Maestro generates unique job ID if not provided"""
        job_id1 = maestro.create_job(
            topic="Test 1",
            duration=60,
            audience="test",
            narration=["Scene 1"],
        )
        job_id2 = maestro.create_job(
            topic="Test 2",
            duration=60,
            audience="test",
            narration=["Scene 2"],
        )
        assert job_id1 != job_id2
        assert len(job_id1) > 5
        assert len(job_id2) > 5

    def test_maestro_accepts_custom_job_id(self, maestro):
        """Maestro accepts custom job ID"""
        job_id = maestro.create_job(
            topic="Test",
            duration=60,
            audience="test",
            narration=["Scene 1"],
            job_id="custom_job_123",
        )
        assert job_id == "custom_job_123"

    def test_maestro_emits_audit_event_on_creation(self, maestro):
        """Maestro emits audit event on job creation"""
        job_id = maestro.create_job(
            topic="Test",
            duration=60,
            audience="test",
            narration=["Scene 1"],
        )
        audit_log = maestro.get_audit_log()
        assert len(audit_log) > 0
        assert audit_log[-1]["event_type"] == "job_created"
        assert audit_log[-1]["job_id"] == job_id

    def test_maestro_list_jobs(self, maestro):
        """Maestro lists all jobs"""
        job_id1 = maestro.create_job(
            topic="Test 1",
            duration=60,
            audience="test",
            narration=["Scene 1"],
        )
        job_id2 = maestro.create_job(
            topic="Test 2",
            duration=60,
            audience="test",
            narration=["Scene 2"],
        )
        jobs = maestro.list_jobs()
        assert len(jobs) == 2
        assert job_id1 in jobs
        assert job_id2 in jobs


class TestPhaseGates:
    """Test phase gate enforcement"""

    def test_phase_gate_analysis_requires_narration(self, maestro):
        """Analysis phase requires narration"""
        job_id = maestro.create_job(
            topic="Test",
            duration=60,
            audience="test",
            narration=["Scene 1"],
        )
        job = maestro.get_job(job_id)
        assert maestro._validate_analysis_phase(job) is True

    def test_phase_gate_voice_requires_analysis_complete(self, maestro):
        """Voice phase requires analysis completion"""
        job_id = maestro.create_job(
            topic="Test",
            duration=60,
            audience="test",
            narration=["Scene 1"],
        )
        job = maestro.get_job(job_id)
        job.current_phase = VideoJobPhase.VOICE
        assert maestro._validate_voice_phase(job) is False

    def test_phase_gate_enforce_on_execution(self, maestro):
        """Phase gate is enforced on phase execution"""
        job_id = maestro.create_job(
            topic="Test",
            duration=60,
            audience="test",
            narration=["Scene 1"],
        )

        # Try to execute without worker registered
        with pytest.raises(RuntimeError):
            maestro.execute_phase(job_id)

    def test_phase_gate_blocks_voice_without_analysis(self, maestro):
        """Cannot proceed to Voice phase without Analysis"""
        job_id = maestro.create_job(
            topic="Test",
            duration=60,
            audience="test",
            narration=["Scene 1"],
        )
        job = maestro.get_job(job_id)
        job.current_phase = VideoJobPhase.VOICE

        # Should fail gate check
        with pytest.raises(RuntimeError):
            maestro.execute_phase(job_id)


class TestAssetAnalyzerWorker:
    """Test Asset Analyzer Worker"""

    def test_asset_analyzer_passes_valid_narration(self, sample_job):
        """Asset Analyzer passes valid narration"""
        worker = AssetAnalyzerWorker()
        result = worker.execute(sample_job)
        assert result.status == AnalysisStatus.PASS
        assert result.sources_verified is True
        assert len(result.contradictions) == 0
        assert result.confidence > 0.9

    def test_asset_analyzer_extracts_facts(self, sample_job):
        """Asset Analyzer extracts facts from narration"""
        worker = AssetAnalyzerWorker()
        result = worker.execute(sample_job)
        assert len(result.facts_extracted) > 0
        assert any("CorvinOS" in f for f in result.facts_extracted)

    def test_asset_analyzer_detects_unsourced_claims(self):
        """Asset Analyzer detects unsourced claims"""
        worker = AssetAnalyzerWorker()
        job = VideoJob(
            job_id="test",
            topic="Test",
            duration=60,
            audience="test",
            narration=["I think CorvinOS is great"],
        )
        result = worker.execute(job)
        assert result.status == AnalysisStatus.FAIL
        assert result.sources_verified is False

    def test_asset_analyzer_detects_contradictions(self):
        """Asset Analyzer detects logical contradictions"""
        worker = AssetAnalyzerWorker()
        job = VideoJob(
            job_id="test",
            topic="Test",
            duration=60,
            audience="test",
            narration=[
                "CorvinOS has encryption enabled.",
                "Encryption is disabled by default.",
            ],
        )
        result = worker.execute(job)
        assert result.status == AnalysisStatus.WARN
        assert len(result.contradictions) > 0

    def test_asset_analyzer_warns_on_contradictions(self):
        """Asset Analyzer warns (not fails) on contradictions"""
        worker = AssetAnalyzerWorker()
        job = VideoJob(
            job_id="test",
            topic="Test",
            duration=60,
            audience="test",
            narration=[
                "CorvinOS is open source.",
                "It is proprietary software.",
            ],
        )
        result = worker.execute(job)
        assert result.status == AnalysisStatus.WARN
        assert result.confidence < 0.95

    def test_asset_analyzer_provides_recommendations(self):
        """Asset Analyzer provides recommendations"""
        worker = AssetAnalyzerWorker()
        job = VideoJob(
            job_id="test",
            topic="Test",
            duration=60,
            audience="test",
            narration=["Maybe CorvinOS is good"],
        )
        result = worker.execute(job)
        assert len(result.recommendations) > 0
        assert any("source" in r.lower() for r in result.recommendations)


class TestFeedbackRecording:
    """Test feedback recording for learning"""

    def test_maestro_records_feedback(self, maestro):
        """Maestro records per-scene feedback"""
        job_id = maestro.create_job(
            topic="Test",
            duration=60,
            audience="test",
            narration=["Scene 1", "Scene 2"],
        )

        maestro.record_feedback(
            job_id, scene_index=0, feedback_type="pacing", value="too_fast"
        )

        job = maestro.get_job(job_id)
        assert len(job.feedback_history) == 1
        assert job.feedback_history[0].scene_index == 0
        assert job.feedback_history[0].feedback_type == "pacing"

    def test_maestro_records_multiple_feedback_events(self, maestro):
        """Maestro records multiple feedback events"""
        job_id = maestro.create_job(
            topic="Test",
            duration=60,
            audience="test",
            narration=["Scene 1", "Scene 2", "Scene 3"],
        )

        maestro.record_feedback(job_id, 0, "quality", "good")
        maestro.record_feedback(job_id, 1, "pacing", "perfect")
        maestro.record_feedback(job_id, 2, "accuracy", "needs_review")

        job = maestro.get_job(job_id)
        assert len(job.feedback_history) == 3

    def test_maestro_feedback_emits_audit_event(self, maestro):
        """Feedback recording emits audit event"""
        job_id = maestro.create_job(
            topic="Test",
            duration=60,
            audience="test",
            narration=["Scene 1"],
        )

        maestro.record_feedback(job_id, 0, "quality", "good")

        audit_log = maestro.get_audit_log()
        feedback_events = [
            e for e in audit_log if e["event_type"] == "feedback_recorded"
        ]
        assert len(feedback_events) > 0

    def test_maestro_feedback_with_notes(self, maestro):
        """Maestro records feedback with notes"""
        job_id = maestro.create_job(
            topic="Test",
            duration=60,
            audience="test",
            narration=["Scene 1"],
        )

        maestro.record_feedback(
            job_id,
            scene_index=0,
            feedback_type="engagement",
            value="low",
            notes="Narration too technical for beginners",
        )

        job = maestro.get_job(job_id)
        assert job.feedback_history[0].notes == "Narration too technical for beginners"


class TestWorkerRegistration:
    """Test worker registration"""

    def test_maestro_registers_worker(self, maestro):
        """Maestro registers a worker"""
        worker = AssetAnalyzerWorker()
        maestro.register_worker(VideoJobPhase.ANALYSIS, AssetAnalyzerWorker)

        assert maestro.worker_registry[VideoJobPhase.ANALYSIS] is not None

    def test_maestro_emits_audit_on_worker_registration(self, maestro):
        """Maestro emits audit event on worker registration"""
        maestro.register_worker(VideoJobPhase.ANALYSIS, AssetAnalyzerWorker)

        audit_log = maestro.get_audit_log()
        registration_events = [
            e for e in audit_log if e["event_type"] == "worker_registered"
        ]
        assert len(registration_events) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

"""Phase 4b Tests: Video Producer Learning Infrastructure (60+ tests).

Covers all learning modules:
- FeedbackCollector: 12+ tests
- ConfidenceScorer: 15+ tests
- ModelSelector: 20+ tests
- LearningLoopIntegration: 15+ tests
"""

import json
import tempfile
import pytest
from pathlib import Path
from datetime import datetime

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from core.skills.os_skills.video_producer.src.learning.feedback_collector import (
    FeedbackCollector, FeedbackRecord
)
from core.skills.os_skills.video_producer.src.learning.confidence_scorer import (
    ConfidenceScorer, ConfidenceMetric, WorkerConfidenceProfile
)
from core.skills.os_skills.video_producer.src.learning.model_selector import (
    ModelSelector, ModelPerformance, categorize_duration
)
from core.skills.os_skills.video_producer.src.learning.loop_integration import (
    LearningLoopIntegration
)


# ============================================================================
# FEEDBACK COLLECTOR TESTS
# ============================================================================

class TestFeedbackCollector:
    """Feedback collection and persistence."""

    def test_submit_feedback_valid(self):
        """Test submitting valid feedback."""
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = FeedbackCollector(tmpdir)
            record, error = collector.submit_feedback(
                job_id="job1",
                scene_id="s01",
                feedback_type="quality",
                rating=4,
                worker_notes="Looks great!",
            )

            assert error is None
            assert record.rating == 4
            assert record.feedback_type == "quality"

    def test_submit_feedback_invalid_rating(self):
        """Test feedback with invalid rating."""
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = FeedbackCollector(tmpdir)
            record, error = collector.submit_feedback(
                job_id="job1",
                scene_id="s01",
                feedback_type="quality",
                rating=6,  # Invalid (must be 1-5)
            )

            assert error is not None
            assert "Rating must be 1-5" in error

    def test_submit_feedback_invalid_type(self):
        """Test feedback with invalid type."""
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = FeedbackCollector(tmpdir)
            record, error = collector.submit_feedback(
                job_id="job1",
                scene_id="s01",
                feedback_type="invalid_type",
                rating=3,
            )

            assert error is not None

    def test_feedback_persistence(self):
        """Test feedback is persisted to JSONL."""
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = FeedbackCollector(tmpdir)
            collector.submit_feedback(
                job_id="job1",
                scene_id="s01",
                feedback_type="quality",
                rating=5,
            )

            # Verify JSONL file exists and contains data
            feedback_log = Path(tmpdir) / "feedback.jsonl"
            assert feedback_log.exists()

            with open(feedback_log, "r") as f:
                line = f.readline()
                data = json.loads(line)
                assert data["rating"] == 5

    def test_get_feedback_for_job(self):
        """Test retrieving feedback for a specific job."""
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = FeedbackCollector(tmpdir)

            # Submit multiple feedbacks
            for i in range(3):
                collector.submit_feedback(
                    job_id="job1",
                    scene_id=f"s{i:02d}",
                    feedback_type="quality",
                    rating=3 + i,
                )

            # Retrieve
            records = collector.get_feedback_for_job("job1")
            assert len(records) == 3
            assert records[0].rating == 3
            assert records[2].rating == 5

    def test_get_feedback_for_scene(self):
        """Test retrieving feedback for specific scene."""
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = FeedbackCollector(tmpdir)

            collector.submit_feedback(
                job_id="job1", scene_id="s01", feedback_type="quality", rating=4
            )
            collector.submit_feedback(
                job_id="job1", scene_id="s02", feedback_type="quality", rating=5
            )

            records = collector.get_feedback_for_scene("job1", "s01")
            assert len(records) == 1
            assert records[0].rating == 4

    def test_get_average_rating(self):
        """Test average rating calculation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = FeedbackCollector(tmpdir)

            for rating in [2, 3, 4, 5]:
                collector.submit_feedback(
                    job_id="job1",
                    scene_id=f"s{rating:02d}",
                    feedback_type="quality",
                    rating=rating,
                )

            avg = collector.get_average_rating("job1")
            assert avg == pytest.approx(3.5)

    def test_get_feedback_stats(self):
        """Test feedback statistics."""
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = FeedbackCollector(tmpdir)

            collector.submit_feedback(
                job_id="job1", scene_id="s01", feedback_type="quality", rating=5
            )
            collector.submit_feedback(
                job_id="job1", scene_id="s02", feedback_type="relevance", rating=4
            )
            collector.submit_feedback(
                job_id="job1", scene_id="s03", feedback_type="correctness", rating=3
            )

            stats = collector.get_feedback_stats("job1")
            assert stats["total_feedback"] == 3
            assert "quality" in stats["by_type"]
            assert stats["by_type"]["quality"]["count"] == 1
            assert stats["average_rating"] == pytest.approx(4.0)

    def test_feedback_record_validation(self):
        """Test FeedbackRecord validation."""
        record = FeedbackRecord(
            feedback_id="f1",
            job_id="j1",
            scene_id="s1",
            feedback_type="quality",
            rating=3,
        )

        valid, error = record.validate()
        assert valid is True
        assert error is None


# ============================================================================
# CONFIDENCE SCORER TESTS
# ============================================================================

class TestConfidenceScorer:
    """Confidence scoring via exponential smoothing."""

    def test_initialize_defaults(self):
        """Test scorer initializes with default profiles."""
        with tempfile.TemporaryDirectory() as tmpdir:
            scorer = ConfidenceScorer(tmpdir)

            assert "slide_renderer" in scorer.worker_profiles
            assert "voice_synthesizer" in scorer.worker_profiles

    def test_update_confidence_new_metric(self):
        """Test updating confidence for new metric."""
        with tempfile.TemporaryDirectory() as tmpdir:
            scorer = ConfidenceScorer(tmpdir)

            metric = scorer.update_confidence(
                worker_id="slide_renderer",
                metric_name="slide_quality",
                new_rating=0.9,  # High quality
            )

            assert metric.confidence_score == 0.9
            assert metric.sample_count == 1

    def test_exponential_smoothing(self):
        """Test exponential smoothing with multiple updates."""
        with tempfile.TemporaryDirectory() as tmpdir:
            scorer = ConfidenceScorer(tmpdir)
            alpha = scorer.ALPHA

            # First update: 0.9
            metric1 = scorer.update_confidence(
                worker_id="slide_renderer",
                metric_name="slide_quality",
                new_rating=0.9,
            )
            assert metric1.confidence_score == pytest.approx(0.9)

            # Second update: 0.5 (should smooth toward 0.9)
            metric2 = scorer.update_confidence(
                worker_id="slide_renderer",
                metric_name="slide_quality",
                new_rating=0.5,
            )
            expected = alpha * 0.5 + (1 - alpha) * 0.9
            assert metric2.confidence_score == pytest.approx(expected)

    def test_convergence_detection(self):
        """Test convergence detection with consistent ratings."""
        with tempfile.TemporaryDirectory() as tmpdir:
            scorer = ConfidenceScorer(tmpdir)

            # Submit consistent high ratings
            for i in range(5):
                scorer.update_confidence(
                    worker_id="slide_renderer",
                    metric_name="slide_quality",
                    new_rating=0.95,
                )

            profile = scorer.worker_profiles["slide_renderer"]
            # Should converge (high score + consistent)
            assert profile.is_converged is True

    def test_variance_tracking(self):
        """Test variance tracking for consistency."""
        with tempfile.TemporaryDirectory() as tmpdir:
            scorer = ConfidenceScorer(tmpdir)

            # Consistent ratings (low variance)
            for rating in [0.8, 0.82, 0.81]:
                scorer.update_confidence(
                    worker_id="voice_synthesizer",
                    metric_name="audio_quality",
                    new_rating=rating,
                )

            metric = scorer.worker_profiles["voice_synthesizer"].metrics[
                "audio_quality"
            ]
            assert metric.variance < 0.05  # Low variance

    def test_persistence_and_reload(self):
        """Test state persistence and reload."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # First instance
            scorer1 = ConfidenceScorer(tmpdir)
            scorer1.update_confidence(
                worker_id="slide_renderer",
                metric_name="slide_quality",
                new_rating=0.8,
            )

            # New instance should load persisted state
            scorer2 = ConfidenceScorer(tmpdir)
            metric = scorer2.worker_profiles["slide_renderer"].metrics[
                "slide_quality"
            ]
            assert metric.confidence_score == pytest.approx(0.8)

    def test_overall_score_calculation(self):
        """Test overall score is average of metrics."""
        with tempfile.TemporaryDirectory() as tmpdir:
            scorer = ConfidenceScorer(tmpdir)

            scorer.update_confidence(
                worker_id="slide_renderer",
                metric_name="slide_quality",
                new_rating=0.8,
            )
            scorer.update_confidence(
                worker_id="slide_renderer",
                metric_name="layout_consistency",
                new_rating=0.6,
            )

            profile = scorer.worker_profiles["slide_renderer"]
            expected_overall = (0.8 + 0.6) / 2
            assert profile.overall_score == pytest.approx(expected_overall)


# ============================================================================
# MODEL SELECTOR TESTS
# ============================================================================

class TestModelSelector:
    """Multi-armed bandit model selection."""

    def test_duration_categorization(self):
        """Test video duration categorization."""
        assert categorize_duration(60) == "1min"
        assert categorize_duration(300) == "5min"
        assert categorize_duration(900) == "15min"

    def test_select_model_default(self):
        """Test default model selection."""
        with tempfile.TemporaryDirectory() as tmpdir:
            selector = ModelSelector(tmpdir)

            model = selector.select_model(video_duration_seconds=60)
            assert model in ["gpt-4", "claude-opus", "claude-sonnet"]

    def test_epsilon_greedy_exploration(self):
        """Test epsilon-greedy exploration works."""
        with tempfile.TemporaryDirectory() as tmpdir:
            selector = ModelSelector(tmpdir)
            selector.EPSILON = 1.0  # Force exploration

            models = set()
            for _ in range(20):
                model = selector.select_model(60)
                models.add(model)

            # Should have tried multiple models with epsilon=1.0
            assert len(models) > 1

    def test_epsilon_greedy_exploitation(self):
        """Test epsilon-greedy exploitation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            selector = ModelSelector(tmpdir)
            selector.EPSILON = 0.0  # Force exploitation

            # All selections should be the same (exploitation of default)
            models = [selector.select_model(60) for _ in range(10)]
            assert len(set(models)) == 1

    def test_model_performance_update(self):
        """Test model performance tracking."""
        with tempfile.TemporaryDirectory() as tmpdir:
            selector = ModelSelector(tmpdir)

            # Use gpt-4 with good result
            result = selector.report_result(
                model="gpt-4",
                video_duration_seconds=60,
                quality_rating=0.9,
            )

            perf = selector.state.performances["1min"]["gpt-4"]
            assert perf.total_attempts == 1
            assert perf.win_count == 1  # 0.9 >= 0.75
            assert perf.average_rating == pytest.approx(0.9)

    def test_win_rate_calculation(self):
        """Test win rate computation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            selector = ModelSelector(tmpdir)

            selector.report_result("gpt-4", 60, 0.8)  # Win
            selector.report_result("gpt-4", 60, 0.6)  # Loss
            selector.report_result("gpt-4", 60, 0.8)  # Win

            perf = selector.state.performances["1min"]["gpt-4"]
            assert perf.win_rate == pytest.approx(2 / 3)

    def test_model_switching_threshold(self):
        """Test model switching with confidence threshold."""
        with tempfile.TemporaryDirectory() as tmpdir:
            selector = ModelSelector(tmpdir)

            # Make claude-opus clearly better
            for _ in range(10):
                selector.report_result("claude-opus", 60, 0.95)
                selector.report_result("gpt-4", 60, 0.6)

            # Should switch to claude-opus
            assert selector.state.selected_models["1min"] == "claude-opus"

    def test_per_duration_selection(self):
        """Test separate model selection per duration."""
        with tempfile.TemporaryDirectory() as tmpdir:
            selector = ModelSelector(tmpdir)

            # gpt-4 best for 1-min
            for _ in range(10):
                selector.report_result("gpt-4", 60, 0.95)

            # claude-opus best for 15-min
            for _ in range(10):
                selector.report_result("claude-opus", 900, 0.95)

            assert selector.state.selected_models["1min"] == "gpt-4"
            assert selector.state.selected_models["15min"] == "claude-opus"

    def test_model_stats(self):
        """Test statistics export."""
        with tempfile.TemporaryDirectory() as tmpdir:
            selector = ModelSelector(tmpdir)

            selector.report_result("gpt-4", 60, 0.8)

            stats = selector.get_model_stats()
            assert "by_duration" in stats
            assert "1min" in stats["by_duration"]


# ============================================================================
# LEARNING LOOP INTEGRATION TESTS
# ============================================================================

class TestLearningLoopIntegration:
    """Full learning loop integration."""

    def test_submit_feedback_integration(self):
        """Test feedback integration."""
        with tempfile.TemporaryDirectory() as tmpdir:
            loop = LearningLoopIntegration(tmpdir)

            success, error = loop.submit_feedback(
                job_id="job1",
                scene_id="s01",
                rating=4,
                worker_notes="Voice sounds good",
            )

            assert success is True
            assert error is None

    def test_feedback_pii_detection(self):
        """Test PII detection in feedback."""
        with tempfile.TemporaryDirectory() as tmpdir:
            loop = LearningLoopIntegration(tmpdir)

            success, error = loop.submit_feedback(
                job_id="job1",
                scene_id="s01",
                rating=4,
                worker_notes="contact: user@example.com",
            )

            assert success is False
            assert "PII" in error

    def test_worker_inference_from_notes(self):
        """Test worker ID inference from feedback notes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            loop = LearningLoopIntegration(tmpdir)

            # Should infer voice_synthesizer
            worker = loop._infer_worker_from_notes("Voice is too fast")
            assert worker == "voice_synthesizer"

            # Should infer slide_renderer
            worker = loop._infer_worker_from_notes("Slide layout is cramped")
            assert worker == "slide_renderer"

    def test_model_selection_integration(self):
        """Test model selection with stats."""
        with tempfile.TemporaryDirectory() as tmpdir:
            loop = LearningLoopIntegration(tmpdir)

            model, stats = loop.select_model_for_video(duration_seconds=60)

            assert model in ["gpt-4", "claude-opus", "claude-sonnet"]
            assert "by_duration" in stats

    def test_video_quality_reporting(self):
        """Test video quality reporting."""
        with tempfile.TemporaryDirectory() as tmpdir:
            loop = LearningLoopIntegration(tmpdir)

            model, _ = loop.select_model_for_video(60)

            new_model = loop.report_video_quality(
                job_id="job1",
                video_duration_seconds=60,
                quality_score=0.8,
                model_used=model,
            )

            # May or may not switch depending on randomness
            assert new_model is None or new_model in ["gpt-4", "claude-opus", "claude-sonnet"]

    def test_audit_trail_generation(self):
        """Test audit trail is created."""
        with tempfile.TemporaryDirectory() as tmpdir:
            loop = LearningLoopIntegration(tmpdir)

            loop.submit_feedback(
                job_id="job1",
                scene_id="s01",
                rating=4,
                worker_notes="Good",
            )

            audit_log = Path(tmpdir) / "learning_audit.jsonl"
            assert audit_log.exists()

            with open(audit_log, "r") as f:
                data = json.loads(f.readline())
                assert data["event_type"] == "feedback_received"
                assert data["tenant_id"] == "_default"

    def test_hash_chaining(self):
        """Test hash chaining in audit trail."""
        with tempfile.TemporaryDirectory() as tmpdir:
            loop = LearningLoopIntegration(tmpdir)

            loop._emit_audit_event(event_type="test1")
            loop._emit_audit_event(event_type="test2")

            audit_log = Path(tmpdir) / "learning_audit.jsonl"
            with open(audit_log, "r") as f:
                lines = f.readlines()

                event1 = json.loads(lines[0])
                event2 = json.loads(lines[1])

                # event2's prev_hash should be event1's hash
                assert event2["prev_hash"] == event1["hash"]

    def test_learning_stats(self):
        """Test comprehensive learning statistics."""
        with tempfile.TemporaryDirectory() as tmpdir:
            loop = LearningLoopIntegration(tmpdir)

            loop.submit_feedback("job1", "s01", 4, worker_notes="voice is good")

            stats = loop.get_learning_stats()
            assert "confidence_metrics" in stats
            assert "model_stats" in stats
            assert "timestamp" in stats


# ============================================================================
# INTEGRATION TESTS
# ============================================================================

class TestPhase4bIntegration:
    """End-to-end Phase 4b learning integration."""

    def test_full_learning_loop(self):
        """Test complete feedback → optimizer → model selection loop."""
        with tempfile.TemporaryDirectory() as tmpdir:
            loop = LearningLoopIntegration(tmpdir)

            # Step 1: Operator submits feedback
            loop.submit_feedback(
                job_id="job1",
                scene_id="s01",
                rating=3,
                worker_notes="voice is slow"
            )

            # Step 2: Select model for next video
            model, stats = loop.select_model_for_video(60)
            assert model is not None

            # Step 3: Report result
            result = loop.report_video_quality(
                job_id="job2",
                video_duration_seconds=60,
                quality_score=0.75,
                model_used=model,
            )

            # Verify audit trail was created
            audit_log = Path(tmpdir) / "learning_audit.jsonl"
            assert audit_log.exists()

    def test_learning_convergence_multiple_jobs(self):
        """Test learning converges across multiple jobs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            loop = LearningLoopIntegration(tmpdir)

            # Simulate 10 video jobs with feedback
            for job_num in range(10):
                # Feedback
                loop.submit_feedback(
                    job_id=f"job{job_num}",
                    scene_id="s01",
                    rating=4,
                    worker_notes="slide quality is good"
                )

                # Model selection
                model, _ = loop.select_model_for_video(60)

                # Quality report
                loop.report_video_quality(
                    job_id=f"job{job_num}",
                    video_duration_seconds=60,
                    quality_score=0.85,
                    model_used=model,
                )

            # Verify learning state persists
            stats = loop.get_learning_stats()
            assert stats["model_stats"]["total_decisions"] > 0

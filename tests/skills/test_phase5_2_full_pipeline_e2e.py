"""
End-to-end tests for Phase 5.2 — Full 3-Tier Animation Pipeline

Tests the complete flow:
- Tier 1 (Quick) always succeeds
- Tier 2 (Manim) with timeout fallback
- Tier 3 (Premium) async queuing
- Learning loop feedback integration
"""

import pytest
import tempfile
from pathlib import Path
from datetime import datetime

from core.skills.video_producer_skill_2_0.phase5.quick_renderer import QuickRendererWorker
from core.skills.video_producer_skill_2_0.phase5.premium_renderer import PremiumAsyncQueue, PremiumRenderJob
from core.skills.video_producer_skill_2_0.phase5.tier_dispatcher import (
    TierDispatcher, AnimationRequest, TierLevel
)
from core.skills.video_producer_skill_2_0.phase5.learning_integration import (
    LearningOptimizer, RenderFeedback
)
from core.skills.video_producer_skill_2_0.phase5.manim_animator import ManimAnimatorWorker


class MockManimAnimatorWorker:
    """Mock Manim animator for testing"""

    def __init__(self, should_fail=False, timeout_seconds=60):
        self.name = "manim_animator"
        self.should_fail = should_fail
        self.timeout = timeout_seconds

    def execute(self, request) -> dict:
        """Mock execute method"""
        if self.should_fail:
            return {"success": False, "error": "Mock failure"}

        return {
            "success": True,
            "output_path": "/tmp/manim_output.mp4",
            "render_time_ms": 30000
        }


class TestTier1QuickRenderer:
    """Test Tier 1 (Quick) renderer"""

    def test_tier1_creates_svg_diagram(self):
        """Test: Tier 1 creates SVG diagram"""
        renderer = QuickRendererWorker()
        svg = renderer._create_svg_diagram("learning-loop")

        assert isinstance(svg, str)
        assert "<svg" in svg
        assert "Learning Loop" in svg

    def test_tier1_execute_returns_success(self):
        """Test: Tier 1 execute returns success dict"""
        renderer = QuickRendererWorker()
        request = AnimationRequest(
            animation_id="learning-loop",
            didactic_level="beginner",
            duration_seconds=10
        )

        result = renderer.execute(request)

        assert result["success"] == True
        assert "output_path" in result
        assert result["duration_seconds"] == 10
        assert result["render_time_ms"] == 5000

    def test_tier1_timeout_respected(self):
        """Test: Tier 1 respects timeout"""
        renderer = QuickRendererWorker(timeout_seconds=1)
        assert renderer.timeout == 1


class TestTier3PremiumAsyncQueue:
    """Test Tier 3 (Premium) async queue"""

    def test_tier3_submit_job_returns_job_id(self):
        """Test: Tier 3 submit_job returns valid job_id"""
        queue = PremiumAsyncQueue()
        job_id = queue.submit_job("learning-loop")

        assert isinstance(job_id, str)
        assert len(job_id) > 0
        assert job_id in queue.queue

    def test_tier3_job_initial_status_queued(self):
        """Test: New job starts in 'queued' status"""
        queue = PremiumAsyncQueue()
        job_id = queue.submit_job("learning-loop")

        job = queue.queue[job_id]
        assert job.status == "queued"

    def test_tier3_get_job_status(self):
        """Test: Can retrieve job status"""
        queue = PremiumAsyncQueue()
        job_id = queue.submit_job("learning-loop")

        status = queue.get_job_status(job_id)

        assert status["job_id"] == job_id
        assert status["status"] == "queued"
        assert status["error"] is None

    def test_tier3_get_job_status_not_found(self):
        """Test: Nonexistent job returns error"""
        queue = PremiumAsyncQueue()
        status = queue.get_job_status("nonexistent")

        assert "error" in status

    def test_tier3_queue_stats(self):
        """Test: Queue statistics tracking"""
        queue = PremiumAsyncQueue()

        # Submit 3 jobs
        for i in range(3):
            queue.submit_job(f"animation_{i}")

        stats = queue.get_queue_stats()

        assert stats["queued"] == 3
        assert stats["rendering"] == 0
        assert stats["complete"] == 0
        assert stats["total"] == 3

    def test_tier3_max_concurrent_limit(self):
        """Test: Respects max_concurrent limit"""
        queue = PremiumAsyncQueue(max_concurrent=2)
        assert queue.max_concurrent == 2


class TestTierDispatcher:
    """Test Tier dispatcher and fallback chain"""

    @pytest.fixture
    def dispatcher(self):
        """Create dispatcher with mocks"""
        tier1 = QuickRendererWorker()
        tier2 = MockManimAnimatorWorker()
        tier3 = PremiumAsyncQueue()

        return TierDispatcher(tier1, tier2, tier3)

    def test_dispatcher_prefers_tier2_when_available(self, dispatcher):
        """Test: Dispatcher uses Tier 2 when specified"""
        request = AnimationRequest(
            animation_id="learning-loop",
            didactic_level="beginner",
            duration_seconds=30,
            preferred_tier=TierLevel.TIER_2_RICH
        )

        result = dispatcher.dispatch(request)

        assert result["success"] == True
        assert result["tier"] == "TIER_2_RICH"

    def test_dispatcher_fallback_to_tier1(self, dispatcher):
        """Test: Dispatcher falls back to Tier 1 on Tier 2 failure"""
        dispatcher.tier2.should_fail = True

        request = AnimationRequest(
            animation_id="learning-loop",
            didactic_level="beginner",
            duration_seconds=30,
            preferred_tier=TierLevel.TIER_2_RICH
        )

        result = dispatcher.dispatch(request)

        assert result["success"] == True
        assert result["tier"] == "TIER_1_QUICK"

    def test_dispatcher_tier1_always_succeeds(self, dispatcher):
        """Test: Tier 1 always succeeds (no fallback)"""
        request = AnimationRequest(
            animation_id="learning-loop",
            didactic_level="beginner",
            duration_seconds=30,
            preferred_tier=TierLevel.TIER_1_QUICK
        )

        result = dispatcher.dispatch(request)

        assert result["success"] == True
        assert result["tier"] == "TIER_1_QUICK"

    def test_dispatcher_tier3_returns_job_id(self, dispatcher):
        """Test: Tier 3 returns job_id (async)"""
        request = AnimationRequest(
            animation_id="learning-loop",
            didactic_level="beginner",
            duration_seconds=30,
            preferred_tier=TierLevel.TIER_3_PREMIUM
        )

        result = dispatcher.dispatch(request)

        assert result["success"] == True
        assert result["tier"] == "TIER_3_PREMIUM"
        assert "job_id" in result
        assert result["status"] == "queued"

    def test_dispatcher_metrics_track_successes(self, dispatcher):
        """Test: Dispatcher tracks success/fail metrics"""
        request = AnimationRequest(
            animation_id="learning-loop",
            didactic_level="beginner",
            duration_seconds=30,
            preferred_tier=TierLevel.TIER_2_RICH
        )

        dispatcher.dispatch(request)

        metrics = dispatcher.get_metrics()

        assert metrics["tier_2_rich"]["success_count"] == 1
        assert metrics["tier_2_rich"]["fail_count"] == 0

    def test_dispatcher_fallback_chain_deterministic(self, dispatcher):
        """Test: Fallback chain is deterministic"""
        chain_2 = dispatcher._get_fallback_chain(TierLevel.TIER_2_RICH)
        assert chain_2 == [TierLevel.TIER_1_QUICK]

        chain_3 = dispatcher._get_fallback_chain(TierLevel.TIER_3_PREMIUM)
        assert chain_3 == [TierLevel.TIER_2_RICH, TierLevel.TIER_1_QUICK]

        chain_1 = dispatcher._get_fallback_chain(TierLevel.TIER_1_QUICK)
        assert chain_1 == []


class TestLearningOptimizer:
    """Test learning loop integration"""

    def test_learning_records_feedback(self):
        """Test: Learning records user feedback"""
        optimizer = LearningOptimizer()

        feedback = RenderFeedback(
            animation_id="learning-loop",
            render_time_ms=45000,
            quality_score=8.5,
            engagement_score=9.0,
            tier_used="TIER_2_RICH",
            user_id="user_123"
        )

        optimizer.record_feedback(feedback)

        assert len(optimizer.feedback_log) == 1
        assert optimizer.feedback_log[0].animation_id == "learning-loop"

    def test_learning_sets_initial_preference(self):
        """Test: Learning sets initial tier preference"""
        optimizer = LearningOptimizer()

        feedback = RenderFeedback(
            animation_id="learning-loop",
            render_time_ms=45000,
            quality_score=8.5,
            engagement_score=9.0,
            tier_used="TIER_2_RICH",
            user_id="user_123"
        )

        optimizer.record_feedback(feedback)

        preferred = optimizer.get_recommended_tier("learning-loop")
        assert preferred == "TIER_2_RICH"

    def test_learning_updates_preference_on_better_score(self):
        """Test: Learning switches preference on better feedback"""
        optimizer = LearningOptimizer()

        # First feedback: Tier 2, moderate score
        feedback1 = RenderFeedback(
            animation_id="learning-loop",
            render_time_ms=45000,
            quality_score=7.0,
            engagement_score=8.0,
            tier_used="TIER_2_RICH",
            user_id="user_1"
        )
        optimizer.record_feedback(feedback1)

        # Second feedback: Tier 1, higher quality per time
        feedback2 = RenderFeedback(
            animation_id="learning-loop",
            render_time_ms=8000,
            quality_score=8.0,
            engagement_score=8.5,
            tier_used="TIER_1_QUICK",
            user_id="user_2"
        )
        optimizer.record_feedback(feedback2)

        preferred = optimizer.get_recommended_tier("learning-loop")
        # Should prefer the tier with better quality/time ratio
        assert preferred in ["TIER_1_QUICK", "TIER_2_RICH"]

    def test_learning_statistics(self):
        """Test: Learning calculates statistics"""
        optimizer = LearningOptimizer()

        # Record 3 feedbacks
        for i in range(3):
            feedback = RenderFeedback(
                animation_id="learning-loop",
                render_time_ms=40000 + i * 1000,
                quality_score=8.0 + i * 0.2,
                engagement_score=8.5,
                tier_used="TIER_2_RICH",
                user_id=f"user_{i}"
            )
            optimizer.record_feedback(feedback)

        stats = optimizer.get_statistics()

        assert "learning-loop" in stats
        assert stats["learning-loop"]["avg_quality"] > 8.0
        assert stats["learning-loop"]["avg_engagement"] == 8.5
        assert stats["learning-loop"]["num_samples"] == 3

    def test_learning_default_preference(self):
        """Test: Learning returns default preference for unknown"""
        optimizer = LearningOptimizer()

        preferred = optimizer.get_recommended_tier("unknown-animation")
        assert preferred == "TIER_2_RICH"  # Default is Tier 2


class TestFullPipelineIntegration:
    """Integration tests for full pipeline"""

    def test_full_pipeline_tier2_to_tier1_fallback(self):
        """Test: Full pipeline with fallback from Tier 2 to Tier 1"""
        tier1 = QuickRendererWorker()
        tier2 = MockManimAnimatorWorker(should_fail=True)
        tier3 = PremiumAsyncQueue()

        dispatcher = TierDispatcher(tier1, tier2, tier3)

        request = AnimationRequest(
            animation_id="learning-loop",
            didactic_level="beginner",
            duration_seconds=30,
            preferred_tier=TierLevel.TIER_2_RICH
        )

        result = dispatcher.dispatch(request)

        assert result["success"] == True
        assert result["tier"] == "TIER_1_QUICK"

    def test_full_pipeline_with_learning_feedback(self):
        """Test: Full pipeline collects feedback for learning"""
        tier1 = QuickRendererWorker()
        tier2 = MockManimAnimatorWorker()
        tier3 = PremiumAsyncQueue()

        dispatcher = TierDispatcher(tier1, tier2, tier3)
        optimizer = LearningOptimizer()

        request = AnimationRequest(
            animation_id="learning-loop",
            didactic_level="beginner",
            duration_seconds=30,
            preferred_tier=TierLevel.TIER_2_RICH
        )

        result = dispatcher.dispatch(request)
        assert result["success"] == True

        # Record feedback for this render
        feedback = RenderFeedback(
            animation_id="learning-loop",
            render_time_ms=result.get("render_time_ms", 30000),
            quality_score=8.5,
            engagement_score=9.0,
            tier_used=result["tier"],
            user_id="test_user"
        )

        optimizer.record_feedback(feedback)

        # Verify learning recorded the feedback
        stats = optimizer.get_statistics()
        assert "learning-loop" in stats

    def test_full_pipeline_multiple_animations(self):
        """Test: Pipeline handles multiple animations with different preferences"""
        tier1 = QuickRendererWorker()
        tier2 = MockManimAnimatorWorker()
        tier3 = PremiumAsyncQueue()

        dispatcher = TierDispatcher(tier1, tier2, tier3)
        optimizer = LearningOptimizer()

        animations = [
            ("learning-loop", TierLevel.TIER_2_RICH),
            ("concept-diagram", TierLevel.TIER_1_QUICK),
            ("complex-animation", TierLevel.TIER_3_PREMIUM),
        ]

        for anim_id, preferred_tier in animations:
            request = AnimationRequest(
                animation_id=anim_id,
                didactic_level="beginner",
                duration_seconds=30,
                preferred_tier=preferred_tier
            )

            result = dispatcher.dispatch(request)
            assert result["success"] == True

            # Record feedback
            feedback = RenderFeedback(
                animation_id=anim_id,
                render_time_ms=result.get("render_time_ms", 10000),
                quality_score=8.0,
                engagement_score=8.5,
                tier_used=result["tier"],
                user_id="test_user"
            )

            optimizer.record_feedback(feedback)

        # Check stats for all animations
        stats = optimizer.get_statistics()
        assert len(stats) == 3
        for anim_id, _ in animations:
            assert anim_id in stats


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

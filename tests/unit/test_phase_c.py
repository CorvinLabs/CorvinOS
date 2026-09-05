"""Phase C tests: Learning loop + Meta-Skills."""

import pytest
from core.engine.skill_learning_loop import SkillLearningLoop, SkillFeedback, SkillConfig
from core.engine.meta_skills import SkillOptimizer, SkillDebugger


class TestLearningLoop:
    """Test learning loop (Tier 1-3)."""

    @pytest.fixture
    def loop(self):
        return SkillLearningLoop()

    @pytest.mark.asyncio
    async def test_record_feedback(self, loop):
        """Record feedback event."""
        feedback = SkillFeedback(
            tenant_id="_default",
            skill_id="os.test",
            request_id="req_1",
            outcome="success",
            confidence=0.85,
            quality_score=0.9,
            latency_ms=100,
            cost_usd=0.01,
        )

        await loop.record_feedback(feedback)
        assert "os.test" in loop.feedback_history
        assert len(loop.feedback_history["os.test"]) == 1

    @pytest.mark.asyncio
    async def test_optimize_config(self, loop):
        """Optimize Skill config based on feedback."""
        skill_id = "os.test"
        config = SkillConfig(
            skill_id=skill_id,
            version="1.0",
            temperature=0.7,
            max_tokens=2048,
        )

        # Record 5 high-quality feedback samples
        for i in range(5):
            feedback = SkillFeedback(
                tenant_id="_default",
                skill_id=skill_id,
                request_id=f"req_{i}",
                outcome="success",
                confidence=0.9,
                quality_score=0.95,
                latency_ms=100,
                cost_usd=0.01,
            )
            await loop.record_feedback(feedback)

        new_config = await loop.optimize_config(skill_id, config)

        # Temperature should decrease (high quality → more deterministic)
        assert new_config.temperature < config.temperature

    @pytest.mark.asyncio
    async def test_get_stats(self, loop):
        """Get Skill performance stats."""
        skill_id = "os.test"

        for i in range(3):
            feedback = SkillFeedback(
                tenant_id="_default",
                skill_id=skill_id,
                request_id=f"req_{i}",
                outcome="success",
                confidence=0.8,
                quality_score=0.85,
                latency_ms=50,
                cost_usd=0.01,
            )
            await loop.record_feedback(feedback)

        stats = loop.get_stats(skill_id)
        assert stats["total_invocations"] == 3
        assert stats["success_rate"] == 1.0  # All success


class TestMetaSkills:
    """Test meta-Skills (Tier 2-3)."""

    @pytest.fixture
    def setup(self):
        loop = SkillLearningLoop()
        return {
            "loop": loop,
            "optimizer": SkillOptimizer(loop),
            "debugger": SkillDebugger(loop),
        }

    @pytest.mark.asyncio
    async def test_optimizer_init(self, setup):
        """Optimizer initializes."""
        assert setup["optimizer"] is not None

    @pytest.mark.asyncio
    async def test_debugger_no_data(self, setup):
        """Debugger handles missing data gracefully."""
        result = await setup["debugger"].debug("unknown_skill")
        assert "error" in result or "status" in result

    @pytest.mark.asyncio
    async def test_optimizer_needs_min_samples(self, setup):
        """Optimizer needs ≥5 feedback samples."""
        config = SkillConfig(skill_id="os.test", version="1.0")

        # Only 1 feedback sample
        feedback = SkillFeedback(
            tenant_id="_default",
            skill_id="os.test",
            request_id="req_1",
            outcome="success",
            confidence=0.8,
            quality_score=0.9,
            latency_ms=100,
            cost_usd=0.01,
        )
        await setup["loop"].record_feedback(feedback)

        new_config = await setup["optimizer"].optimize("os.test", config)

        # Config should not change (insufficient data)
        assert new_config.version == config.version


class TestAdversarial:
    """Adversarial tests (Tier 5)."""

    def test_feedback_immutable(self):
        """SkillFeedback is frozen."""
        feedback = SkillFeedback(
            tenant_id="test",
            skill_id="test",
            request_id="req",
            outcome="success",
            confidence=0.5,
            quality_score=0.5,
            latency_ms=100,
            cost_usd=0.01,
        )
        with pytest.raises(AttributeError):
            feedback.outcome = "failure"

    def test_config_mutable(self):
        """SkillConfig is mutable (for learning)."""
        config = SkillConfig(skill_id="test", version="1.0", temperature=0.7)
        config.temperature = 0.5  # Should work
        assert config.temperature == 0.5

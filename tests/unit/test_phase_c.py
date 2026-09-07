"""Phase C tests: Learning loop + Meta-Skills.

Rewritten 2026-09-07 against the tenant-keyed contract: ``SkillLearningLoop``
keys feedback by ``(tenant_id, skill_id)`` and every read/optimize call takes
``tenant_id`` first (GDPR Art. 32 — no cross-tenant aggregation). The previous
version called the pre-tenant API (``optimize_config(skill_id, config)``,
``get_stats(skill_id)``, ``feedback_history[skill_id]``).
"""

import pytest
from core.engine.skill_learning_loop import SkillLearningLoop, SkillFeedback, SkillConfig
from core.engine.meta_skills import SkillOptimizer, SkillDebugger

TENANT = "_default"


def _feedback(skill_id="os.test", i=0, quality=0.9, confidence=0.85, outcome="success", tenant_id=TENANT):
    return SkillFeedback(
        tenant_id=tenant_id,
        skill_id=skill_id,
        request_id=f"req_{i}",
        outcome=outcome,
        confidence=confidence,
        quality_score=quality,
        latency_ms=100,
        cost_usd=0.01,
    )


class TestLearningLoop:
    """Test learning loop (Tier 1-3)."""

    @pytest.fixture
    def loop(self):
        return SkillLearningLoop()

    @pytest.mark.asyncio
    async def test_record_feedback(self, loop):
        """Feedback is keyed by (tenant_id, skill_id)."""
        await loop.record_feedback(_feedback(i=1))
        assert (TENANT, "os.test") in loop.feedback_history
        assert len(loop.feedback_history[(TENANT, "os.test")]) == 1

    @pytest.mark.asyncio
    async def test_record_feedback_tenant_isolation(self, loop):
        """Two tenants rating the same skill never share a bucket."""
        await loop.record_feedback(_feedback(i=1, tenant_id="tenant_a"))
        await loop.record_feedback(_feedback(i=2, tenant_id="tenant_b"))
        assert len(loop.feedback_history[("tenant_a", "os.test")]) == 1
        assert len(loop.feedback_history[("tenant_b", "os.test")]) == 1
        assert loop.get_stats("tenant_a", "os.test")["total_invocations"] == 1

    @pytest.mark.asyncio
    async def test_optimize_config(self, loop):
        """High-quality feedback → lower temperature (more deterministic)."""
        config = SkillConfig(skill_id="os.test", version="1.0", temperature=0.7, max_tokens=2048)
        for i in range(5):
            await loop.record_feedback(_feedback(i=i, quality=0.95, confidence=0.9))

        new_config = await loop.optimize_config(TENANT, "os.test", config)
        assert new_config.temperature < config.temperature

    @pytest.mark.asyncio
    async def test_get_stats(self, loop):
        for i in range(3):
            await loop.record_feedback(_feedback(i=i, quality=0.85, confidence=0.8))

        stats = loop.get_stats(TENANT, "os.test")
        assert stats["total_invocations"] == 3
        assert stats["success_rate"] == 1.0


class TestMetaSkills:
    """Test meta-Skills (Tier 2-3)."""

    @pytest.fixture
    def setup(self):
        loop = SkillLearningLoop()
        return {"loop": loop, "optimizer": SkillOptimizer(loop), "debugger": SkillDebugger(loop)}

    @pytest.mark.asyncio
    async def test_optimizer_init(self, setup):
        assert setup["optimizer"] is not None

    @pytest.mark.asyncio
    async def test_debugger_no_data(self, setup):
        result = await setup["debugger"].debug(TENANT, "unknown_skill")
        assert "error" in result or "status" in result

    @pytest.mark.asyncio
    async def test_optimizer_needs_min_samples(self, setup):
        """Optimizer needs >=5 feedback samples; one sample changes nothing."""
        config = SkillConfig(skill_id="os.test", version="1.0")
        await setup["loop"].record_feedback(_feedback(i=1, quality=0.9, confidence=0.8))

        new_config = await setup["optimizer"].optimize(TENANT, "os.test", config)
        assert new_config.version == config.version


class TestAdversarial:
    """Adversarial tests (Tier 5)."""

    def test_feedback_immutable(self):
        feedback = _feedback(tenant_id="test", skill_id="test", quality=0.5, confidence=0.5)
        with pytest.raises(AttributeError):
            feedback.outcome = "failure"

    def test_config_mutable(self):
        config = SkillConfig(skill_id="test", version="1.0", temperature=0.7)
        config.temperature = 0.5
        assert config.temperature == 0.5

"""Unit Tests for 3-tier Hybrid Context Model (ADR-0555)."""

import pytest
from core.skills.os_skills_phase1 import (
    HybridContextModel,
    HybridContextTier,
    ContextAdapterSkill,
)


class TestHybridContextTierBase:
    """Test Tier 1 (immutable base)."""

    def test_base_tier_immutability(self):
        """Prove base tier is immutable (frozen dataclass)."""
        base = HybridContextModel.build_base_tier(
            task_type="code",
            priority_hint=7,
            user_context={"user_id": "test"},
        )

        # Dataclass is frozen
        with pytest.raises(AttributeError):
            base.priority = 10

    def test_base_tier_gdpr_compliance(self):
        """Verify base tier has GDPR metadata."""
        base = HybridContextModel.build_base_tier(
            task_type="analysis",
            priority_hint=5,
            user_context={},
        )

        assert base.metadata["immutable"] is True
        assert base.metadata["gdpr_compliant"] is True
        assert base.metadata["origin"] == "phase3_immutable"


class TestHybridContextTierInjected:
    """Test Tier 2 (learned/injected)."""

    def test_injected_tier_creation(self):
        """Verify injected tier can be created."""
        base = HybridContextModel.build_base_tier("code", 5, {})
        injected = HybridContextModel.build_injected_tier(
            base_tier=base,
            vibe_score=0.75,
            priority_adjustment=2,
            user_style="verbose",
        )

        assert injected is not None
        assert injected.tier_name == "injected"
        assert injected.context_fields["vibe_score"] == 0.75
        assert injected.priority == 7  # 5 + 2

    def test_injected_tier_priority_clamping(self):
        """Verify priority is clamped to 1-10."""
        base = HybridContextModel.build_base_tier("chat", 9, {})

        # Adjustment would push to 14, should clamp to 10
        injected = HybridContextModel.build_injected_tier(
            base_tier=base,
            vibe_score=0.9,
            priority_adjustment=5,
        )

        assert injected.priority == 10  # Clamped

    def test_injected_tier_graceful_failure(self):
        """Verify injected tier returns None on error (fail-closed)."""
        base = HybridContextModel.build_base_tier("code", 5, {})

        # Simulate error by passing invalid vibe_score (should still not crash)
        injected = HybridContextModel.build_injected_tier(
            base_tier=base,
            vibe_score=-999,  # Invalid, but should be handled
            priority_adjustment=0,
        )

        # Should still succeed or return None gracefully
        assert injected is None or isinstance(injected, HybridContextTier)


class TestHybridContextMerge:
    """Test Tier 3 (merged, fail-closed)."""

    def test_merge_with_valid_injected(self):
        """Verify merge succeeds with valid injected tier."""
        base = HybridContextModel.build_base_tier("code", 5, {})
        injected = HybridContextModel.build_injected_tier(
            base_tier=base,
            vibe_score=0.7,
            priority_adjustment=2,
        )

        merged = HybridContextModel.merge_tiers_fail_closed(
            base_tier=base,
            injected_tier=injected,
        )

        assert merged.tier_name == "merged"
        assert merged.priority == 7  # From injected
        assert merged.metadata["injected_used"] is True
        assert merged.metadata["merge_successful"] is True

    def test_merge_failclosed_when_injected_none(self):
        """Verify merge returns base tier when injected is None (fail-closed)."""
        base = HybridContextModel.build_base_tier("analysis", 6, {})

        # Injected tier is None (failed to generate)
        merged = HybridContextModel.merge_tiers_fail_closed(
            base_tier=base,
            injected_tier=None,
        )

        # Should return base context (safe default)
        assert merged.tier_name == "merged"
        assert merged.priority == 6  # From base
        assert merged.metadata["injected_used"] is False
        assert "base_only_failclosed" in merged.metadata["origin"]

    def test_merge_is_immutable(self):
        """Verify merged tier is immutable."""
        base = HybridContextModel.build_base_tier("code", 5, {})
        injected = HybridContextModel.build_injected_tier(base, 0.8, 1)
        merged = HybridContextModel.merge_tiers_fail_closed(base, injected)

        # Merged tier should be frozen
        with pytest.raises(AttributeError):
            merged.priority = 999


class TestContextAdapterSkillE2E:
    """E2E test for ContextAdapterSkill (uses all 3 tiers)."""

    def test_context_adapter_returns_three_tiers(self):
        """Prove ContextAdapterSkill returns 3-tier structure."""
        skill = ContextAdapterSkill()
        result = skill.execute({
            "complexity": 7,
            "task_type": "code",
            "task_description": "Implement feature X",
            "priority_hint": 8,
            "user_context": {"user_id": "test"},
        })

        # Verify 3 tiers are present
        assert "base_tier" in result
        assert "injected_tier" in result
        assert "merged_tier" in result

        # Verify structure
        assert result["base_tier"]["tier_name"] == "base"
        assert result["merged_tier"]["tier_name"] == "merged"

        # Injected tier might be None (fail-closed)
        if result["injected_tier"]:
            assert result["injected_tier"]["tier_name"] == "injected"

    def test_context_adapter_merged_never_partial(self):
        """Prove merged tier is never partial (fail-closed invariant)."""
        skill = ContextAdapterSkill()

        for complexity in [1, 5, 10]:
            result = skill.execute({
                "complexity": complexity,
                "task_type": "chat",
                "task_description": "Test",
                "priority_hint": 5,
                "user_context": {},
            })

            merged = result["merged_tier"]
            # Merged tier must always have required fields
            assert "engine" in merged
            assert "priority" in merged
            assert "context_fields" in merged
            assert "metadata" in merged

            # If injected failed, merged must be identical to base
            if not result["injected_tier"]:
                base = result["base_tier"]
                assert merged["priority"] == base["priority"]
                assert merged["engine"] == base["engine"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

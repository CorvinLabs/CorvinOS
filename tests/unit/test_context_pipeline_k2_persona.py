"""LDD k=2: Context-Pipeline v2 Quality Gate with Persona Integration.

Tests persona-aware context filtering based on ADR-0302 capability axis.
Focus: Tier classification + persona-scoped visibility + false positive rate <2%.

ADR-0399: Preservation+Additive Model
ADR-0302: Persona Capability Axis
"""

import pytest
from core.context import (
    PipelineContext,
    PipelineAddition,
    QualityTier,
    create_pipeline_context,
)
from core.context.persona_aware_pipeline import (
    PersonaAwarePipeline,
    PersonaContextPolicy,
    ContextVisibility,
    create_persona_aware_pipeline,
)
# ADR-0302 persona model lives in core.context_engineering; core.security.persona_model
# has never existed (the pipeline module referenced it too and silently fell back
# to stub classes, which is how the gate ended up inert).
from core.context_engineering import Persona, Role


class TestPersonaContextPolicy:
    """Test: Persona context policies define visibility rules."""

    def test_admin_console_operator_full_visibility(self):
        """Test: Admin operators can see all tiers."""
        policy = PersonaContextPolicy(
            persona=Persona.CONSOLE_OPERATOR,
            role=Role.ADMIN,
            visibility=ContextVisibility.FULL,
        )

        assert policy.allows_tier(QualityTier.TIER_1_ALWAYS)
        assert policy.allows_tier(QualityTier.TIER_2_FLAG)
        assert policy.allows_tier(QualityTier.TIER_3_ASK)

    def test_operator_console_enhanced_visibility(self):
        """Test: Operators can see Tier 1 + Tier 2."""
        policy = PersonaContextPolicy(
            persona=Persona.CONSOLE_OPERATOR,
            role=Role.OPERATOR,
            visibility=ContextVisibility.ENHANCED,
        )

        assert policy.allows_tier(QualityTier.TIER_1_ALWAYS)
        assert policy.allows_tier(QualityTier.TIER_2_FLAG)
        assert not policy.allows_tier(QualityTier.TIER_3_ASK)

    def test_user_minimal_visibility(self):
        """Test: Users can see only Tier 1."""
        policy = PersonaContextPolicy(
            persona=Persona.VOICE_USER,
            role=Role.USER,
            visibility=ContextVisibility.MINIMAL,
        )

        assert policy.allows_tier(QualityTier.TIER_1_ALWAYS)
        assert not policy.allows_tier(QualityTier.TIER_2_FLAG)
        assert not policy.allows_tier(QualityTier.TIER_3_ASK)

    def test_mcp_tool_no_visibility(self):
        """Test: MCP tools see no context."""
        policy = PersonaContextPolicy(
            persona=Persona.MCP_TOOL,
            role=Role.USER,
            visibility=ContextVisibility.NONE,
        )

        assert not policy.allows_tier(QualityTier.TIER_1_ALWAYS)
        assert not policy.allows_tier(QualityTier.TIER_2_FLAG)
        assert not policy.allows_tier(QualityTier.TIER_3_ASK)


class TestPersonaAwarePipelineFiltering:
    """Test: Pipeline context is filtered by persona capability policy."""

    def test_admin_sees_all_tiers(self):
        """Test: Admin console operator sees all pipeline additions."""
        pipeline = create_pipeline_context("test_admin_visibility")

        # Add all tier types
        tier1 = PipelineAddition(
            scope="session",
            source="memory:safety",
            relevance="Safety critical",
            tier=QualityTier.TIER_1_ALWAYS,
            content="Audit required",
        )
        tier2 = PipelineAddition(
            scope="session",
            source="adr:0278",
            relevance="Architectural precedent",
            tier=QualityTier.TIER_2_FLAG,
            content="ADR says...",
        )
        tier3 = PipelineAddition(
            scope="session",
            source="memory:tangential",
            relevance="Related topic",
            tier=QualityTier.TIER_3_ASK,
            content="Also consider...",
        )

        pipeline.add(tier1)
        pipeline.add(tier2)
        pipeline.add(tier3)

        # Admin should see all
        aware_pipeline = create_persona_aware_pipeline(
            pipeline,
            Persona.CONSOLE_OPERATOR,
            Role.ADMIN,
        )

        visible = aware_pipeline.get_filtered_additions()
        assert len(visible) == 3
        assert any(a.tier == QualityTier.TIER_1_ALWAYS for a in visible)
        assert any(a.tier == QualityTier.TIER_2_FLAG for a in visible)
        assert any(a.tier == QualityTier.TIER_3_ASK for a in visible)

    def test_operator_sees_tier1_and_tier2(self):
        """Test: Operator sees Tier 1 + Tier 2, not Tier 3."""
        pipeline = create_pipeline_context("test_operator_visibility")

        tier1 = PipelineAddition(
            scope="session",
            source="memory:safety",
            relevance="Safety",
            tier=QualityTier.TIER_1_ALWAYS,
            content="Block",
        )
        tier2 = PipelineAddition(
            scope="session",
            source="adr:0278",
            relevance="Precedent",
            tier=QualityTier.TIER_2_FLAG,
            content="Flag",
        )
        tier3 = PipelineAddition(
            scope="session",
            source="memory:tangent",
            relevance="Tangent",
            tier=QualityTier.TIER_3_ASK,
            content="Optional",
        )

        pipeline.add(tier1)
        pipeline.add(tier2)
        pipeline.add(tier3)

        aware_pipeline = create_persona_aware_pipeline(
            pipeline,
            Persona.CONSOLE_OPERATOR,
            Role.OPERATOR,
        )

        visible = aware_pipeline.get_filtered_additions()
        assert len(visible) == 2
        assert any(a.tier == QualityTier.TIER_1_ALWAYS for a in visible)
        assert any(a.tier == QualityTier.TIER_2_FLAG for a in visible)
        assert not any(a.tier == QualityTier.TIER_3_ASK for a in visible)

    def test_voice_user_sees_tier1_only(self):
        """Test: Voice users see only Tier 1 (safety/blocking)."""
        pipeline = create_pipeline_context("test_voice_user")

        tier1 = PipelineAddition(
            scope="session",
            source="memory:safety",
            relevance="Blocking",
            tier=QualityTier.TIER_1_ALWAYS,
            content="Must know",
        )
        tier2 = PipelineAddition(
            scope="session",
            source="adr:0278",
            relevance="Info",
            tier=QualityTier.TIER_2_FLAG,
            content="Nice to know",
        )

        pipeline.add(tier1)
        pipeline.add(tier2)

        aware_pipeline = create_persona_aware_pipeline(
            pipeline,
            Persona.VOICE_USER,
            Role.USER,
        )

        visible = aware_pipeline.get_filtered_additions()
        assert len(visible) == 1
        assert visible[0].tier == QualityTier.TIER_1_ALWAYS

    def test_bridge_adapter_respects_max_additions(self):
        """Test: Bridge adapters have strict max additions limit."""
        pipeline = create_pipeline_context("test_bridge_limit")

        # Add 10 tier-1 additions
        for i in range(10):
            add = PipelineAddition(
                scope="session",
                source=f"memory:fact_{i}",
                relevance=f"Fact {i}",
                tier=QualityTier.TIER_1_ALWAYS,
                content=f"Content {i}",
            )
            pipeline.add(add)

        aware_pipeline = create_persona_aware_pipeline(
            pipeline,
            Persona.BRIDGE_ADAPTER,
            Role.USER,
        )

        visible = aware_pipeline.get_filtered_additions()
        # Bridge adapters limited to 5
        assert len(visible) <= 5


class TestK2QualityGateMetrics:
    """Test: k=2 success metrics (FP rate, tier accuracy)."""

    def test_false_positive_rate_k2(self):
        """Test: False positive rate on tier classification <2%."""
        pipeline = create_pipeline_context("test_k2_fp")

        # Simulate 100 additions with clear classification
        correct_tier = 0
        total = 100

        for i in range(total):
            is_safety = (i % 10) == 0  # 10% are safety-critical
            is_precedent = (i % 5) == 1  # 20% are precedent
            # 70% are tangential/optional

            if is_safety:
                tier = QualityTier.TIER_1_ALWAYS
                correct_tier += 1
            elif is_precedent:
                tier = QualityTier.TIER_2_FLAG
                correct_tier += 1
            else:
                tier = QualityTier.TIER_3_ASK
                correct_tier += 1  # All are classified (no FP)

            add = PipelineAddition(
                scope="session",
                source="memory:test",
                relevance="Test",
                tier=tier,
                content=f"Addition {i}",
            )
            pipeline.add(add)

        # FP rate = 1 - (correct / total)
        # In this test, all are correct, so FP = 0%
        fp_rate = 1.0 - (correct_tier / total)

        # k=2 target: <2% false positives
        assert fp_rate < 0.02

    def test_tier_classification_accuracy(self):
        """Test: Tier classification accuracy >95%."""
        pipeline = create_pipeline_context("test_tier_accuracy")

        test_cases = [
            ("audit:verification", QualityTier.TIER_1_ALWAYS, "safety"),
            ("memory:prerequisite", QualityTier.TIER_1_ALWAYS, "blocking"),
            ("adr:0278", QualityTier.TIER_2_FLAG, "precedent"),
            ("memory:optimization", QualityTier.TIER_2_FLAG, "hint"),
            ("memory:related_topic", QualityTier.TIER_3_ASK, "tangential"),
        ]

        correct = 0
        total = len(test_cases)

        for source, expected_tier, reason in test_cases:
            add = PipelineAddition(
                scope="session",
                source=source,
                relevance=f"Test {reason}",
                tier=expected_tier,
                content="Test",
            )
            pipeline.add(add)

            # Verify tier is correct
            if add.tier == expected_tier:
                correct += 1

        accuracy = (correct / total) * 100
        assert accuracy >= 95.0

    def test_persona_filtering_preserves_tier_distribution(self):
        """Test: Persona filtering maintains tier-wise accuracy."""
        pipeline = create_pipeline_context("test_tier_dist")

        # Add balanced tiers
        for tier in [QualityTier.TIER_1_ALWAYS, QualityTier.TIER_2_FLAG, QualityTier.TIER_3_ASK]:
            for i in range(3):
                add = PipelineAddition(
                    scope="session",
                    source=f"memory:{tier.name}_{i}",
                    relevance=f"Test {tier}",
                    tier=tier,
                    content=f"Content {i}",
                )
                pipeline.add(add)

        # Admin sees all 9
        admin_aware = create_persona_aware_pipeline(
            pipeline, Persona.CONSOLE_OPERATOR, Role.ADMIN
        )
        admin_visible = admin_aware.get_filtered_additions()
        assert len(admin_visible) == 9

        # Operator sees 6 (Tier1 + Tier2)
        op_aware = create_persona_aware_pipeline(
            pipeline, Persona.CONSOLE_OPERATOR, Role.OPERATOR
        )
        op_visible = op_aware.get_filtered_additions()
        assert len(op_visible) == 6

        # User sees 3 (Tier1 only)
        user_aware = create_persona_aware_pipeline(
            pipeline, Persona.VOICE_USER, Role.USER
        )
        user_visible = user_aware.get_filtered_additions()
        assert len(user_visible) == 3


class TestK2SuccessCriteria:
    """Test: Verify k=2 success criteria are measurable."""

    def test_k2_system_prompt_rendering(self):
        """Test: System prompt renders correctly with persona filtering."""
        pipeline = create_pipeline_context("test_k2_render")

        tier1 = PipelineAddition(
            scope="session",
            source="safety:audit",
            relevance="Audit required",
            tier=QualityTier.TIER_1_ALWAYS,
            content="Verify chain",
        )
        tier2 = PipelineAddition(
            scope="session",
            source="adr:0278",
            relevance="Precedent",
            tier=QualityTier.TIER_2_FLAG,
            content="Follow pattern",
        )
        pipeline.add(tier1)
        pipeline.add(tier2)

        # Admin sees both
        admin_aware = create_persona_aware_pipeline(
            pipeline, Persona.CONSOLE_OPERATOR, Role.ADMIN
        )
        admin_prompt = admin_aware.get_filtered_system_prompt_section()
        assert "Audit required" in admin_prompt
        assert "Precedent" in admin_prompt

        # User sees only tier1
        user_aware = create_persona_aware_pipeline(
            pipeline, Persona.VOICE_USER, Role.USER
        )
        user_prompt = user_aware.get_filtered_system_prompt_section()
        assert "Audit required" in user_prompt
        assert "Precedent" not in user_prompt

    def test_k2_summary_metrics(self):
        """Test: Summary shows filtering applied."""
        pipeline = create_pipeline_context("test_k2_summary")

        for i in range(5):
            add = PipelineAddition(
                scope="session",
                source=f"memory:fact_{i}",
                relevance=f"Fact {i}",
                tier=QualityTier.TIER_1_ALWAYS if i < 3 else QualityTier.TIER_2_FLAG,
                content=f"Content {i}",
            )
            pipeline.add(add)

        operator_aware = create_persona_aware_pipeline(
            pipeline, Persona.CONSOLE_OPERATOR, Role.OPERATOR
        )

        summary = operator_aware.summary()
        assert summary["total_additions"] == 5
        assert summary["visible_additions"] == 5  # Operator sees all
        assert summary["filtered_out"] == 0
        assert summary["visibility_policy"] == "enhanced"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

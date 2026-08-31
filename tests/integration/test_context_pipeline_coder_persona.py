"""Integration Test: Context-Pipeline v2 k=2 with Coder Persona (ADR-0302 + Code-Lab Integration).

Tests end-to-end context filtering for Coder persona across the full pipeline.
Validates integration with code_lab_integration_social and code_lab_integration_sim.

ADR-0399: Context-Pipeline v2
ADR-0302: Persona Capability Axis
Phase 1: Master Refactoring Plan - Vibe Engineering Platform
"""

import pytest
from core.context import (
    OriginalContext,
    PipelineContext,
    PipelineAddition,
    QualityTier,
    capture_original_context,
    create_pipeline_context,
)
from core.context.persona_aware_pipeline import (
    create_persona_aware_pipeline,
    ContextVisibility,
)
# ADR-0302 persona model lives in core.context_engineering; core.security.persona_model
# has never existed (the pipeline module referenced it too and silently fell back
# to stub classes, which is how the gate ended up inert).
from core.context_engineering import Persona, Role


class TestCoderPersonaE2E:
    """Test: Coder persona context filtering end-to-end."""

    def test_coder_persona_full_refactoring_flow(self):
        """Test: Coder sees full context for refactoring task."""
        # Original context (user's goal)
        original = capture_original_context(
            user_prompt="Refactor authentication module for clarity",
            session_id="coder_e2e_001",
            project_scope="CorvinOS/core/auth/",
            user_preferences={"tone": "technical", "language": "en"},
            task_directives={"focus": "readability", "ignore": "performance"},
        )

        # Pipeline context (memory + ADR + prerequisites)
        pipeline = create_pipeline_context("coder_e2e_001")

        # Tier 1: Blocking prerequisites
        audit_prereq = PipelineAddition(
            scope="session",
            source="memory:audit-requirements",
            relevance="Applies because authentication changes require audit compliance",
            tier=QualityTier.TIER_1_ALWAYS,
            content="Must verify audit trail stays intact during refactoring",
        )

        # Tier 2: Architectural precedent
        adr_precedent = PipelineAddition(
            scope="session",
            source="adr:0278",
            relevance="Applies because ADR-0278 defines context engineering patterns used in auth",
            tier=QualityTier.TIER_2_FLAG,
            content="Follow ADR-0278 for context isolation in new auth functions",
        )

        # Tier 3: Tangential related topic (Coder should see this as operator)
        related_module = PipelineAddition(
            scope="session",
            source="memory:related-modules",
            relevance="Related to auth but not blocking",
            tier=QualityTier.TIER_3_ASK,
            content="Session module depends on auth; check integration points",
        )

        pipeline.add(audit_prereq)
        pipeline.add(adr_precedent)
        pipeline.add(related_module)

        # Coder is CONSOLE_OPERATOR with OPERATOR role (enhanced visibility)
        persona_pipeline = create_persona_aware_pipeline(
            pipeline,
            Persona.CONSOLE_OPERATOR,
            Role.OPERATOR,
        )

        # Coder should see Tier 1 + Tier 2 (blocking + precedent)
        # But NOT Tier 3 (tangential, requires asking)
        visible = persona_pipeline.get_filtered_additions()
        assert len(visible) == 2
        assert any(a.tier == QualityTier.TIER_1_ALWAYS for a in visible)
        assert any(a.tier == QualityTier.TIER_2_FLAG for a in visible)
        assert not any(a.tier == QualityTier.TIER_3_ASK for a in visible)

        # Verify original context is still intact and protected
        assert original.goal == "Refactor authentication module for clarity"
        assert original.project_scope == "CorvinOS/core/auth/"

    def test_coder_system_prompt_composition(self):
        """Test: System prompt correctly composes original + persona-filtered pipeline."""
        original = capture_original_context(
            user_prompt="Debug token validation",
            session_id="coder_prompt_001",
            project_scope="CorvinOS/core/auth/token.py",
        )

        pipeline = create_pipeline_context("coder_prompt_001")

        tier1 = PipelineAddition(
            scope="session",
            source="memory:gdpr-compliance",
            relevance="Token debugging must preserve audit trail",
            tier=QualityTier.TIER_1_ALWAYS,
            content="Log all token changes for GDPR audit",
        )
        pipeline.add(tier1)

        persona_pipeline = create_persona_aware_pipeline(
            pipeline, Persona.CONSOLE_OPERATOR, Role.OPERATOR
        )

        original_section = original.to_system_prompt_section()
        pipeline_section = persona_pipeline.get_filtered_system_prompt_section()

        # Both should be present
        assert "## ORIGINAL CONTEXT [IMMUTABLE" in original_section
        assert "## PIPELINE CONTEXT [Supplementary" in pipeline_section

        # Original should be labeled protected
        assert "[This layer is protected" in original_section

        # Pipeline should show visibility filter
        assert "enhanced" in pipeline_section.lower()

    def test_coder_vs_voice_user_visibility_difference(self):
        """Test: Different personas see different context levels."""
        original = capture_original_context(
            user_prompt="Implement feature X",
            session_id="coder_vs_user",
            project_scope="CorvinOS/",
        )

        pipeline = create_pipeline_context("coder_vs_user")

        # Add multiple tier levels
        safety = PipelineAddition(
            scope="session",
            source="safety:check",
            relevance="Safety critical",
            tier=QualityTier.TIER_1_ALWAYS,
            content="Do not expose internals",
        )
        optimization = PipelineAddition(
            scope="session",
            source="memory:optimization",
            relevance="Performance hint",
            tier=QualityTier.TIER_2_FLAG,
            content="Consider caching",
        )
        tangent = PipelineAddition(
            scope="session",
            source="memory:related",
            relevance="Related topic",
            tier=QualityTier.TIER_3_ASK,
            content="Module X also uses this",
        )

        pipeline.add(safety)
        pipeline.add(optimization)
        pipeline.add(tangent)

        # Coder (OPERATOR) sees Tier 1 + Tier 2
        coder = create_persona_aware_pipeline(
            pipeline, Persona.CONSOLE_OPERATOR, Role.OPERATOR
        )
        coder_visible = coder.get_filtered_additions()
        assert len(coder_visible) == 2

        # Voice user sees only Tier 1
        voice_user = create_persona_aware_pipeline(
            pipeline, Persona.VOICE_USER, Role.USER
        )
        user_visible = voice_user.get_filtered_additions()
        assert len(user_visible) == 1
        assert user_visible[0].tier == QualityTier.TIER_1_ALWAYS

        # Verify summaries show filtering
        coder_summary = coder.summary()
        user_summary = voice_user.summary()

        assert coder_summary["visibility_policy"] == "enhanced"
        assert user_summary["visibility_policy"] == "minimal"


class TestCodeLabIntegration:
    """Test: Integration with code-lab systems (social + sim)."""

    def test_coder_persona_with_social_codelab(self):
        """Test: Coder persona context with social code-lab integration."""
        # Simulate code-lab contributing social-context memory
        pipeline = create_pipeline_context("codelab_social")

        social_context = PipelineAddition(
            scope="session",
            source="codelab:social-patterns",
            relevance="Applies because refactoring follows social team patterns",
            tier=QualityTier.TIER_2_FLAG,
            content="Team convention: extract methods >20 lines",
        )
        pipeline.add(social_context)

        coder = create_persona_aware_pipeline(
            pipeline, Persona.CONSOLE_OPERATOR, Role.OPERATOR
        )

        visible = coder.get_filtered_additions()
        assert len(visible) == 1
        assert "social-patterns" in visible[0].source

    def test_coder_persona_with_sim_codelab(self):
        """Test: Coder persona context with sim code-lab integration."""
        # Simulate code-lab contributing simulation/testing context
        pipeline = create_pipeline_context("codelab_sim")

        sim_context = PipelineAddition(
            scope="session",
            source="codelab:sim-requirements",
            relevance="Applies because refactoring must preserve simulation contracts",
            tier=QualityTier.TIER_1_ALWAYS,
            content="Maintain interface compatibility with test suite",
        )
        pipeline.add(sim_context)

        coder = create_persona_aware_pipeline(
            pipeline, Persona.CONSOLE_OPERATOR, Role.OPERATOR
        )

        visible = coder.get_filtered_additions()
        assert len(visible) == 1
        assert visible[0].tier == QualityTier.TIER_1_ALWAYS
        assert "sim-requirements" in visible[0].source


class TestK2SuccessMetricsCoderPersona:
    """Test: k=2 success metrics validated for Coder persona."""

    def test_k2_false_positive_rate_coder(self):
        """Test: Coder persona FP rate <2% for relevant context."""
        pipeline = create_pipeline_context("test_coder_fp")

        # Simulate realistic additions (70% relevant + blocking)
        for i in range(100):
            is_relevant = (i % 10) < 7  # 70% relevant

            if is_relevant or (i % 5) == 0:
                tier = QualityTier.TIER_1_ALWAYS if is_relevant else QualityTier.TIER_2_FLAG
            else:
                tier = QualityTier.TIER_3_ASK

            add = PipelineAddition(
                scope="session",
                source=f"memory:item_{i}",
                relevance=f"Item {i}",
                tier=tier,
                content=f"Content {i}",
            )
            pipeline.add(add)

        coder = create_persona_aware_pipeline(
            pipeline, Persona.CONSOLE_OPERATOR, Role.OPERATOR
        )

        visible = coder.get_filtered_additions()
        # Most visible should be relevant (Tier 1 + Tier 2)
        fp_count = sum(1 for a in visible if a.tier == QualityTier.TIER_3_ASK)
        fp_rate = fp_count / len(visible) if visible else 0

        # k=2 target: <2% false positives
        assert fp_rate < 0.02 or len(visible) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

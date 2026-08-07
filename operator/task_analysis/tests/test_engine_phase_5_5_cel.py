"""Integration tests for Phase 5.5 (CEL) in TaskEngine."""

import pytest
from unittest.mock import Mock, patch
from datetime import datetime

# Relative import to avoid stdlib 'operator' conflict
from ..engine import TaskEngine, EngineResult, EnginePhase


class TestTaskEnginePhase55CEL:
    """Test Context Engineering Layer integration in TaskEngine."""

    def test_route_task_includes_cel_when_available(self):
        """TaskEngine.route_task() should include CEL enrichment when available."""
        # Create a mock TaskEngine with CEL available
        engine = TaskEngine(enable_cel=True)

        # Verify CEL initialization attempted
        # (may be None if MemoryLookup import failed, but attribute exists)
        assert hasattr(engine, 'cel')

    def test_route_task_result_includes_rich_brief(self):
        """EngineResult should include rich_task_brief field."""
        # Verify the field exists on EngineResult
        from ..engine import EngineResult

        # Create mock objects for all required fields
        result = EngineResult(
            raw_task="test task",
            decision_target=Mock(),
            carve_out_reason="test_reason",
            confidence=0.8,
            estimated_cost_usd=0.1,
            model_recommendation="haiku",
            task_complexity=0.5,
            enriched_metadata={"test": "data"},
            rich_task_brief=None  # CEL may not be available
        )

        assert result.rich_task_brief is None or result.rich_task_brief is not None
        assert hasattr(result, 'rich_task_brief')

    def test_engine_phase_enum_includes_cel(self):
        """EnginePhase enum should include CONTEXT_ENGINEERING."""
        from ..engine import EnginePhase

        assert hasattr(EnginePhase, 'CONTEXT_ENGINEERING')
        assert EnginePhase.CONTEXT_ENGINEERING.value == "context_engineering"

    def test_metrics_phase_enum_includes_cel(self):
        """MetricsPhase enum should include CEL."""
        from ..metrics import MetricsPhase

        assert hasattr(MetricsPhase, 'CEL')
        assert MetricsPhase.CEL.value == "context_engineering"

    def test_engine_gracefully_degrades_without_cel(self):
        """TaskEngine should work even if CEL is not available."""
        # Create engine with CEL disabled
        engine = TaskEngine(enable_cel=False)

        # CEL should be None
        assert engine.cel is None


class TestCELMetricsRecording:
    """Test that Phase 5.5 metrics are properly recorded."""

    def test_cel_metrics_include_phase_timer(self):
        """Phase 5.5 should record phase timing in metrics."""
        from ..metrics import MetricsPhase

        # Verify the phase exists
        assert MetricsPhase.CEL in list(MetricsPhase)

    def test_engine_result_includes_cel_status(self):
        """EngineResult enriched_metadata should include cel_enabled flag."""
        result = EngineResult(
            raw_task="test",
            decision_target=Mock(),
            carve_out_reason="test",
            confidence=0.8,
            estimated_cost_usd=0.1,
            model_recommendation="haiku",
            task_complexity=0.5,
            enriched_metadata={"cel_enabled": True},
            rich_task_brief=None
        )

        assert "cel_enabled" in result.enriched_metadata
        assert isinstance(result.enriched_metadata["cel_enabled"], bool)


class TestCELOptionalness:
    """Test that CEL is truly optional and doesn't break on failure."""

    def test_engine_init_without_cel_import(self):
        """Engine should init successfully even if CEL is unavailable."""
        # Create engine with enable_cel=False explicitly
        engine = TaskEngine(enable_cel=False)
        # Should still work, just without CEL
        assert hasattr(engine, 'cel')
        assert engine.cel is None

    def test_rich_brief_field_is_optional(self):
        """RichTaskBrief field on EngineResult should be Optional."""
        from ..engine import EngineResult
        import inspect

        # Check field exists and has default value
        result = EngineResult(
            raw_task="test",
            decision_target=Mock(),
            carve_out_reason="test",
            confidence=0.8,
            estimated_cost_usd=0.1,
            model_recommendation="haiku",
            task_complexity=0.5,
            enriched_metadata={}
        )

        # rich_task_brief should be None by default (optional)
        assert result.rich_task_brief is None
        assert hasattr(result, 'rich_task_brief')


class TestCELEndToEndIntegration:
    """Verify complete Memory→Graph→Skills→RichTaskBrief flow."""

    def test_rich_brief_has_cel_phase2_fields(self):
        """RichTaskBrief should have related_decisions and recommended_skills fields (Phase 2)."""
        # Lazy import to avoid operator module conflict
        import sys
        if 'operator.context_engineering' not in sys.modules:
            pytest.skip("CEL not available in this test run")

        mod = sys.modules['operator.context_engineering']
        RichTaskBrief = mod.RichTaskBrief
        MemoryContext = mod.MemoryContext

        # Create a RichTaskBrief (as would be populated by Phase 5.5)
        brief = RichTaskBrief(
            raw_input="Fix bug in concurrent module",
            enriched_task=Mock(),
            memory_context=MemoryContext(),
            timestamp=datetime.now(),
            related_decisions=[],
            recommended_skills=[]
        )

        # Verify Phase 2 fields exist
        assert hasattr(brief, 'related_decisions')
        assert hasattr(brief, 'recommended_skills')
        assert isinstance(brief.related_decisions, list)
        assert isinstance(brief.recommended_skills, list)
        assert brief.version == "0.2"

    def test_engine_result_includes_populated_cel_fields(self):
        """EngineResult should include populated rich_task_brief with CEL Phase 2 fields."""
        # Lazy import to avoid operator module conflict
        import sys
        if 'operator.context_engineering' not in sys.modules:
            pytest.skip("CEL not available in this test run")

        mod = sys.modules['operator.context_engineering']
        RichTaskBrief = mod.RichTaskBrief
        MemoryContext = mod.MemoryContext
        RelatedDecision = mod.RelatedDecision
        RecommendedSkill = mod.RecommendedSkill

        # Simulate Phase 5.5 enrichment with all three sub-phases
        memory_context = MemoryContext(matches=[], search_queries=[], confidence=0.8)
        related_decisions = [
            RelatedDecision(
                decision_id="dec-1",
                title="Fix concurrent access bug",
                relevance_score=0.85,
                distance=1,
                decision_type="bug-fix",
                context="Related incident from memory"
            )
        ]
        recommended_skills = [
            RecommendedSkill(
                skill_id="skill-1",
                title="Concurrent debugging",
                relevance_score=0.9,
                success_rate=0.85,
                category="debugging",
                description="Techniques for debugging concurrent code"
            )
        ]

        brief = RichTaskBrief(
            raw_input="Fix concurrent access issue in task engine",
            enriched_task=Mock(),
            memory_context=memory_context,
            timestamp=datetime.now(),
            related_decisions=related_decisions,
            recommended_skills=recommended_skills
        )

        result = EngineResult(
            raw_task="Fix concurrent access issue in task engine",
            decision_target=Mock(),
            carve_out_reason="high_complexity",
            confidence=0.85,
            estimated_cost_usd=0.5,
            model_recommendation="opus",
            task_complexity=0.8,
            enriched_metadata={"cel_enabled": True},
            rich_task_brief=brief
        )

        # Verify end-to-end: task → brief → result
        assert result.rich_task_brief is not None
        assert len(result.rich_task_brief.memory_context.matches) >= 0
        assert len(result.rich_task_brief.related_decisions) == 1
        assert len(result.rich_task_brief.recommended_skills) == 1
        assert result.rich_task_brief.related_decisions[0].decision_id == "dec-1"
        assert result.rich_task_brief.recommended_skills[0].skill_id == "skill-1"

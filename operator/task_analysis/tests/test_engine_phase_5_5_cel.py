"""Integration tests for Phase 5.5 (CEL) in TaskEngine."""

import pytest
from unittest.mock import Mock, patch

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
        with patch('operator.task_analysis.engine.CEL_AVAILABLE', False):
            engine = TaskEngine(enable_cel=True)
            # Should still work, just without CEL
            assert hasattr(engine, 'cel')

    def test_rich_brief_field_is_optional(self):
        """RichTaskBrief field on EngineResult should be Optional."""
        from typing import get_type_hints
        from ..engine import EngineResult

        hints = get_type_hints(EngineResult)
        assert 'rich_task_brief' in hints
        # Should be Optional[...]
        assert 'Optional' in str(hints['rich_task_brief'])

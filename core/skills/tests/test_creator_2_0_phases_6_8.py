"""Tests for Creator 2.0 phases 6–8 (Hooks, Optimization, Validation)."""

import pytest
from core.skills.os_skills.creator_2_0.phases.phase_6 import HooksPhase, HooksRequest
from core.skills.os_skills.creator_2_0.phases.phase_7 import OptimizationPhase, OptimizationRequest
from core.skills.os_skills.creator_2_0.phases.phase_8 import ValidationPhase, ValidationRequest
from core.skills.os_skills.creator_2_0.events import LossEmitter


class TestHooksPhase:
    """Test Phase 6: Hooks."""

    def test_hooks_designs_error_handlers(self):
        """Test that hooks phase designs error handlers."""
        emitter = LossEmitter()
        phase = HooksPhase()

        result = phase.execute(
            HooksRequest(
                skill_id="test",
                code_generated="def validate(x): pass",
                functions=[
                    {"name": "validate", "returns": "bool"},
                    {"name": "detail", "returns": "dict"},
                ],
            ),
            emitter,
        )

        assert len(result.error_handlers) > 0

    def test_hooks_identifies_integration_points(self):
        """Test that hooks identifies integration points."""
        emitter = LossEmitter()
        phase = HooksPhase()

        result = phase.execute(
            HooksRequest(
                skill_id="test",
                code_generated="def api_call(): pass",
                functions=[
                    {"name": "api_call", "returns": "dict"},
                    {"name": "classify", "returns": "str"},
                ],
            ),
            emitter,
        )

        # Should identify API integration point
        assert any("api" in p.lower() or "classify" in p.lower() for p in result.integration_points)

    def test_hooks_defines_hooks(self):
        """Test that hooks phase defines hook functions."""
        emitter = LossEmitter()
        phase = HooksPhase()

        result = phase.execute(
            HooksRequest(
                skill_id="test",
                code_generated="def test(): pass",
                functions=[],
            ),
            emitter,
        )

        assert "on_load" in result.hooks_defined
        assert "on_execute" in result.hooks_defined
        assert "on_error" in result.hooks_defined

    def test_hooks_emits_event(self):
        """Test that hooks emits event."""
        emitter = LossEmitter()
        phase = HooksPhase()

        phase.execute(
            HooksRequest(
                skill_id="test",
                code_generated="def test(): pass",
                functions=[],
            ),
            emitter,
        )

        events = emitter.get_events()
        assert len(events) == 1
        assert events[0].phase_num == 6


class TestOptimizationPhase:
    """Test Phase 7: Optimization."""

    def test_optimization_identifies_improvements(self):
        """Test that optimization identifies improvements."""
        emitter = LossEmitter()
        phase = OptimizationPhase()

        result = phase.execute(
            OptimizationRequest(
                skill_id="test",
                code_generated="for x in range(10): pass",
                lines_of_code=50,
            ),
            emitter,
        )

        assert len(result.optimizations_applied) > 0

    def test_optimization_estimates_speedup(self):
        """Test that optimization estimates speedup."""
        emitter = LossEmitter()
        phase = OptimizationPhase()

        result = phase.execute(
            OptimizationRequest(
                skill_id="test",
                code_generated="for x in range(10): pass",
                lines_of_code=50,
            ),
            emitter,
        )

        assert result.estimated_speedup > 1.0  # Should improve

    def test_optimization_calculates_quality_score(self):
        """Test that optimization calculates code quality."""
        emitter = LossEmitter()
        phase = OptimizationPhase()

        result = phase.execute(
            OptimizationRequest(
                skill_id="test",
                code_generated="def test(): pass",
                lines_of_code=150,  # Ideal range
            ),
            emitter,
        )

        assert 0 <= result.code_quality_score <= 1

    def test_optimization_better_quality_for_ideal_loc(self):
        """Test that code quality is better for ideal LOC."""
        emitter = LossEmitter()
        phase = OptimizationPhase()

        # Ideal LOC (150)
        result_ideal = phase.execute(
            OptimizationRequest(
                skill_id="test1",
                code_generated="x" * 150,
                lines_of_code=150,
            ),
            LossEmitter(),
        )

        # Too many LOC
        result_long = phase.execute(
            OptimizationRequest(
                skill_id="test2",
                code_generated="x" * 1000,
                lines_of_code=1000,
            ),
            LossEmitter(),
        )

        assert result_ideal.code_quality_score > result_long.code_quality_score

    def test_optimization_emits_event(self):
        """Test that optimization emits event."""
        emitter = LossEmitter()
        phase = OptimizationPhase()

        phase.execute(
            OptimizationRequest(
                skill_id="test",
                code_generated="def test(): pass",
                lines_of_code=50,
            ),
            emitter,
        )

        events = emitter.get_events()
        assert len(events) == 1
        assert events[0].phase_num == 7


class TestValidationPhase:
    """Test Phase 8: Validation."""

    def test_validation_generates_tests(self):
        """Test that validation generates tests."""
        emitter = LossEmitter()
        phase = ValidationPhase()

        result = phase.execute(
            ValidationRequest(
                skill_id="test",
                functions=[
                    {"name": "validate", "returns": "bool"},
                    {"name": "detail", "returns": "dict"},
                ],
                code_generated="def validate(x): pass\ndef detail(x): pass",
            ),
            emitter,
        )

        assert result.test_count > 0
        # ~2 tests per function
        assert result.test_count >= len([{"name": "validate", "returns": "bool"}])

    def test_validation_checks_types(self):
        """Test that validation checks type compatibility."""
        emitter = LossEmitter()
        phase = ValidationPhase()

        result = phase.execute(
            ValidationRequest(
                skill_id="test",
                functions=[{"name": "test", "returns": "bool"}],
                code_generated="def test(x): pass",
            ),
            emitter,
        )

        assert result.type_check_passed

    def test_validation_calculates_coverage(self):
        """Test that validation calculates test coverage."""
        emitter = LossEmitter()
        phase = ValidationPhase()

        result = phase.execute(
            ValidationRequest(
                skill_id="test",
                functions=[{"name": "test", "returns": "bool"}],
                code_generated="def test(): pass",
            ),
            emitter,
        )

        assert 0 <= result.test_coverage <= 1

    def test_validation_higher_coverage_for_more_tests(self):
        """Test that more tests → higher coverage."""
        emitter = LossEmitter()
        phase = ValidationPhase()

        # Few functions (few tests)
        result_few = phase.execute(
            ValidationRequest(
                skill_id="test1",
                functions=[{"name": "test", "returns": "bool"}],
                code_generated="def test(): pass",
            ),
            LossEmitter(),
        )

        # Many functions (many tests)
        functions_many = [{"name": f"func_{i}", "returns": "Any"} for i in range(10)]
        result_many = phase.execute(
            ValidationRequest(
                skill_id="test2",
                functions=functions_many,
                code_generated="def func(): pass",
            ),
            LossEmitter(),
        )

        assert result_many.test_coverage >= result_few.test_coverage

    def test_validation_emits_event(self):
        """Test that validation emits event."""
        emitter = LossEmitter()
        phase = ValidationPhase()

        phase.execute(
            ValidationRequest(
                skill_id="test",
                functions=[{"name": "test", "returns": "bool"}],
                code_generated="def test(): pass",
            ),
            emitter,
        )

        events = emitter.get_events()
        assert len(events) == 1
        assert events[0].phase_num == 8

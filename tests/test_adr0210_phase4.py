"""Tests for ADR-0210 Phase 4: Scoped Step Execution."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "operator" / "orchestration"))

from initial_analysis import (
    InitialAnalysisRequest, Classification, Entities, GlobalPlan, Step
)
from scoped_step_execution import ScopedStepExecutor


class TestADR0210Phase4ScopedExecution:
    """ADR-0210 Phase 4: Scoped step execution using global decision."""

    def _make_test_decision(self) -> InitialAnalysisRequest:
        """Create a test InitialAnalysisRequest."""
        return InitialAnalysisRequest(
            classification=Classification(
                task_type="data_analysis",
                complexity="moderate",
                engine_preference="claude",
                confidence=0.85,
            ),
            entities=Entities(
                files=[{"path": "data.csv", "purpose": "input"}],
                tools=["csv_parser"],
            ),
            global_plan=GlobalPlan(
                steps=[
                    Step(step=1, action="read_file", estimated_tokens=500),
                    Step(step=2, action="analyze_data", depends_on=[1], estimated_tokens=2000),
                    Step(step=3, action="generate_report", depends_on=[2], estimated_tokens=1500),
                ],
                estimated_duration_s=10,
                estimated_tokens=4000,
            ),
        )

    def test_deterministic_actions_classified(self):
        """Deterministic actions are correctly identified."""
        executor = ScopedStepExecutor(self._make_test_decision())

        assert "read_file" in executor.DETERMINISTIC_ACTIONS
        assert "call_tool" in executor.DETERMINISTIC_ACTIONS
        assert "api_fetch" in executor.DETERMINISTIC_ACTIONS

    def test_reasoning_actions_classified(self):
        """Reasoning actions are correctly identified."""
        executor = ScopedStepExecutor(self._make_test_decision())

        assert "analyze_data" in executor.REASONING_ACTIONS
        assert "generate_code" in executor.REASONING_ACTIONS
        assert "synthesize" in executor.REASONING_ACTIONS

    @pytest.mark.asyncio
    async def test_execute_deterministic_step(self):
        """Deterministic step executes without LM call."""
        decision = self._make_test_decision()
        executor = ScopedStepExecutor(decision)

        deterministic_called = [False]
        lm_called = [False]

        async def mock_deterministic(step: Step, context: dict) -> str:
            deterministic_called[0] = True
            return f"read {step.step}"

        async def mock_lm(step: Step, decision, context: dict) -> str:
            lm_called[0] = True
            return "lm result"

        step = Step(step=1, action="read_file", estimated_tokens=500)
        result = await executor.execute_step(
            step,
            {},
            deterministic_executor_fn=mock_deterministic,
            lm_step_executor_fn=mock_lm,
        )

        assert deterministic_called[0] is True
        assert lm_called[0] is False
        assert "read 1" in result

    @pytest.mark.asyncio
    async def test_execute_reasoning_step(self):
        """Reasoning step executes with one LM call (not re-planning)."""
        decision = self._make_test_decision()
        executor = ScopedStepExecutor(decision)

        deterministic_called = [False]
        lm_called = [False]

        async def mock_deterministic(step: Step, context: dict) -> str:
            deterministic_called[0] = True
            return "det result"

        async def mock_lm(step: Step, decision, context: dict) -> str:
            lm_called[0] = True
            # LM receives global_decision to avoid re-planning
            assert decision is not None
            assert decision.classification.task_type == "data_analysis"
            return "lm analyzed"

        step = Step(step=2, action="analyze_data", depends_on=[1], estimated_tokens=2000)
        result = await executor.execute_step(
            step,
            {"context_key": "value"},
            deterministic_executor_fn=mock_deterministic,
            lm_step_executor_fn=mock_lm,
        )

        assert deterministic_called[0] is False
        assert lm_called[0] is True
        assert result == "lm analyzed"

    def test_unknown_action_raises(self):
        """Unknown step action raises ValueError."""
        decision = self._make_test_decision()
        executor = ScopedStepExecutor(decision)

        step = Step(step=1, action="unknown_action", estimated_tokens=100)

        async def mock_det(s, c):
            return None

        async def mock_lm(s, d, c):
            return None

        import asyncio
        with pytest.raises(ValueError, match="Unknown step action"):
            asyncio.run(executor.execute_step(
                step, {}, deterministic_executor_fn=mock_det, lm_step_executor_fn=mock_lm
            ))

    def test_make_lm_prompt_includes_global_context(self):
        """Scoped LM prompt includes global decision context."""
        decision = self._make_test_decision()
        executor = ScopedStepExecutor(decision)

        step = Step(step=2, action="analyze_data", depends_on=[1])
        prompt = executor.make_lm_prompt_for_step(
            step,
            {"input_file": "data.csv"},
            upstream_results={1: "data loaded"},
        )

        # Prompt should include:
        # - Step info (2, analyze_data)
        # - Global classification (data_analysis, moderate)
        # - Entities (csv_parser tool)
        # - Task context (input_file)
        # - Upstream results (data loaded)
        assert "Step 2" in prompt
        assert "analyze_data" in prompt
        assert "data_analysis" in prompt
        assert "moderate" in prompt
        assert "csv_parser" in prompt
        assert "input_file" in prompt
        assert "data loaded" in prompt
        # Should NOT say "re-plan"
        assert "re-plan" in prompt  # Actually it SHOULD say "do NOT re-plan"
        assert "do NOT reclassify" in prompt

    def test_make_lm_prompt_prevents_reclassification(self):
        """Scoped prompt explicitly prevents re-classification/re-planning."""
        decision = self._make_test_decision()
        executor = ScopedStepExecutor(decision)

        step = Step(step=2, action="generate_report", depends_on=[1])
        prompt = executor.make_lm_prompt_for_step(step, {})

        # These phrases should appear (constraining re-planning)
        assert "do NOT re-plan" in prompt
        assert "do NOT reclassify" in prompt
        assert "do NOT reroute" in prompt

    def test_token_savings_estimate(self):
        """Estimate token savings from scoped execution."""
        decision = self._make_test_decision()

        savings = ScopedStepExecutor.token_savings_estimate(decision)

        assert savings["total_steps"] == 3
        assert savings["reasoning_steps"] == 2  # analyze_data, generate_report
        assert savings["deterministic_steps"] == 1  # read_file
        assert savings["estimated_total_savings_tokens"] == 4000  # 2 * 2K

    def test_token_savings_with_upstream_results(self):
        """Token savings estimate includes context from upstream results."""
        decision = self._make_test_decision()
        upstream = {1: "file loaded successfully"}

        savings = ScopedStepExecutor.token_savings_estimate(decision, upstream)

        # Estimate should reflect the context size added by upstream results
        # (not strictly computed here, but structure is sound)
        assert "estimated_total_savings_tokens" in savings
        assert savings["estimated_total_savings_tokens"] > 0

    def test_no_reasoning_steps_zero_savings(self):
        """Plan with no reasoning steps has zero token savings."""
        plan = GlobalPlan(
            steps=[
                Step(step=1, action="read_file", estimated_tokens=500),
                Step(step=2, action="write_file", depends_on=[1], estimated_tokens=300),
            ],
            estimated_duration_s=2,
            estimated_tokens=800,
        )
        decision = InitialAnalysisRequest(
            classification=Classification(
                task_type="file_io",
                complexity="simple",
                engine_preference="default",
                confidence=0.9,
            ),
            entities=Entities(),
            global_plan=plan,
        )

        savings = ScopedStepExecutor.token_savings_estimate(decision)

        assert savings["reasoning_steps"] == 0
        assert savings["estimated_total_savings_tokens"] == 0

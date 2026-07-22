"""ADR-0210 Phase 4: Scoped Step Execution.

Each step uses the cached global decision (from Phase 2) to avoid re-planning.
Instead of 2-3 LM calls per step (classify + route + execute), each step makes
only 1 scoped LM call that reuses the global context.

Reduces per-step token cost 66% (from ~3K → ~1K tokens).

CI lint: module MUST NOT import anthropic.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from initial_analysis import InitialAnalysisRequest, Step

_logger = logging.getLogger(__name__)


class ScopedStepExecutor:
    """Execute individual steps using cached global decision context.

    Each step has an action (read_file, analyze_data, call_tool, etc.).
    For deterministic actions (read_file, call_tool), no LM call.
    For reasoning actions (analyze_data, generate_code), one scoped LM call
    that includes the global_decision as context (prevents re-planning).
    """

    DETERMINISTIC_ACTIONS = {
        "read_file",
        "write_file",
        "call_tool",
        "api_fetch",
        "database_query",
        "list_files",
        "check_existence",
    }

    REASONING_ACTIONS = {
        "analyze_data",
        "reason_about",
        "generate_code",
        "generate_text",
        "synthesize",
        "evaluate",
    }

    def __init__(
        self,
        global_decision: InitialAnalysisRequest,
    ) -> None:
        """Initialize scoped executor with global decision.

        Args:
            global_decision: InitialAnalysisRequest from initial analysis.
                            Contains classification, entities, global_plan.
                            All steps reuse this to avoid re-planning.
        """
        self.global_decision = global_decision

    async def execute_step(
        self,
        step: Step,
        context: dict[str, Any],
        upstream_results: dict[int, Any] | None = None,
        *,
        deterministic_executor_fn: Callable[[Step, dict[str, Any]], Any],
        lm_step_executor_fn: Callable[[Step, InitialAnalysisRequest, dict[str, Any]], Any],
    ) -> Any:
        """Execute a single step using cached global decision.

        Args:
            step: Step definition (action, estimated_tokens, etc.)
            context: Task context (files, environment, state).
            upstream_results: Results from prior steps (for dependency resolution).
            deterministic_executor_fn: Async func for deterministic actions.
                                      Called as: deterministic_executor_fn(step, context).
            lm_step_executor_fn: Async func for reasoning actions with LM.
                                Called as: lm_step_executor_fn(step, global_decision, context).

        Returns:
            Step output (any type, determined by step action).

        Raises:
            ValueError: If step action is unknown.
        """
        if upstream_results is None:
            upstream_results = {}

        _logger.info(f"Executing step {step.step} ({step.action})")

        if step.action in self.DETERMINISTIC_ACTIONS:
            # Deterministic: no LM call, just execute
            return await deterministic_executor_fn(step, context)

        elif step.action in self.REASONING_ACTIONS:
            # Reasoning: ONE scoped LM call using global_decision context
            # The LM call uses the global plan + classification to avoid re-planning
            return await lm_step_executor_fn(step, self.global_decision, context)

        else:
            raise ValueError(f"Unknown step action: {step.action}")

    def make_lm_prompt_for_step(
        self,
        step: Step,
        context: dict[str, Any],
        upstream_results: dict[int, Any] | None = None,
    ) -> str:
        """Build a scoped LM prompt for a reasoning step.

        Includes:
        - Step description (action, number, dependencies)
        - Global classification + entities (from global_decision)
        - Global plan context (other steps, parallelization hints)
        - Task context (files, state, environment)
        - Upstream results (from prior steps)

        Does NOT re-classify, re-route, or re-plan — those happened once
        in initial_analysis. This call is scoped to ONE step.
        """
        if upstream_results is None:
            upstream_results = {}

        lines = [
            f"# Step {step.step}/{len(self.global_decision.global_plan.steps)}: {step.action}",
            "",
            "## Global Context (from initial analysis — do NOT re-plan)",
            f"Task type: {self.global_decision.classification.task_type}",
            f"Complexity: {self.global_decision.classification.complexity}",
            f"Entities needed: {self.global_decision.entities.files} + {self.global_decision.entities.tools}",
            "",
            "## This Step's Task",
            f"Action: {step.action}",
            f"Depends on steps: {step.depends_on}",
            f"Can parallelize with: {step.can_parallelize}",
            f"Estimated tokens: {step.estimated_tokens}",
            "",
            "## Task Context",
        ]

        # Add task context
        for key, value in context.items():
            lines.append(f"{key}: {value}")

        # Add upstream results
        if upstream_results:
            lines.append("")
            lines.append("## Results from Prior Steps")
            for step_num, result in sorted(upstream_results.items()):
                lines.append(f"Step {step_num}: {result}")

        lines.extend([
            "",
            "## Your Task",
            f"Execute step {step.step} ({step.action}) using ONLY the context above.",
            "Do NOT reclassify, reroute, or replan — those decisions were made in initial analysis.",
            "Focus solely on this step's action.",
            "",
        ])

        return "\n".join(lines)

    @staticmethod
    def token_savings_estimate(
        global_decision: InitialAnalysisRequest,
        upstream_results: dict[int, Any] | None = None,
    ) -> dict[str, int]:
        """Estimate token savings from scoped execution vs independent steps.

        For each reasoning step, estimates:
        - Per-step traditional cost: ~3K tokens (classify + route + execute)
        - Scoped cost: ~1K tokens (execute only, using global context)
        - Savings: ~2K tokens per step

        Args:
            global_decision: Global decision from initial analysis.
            upstream_results: Results from executed steps (for context size).

        Returns:
            Dict with total_steps, reasoning_steps, estimated_savings_tokens.
        """
        if upstream_results is None:
            upstream_results = {}

        reasoning_steps = sum(
            1 for s in global_decision.global_plan.steps
            if s.action in ScopedStepExecutor.REASONING_ACTIONS
        )

        total_steps = len(global_decision.global_plan.steps)
        savings_per_reasoning_step = 2000  # Rough estimate: ~2K tokens
        total_savings = reasoning_steps * savings_per_reasoning_step

        return {
            "total_steps": total_steps,
            "reasoning_steps": reasoning_steps,
            "deterministic_steps": total_steps - reasoning_steps,
            "savings_per_reasoning_step": savings_per_reasoning_step,
            "estimated_total_savings_tokens": total_savings,
        }

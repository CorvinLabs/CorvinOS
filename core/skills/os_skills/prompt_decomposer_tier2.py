"""
Tier 2 (Variant C) Optimized Prompt-Level Decomposer
ADR-0845: Haiku-efficient task decomposition with compact plans

Improvements over base decomposer:
- Generates 3-step plans (vs. 4-step) for token efficiency
- Haiku-optimized instructions (shorter context, clearer scope)
- Token-aware decomposition heuristics
- Pre-execution planning reduces feedback loops
"""

import json
import logging
from dataclasses import dataclass, asdict, field
from typing import List, Dict, Optional, Any
from enum import Enum

logger = logging.getLogger(__name__)


class StepType(Enum):
    """Types of decomposition steps."""
    ANALYZE = "analyze"
    EXECUTE = "execute"
    REFINE = "refine"
    SYNTHESIZE = "synthesize"


@dataclass(frozen=True)
class HaikuOptimizedStep:
    """Haiku-optimized decomposition step (compact, immutable)."""

    index: int
    name: str
    step_type: str
    instruction: str  # Concise (<=200 tokens)
    focus: str  # Single-sentence focus
    expected_output_type: str  # "text" | "json" | "code"

    def to_dict(self) -> Dict[str, Any]:
        """Audit-safe serialization."""
        return {
            "index": self.index,
            "name": self.name,
            "step_type": self.step_type,
            "instruction": self.instruction,
            "focus": self.focus,
            "expected_output_type": self.expected_output_type,
        }


@dataclass(frozen=True)
class Tier2DecompositionPlan:
    """Optimized decomposition plan for Haiku efficiency."""

    task_id: str
    original_task: str
    task_type: str
    steps: List[HaikuOptimizedStep] = field(default_factory=list)
    synthesis_instruction: str = ""
    confidence: float = 0.0
    haiku_estimated_success: float = 0.0
    estimated_tokens: int = 0  # Estimated total tokens for plan + execution

    def to_dict(self) -> Dict[str, Any]:
        """Audit-safe serialization."""
        return {
            "task_id": self.task_id,
            "original_task": self.original_task,
            "task_type": self.task_type,
            "steps": [step.to_dict() for step in self.steps],
            "synthesis_instruction": self.synthesis_instruction,
            "confidence": self.confidence,
            "haiku_estimated_success": self.haiku_estimated_success,
            "estimated_tokens": self.estimated_tokens,
        }


class Tier2PromptDecomposer:
    """
    Optimized Haiku-focused decomposer for Tier 2.

    Goals:
    - Generate compact 3-step plans (vs. 4-5 in original)
    - Minimize token overhead (<800 tokens for plan)
    - Maximize Haiku efficiency through clear, focused steps
    - Quality >= 90% vs. Sonnet baseline
    """

    def __init__(self, task_id: str = "default"):
        self.task_id = task_id
        self.last_plan: Optional[Tier2DecompositionPlan] = None

    def decompose(
        self,
        task_input: str,
        task_type: Optional[str] = None,
        haiku_success_rate: float = 0.90,
        tenant_id: str = "_default",
    ) -> Tier2DecompositionPlan:
        """Decompose task into Haiku-optimized steps."""
        task_type = task_type or "general"

        # Generate 3-step plan (compact)
        steps = self._generate_compact_steps(task_input, task_type)

        # Build synthesis instruction
        synthesis = self._build_synthesis(steps, task_type)

        # Estimate confidence and tokens
        confidence = self._estimate_confidence(task_input, steps)
        estimated_tokens = self._estimate_total_tokens(task_input, steps)

        plan = Tier2DecompositionPlan(
            task_id=self.task_id,
            original_task=task_input,
            task_type=task_type,
            steps=steps,
            synthesis_instruction=synthesis,
            confidence=confidence,
            haiku_estimated_success=haiku_success_rate,
            estimated_tokens=estimated_tokens,
        )

        self.last_plan = plan
        logger.info(f"Decomposed into {len(steps)} Haiku-optimized steps (est. {estimated_tokens} tokens)")

        return plan

    def _generate_compact_steps(
        self,
        task_input: str,
        task_type: str,
    ) -> List[HaikuOptimizedStep]:
        """Generate 3-step compact decomposition."""
        steps = []

        # Task-specific compact decomposition
        if task_type == "code_review":
            steps = self._compact_code_review(task_input)
        elif task_type == "testing":
            steps = self._compact_testing(task_input)
        elif task_type == "analysis":
            steps = self._compact_analysis(task_input)
        elif task_type == "refactoring":
            steps = self._compact_refactoring(task_input)
        elif task_type == "documentation":
            steps = self._compact_documentation(task_input)
        elif task_type == "code_gen":
            steps = self._compact_code_gen(task_input)
        else:
            steps = self._compact_generic(task_input)

        # Add synthesis step (always last)
        if steps:
            synthesis_step = HaikuOptimizedStep(
                index=len(steps) + 1,
                name="Synthesize",
                step_type=StepType.SYNTHESIZE.value,
                instruction="Combine all outputs into a complete final response",
                focus="Integration and polishing",
                expected_output_type="text",
            )
            steps.append(synthesis_step)

        return steps

    def _compact_code_review(self, task_input: str) -> List[HaikuOptimizedStep]:
        """Compact 3-step code review."""
        steps = []

        # Step 1: Combined security & performance check
        steps.append(HaikuOptimizedStep(
            index=1,
            name="Security & Performance",
            step_type=StepType.ANALYZE.value,
            instruction="Analyze code for security vulnerabilities and performance issues (N+1 queries, inefficient algorithms)",
            focus="Find critical issues first",
            expected_output_type="json",
        ))

        # Step 2: Code quality & best practices
        steps.append(HaikuOptimizedStep(
            index=2,
            name="Quality & Best Practices",
            step_type=StepType.ANALYZE.value,
            instruction="Review code quality (naming, structure, documentation) and check best practices compliance",
            focus="Improve maintainability and standards adherence",
            expected_output_type="json",
        ))

        # Step 3: Summary with recommendations
        steps.append(HaikuOptimizedStep(
            index=3,
            name="Recommendations",
            step_type=StepType.REFINE.value,
            instruction="Prioritize issues and provide concrete, actionable recommendations with examples",
            focus="Actionable guidance for improvements",
            expected_output_type="text",
        ))

        return steps

    def _compact_testing(self, task_input: str) -> List[HaikuOptimizedStep]:
        """Compact 3-step testing plan."""
        steps = []

        steps.append(HaikuOptimizedStep(
            index=1,
            name="Happy Path & Error Cases",
            step_type=StepType.EXECUTE.value,
            instruction="Generate tests for happy path execution and error conditions (exceptions, invalid input)",
            focus="Core functionality and failure modes",
            expected_output_type="code",
        ))

        steps.append(HaikuOptimizedStep(
            index=2,
            name="Edge Cases & Integration",
            step_type=StepType.EXECUTE.value,
            instruction="Generate tests for boundary conditions and integration with other components",
            focus="Coverage of corner cases and dependencies",
            expected_output_type="code",
        ))

        steps.append(HaikuOptimizedStep(
            index=3,
            name="Test Optimization",
            step_type=StepType.REFINE.value,
            instruction="Review test quality, remove duplication, add missing assertions",
            focus="Comprehensive, maintainable test suite",
            expected_output_type="text",
        ))

        return steps

    def _compact_analysis(self, task_input: str) -> List[HaikuOptimizedStep]:
        """Compact 3-step data analysis."""
        steps = []

        steps.append(HaikuOptimizedStep(
            index=1,
            name="Patterns & Metrics",
            step_type=StepType.ANALYZE.value,
            instruction="Identify key patterns, trends, and quantify findings with statistics (counts, percentages, distributions)",
            focus="Uncover themes and quantify data",
            expected_output_type="json",
        ))

        steps.append(HaikuOptimizedStep(
            index=2,
            name="Correlations & Insights",
            step_type=StepType.ANALYZE.value,
            instruction="Find correlations between variables, identify relationships and causal factors",
            focus="Understand interdependencies",
            expected_output_type="json",
        ))

        steps.append(HaikuOptimizedStep(
            index=3,
            name="Recommendations",
            step_type=StepType.REFINE.value,
            instruction="Generate actionable recommendations with priorities and expected impact",
            focus="Business-driven insights",
            expected_output_type="text",
        ))

        return steps

    def _compact_refactoring(self, task_input: str) -> List[HaikuOptimizedStep]:
        """Compact 3-step refactoring plan."""
        steps = []

        steps.append(HaikuOptimizedStep(
            index=1,
            name="Issue Analysis & Design",
            step_type=StepType.ANALYZE.value,
            instruction="Analyze current code issues (bottlenecks, anti-patterns, debt) and design refactoring approach",
            focus="Root cause analysis and architecture plan",
            expected_output_type="text",
        ))

        steps.append(HaikuOptimizedStep(
            index=2,
            name="Implementation Plan",
            step_type=StepType.EXECUTE.value,
            instruction="Create phased implementation steps with migration strategy and testing approach",
            focus="Practical, incremental changes",
            expected_output_type="text",
        ))

        steps.append(HaikuOptimizedStep(
            index=3,
            name="Validation & Polish",
            step_type=StepType.REFINE.value,
            instruction="Verify refactoring correctness, performance improvements, and documentation",
            focus="Quality assurance and completeness",
            expected_output_type="text",
        ))

        return steps

    def _compact_documentation(self, task_input: str) -> List[HaikuOptimizedStep]:
        """Compact 3-step documentation."""
        steps = []

        steps.append(HaikuOptimizedStep(
            index=1,
            name="API Overview & Parameters",
            step_type=StepType.EXECUTE.value,
            instruction="Document API overview, endpoints, HTTP methods, and all parameters with types and constraints",
            focus="Complete reference documentation",
            expected_output_type="json",
        ))

        steps.append(HaikuOptimizedStep(
            index=2,
            name="Responses & Error Handling",
            step_type=StepType.EXECUTE.value,
            instruction="Document response schemas, status codes, and error handling with examples",
            focus="Success and error cases",
            expected_output_type="json",
        ))

        steps.append(HaikuOptimizedStep(
            index=3,
            name="Examples & Usage Guides",
            step_type=StepType.REFINE.value,
            instruction="Provide code examples in multiple languages and usage best practices",
            focus="Developer-friendly guides",
            expected_output_type="code",
        ))

        return steps

    def _compact_code_gen(self, task_input: str) -> List[HaikuOptimizedStep]:
        """Compact 3-step code generation."""
        steps = []

        steps.append(HaikuOptimizedStep(
            index=1,
            name="Specification & Design",
            step_type=StepType.ANALYZE.value,
            instruction="Create implementation specification with API contracts, data structures, and algorithms",
            focus="Clear technical requirements",
            expected_output_type="text",
        ))

        steps.append(HaikuOptimizedStep(
            index=2,
            name="Implementation with Validation",
            step_type=StepType.EXECUTE.value,
            instruction="Generate core logic with error handling, input validation, and exception handling",
            focus="Correct, robust implementation",
            expected_output_type="code",
        ))

        steps.append(HaikuOptimizedStep(
            index=3,
            name="Documentation & Polish",
            step_type=StepType.REFINE.value,
            instruction="Add code comments, docstrings, and usage examples. Verify quality.",
            focus="Maintainability and usability",
            expected_output_type="text",
        ))

        return steps

    def _compact_generic(self, task_input: str) -> List[HaikuOptimizedStep]:
        """Compact 3-step generic decomposition."""
        steps = []

        steps.append(HaikuOptimizedStep(
            index=1,
            name="Understanding",
            step_type=StepType.ANALYZE.value,
            instruction="Analyze task requirements and understand the goal",
            focus="Clear comprehension",
            expected_output_type="text",
        ))

        steps.append(HaikuOptimizedStep(
            index=2,
            name="Execution",
            step_type=StepType.EXECUTE.value,
            instruction="Execute the core work of the task",
            focus="Main deliverable",
            expected_output_type="text",
        ))

        steps.append(HaikuOptimizedStep(
            index=3,
            name="Verification",
            step_type=StepType.REFINE.value,
            instruction="Review output quality and completeness",
            focus="Quality assurance",
            expected_output_type="text",
        ))

        return steps

    def _build_synthesis(self, steps: List[HaikuOptimizedStep], task_type: str) -> str:
        """Build synthesis instruction (compact)."""
        if not steps:
            return "Synthesize outputs"

        step_names = [s.name for s in steps if s.step_type != StepType.SYNTHESIZE.value]

        synthesis = f"Integrate results from: {', '.join(step_names)}.\n"
        synthesis += "Ensure coherence, completeness, and actionability."

        return synthesis

    def _estimate_confidence(self, task_input: str, steps: List[HaikuOptimizedStep]) -> float:
        """Estimate confidence (0.0-1.0)."""
        confidence = 0.6

        # More steps = better decomposition understanding
        confidence += min(0.2, len(steps) * 0.07)

        # Structured input = more confidence
        if any(c in task_input for c in [":", "-", "1.", "•", "Analyze"]):
            confidence += 0.1

        return min(1.0, confidence)

    def _estimate_total_tokens(self, task_input: str, steps: List[HaikuOptimizedStep]) -> int:
        """Estimate total tokens for task execution with this plan."""
        # Plan overhead
        plan_overhead = 300  # Metadata + structure

        # Task input
        task_tokens = len(task_input.split()) * 1.3

        # Steps
        step_tokens = 0
        for step in steps:
            step_tokens += len((step.instruction + step.focus).split()) * 1.3

        # Execution (estimated Haiku tokens per step)
        execution_tokens = len(steps) * 200  # ~200 tokens per step execution

        return int(plan_overhead + task_tokens + step_tokens + execution_tokens)

    def validate_plan(self, plan: Tier2DecompositionPlan) -> tuple[bool, str]:
        """Validate a decomposition plan."""
        if not plan.steps:
            return False, "Plan has no steps"

        # Check indices are sequential
        indices = [s.index for s in plan.steps if s.step_type != StepType.SYNTHESIZE.value]
        if indices != list(range(1, len(indices) + 1)):
            return False, f"Step indices not sequential: {indices}"

        # Check synthesis is last
        if plan.steps[-1].step_type != StepType.SYNTHESIZE.value:
            return False, "Last step should be synthesis"

        # Check step types
        valid_types = {t.value for t in StepType}
        for step in plan.steps:
            if step.step_type not in valid_types:
                return False, f"Invalid step type: {step.step_type}"

        return True, ""


# Factory function
def create_tier2_decomposer(task_id: str = "default") -> Tier2PromptDecomposer:
    """Create a Tier 2 optimized PromptDecomposer."""
    return Tier2PromptDecomposer(task_id)

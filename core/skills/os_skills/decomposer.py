"""
k=3 Prompt-Level Decomposition Skill (ADR-0845, Tier 2)

Breaks a task into structured steps that can be executed independently by Haiku.
Receives full task from OS (Sonnet reasoning), outputs decomposition plan.
Worker then executes steps with appropriate model (Haiku if possible).

Mechanism:
1. OS receives full task + decomposition_hint="prompt_structured"
2. OS (Sonnet) reasons about task structure
3. Outputs JSON plan: steps 1–N, each with instruction + context
4. Worker (Haiku) executes steps, aggregates results
5. Worker synthesizes final response

Quality target: >= 95% vs. full-Sonnet (due to OS-structured planning)
Token savings target: >= 40% (Haiku more efficient than Sonnet on sub-steps)
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
    GENERATE = "generate"
    REVIEW = "review"
    AGGREGATE = "aggregate"
    SYNTHESIZE = "synthesize"
    FALLBACK = "fallback"


@dataclass(frozen=True)
class DecompositionStep:
    """A single step in the decomposition plan (immutable)."""

    index: int                      # Step number (1-N)
    name: str                       # Human-readable name
    step_type: str                  # StepType enum value
    instruction: str                # Task for this step
    context: str                    # Relevant context from full task
    expected_output_type: str       # "text" | "json" | "list" | "code"
    depends_on: List[int] = field(default_factory=list)  # Indices of prior steps
    resources_needed: List[str] = field(default_factory=list)  # External resources

    def to_dict(self) -> Dict[str, Any]:
        """Audit-safe serialization."""
        return {
            "index": self.index,
            "name": self.name,
            "step_type": self.step_type,
            "instruction": self.instruction,
            "context": self.context,
            "expected_output_type": self.expected_output_type,
            "depends_on": self.depends_on,
            "resources_needed": self.resources_needed,
        }


@dataclass(frozen=True)
class DecompositionPlan:
    """Complete decomposition plan (immutable, audit-safe)."""

    task_id: str                    # Unique task identifier
    original_task: str              # Full task text
    task_type: str                  # "code_review", "testing", etc.
    decomposition_strategy: str     # "sequential", "parallel", "mixed"
    steps: List[DecompositionStep] = field(default_factory=list)
    synthesis_instruction: str = ""  # How to combine step outputs
    confidence: float = 0.0          # Planner confidence (0.0-1.0)
    haiku_estimated_success: float = 0.0  # Expected Haiku success rate

    def to_dict(self) -> Dict[str, Any]:
        """Audit-safe serialization."""
        return {
            "task_id": self.task_id,
            "original_task": self.original_task,
            "task_type": self.task_type,
            "decomposition_strategy": self.decomposition_strategy,
            "steps": [step.to_dict() for step in self.steps],
            "synthesis_instruction": self.synthesis_instruction,
            "confidence": self.confidence,
            "haiku_estimated_success": self.haiku_estimated_success,
        }


class PromptDecomposer:
    """
    Decompose a task into structured steps for execution.

    Used by OS layer (Sonnet) to reason about task structure.
    Output fed to Worker layer (Haiku) for execution.
    """

    def __init__(self, task_id: str = "default"):
        self.task_id = task_id
        self.last_plan: Optional[DecompositionPlan] = None

    def decompose(
        self,
        task_input: str,
        task_type: Optional[str] = None,
        haiku_success_rate: float = 0.90,
        tenant_id: str = "_default",
    ) -> DecompositionPlan:
        """
        Decompose a task into structured steps.

        Args:
            task_input: Full task description
            task_type: Task classification (code_review, testing, etc.)
            haiku_success_rate: Expected Haiku success rate for this task type
            tenant_id: Tenant scope (audit trail)

        Returns:
            DecompositionPlan with structured steps
        """
        task_type = task_type or "general"

        # Detect decomposition strategy based on task type
        strategy = self._detect_strategy(task_input, task_type)

        # Generate decomposition steps based on task type
        steps = self._generate_steps(task_input, task_type, strategy)

        # Calculate synthesis instruction
        synthesis = self._build_synthesis_instruction(steps, task_type)

        # Estimate confidence (based on task clarity and structure)
        confidence = self._estimate_confidence(task_input, steps)

        plan = DecompositionPlan(
            task_id=self.task_id,
            original_task=task_input,
            task_type=task_type,
            decomposition_strategy=strategy,
            steps=steps,
            synthesis_instruction=synthesis,
            confidence=confidence,
            haiku_estimated_success=haiku_success_rate,
        )

        self.last_plan = plan
        logger.info(f"Decomposed task into {len(steps)} steps (strategy: {strategy})")

        return plan

    def _detect_strategy(self, task_input: str, task_type: str) -> str:
        """Detect optimal decomposition strategy."""
        task_lower = task_input.lower()

        # Parallel strategies for independent analyses
        if task_type in {"code_review", "analysis", "testing"}:
            if any(word in task_lower for word in ["and", "both", "multiple", "different"]):
                return "parallel"

        # Sequential for workflows with dependencies
        if task_type in {"refactoring", "code_gen", "implementation"}:
            if any(word in task_lower for word in ["then", "after", "depends", "requires"]):
                return "sequential"

        # Mixed for complex tasks
        if task_type in {"system_design", "architecture"}:
            return "mixed"

        # Default: sequential (safe for most tasks)
        return "sequential"

    def _generate_steps(
        self,
        task_input: str,
        task_type: str,
        strategy: str,
    ) -> List[DecompositionStep]:
        """Generate decomposition steps based on task type."""
        steps = []

        # Task-specific decomposition patterns
        if task_type == "code_review":
            steps = self._decompose_code_review(task_input)
        elif task_type == "testing":
            steps = self._decompose_testing(task_input)
        elif task_type == "documentation":
            steps = self._decompose_documentation(task_input)
        elif task_type == "analysis":
            steps = self._decompose_analysis(task_input)
        elif task_type == "refactoring":
            steps = self._decompose_refactoring(task_input)
        elif task_type == "code_gen":
            steps = self._decompose_code_gen(task_input)
        else:
            # Generic decomposition
            steps = self._decompose_generic(task_input, task_type)

        # Add synthesis step (always last)
        if steps:
            synthesis_step = DecompositionStep(
                index=len(steps) + 1,
                name="Synthesize",
                step_type=StepType.SYNTHESIZE.value,
                instruction="Combine outputs from all steps into a cohesive final response",
                context="Integration of all step results",
                expected_output_type="text",
                depends_on=list(range(1, len(steps) + 1)),
            )
            steps.append(synthesis_step)

        return steps

    def _decompose_code_review(self, task_input: str) -> List[DecompositionStep]:
        """Decompose code review into parallel steps."""
        steps = []

        # Step 1: Security review
        steps.append(DecompositionStep(
            index=1,
            name="Security Review",
            step_type=StepType.REVIEW.value,
            instruction="Review code for security vulnerabilities (SQL injection, auth flaws, insecure patterns)",
            context="Focus on authentication, data validation, and access control",
            expected_output_type="json",
        ))

        # Step 2: Performance review
        steps.append(DecompositionStep(
            index=2,
            name="Performance Review",
            step_type=StepType.REVIEW.value,
            instruction="Identify performance issues (N+1 queries, inefficient algorithms, memory leaks)",
            context="Analyze time and space complexity",
            expected_output_type="json",
        ))

        # Step 3: Code quality review
        steps.append(DecompositionStep(
            index=3,
            name="Code Quality Review",
            step_type=StepType.REVIEW.value,
            instruction="Assess code quality (naming, documentation, structure, maintainability)",
            context="Evaluate readability and adherence to style guides",
            expected_output_type="json",
        ))

        # Step 4: Best practices check
        steps.append(DecompositionStep(
            index=4,
            name="Best Practices",
            step_type=StepType.REVIEW.value,
            instruction="Verify best practices compliance (error handling, logging, testing)",
            context="Check against language-specific and framework-specific guidelines",
            expected_output_type="json",
        ))

        return steps

    def _decompose_testing(self, task_input: str) -> List[DecompositionStep]:
        """Decompose testing into phases."""
        steps = []

        steps.append(DecompositionStep(
            index=1,
            name="Happy Path Tests",
            step_type=StepType.GENERATE.value,
            instruction="Generate tests for successful execution paths",
            context="Normal operation scenarios",
            expected_output_type="code",
        ))

        steps.append(DecompositionStep(
            index=2,
            name="Error Handling Tests",
            step_type=StepType.GENERATE.value,
            instruction="Generate tests for error conditions and exception handling",
            context="Failure modes and recovery",
            expected_output_type="code",
        ))

        steps.append(DecompositionStep(
            index=3,
            name="Edge Case Tests",
            step_type=StepType.GENERATE.value,
            instruction="Generate tests for edge cases and boundary conditions",
            context="Extreme values, empty inputs, timeouts",
            expected_output_type="code",
        ))

        steps.append(DecompositionStep(
            index=4,
            name="Integration Tests",
            step_type=StepType.GENERATE.value,
            instruction="Generate tests for integration with other components",
            context="Inter-module interactions",
            expected_output_type="code",
        ))

        return steps

    def _decompose_documentation(self, task_input: str) -> List[DecompositionStep]:
        """Decompose documentation generation."""
        steps = []

        steps.append(DecompositionStep(
            index=1,
            name="API Overview",
            step_type=StepType.GENERATE.value,
            instruction="Document API endpoints and high-level overview",
            context="List operations, HTTP methods, base URLs",
            expected_output_type="text",
        ))

        steps.append(DecompositionStep(
            index=2,
            name="Parameter Documentation",
            step_type=StepType.GENERATE.value,
            instruction="Document all parameters (required, optional, types, constraints)",
            context="Request parameters and options",
            expected_output_type="json",
        ))

        steps.append(DecompositionStep(
            index=3,
            name="Response Schemas",
            step_type=StepType.GENERATE.value,
            instruction="Specify response structures and status codes",
            context="Success and error responses",
            expected_output_type="json",
        ))

        steps.append(DecompositionStep(
            index=4,
            name="Examples & Usage",
            step_type=StepType.GENERATE.value,
            instruction="Provide code examples in multiple languages",
            context="Python, JavaScript, cURL examples",
            expected_output_type="code",
        ))

        return steps

    def _decompose_analysis(self, task_input: str) -> List[DecompositionStep]:
        """Decompose data analysis task."""
        steps = []

        steps.append(DecompositionStep(
            index=1,
            name="Pattern Identification",
            step_type=StepType.ANALYZE.value,
            instruction="Identify key themes and patterns in the data",
            context="Thematic analysis, clustering, grouping",
            expected_output_type="json",
        ))

        steps.append(DecompositionStep(
            index=2,
            name="Quantitative Analysis",
            step_type=StepType.ANALYZE.value,
            instruction="Quantify findings with metrics and statistics",
            context="Counts, percentages, distributions",
            expected_output_type="json",
        ))

        steps.append(DecompositionStep(
            index=3,
            name="Correlation Analysis",
            step_type=StepType.ANALYZE.value,
            instruction="Find correlations and relationships between variables",
            context="Dependencies, causality, interactions",
            expected_output_type="json",
        ))

        steps.append(DecompositionStep(
            index=4,
            name="Recommendations",
            step_type=StepType.GENERATE.value,
            instruction="Generate recommendations based on analysis",
            context="Actionable insights, prioritization",
            expected_output_type="text",
        ))

        return steps

    def _decompose_refactoring(self, task_input: str) -> List[DecompositionStep]:
        """Decompose refactoring task (sequential)."""
        steps = []

        steps.append(DecompositionStep(
            index=1,
            name="Issue Analysis",
            step_type=StepType.ANALYZE.value,
            instruction="Analyze current code and identify issues",
            context="Bottlenecks, anti-patterns, technical debt",
            expected_output_type="json",
        ))

        steps.append(DecompositionStep(
            index=2,
            name="Redesign Plan",
            step_type=StepType.GENERATE.value,
            instruction="Design refactoring approach and new structure",
            context="Architecture changes, breaking changes",
            expected_output_type="text",
            depends_on=[1],
        ))

        steps.append(DecompositionStep(
            index=3,
            name="Implementation Steps",
            step_type=StepType.GENERATE.value,
            instruction="Outline step-by-step refactoring plan",
            context="Phased approach, migration strategy",
            expected_output_type="text",
            depends_on=[2],
        ))

        steps.append(DecompositionStep(
            index=4,
            name="Testing Strategy",
            step_type=StepType.GENERATE.value,
            instruction="Plan testing approach during refactoring",
            context="Regression tests, compatibility tests",
            expected_output_type="text",
            depends_on=[3],
        ))

        return steps

    def _decompose_code_gen(self, task_input: str) -> List[DecompositionStep]:
        """Decompose code generation task."""
        steps = []

        steps.append(DecompositionStep(
            index=1,
            name="Specification",
            step_type=StepType.ANALYZE.value,
            instruction="Analyze requirements and create implementation spec",
            context="API contracts, data structures, algorithms",
            expected_output_type="text",
        ))

        steps.append(DecompositionStep(
            index=2,
            name="Core Implementation",
            step_type=StepType.GENERATE.value,
            instruction="Generate core logic and business logic",
            context="Main algorithms and data processing",
            expected_output_type="code",
            depends_on=[1],
        ))

        steps.append(DecompositionStep(
            index=3,
            name="Error Handling",
            step_type=StepType.GENERATE.value,
            instruction="Add error handling and validation",
            context="Input validation, exception handling",
            expected_output_type="code",
            depends_on=[2],
        ))

        steps.append(DecompositionStep(
            index=4,
            name="Documentation",
            step_type=StepType.GENERATE.value,
            instruction="Add code comments and docstrings",
            context="API documentation, usage examples",
            expected_output_type="text",
            depends_on=[3],
        ))

        return steps

    def _decompose_generic(self, task_input: str, task_type: str) -> List[DecompositionStep]:
        """Generic decomposition for unknown task types."""
        steps = []

        # Default: analyze → plan → implement
        steps.append(DecompositionStep(
            index=1,
            name="Analysis",
            step_type=StepType.ANALYZE.value,
            instruction="Analyze the task requirements",
            context="Understand what needs to be done",
            expected_output_type="text",
        ))

        steps.append(DecompositionStep(
            index=2,
            name="Planning",
            step_type=StepType.GENERATE.value,
            instruction="Plan approach and strategy",
            context="How to accomplish the task",
            expected_output_type="text",
            depends_on=[1],
        ))

        steps.append(DecompositionStep(
            index=3,
            name="Implementation",
            step_type=StepType.GENERATE.value,
            instruction="Execute the plan",
            context="Actual execution",
            expected_output_type="text",
            depends_on=[2],
        ))

        return steps

    def _build_synthesis_instruction(self, steps: List[DecompositionStep], task_type: str) -> str:
        """Build instruction for synthesizing step outputs."""
        if not steps:
            return "Synthesize the outputs"

        step_names = [s.name for s in steps if s.step_type != StepType.SYNTHESIZE.value]

        synthesis = f"Combine results from the following steps into a comprehensive final response:\n"
        for i, name in enumerate(step_names, 1):
            synthesis += f"- {i}. {name}\n"
        synthesis += "\nEnsure the final response is cohesive, well-organized, and addresses the original task completely."

        return synthesis

    def _estimate_confidence(self, task_input: str, steps: List[DecompositionStep]) -> float:
        """Estimate confidence in decomposition."""
        confidence = 0.5  # Base confidence

        # Increase confidence with more steps (indicates better understanding)
        confidence += min(0.3, len(steps) * 0.05)

        # Increase confidence with clear task structure
        if any(c in task_input for c in [":", "-", "1.", "•"]):
            confidence += 0.1

        # Increase confidence with dependency clarity
        has_dependencies = any(s.depends_on for s in steps)
        if has_dependencies or any(word in task_input.lower() for word in ["then", "after", "depends"]):
            confidence += 0.05

        return min(1.0, confidence)

    def validate_plan(self, plan: DecompositionPlan) -> tuple[bool, str]:
        """
        Validate a decomposition plan.

        Returns:
            (is_valid, error_message)
        """
        if not plan.steps:
            return False, "Plan has no steps"

        # Check indices are sequential
        indices = [s.index for s in plan.steps if s.step_type != StepType.SYNTHESIZE.value]
        if indices != list(range(1, len(indices) + 1)):
            return False, f"Step indices not sequential: {indices}"

        # Check dependencies exist
        all_indices = set(s.index for s in plan.steps)
        for step in plan.steps:
            for dep in step.depends_on:
                if dep not in all_indices:
                    return False, f"Step {step.index} depends on non-existent step {dep}"

        # Check no circular dependencies (basic check)
        # A more complete check would need topological sort

        return True, ""


# Factory function
def create_decomposer(task_id: str = "default") -> PromptDecomposer:
    """Create a PromptDecomposer instance."""
    return PromptDecomposer(task_id)

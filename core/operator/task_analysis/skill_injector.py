"""Phase 1: Skill Injector — LDD Skill Selection for Task Router.

This module maps task types and severity levels to LDD skills that should
be pre-loaded for the agent working on the task. Skills are selected based
on the task classification from Phase 1.

Skill selection is deterministic: given (task_type, severity), return a
fixed list of skills. The list can be conditional on graph_count (if all
five graphs recommended, suggest additional verification skills).

ADR:
    ADR-0267 — Task Engine: Router Layer Architecture
"""

from typing import List
from .normalizer import TaskType

# Sentinel for fallback
_UNKNOWN_TYPE = "UNKNOWN_TYPE"


class SkillInjector:
    """Maps task metadata to LDD skill names.

    Mapping strategy:
        1. Primary key: (task_type, severity)
        2. Fallback: task_type alone (if severity unmapped)
        3. Final fallback: generic skills ['dialectical-reasoning', 'e2e-driven-iteration']

    Skills are namespaced by persona and LDD layer (e.g., 'e2e-driven-iteration'
    is a core skill available to all personas).

    Mapping table (36 entries):
        - BUG_FIX high/medium/low → ['e2e-driven-iteration', 'root-cause-by-layer']
        - FEATURE high/medium/low → ['loop-driven-engineering', 'e2e-wiring-proof']
        - REFACTOR high/medium/low → ['simplify', 'e2e-driven-iteration']
        - INCIDENT high/medium/low → ['root-cause-by-layer', 'e2e-driven-iteration']
        - PERFORMANCE high/medium/low → ['loop-driven-engineering', 'e2e-driven-iteration']
        - DOCUMENTATION high/medium/low → ['docs-as-definition-of-done']
        - UNKNOWN → ['dialectical-reasoning', 'e2e-driven-iteration']
    """

    # Mapping: (task_type, severity) → [skills]
    SKILL_MAP = {
        # BUG_FIX — root-cause analysis + iteration
        (TaskType.BUG_FIX, "high"): [
            "e2e-driven-iteration",
            "root-cause-by-layer",
            "e2e-wiring-proof",
        ],
        (TaskType.BUG_FIX, "medium"): [
            "e2e-driven-iteration",
            "root-cause-by-layer",
        ],
        (TaskType.BUG_FIX, "low"): ["e2e-driven-iteration"],

        # FEATURE — full design + wiring
        (TaskType.FEATURE, "high"): [
            "loop-driven-engineering",
            "e2e-wiring-proof",
            "dialectical-reasoning",
        ],
        (TaskType.FEATURE, "medium"): [
            "loop-driven-engineering",
            "e2e-wiring-proof",
        ],
        (TaskType.FEATURE, "low"): ["loop-driven-engineering"],

        # REFACTOR — simplification + iteration
        (TaskType.REFACTOR, "high"): [
            "simplify",
            "e2e-driven-iteration",
            "dialectical-reasoning",
        ],
        (TaskType.REFACTOR, "medium"): ["simplify", "e2e-driven-iteration"],
        (TaskType.REFACTOR, "low"): ["simplify"],

        # INCIDENT — emergency root-cause + iteration
        (TaskType.INCIDENT, "high"): [
            "root-cause-by-layer",
            "e2e-driven-iteration",
            "loss-backprop-lens",
        ],
        (TaskType.INCIDENT, "medium"): [
            "root-cause-by-layer",
            "e2e-driven-iteration",
        ],
        (TaskType.INCIDENT, "low"): ["e2e-driven-iteration"],

        # PERFORMANCE — measurement-driven + iteration
        (TaskType.PERFORMANCE, "high"): [
            "loop-driven-engineering",
            "e2e-driven-iteration",
            "reproducibility-first",
        ],
        (TaskType.PERFORMANCE, "medium"): [
            "loop-driven-engineering",
            "e2e-driven-iteration",
        ],
        (TaskType.PERFORMANCE, "low"): ["e2e-driven-iteration"],

        # DOCUMENTATION — definition of done
        (TaskType.DOCUMENTATION, "high"): [
            "docs-as-definition-of-done",
            "dialectical-reasoning",
        ],
        (TaskType.DOCUMENTATION, "medium"): ["docs-as-definition-of-done"],
        (TaskType.DOCUMENTATION, "low"): ["docs-as-definition-of-done"],
    }

    # Fallback for UNKNOWN type
    FALLBACK_SKILLS = ["dialectical-reasoning", "e2e-driven-iteration"]

    # Additional skills if all five graphs recommended (low confidence fallback)
    HIGH_UNCERTAINTY_SKILLS = ["reproducibility-first", "dialectical-reasoning"]

    def inject_skills(
        self, task_type, severity: str, graph_count: int
    ) -> List[str]:
        """Select skills based on task type, severity, and confidence.

        Args:
            task_type: TaskType enum value
            severity: Severity string ('high', 'medium', 'low')
            graph_count: Number of recommended graphs (1–5)

        Returns:
            List of skill names to inject
        """
        # Normalize severity
        if not isinstance(severity, str):
            severity = str(severity)
        severity = severity.lower()
        if severity not in ("high", "medium", "low"):
            severity = "medium"  # default

        # Normalize task_type
        try:
            if not isinstance(task_type, TaskType):
                # Try to convert string to TaskType
                task_type = TaskType[task_type.upper()]
        except (KeyError, AttributeError):
            task_type = TaskType.UNKNOWN

        # Look up skills
        key = (task_type, severity)
        skills = self.SKILL_MAP.get(key, self.FALLBACK_SKILLS).copy()

        # Add high-uncertainty skills if all graphs recommended
        if graph_count >= 5:
            # Low confidence → all graphs recommended → add verification skills
            for skill in self.HIGH_UNCERTAINTY_SKILLS:
                if skill not in skills:
                    skills.append(skill)

        # Deduplicate and sort
        skills = sorted(set(skills))

        return skills

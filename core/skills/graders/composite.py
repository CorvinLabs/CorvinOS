"""Composite Skill Grader — chains multiple graders (ADR-0307)."""

from __future__ import annotations

from typing import Any

from core.skills.skill import Grade


class CompositeGrader:
    """Chains multiple graders and combines their scores.

    Strategies:
    - average: mean of all successful grades
    - weighted: weighted mean (requires weights)
    - max: highest score
    - min: lowest score
    - first: return first non-None grade
    """

    def __init__(
        self,
        graders: list[tuple[Any, float]],  # (grader, weight) pairs
        strategy: str = "average",
    ):
        """Initialize composite grader.

        Args:
            graders: List of (grader, weight) tuples
            strategy: Combination strategy (average | weighted | max | min | first)
        """
        if not graders:
            raise ValueError("CompositeGrader requires at least one grader")

        self.graders = graders
        self.strategy = strategy

        if strategy == "weighted" and not all(w > 0 for _, w in graders):
            raise ValueError("Weighted strategy requires positive weights")

    async def grade(self, request: dict[str, Any]) -> Grade | None:
        """Grade using composite strategy.

        Args:
            request: Invocation metadata dict

        Returns:
            Grade combining all graders, or None if all fail.
        """
        grades = []
        weights = []

        # Collect grades from all graders
        for grader, weight in self.graders:
            try:
                grade = await grader.grade(request)
                if grade:
                    grades.append(grade)
                    weights.append(weight)
            except Exception:
                pass  # Grader failed, skip it

        if not grades:
            return None  # All graders failed

        # Combine scores based on strategy
        if self.strategy == "average":
            avg_value = sum(g.value for g in grades) / len(grades)
            feedback = f"Average of {len(grades)} graders: {avg_value:.2f}"
            return Grade(value=avg_value, feedback=feedback)

        elif self.strategy == "weighted":
            total_weight = sum(weights)
            weighted_sum = sum(g.value * w for g, w in zip(grades, weights))
            avg_value = weighted_sum / total_weight
            feedback = f"Weighted average ({len(grades)} graders): {avg_value:.2f}"
            return Grade(value=avg_value, feedback=feedback)

        elif self.strategy == "max":
            max_grade = max(grades, key=lambda g: g.value)
            return Grade(value=max_grade.value, feedback=f"Max of {len(grades)}: {max_grade.feedback}")

        elif self.strategy == "min":
            min_grade = min(grades, key=lambda g: g.value)
            return Grade(value=min_grade.value, feedback=f"Min of {len(grades)}: {min_grade.feedback}")

        elif self.strategy == "first":
            return grades[0]

        else:
            raise ValueError(f"Unknown strategy: {self.strategy}")

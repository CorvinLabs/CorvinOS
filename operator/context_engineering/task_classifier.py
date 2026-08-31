"""Task complexity classifier for Phase 3 adaptive routing (ADR-0391).

Classifies task complexity to enable dynamic budget allocation:
  SIMPLE → skip expensive stages (graph, skills)
  MODERATE → balanced allocation across all stages
  COMPLEX → full allocation to all stages

Uses keyword-based heuristics for classification with confidence scoring.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TaskComplexity(Enum):
    """Task complexity classification levels."""
    SIMPLE = "simple"
    MODERATE = "moderate"
    COMPLEX = "complex"


@dataclass(frozen=True)
class ClassificationResult:
    """Result of task complexity classification."""
    complexity: TaskComplexity
    confidence: float  # 0.0-1.0
    keyword_matches: int


# Heuristic keywords for each complexity level
_SIMPLE_KEYWORDS = {
    "rename", "delete", "remove", "format", "comment", "typo", "fix",
    "syntax", "lint", "sort", "reorder", "cleanup", "clean", "trim",
    "strip", "pad", "align", "case", "replace", "substitute",
}

_COMPLEX_KEYWORDS = {
    "refactor", "design", "architecture", "optimize", "implement",
    "integrate", "build", "create", "feature", "framework", "pattern",
    "algorithm", "performance", "security", "scale", "new", "rewrite",
    "restructure", "migrate", "transform", "generalize", "abstract",
    "architect", "complex", "sophisticated",
}

# Words that boost confidence in the classification
_CONFIDENCE_BOOSTERS = {
    # Simple task indicators
    "just": 0.1,
    "simple": 0.15,
    "quick": 0.1,
    "trivial": 0.15,
    "minor": 0.1,
    # Complex task indicators
    "complex": 0.15,
    "intricate": 0.15,
    "sophisticated": 0.15,
    "comprehensive": 0.1,
    "substantial": 0.1,
}


def classify(task_text: str) -> ClassificationResult:
    """Classify a task's complexity based on keyword heuristics.

    Args:
        task_text: The task description to classify

    Returns:
        ClassificationResult with complexity level and confidence score
    """
    if not task_text or not isinstance(task_text, str):
        return ClassificationResult(
            complexity=TaskComplexity.MODERATE,
            confidence=0.0,
            keyword_matches=0
        )

    lower_text = task_text.lower()
    words = set(lower_text.split())

    # Count keyword matches
    simple_matches = len(words & _SIMPLE_KEYWORDS)
    complex_matches = len(words & _COMPLEX_KEYWORDS)
    booster_matches = {k: v for k, v in _CONFIDENCE_BOOSTERS.items()
                      if k in words}

    total_matches = simple_matches + complex_matches

    if total_matches == 0:
        # No keywords found → MODERATE with low confidence
        return ClassificationResult(
            complexity=TaskComplexity.MODERATE,
            confidence=0.1,
            keyword_matches=0
        )

    # Determine complexity based on keyword ratio
    if simple_matches > complex_matches * 2:
        complexity = TaskComplexity.SIMPLE
    elif complex_matches > simple_matches * 2:
        complexity = TaskComplexity.COMPLEX
    else:
        complexity = TaskComplexity.MODERATE

    # Calculate confidence score (0.0-1.0)
    # Base: keyword density (matches per word)
    base_confidence = min(0.8, total_matches / max(len(words), 1))

    # Boost by confidence boosters
    booster_sum = sum(booster_matches.values())
    confidence = min(1.0, base_confidence + booster_sum)

    return ClassificationResult(
        complexity=complexity,
        confidence=confidence,
        keyword_matches=total_matches
    )


def classify_simple(task_text: str) -> TaskComplexity:
    """Quick classification returning just the complexity level."""
    return classify(task_text).complexity

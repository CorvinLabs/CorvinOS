"""Guidance Classifier subsystem for Vibe Engineering midstream steering.

Classifies voice input into guidance categories:
- task_input: Original task description
- midstream_guidance: Instruction to modify current task
- task_question: Question about current progress
- interrupt: Stop/cancel/pause command

ADR: ADR-0280 (Voice-Native Midstream Guidance Classifier)
"""

from .classifier import GuidanceClassifier
from .classifier_types import (
    GuidanceEvent,
    ClassificationResult,
    GuidanceClass,
    RiskLevel,
)

__all__ = [
    "GuidanceClassifier",
    "GuidanceEvent",
    "ClassificationResult",
    "GuidanceClass",
    "RiskLevel",
]

"""Task analysis and normalization module.

Provides components for analyzing, normalizing, and enriching raw task descriptions
with structured metadata for downstream processing.

This module is part of the Task Engine (ADR-0267) and implements Phase 0: Task Normalizer.

Example:
    >>> from operator.task_analysis.normalizer import TaskNormalizer
    >>> normalizer = TaskNormalizer()
    >>> task = "Fix crash in voice module when processing long audio files"
    >>> normalized = normalizer.normalize(task)
    >>> print(normalized.type)
    TaskType.BUG_FIX
    >>> print(normalized.severity)
    'high'
"""

from .normalizer import (
    TaskNormalizer,
    TaskType,
    Severity,
    NormalizedTask,
    SufficiencyCheck,
    InsufficientTaskInfo,
)

__all__ = [
    'TaskNormalizer',
    'TaskType',
    'Severity',
    'NormalizedTask',
    'SufficiencyCheck',
    'InsufficientTaskInfo',
]

__version__ = '0.1.0'

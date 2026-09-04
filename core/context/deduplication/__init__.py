"""Context Deduplication (ADR-0564: Phases 3-4)."""

from .deduplicator import (
    ContextDeduplicator,
    DedupeResult,
    deduplicate_exact,
)
from .frequency import FrequencyDatabase
from .semantic import SemanticDeduplicator
from .learner import SemanticDedupLearner

__all__ = [
    'ContextDeduplicator',
    'DedupeResult',
    'deduplicate_exact',
    'FrequencyDatabase',
    'SemanticDeduplicator',
    'SemanticDedupLearner',
]

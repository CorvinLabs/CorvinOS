"""Task analysis and routing module.

Provides components for analyzing, normalizing, routing, and classifying
raw task descriptions with structured metadata for downstream processing.

This module is part of the Task Engine (ADR-0267) and implements:
    - Phase 0: Task Normalizer (normalizer.py)
    - Phase 1: Graph Routers (graph_routing.py)
    - Phase 1: Confidence Scorer (confidence_scorer.py)
    - Phase 1: Task Classifier (classifier.py)
    - Phase 1: Skill Injector (skill_injector.py)

Example:
    >>> from operator.task_analysis import TaskNormalizer, TaskClassifier
    >>> normalizer = TaskNormalizer()
    >>> task = "Fix crash in voice module when processing long audio files"
    >>> normalized = normalizer.normalize(task)
    >>> classifier = TaskClassifier()
    >>> classified = classifier.classify(normalized)
    >>> print(classified.confidence)
    0.75
    >>> print(classified.skills_to_inject)
    ['e2e-driven-iteration', 'root-cause-by-layer']
"""

from .normalizer import (
    TaskNormalizer,
    TaskType,
    Severity,
    NormalizedTask,
    SufficiencyCheck,
    InsufficientTaskInfo,
)

from .graph_routing import (
    CallGraphRouter,
    TestGraphRouter,
    ADRGraphRouter,
    LayerGraphRouter,
    CodeDiffGraphRouter,
    GraphMatch,
)

from .confidence_scorer import (
    ConfidenceScorer,
    ScoredRouters,
)

from .classifier import (
    TaskClassifier,
    ClassifiedTask,
)

from .skill_injector import (
    SkillInjector,
)

from .engine import (
    TaskEngine,
    EngineResult,
    EnginePhase,
    EngineError,
)

from .metrics import (
    TaskMetrics,
    MetricsPhase,
    MetricsOutcome,
    PhaseMetrics,
)

__all__ = [
    # Normalizer (Phase 0)
    'TaskNormalizer',
    'TaskType',
    'Severity',
    'NormalizedTask',
    'SufficiencyCheck',
    'InsufficientTaskInfo',

    # Routers (Phase 1)
    'CallGraphRouter',
    'TestGraphRouter',
    'ADRGraphRouter',
    'LayerGraphRouter',
    'CodeDiffGraphRouter',
    'GraphMatch',

    # Scorer (Phase 1)
    'ConfidenceScorer',
    'ScoredRouters',

    # Classifier (Phase 1)
    'TaskClassifier',
    'ClassifiedTask',

    # Skill Injector (Phase 1)
    'SkillInjector',

    # Engine (Phases 0–5)
    'TaskEngine',
    'EngineResult',
    'EnginePhase',
    'EngineError',

    # Metrics & Monitoring
    'TaskMetrics',
    'MetricsPhase',
    'MetricsOutcome',
    'PhaseMetrics',
]

__version__ = '0.2.0'  # Phase 1 completion

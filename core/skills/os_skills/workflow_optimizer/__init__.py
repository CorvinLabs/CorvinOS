"""
Workflow Optimizer Skill (Phase 10 Stream 1)

Production-ready OS-level Skill for learning optimal task routing from operator feedback.

**Main Classes:**
- WorkflowOptimizer: Core skill logic (task classification, routing, learning)
- TaskComplexityClassifier: Feature extraction + scoring
- RoutingDecision: Immutable decision output
- SkillConfig: Persistent routing configuration

**Quick Start:**
```python
from core.skills.os_skills.workflow_optimizer import (
    WorkflowOptimizer,
    RoutingInput,
)

optimizer = WorkflowOptimizer()
decision = optimizer.route_task(RoutingInput(
    task_id="task_1",
    task_content="Refactor authentication to OAuth2",
    tenant_id="_default"
))

print(f"Routed to {decision.model.value} (confidence={decision.confidence:.2f})")
```

**Documentation:**
- See README.md for full API reference
- See ADR-2030 for architecture
- See STREAM1_BOOTSTRAP_STATUS.md for 10-week roadmap

**Status:**
- Phase 10 Stream 1 (bootstrap complete, Week 1 starting)
- ADR-2030 (ACCEPTED)
- Gate 1 target: 2026-09-29 (10 tests passing)
"""

from .skill import (
    WorkflowOptimizer,
    WorkflowOptimizerInstance,
    TaskComplexity,
    ModelTier,
    RoutingDecision,
    RoutingInput,
    SkillConfig,
)
from .classifier import (
    TaskComplexityClassifier,
    extract_features,
    score_task,
    classify_task,
)

__all__ = [
    # Skill
    "WorkflowOptimizer",
    "WorkflowOptimizerInstance",
    # Types
    "TaskComplexity",
    "ModelTier",
    "RoutingDecision",
    "RoutingInput",
    "SkillConfig",
    # Classifier
    "TaskComplexityClassifier",
    "extract_features",
    "score_task",
    "classify_task",
]

__version__ = "1.0.0"
__status__ = "production-ready (bootstrap phase)"
__adr__ = "ADR-2030"

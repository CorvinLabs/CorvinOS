"""L5 Staging Deployment Package.

3-week autonomous L5 deployment infrastructure:
- Week 1: Staging deployment, feedback collection, learning verification
- Week 2: Operator beta testing, threshold tuning, training refinement
- Week 3: Production rollout, SLA monitoring, success criteria verification
"""

from .staging_config import StagingL5Config, get_staging_config, staging_config_as_dict
from .feedback_collector import (
    FeedbackCollector,
    DecisionRecord,
    FeedbackCycleMetrics,
    OperatorDecision,
)
from .operator_beta import (
    OperatorBetaManager,
    OperatorFeedback,
    TunedAlertThresholds,
    OperatorBetaMetrics,
)
from .production_rollout import (
    ProductionRolloutManager,
    CanaryPhaseResult,
    ProductionRolloutMetrics,
)

__all__ = [
    # Staging Config
    "StagingL5Config",
    "get_staging_config",
    "staging_config_as_dict",
    # Week 1: Feedback Collection
    "FeedbackCollector",
    "DecisionRecord",
    "FeedbackCycleMetrics",
    "OperatorDecision",
    # Week 2: Operator Beta
    "OperatorBetaManager",
    "OperatorFeedback",
    "TunedAlertThresholds",
    "OperatorBetaMetrics",
    # Week 3: Production Rollout
    "ProductionRolloutManager",
    "CanaryPhaseResult",
    "ProductionRolloutMetrics",
]

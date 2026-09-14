"""Definition-of-Done Verifier Skill 2.0 - Phase 1 + Phase 2."""

__version__ = "0.2.0"
__status__ = "beta"

from .skill import DoD_VerifierSkill, DoD_VerificationResult, AuditFailedError
from .loss_signal import DoD_LossSignal
from .feedback_event import DoD_FeedbackEvent
from .weight_optimizer import DoD_WeightOptimizer
from .weight_persistence import WeightPersistence
from .api_handlers import DoD_OutcomeSink, DoD_FeedbackCollector

__all__ = [
    "DoD_VerifierSkill",
    "DoD_VerificationResult",
    "AuditFailedError",
    "DoD_LossSignal",
    "DoD_FeedbackEvent",
    "DoD_WeightOptimizer",
    "WeightPersistence",
    "DoD_OutcomeSink",
    "DoD_FeedbackCollector",
]

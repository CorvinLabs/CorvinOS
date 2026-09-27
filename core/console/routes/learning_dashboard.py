"""Learning Dashboard API endpoints.

Track B: Learning Loop integration (ADR-0676).

NOT WIRED: no production caller as of 2026-09-27 (adversarial review). This
module sits OUTSIDE the ``corvin_console`` package; no app mounts its router
(``/api/v1/console/learning``). The live learning surface is
``corvin_console/routes/method_discovery_api.py`` (``/v1/console/learning/*``:
real, session-bound, consent-gated feedback and optimizer epochs).

NOT IMPLEMENTED (defused 2026-09-27). Every handler here fabricated its
result: ``/feedback`` answered "accepted" without storing anything,
``/optimize`` answered "queued" without queuing, ``/loss-signals/detect`` fed a
DUMMY confidence series, all routes ran with no session and a hard-coded
``_default`` tenant, and ``LearningDashboardAPI`` returned invented lifecycles
and — worst — ``verify_audit_chain`` reported ``all_hashes_valid: True`` for a
chain it never read. Every route now answers 501 ``not_implemented`` and every
``LearningDashboardAPI`` method raises ``NotImplementedError``. The request /
response models are kept as the contract.
"""

from typing import Dict, Any, List, Optional
from datetime import datetime
from pydantic import BaseModel, Field
from uuid import uuid4
from fastapi import APIRouter, HTTPException, Depends
import logging

logger = logging.getLogger(__name__)

# ============================================================================
# PYDANTIC MODELS (Request/Response)
# ============================================================================

class FeedbackRequest(BaseModel):
    """User submits feedback on a Skill execution."""
    skill_id: str = Field(..., description="e.g., 'os.delegation_router'")
    task_id: str = Field(..., description="Task this feedback is about")
    outcome_feedback: Optional[str] = Field(None, description="yes/no/unknown")
    quality_rating: Optional[int] = Field(None, description="1–5 stars", ge=1, le=5)
    preference_feedback: Optional[str] = Field(None, description="llm/deterministic/either")
    reason: Optional[str] = Field(None, description="User's explanation (auto-scrubbed of PII)")
    confidence: Optional[float] = Field(None, description="User's confidence in feedback (0–1)", ge=0, le=1)


class FeedbackResponse(BaseModel):
    """Response after feedback submission."""
    feedback_id: str
    skill_id: str
    task_id: str
    status: str  # "accepted" | "rejected"
    reason: Optional[str] = None
    timestamp: str


class OptimizeRequest(BaseModel):
    """Trigger skill config optimization."""
    skill_id: Optional[str] = Field(None, description="Optimize one skill, or None for all")
    force: bool = Field(False, description="Force optimization even if < 10 feedback samples")


class OptimizeResponse(BaseModel):
    """Response after optimization trigger."""
    optimization_id: str
    skill_id: Optional[str]
    status: str  # "queued" | "in_progress" | "completed"
    feedback_count: int
    config_updates: Dict[str, float] = Field(default_factory=dict)
    convergence_detected: bool
    timestamp: str


# ============================================================================
# LEARNING LOOP COMPONENTS (Dependency Injection)
# ============================================================================

_feedback_collector = None
_optimizer = None
_skill_forge = None


def get_feedback_collector():
    """DI: Get feedback collector singleton."""
    global _feedback_collector
    if _feedback_collector is None:
        from core.learning.feedback_sink import FeedbackCollector, FeedbackEvent
        _feedback_collector = FeedbackCollector()
    return _feedback_collector


def get_optimizer():
    """DI: Get optimizer singleton."""
    global _optimizer
    if _optimizer is None:
        from core.learning.skill_optimizer import SkillOptimizer
        _optimizer = SkillOptimizer()
    return _optimizer


def get_skill_forge():
    """DI: Get skill forge singleton."""
    global _skill_forge
    if _skill_forge is None:
        from core.skill_forge.skill_forge_v2 import get_skill_forge as get_sf
        _skill_forge = get_sf()
    return _skill_forge


# ============================================================================
# LEARNING DASHBOARD API
# ============================================================================

class LearningDashboardAPI:
    """Console API for learning lifecycle visualization — NOT IMPLEMENTED.

    Every method raises ``NotImplementedError``; none may be answered from
    placeholder data (``verify_audit_chain`` in particular must never report a
    chain as valid without verifying it — use ``forge.security_events``'
    verifier on ``tenant_audit_chain(tenant)``).
    """

    def __init__(self, audit_store=None, learning_daemon=None):
        self.audit_store = audit_store
        self.daemon = learning_daemon

    async def get_skill_lifecycle(self, skill_id: str) -> Dict[str, Any]:
        raise NotImplementedError("skill lifecycle tracing is not implemented")

    async def get_audit_chain(self, skill_id: str) -> Dict[str, Any]:
        raise NotImplementedError("per-skill audit chain view is not implemented")

    async def verify_audit_chain(self) -> Dict[str, Any]:
        raise NotImplementedError("chain verification is not implemented here")

    async def export_compliance_report(self, start_date: str, end_date: str) -> Dict[str, Any]:
        raise NotImplementedError("compliance export is not implemented here")


# ============================================================================
# FASTAPI ROUTER (Real Entry Points)
# ============================================================================

router = APIRouter(prefix="/api/v1/console/learning", tags=["console-learning"])


def _not_implemented() -> HTTPException:
    return HTTPException(
        status_code=501,
        detail={"status": "not_implemented",
                "reason": "use /v1/console/learning/* (method_discovery_api) on this build"},
    )


@router.post("/feedback", response_model=FeedbackResponse)
async def submit_feedback(req: FeedbackRequest) -> FeedbackResponse:
    """NOT IMPLEMENTED (501) — nothing would be stored."""
    raise _not_implemented()


@router.post("/optimize", response_model=OptimizeResponse)
async def trigger_optimization(req: OptimizeRequest) -> OptimizeResponse:
    """NOT IMPLEMENTED (501) — nothing would be queued."""
    raise _not_implemented()


# ============================================================================
# LOSS SIGNAL MONITORING ENDPOINTS (Phase 3)
# ============================================================================

class LossSignalRequest(BaseModel):
    """Request to emit a loss signal."""
    signal_type: str  # "latency" | "confidence" | "feedback" | "ab_test"
    metric_value: float
    threshold: float


class LossSignalResponse(BaseModel):
    """Response after loss signal detection."""
    detected: bool
    signal_type: Optional[str] = None
    severity: Optional[str] = None
    recommendation: Optional[str] = None
    timestamp: str


class LearningLoopStatusResponse(BaseModel):
    """Response with learning loop status."""
    tenant_id: str
    convergence_status: str  # "converged" | "converging" | "stalled" | "diverging"
    current_confidence: float
    target_confidence: float = 0.90
    estimated_days_to_target: float
    loop_velocity_cycles_24h: int
    mean_cycle_time_minutes: float


def get_loss_signal_emitter():
    """DI: Get loss signal emitter singleton."""
    from core.vibe.loss_signal_emitter import get_emitter
    return get_emitter()


def get_learning_loop_tracker():
    """DI: Get learning loop tracker singleton."""
    from core.vibe.learning_loop_tracker import get_tracker
    return get_tracker()


@router.post("/loss-signals/detect", response_model=LossSignalResponse)
async def detect_loss_signal(req: LossSignalRequest) -> LossSignalResponse:
    """NOT IMPLEMENTED (501)."""
    raise _not_implemented()


@router.get("/loss-signals/recent", response_model=List[Dict[str, Any]])
async def get_recent_loss_signals(minutes: int = 60) -> List[Dict[str, Any]]:
    """NOT IMPLEMENTED (501)."""
    raise _not_implemented()


@router.get("/loss-signals/critical", response_model=List[Dict[str, Any]])
async def get_critical_loss_signals() -> List[Dict[str, Any]]:
    """NOT IMPLEMENTED (501)."""
    raise _not_implemented()


@router.get("/learning-loop/status", response_model=LearningLoopStatusResponse)
async def get_learning_loop_status() -> LearningLoopStatusResponse:
    """NOT IMPLEMENTED (501)."""
    raise _not_implemented()


@router.get("/learning-loop/convergence-forecast", response_model=Dict[str, Any])
async def get_convergence_forecast() -> Dict[str, Any]:
    """NOT IMPLEMENTED (501)."""
    raise _not_implemented()


@router.get("/learning-loop/velocity", response_model=Dict[str, Any])
async def get_learning_loop_velocity(window_hours: int = 24) -> Dict[str, Any]:
    """NOT IMPLEMENTED (501)."""
    raise _not_implemented()

"""
ADR-0377: Multi-Model Routing & Cost Optimizer

Core data structures for per-subsystem model assignment and cost tracking.
"""

from dataclasses import dataclass, field
from typing import Dict, Optional, Any
from datetime import datetime
from enum import Enum
import hashlib


class ModelChoice(str, Enum):
    """Supported model choices."""
    HAIKU = "Haiku"
    OPUS = "Opus"
    SONNET = "Sonnet"


@dataclass(frozen=True)
class ModelRoutingPlan:
    """Immutable routing decision for a task."""

    task_id: str
    task_type: str
    subsystem_routes: Dict[str, str]  # subsystem_name → "Haiku" | "Opus" | "Sonnet"
    estimated_total_cost: float
    confidence: float  # 0.0 to 1.0 — confidence in routing accuracy
    reasoning: Dict[str, Any] = field(default_factory=dict)  # Detailed reasoning per subsystem
    created_at: datetime = field(default_factory=datetime.utcnow)

    def get_model_for_subsystem(self, subsystem: str) -> str:
        """Retrieve model assignment for a subsystem."""
        return self.subsystem_routes.get(subsystem, ModelChoice.OPUS)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for audit trail."""
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "subsystem_routes": self.subsystem_routes,
            "estimated_total_cost": self.estimated_total_cost,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True)
class SubsystemCostEvent:
    """Immutable cost tracking event (for audit trail)."""

    task_id: str
    subsystem: str
    model_used: str
    estimated_cost: float
    actual_cost: float
    variance: float  # actual - estimated
    success: bool = True  # Did this subsystem succeed?
    error_msg: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.utcnow)
    routing_plan_hash: Optional[str] = None  # Hash of the routing plan this was based on

    def variance_pct(self) -> float:
        """Variance as percentage of estimate."""
        if self.estimated_cost == 0:
            return 0.0
        return (self.variance / self.estimated_cost) * 100.0

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for audit trail."""
        return {
            "task_id": self.task_id,
            "subsystem": self.subsystem,
            "model_used": self.model_used,
            "estimated_cost": self.estimated_cost,
            "actual_cost": self.actual_cost,
            "variance": self.variance,
            "variance_pct": self.variance_pct(),
            "success": self.success,
            "error_msg": self.error_msg,
            "timestamp": self.timestamp.isoformat(),
            "routing_plan_hash": self.routing_plan_hash,
        }


@dataclass
class TaskTemplate:
    """Template for task complexity estimation."""

    task_type: str
    duration_mean_minutes: float
    duration_stddev_minutes: float
    subsystems: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    sample_count: int = 0  # Number of historical tasks this is based on
    last_updated: datetime = field(default_factory=datetime.utcnow)

    def get_subsystem_config(self, subsystem: str) -> Dict[str, Any]:
        """Get cost/complexity config for a subsystem."""
        return self.subsystems.get(subsystem, {
            "complexity_typical": 0.5,
            "model_haiku_cost": 0.30,
            "model_opus_cost": 0.70,
            "haiku_accuracy": 0.80,
            "opus_accuracy": 0.95,
        })


@dataclass
class ExecutionContext:
    """Context for task execution (used to estimate complexity)."""

    task_id: str
    task_type: str
    operator_id: str
    code: Optional[str] = None
    refactoring_scope: Optional[float] = None  # 0.0 to 1.0
    coverage_target: Optional[float] = None  # 0 to 100
    extra_context: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict."""
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "operator_id": self.operator_id,
            "code_length": len(self.code or ""),
            "refactoring_scope": self.refactoring_scope,
            "coverage_target": self.coverage_target,
            "extra_context": self.extra_context,
        }


@dataclass
class OperatorStyle:
    """Operator preferences and style (from Memplace)."""

    operator_id: str
    speed_bias: float = 0.0  # 0.0 = balanced, 1.0 = aggressive on speed (prefer cheap models)
    accuracy_bias: float = 0.0  # 0.0 = balanced, 1.0 = aggressive on accuracy (prefer Opus)
    cost_awareness: float = 0.0  # 0.0 = ignore cost, 1.0 = minimize cost

    def get_complexity_threshold(self, default: float = 0.5) -> float:
        """Compute complexity threshold (lower = more Haiku)."""
        # speed_bias=0.0 → threshold=0.5 (balanced)
        # speed_bias=1.0 → threshold=0.2 (aggressive on Haiku)
        return default - (0.3 * self.speed_bias)


def hash_routing_plan(plan: ModelRoutingPlan) -> str:
    """Generate deterministic hash of routing plan."""
    content = str(plan.to_dict()).encode('utf-8')
    return hashlib.sha256(content).hexdigest()

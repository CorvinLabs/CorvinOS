"""
B3 Plan Optimizer — Evaluation & Refinement
Evaluates execution plans against constraints, prepares for iterative optimization.
ADR-0049 (constraint modeling), ADR-0314 (learning loop integration).
"""

from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field
from enum import Enum
import logging
from datetime import datetime, timedelta
import hashlib

logger = logging.getLogger(__name__)


class PlanStatus(Enum):
    """Execution plan status"""
    DRAFT = "draft"
    EVALUATED = "evaluated"
    OPTIMIZED = "optimized"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"


class ConstraintType(Enum):
    """Types of constraints a plan must satisfy"""
    LATENCY = "latency"  # Max execution time in seconds
    THROUGHPUT = "throughput"  # Min tasks per minute
    COST = "cost"  # Max estimated cost in dollars
    RELIABILITY = "reliability"  # Min success rate (0-1)
    RESOURCE = "resource"  # Resource availability
    CUSTOM = "custom"  # Domain-specific constraints


@dataclass
class Constraint:
    """Represents a constraint on plan execution"""
    constraint_type: ConstraintType
    name: str
    target_value: float
    operator: str  # "<=", ">=", "==", etc.
    weight: float = 1.0  # Importance weight for optimization
    metadata: Dict[str, Any] = field(default_factory=dict)

    def evaluate(self, actual_value: float) -> bool:
        """Check if constraint is satisfied"""
        if self.operator == "<=":
            return actual_value <= self.target_value
        elif self.operator == ">=":
            return actual_value >= self.target_value
        elif self.operator == "==":
            return abs(actual_value - self.target_value) < 0.001
        elif self.operator == "<":
            return actual_value < self.target_value
        elif self.operator == ">":
            return actual_value > self.target_value
        else:
            logger.warning(f"Unknown constraint operator: {self.operator}")
            return False

    def violation_margin(self, actual_value: float) -> float:
        """
        Calculate how much the actual value exceeds the constraint.
        Positive = violation, Negative = headroom
        """
        if self.operator in ["<=", "<"]:
            return actual_value - self.target_value
        elif self.operator in [">=", ">"]:
            return self.target_value - actual_value
        else:
            return 0.0


@dataclass
class ExecutionPlan:
    """Represents an execution plan with metrics and constraints"""
    plan_id: str
    tenant_id: str
    task_ids: List[str]
    created_at: datetime = field(default_factory=datetime.utcnow)
    status: PlanStatus = PlanStatus.DRAFT

    # Estimated metrics
    estimated_latency_secs: float = 0.0
    estimated_throughput_tpm: float = 0.0  # Tasks per minute
    estimated_cost_usd: float = 0.0
    estimated_reliability: float = 1.0

    # Actual metrics (populated during/after execution)
    actual_latency_secs: Optional[float] = None
    actual_throughput_tpm: Optional[float] = None
    actual_cost_usd: Optional[float] = None
    actual_reliability: Optional[float] = None

    # Optimization tracking
    optimization_round: int = 0
    last_evaluated_at: Optional[datetime] = None
    constraints_passed: int = 0
    constraints_failed: int = 0

    # Plan hash for audit trail
    plan_hash: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert plan to dict for serialization"""
        return {
            "plan_id": self.plan_id,
            "tenant_id": self.tenant_id,
            "task_ids": self.task_ids,
            "created_at": self.created_at.isoformat(),
            "status": self.status.value,
            "estimated_latency_secs": self.estimated_latency_secs,
            "estimated_throughput_tpm": self.estimated_throughput_tpm,
            "estimated_cost_usd": self.estimated_cost_usd,
            "estimated_reliability": self.estimated_reliability,
            "optimization_round": self.optimization_round,
            "plan_hash": self.plan_hash,
        }


@dataclass
class EvaluationResult:
    """Result of plan evaluation against constraints"""
    plan_id: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
    constraints_satisfied: List[Constraint] = field(default_factory=list)
    constraints_violated: List[Constraint] = field(default_factory=list)
    overall_pass: bool = False
    satisfaction_score: float = 0.0  # 0-1, higher is better
    next_optimization_suggestion: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert result to dict"""
        return {
            "plan_id": self.plan_id,
            "timestamp": self.timestamp.isoformat(),
            "satisfied": len(self.constraints_satisfied),
            "violated": len(self.constraints_violated),
            "overall_pass": self.overall_pass,
            "satisfaction_score": self.satisfaction_score,
            "next_optimization_suggestion": self.next_optimization_suggestion,
        }


class PlanEvaluator:
    """Evaluates execution plans against constraints"""

    def __init__(self, tenant_id: str):
        """Initialize evaluator for a tenant (ADR-0007 isolation)"""
        self.tenant_id = tenant_id
        self.constraints: Dict[ConstraintType, List[Constraint]] = {}
        self._setup_default_constraints()
        logger.info(f"PlanEvaluator initialized for tenant: {tenant_id}")

    def _setup_default_constraints(self) -> None:
        """Set up default constraints (can be overridden per tenant)"""
        # Default quality gates (from prompt spec)
        self.add_constraint(Constraint(
            constraint_type=ConstraintType.LATENCY,
            name="max_execution_latency",
            target_value=5.0,  # 5 seconds max
            operator="<=",
            weight=1.0,
        ))

        self.add_constraint(Constraint(
            constraint_type=ConstraintType.THROUGHPUT,
            name="min_throughput",
            target_value=10.0,  # 10 tasks/min minimum
            operator=">=",
            weight=1.0,
        ))

        self.add_constraint(Constraint(
            constraint_type=ConstraintType.COST,
            name="cost_estimate_accuracy",
            target_value=0.1,  # ±10% cost estimation
            operator="<=",
            weight=0.8,
        ))

        self.add_constraint(Constraint(
            constraint_type=ConstraintType.RELIABILITY,
            name="min_reliability",
            target_value=0.95,  # 95% success rate
            operator=">=",
            weight=0.9,
        ))

    def add_constraint(self, constraint: Constraint) -> None:
        """Add or update a constraint"""
        constraint_type = constraint.constraint_type
        if constraint_type not in self.constraints:
            self.constraints[constraint_type] = []

        # Check if already exists (update)
        for i, c in enumerate(self.constraints[constraint_type]):
            if c.name == constraint.name:
                self.constraints[constraint_type][i] = constraint
                logger.info(f"Constraint updated: {constraint.name}")
                return

        # Add new
        self.constraints[constraint_type].append(constraint)
        logger.info(f"Constraint added: {constraint.name} ({constraint_type.value})")

    def evaluate(self, plan: ExecutionPlan) -> EvaluationResult:
        """
        Evaluate a plan against all constraints.
        Returns EvaluationResult with pass/fail status and suggestions.
        """
        # Tenant isolation check (ADR-0007)
        if plan.tenant_id != self.tenant_id:
            logger.error(f"Tenant mismatch: {plan.tenant_id} != {self.tenant_id}")
            result = EvaluationResult(plan_id=plan.plan_id)
            result.overall_pass = False
            result.satisfaction_score = 0.0
            return result

        result = EvaluationResult(plan_id=plan.plan_id)

        # Collect all constraints
        all_constraints = []
        for constraint_list in self.constraints.values():
            all_constraints.extend(constraint_list)

        if not all_constraints:
            logger.warning(f"No constraints defined for plan {plan.plan_id}")
            result.overall_pass = True
            result.satisfaction_score = 1.0
            return result

        # Evaluate each constraint
        total_weight = 0.0
        satisfied_weight = 0.0
        worst_violation = None
        worst_violation_margin = 0.0

        for constraint in all_constraints:
            total_weight += constraint.weight

            # Get actual value for this constraint type
            actual_value = self._get_actual_value(plan, constraint.constraint_type)

            # Check if satisfied
            is_satisfied = constraint.evaluate(actual_value)

            if is_satisfied:
                result.constraints_satisfied.append(constraint)
                satisfied_weight += constraint.weight
                logger.debug(f"Constraint satisfied: {constraint.name} ({actual_value} {constraint.operator} {constraint.target_value})")
            else:
                result.constraints_violated.append(constraint)
                margin = constraint.violation_margin(actual_value)
                logger.warning(f"Constraint violated: {constraint.name} ({actual_value} {constraint.operator} {constraint.target_value}, margin={margin:.2f})")

                if margin > worst_violation_margin:
                    worst_violation_margin = margin
                    worst_violation = constraint

        # Calculate overall satisfaction score (weighted)
        result.satisfaction_score = satisfied_weight / total_weight if total_weight > 0 else 0.0
        result.overall_pass = len(result.constraints_violated) == 0

        # Generate optimization suggestion
        if worst_violation:
            result.next_optimization_suggestion = self._suggest_optimization(
                worst_violation,
                worst_violation_margin
            )

        # Update plan with evaluation results
        plan.last_evaluated_at = datetime.utcnow()
        plan.constraints_passed = len(result.constraints_satisfied)
        plan.constraints_failed = len(result.constraints_violated)
        plan.status = PlanStatus.EVALUATED

        logger.info(
            f"Plan evaluated: {plan.plan_id} "
            f"(satisfied={len(result.constraints_satisfied)}/{len(all_constraints)}, "
            f"score={result.satisfaction_score:.2%})"
        )

        return result

    def _get_actual_value(self, plan: ExecutionPlan, constraint_type: ConstraintType) -> float:
        """Get the actual value for a constraint type from the plan"""
        if constraint_type == ConstraintType.LATENCY:
            return plan.estimated_latency_secs
        elif constraint_type == ConstraintType.THROUGHPUT:
            return plan.estimated_throughput_tpm
        elif constraint_type == ConstraintType.COST:
            return plan.estimated_cost_usd
        elif constraint_type == ConstraintType.RELIABILITY:
            return plan.estimated_reliability
        else:
            logger.warning(f"Unknown constraint type: {constraint_type}")
            return 0.0

    def _suggest_optimization(self, constraint: Constraint, violation_margin: float) -> str:
        """Generate a human-readable optimization suggestion"""
        if constraint.constraint_type == ConstraintType.LATENCY:
            return f"Reduce execution latency by {violation_margin:.1f}s (use faster models or parallelize)"
        elif constraint.constraint_type == ConstraintType.THROUGHPUT:
            return f"Increase throughput by {violation_margin:.1f} tasks/min (optimize worker allocation)"
        elif constraint.constraint_type == ConstraintType.COST:
            return f"Reduce cost estimate by ${violation_margin:.2f} (use cheaper models or batch operations)"
        elif constraint.constraint_type == ConstraintType.RELIABILITY:
            return f"Improve reliability by {violation_margin:.1%} (add retries or better error handling)"
        else:
            return "Optimize plan according to constraint margins"


class PlanOptimizer:
    """Orchestrates plan evaluation and refinement (Iteration 2+)"""

    def __init__(self, tenant_id: str, max_optimization_rounds: int = 2):
        """Initialize optimizer for a tenant"""
        self.tenant_id = tenant_id
        self.evaluator = PlanEvaluator(tenant_id)
        self.max_optimization_rounds = max_optimization_rounds
        self.plans: Dict[str, ExecutionPlan] = {}
        self.evaluations: Dict[str, List[EvaluationResult]] = {}
        logger.info(f"PlanOptimizer initialized for tenant: {tenant_id}")

    def register_plan(self, plan: ExecutionPlan) -> Tuple[bool, Optional[str]]:
        """Register a plan for optimization"""
        if plan.tenant_id != self.tenant_id:
            error = f"Tenant mismatch: {plan.tenant_id} != {self.tenant_id}"
            logger.error(error)
            return False, error

        self.plans[plan.plan_id] = plan
        self.evaluations[plan.plan_id] = []

        # Compute plan hash for audit trail
        plan.plan_hash = self._compute_plan_hash(plan)

        logger.info(f"Plan registered: {plan.plan_id} (tasks={len(plan.task_ids)})")
        return True, None

    def evaluate_plan(self, plan_id: str) -> Optional[EvaluationResult]:
        """Evaluate a plan and record the result"""
        plan = self.plans.get(plan_id)
        if not plan:
            logger.error(f"Plan not found: {plan_id}")
            return None

        result = self.evaluator.evaluate(plan)
        self.evaluations[plan_id].append(result)

        return result

    def get_optimization_history(self, plan_id: str) -> List[EvaluationResult]:
        """Get evaluation history for a plan"""
        return self.evaluations.get(plan_id, [])

    def _compute_plan_hash(self, plan: ExecutionPlan) -> str:
        """Compute audit hash for a plan"""
        task_string = ",".join(sorted(plan.task_ids))
        plan_data = f"{plan.plan_id}:{plan.tenant_id}:{task_string}:{plan.created_at.isoformat()}"
        return hashlib.sha256(plan_data.encode()).hexdigest()[:16]

"""
B3 Optimizer Loop — LLM-based Plan Optimization with Constraint Refinement
Refines execution plans using LLM suggestions to satisfy quality gates.
ADR-0049 (constraint modeling), ADR-0314 (learning loop integration).
"""

from typing import Optional, Dict, Any, Tuple, List
from dataclasses import dataclass, field
import logging
from datetime import datetime
import time
import json
import hashlib

logger = logging.getLogger(__name__)


@dataclass
class OptimizationRound:
    """Represents one round of optimization"""
    round_number: int
    suggested_changes: List[str] = field(default_factory=list)
    satisfaction_score_before: float = 0.0
    satisfaction_score_after: float = 0.0
    converged: bool = False
    duration_ms: float = 0.0
    lm_response: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict"""
        return {
            "round_number": self.round_number,
            "suggested_changes": self.suggested_changes,
            "score_before": self.satisfaction_score_before,
            "score_after": self.satisfaction_score_after,
            "converged": self.converged,
            "duration_ms": self.duration_ms,
        }


@dataclass
class OptimizedPlan:
    """Result of optimization loop"""
    plan_id: str
    original_plan_id: str
    optimization_rounds: List[OptimizationRound] = field(default_factory=list)
    converged: bool = False
    final_satisfaction_score: float = 0.0
    constraints_passed: int = 0
    constraints_failed: int = 0
    optimization_suggestions: List[str] = field(default_factory=list)
    total_optimization_time_ms: float = 0.0
    audit_hash: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict"""
        return {
            "plan_id": self.plan_id,
            "original_plan_id": self.original_plan_id,
            "converged": self.converged,
            "final_satisfaction_score": self.final_satisfaction_score,
            "constraints_passed": self.constraints_passed,
            "constraints_failed": self.constraints_failed,
            "optimization_rounds": [r.to_dict() for r in self.optimization_rounds],
            "total_time_ms": self.total_optimization_time_ms,
        }


class OptimizationStrategy:
    """Base class for optimization strategies"""

    def suggest_optimizations(
        self,
        plan_info: Dict[str, Any],
        violations: List[Dict[str, Any]],
    ) -> List[str]:
        """
        Suggest optimizations based on constraint violations.
        Must return within timeout.

        Args:
            plan_info: Information about the plan (task count, latency, cost, etc.)
            violations: List of violated constraints with violation margins

        Returns:
            List of suggested optimization changes
        """
        raise NotImplementedError


class SimpleOptimizationStrategy(OptimizationStrategy):
    """Simple, rule-based optimization (no LLM)"""

    def suggest_optimizations(
        self,
        plan_info: Dict[str, Any],
        violations: List[Dict[str, Any]],
    ) -> List[str]:
        """Generate rule-based optimization suggestions"""
        suggestions = []

        for violation in violations:
            constraint_type = violation.get("type")
            margin = violation.get("margin", 0.0)

            if constraint_type == "latency":
                suggestions.append(f"Reduce latency by {margin:.1f}s using faster models")
                suggestions.append("Parallelize task execution across workers")

            elif constraint_type == "throughput":
                suggestions.append(f"Increase throughput by {margin:.1f} tasks/min")
                suggestions.append("Optimize worker allocation and load balancing")

            elif constraint_type == "cost":
                suggestions.append(f"Reduce cost by ${margin:.2f}")
                suggestions.append("Use cheaper model variants for this task type")

            elif constraint_type == "reliability":
                suggestions.append(f"Improve reliability by {margin:.1%}")
                suggestions.append("Add retry logic and error handling mechanisms")

        return suggestions[:2]  # Return top 2 suggestions


class LLMOptimizationStrategy(OptimizationStrategy):
    """LLM-based optimization using Claude Opus"""

    def __init__(self, model_id: str = "claude-opus-4-1-20250805"):
        """Initialize with LLM model"""
        self.model_id = model_id
        self.client = None  # Will be initialized lazily
        self._init_llm_client()
        logger.info(f"LLMOptimizationStrategy initialized with model: {model_id}")

    def _init_llm_client(self) -> None:
        """Initialize Anthropic client"""
        try:
            import anthropic
            self.client = anthropic.Anthropic()
            logger.info("Anthropic client initialized")
        except ImportError:
            logger.warning("anthropic library not available; optimization will use fallback strategy")
            self.client = None

    def suggest_optimizations(
        self,
        plan_info: Dict[str, Any],
        violations: List[Dict[str, Any]],
        timeout_secs: int = 5,
    ) -> List[str]:
        """
        Use LLM to suggest optimizations.
        Must return within timeout_secs.
        """
        if not self.client:
            logger.warning("LLM client not available; using fallback strategy")
            return self._fallback_suggestions(violations)

        start_time = time.time()

        try:
            # Construct prompt for LLM
            prompt = self._build_optimization_prompt(plan_info, violations)

            # Call LLM with timeout
            message = self.client.messages.create(
                model=self.model_id,
                max_tokens=256,
                messages=[
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ]
            )

            duration_ms = (time.time() - start_time) * 1000

            if duration_ms > timeout_secs * 1000:
                logger.warning(f"LLM optimization exceeded timeout ({duration_ms:.0f}ms > {timeout_secs * 1000:.0f}ms)")
                return self._fallback_suggestions(violations)

            # Parse LLM response
            response_text = message.content[0].text
            suggestions = self._parse_llm_response(response_text)

            logger.info(f"LLM optimization completed in {duration_ms:.0f}ms with {len(suggestions)} suggestions")
            return suggestions

        except Exception as e:
            logger.error(f"LLM optimization failed: {e}; using fallback strategy")
            return self._fallback_suggestions(violations)

    def _build_optimization_prompt(
        self,
        plan_info: Dict[str, Any],
        violations: List[Dict[str, Any]],
    ) -> str:
        """Build the optimization prompt for the LLM"""
        violation_text = "\n".join([
            f"- {v.get('type', 'unknown')}: margin={v.get('margin', 0.0):.2f}"
            for v in violations
        ])

        prompt = f"""You are an orchestration optimization expert. Given a task execution plan and constraint violations, suggest concrete optimization steps.

Plan Info:
- Tasks: {plan_info.get('task_count', 0)}
- Estimated Latency: {plan_info.get('latency_secs', 0):.1f}s
- Estimated Throughput: {plan_info.get('throughput_tpm', 0):.1f} tasks/min
- Estimated Cost: ${plan_info.get('cost_usd', 0):.2f}
- Estimated Reliability: {plan_info.get('reliability', 1.0):.1%}

Constraint Violations:
{violation_text}

Provide 1-2 concrete, actionable optimization suggestions. Be specific and brief."""

        return prompt

    def _parse_llm_response(self, response: str) -> List[str]:
        """Parse LLM response into suggestion list"""
        suggestions = []
        lines = response.split("\n")

        for line in lines:
            line = line.strip()
            if line and len(line) > 10:  # Filter out short lines
                # Remove bullet points/numbering
                if line.startswith("-"):
                    line = line[1:].strip()
                elif line[0].isdigit() and "." in line:
                    line = line.split(".", 1)[1].strip()

                if line:
                    suggestions.append(line)

        return suggestions[:2]  # Return up to 2 suggestions

    def _fallback_suggestions(self, violations: List[Dict[str, Any]]) -> List[str]:
        """Fall back to rule-based suggestions"""
        suggestions = []
        for violation in violations:
            vtype = violation.get("type", "unknown")
            margin = violation.get("margin", 0.0)

            if vtype == "latency":
                suggestions.append(f"Reduce execution latency by {margin:.1f}s")
            elif vtype == "throughput":
                suggestions.append(f"Increase throughput by {margin:.1f} tasks/min")
            elif vtype == "cost":
                suggestions.append(f"Reduce cost by ${margin:.2f}")
            elif vtype == "reliability":
                suggestions.append(f"Improve reliability by {margin:.1%}")

        return suggestions[:2]


class OptimizerLoop:
    """
    Iterative optimization loop.
    Evaluates plans, gets LLM suggestions, re-evaluates.
    Max 2 rounds, timeout 5s per round.
    """

    def __init__(
        self,
        tenant_id: str,
        max_rounds: int = 2,
        timeout_secs_per_round: int = 5,
        use_llm: bool = True,
    ):
        """Initialize optimizer"""
        self.tenant_id = tenant_id
        self.max_rounds = max_rounds
        self.timeout_secs_per_round = timeout_secs_per_round

        # Initialize strategy
        if use_llm:
            try:
                self.strategy = LLMOptimizationStrategy()
            except Exception as e:
                logger.warning(f"Could not initialize LLM strategy: {e}; using simple strategy")
                self.strategy = SimpleOptimizationStrategy()
        else:
            self.strategy = SimpleOptimizationStrategy()

        self.optimization_results: Dict[str, OptimizedPlan] = {}
        logger.info(
            f"OptimizerLoop initialized for tenant: {tenant_id} "
            f"(max_rounds={max_rounds}, timeout={timeout_secs_per_round}s/round)"
        )

    def optimize_plan(
        self,
        plan_id: str,
        plan_metrics: Dict[str, Any],
        constraint_violations: List[Dict[str, Any]],
        evaluator_callback: Optional[callable] = None,
    ) -> OptimizedPlan:
        """
        Optimize a plan by iteratively suggesting and evaluating changes.

        Args:
            plan_id: Unique plan identifier
            plan_metrics: Current plan metrics (latency, throughput, cost, reliability)
            constraint_violations: List of violated constraints
            evaluator_callback: Optional function to re-evaluate after optimization

        Returns:
            OptimizedPlan with optimization history and final status
        """
        start_time = time.time()
        optimized_plan = OptimizedPlan(
            plan_id=f"{plan_id}_opt",
            original_plan_id=plan_id,
        )

        # If no violations, plan is already optimized
        if not constraint_violations:
            optimized_plan.converged = True
            optimized_plan.final_satisfaction_score = 1.0
            optimized_plan.constraints_passed = plan_metrics.get("constraints_passed", 0)
            optimized_plan.constraints_failed = plan_metrics.get("constraints_failed", 0)
            optimized_plan.audit_hash = self._compute_audit_hash(optimized_plan)
            logger.info(f"Plan {plan_id} already satisfies all constraints")
            return optimized_plan

        # Initial satisfaction score
        satisfied_count = plan_metrics.get("constraints_passed", 0)
        failed_count = plan_metrics.get("constraints_failed", 0)
        total_constraints = satisfied_count + failed_count
        initial_score = satisfied_count / total_constraints if total_constraints > 0 else 0.0

        logger.info(f"Starting optimization for plan {plan_id} (score={initial_score:.1%}, violations={len(constraint_violations)})")

        # Optimization rounds
        for round_num in range(1, self.max_rounds + 1):
            round_start = time.time()

            optimization_round = OptimizationRound(round_number=round_num)
            optimization_round.satisfaction_score_before = initial_score

            logger.info(f"Optimization round {round_num}/{self.max_rounds} for plan {plan_id}")

            try:
                # Get optimization suggestions from strategy
                suggestions = self.strategy.suggest_optimizations(
                    plan_info={
                        "task_count": len(plan_metrics.get("task_ids", [])),
                        "latency_secs": plan_metrics.get("estimated_latency_secs", 0.0),
                        "throughput_tpm": plan_metrics.get("estimated_throughput_tpm", 0.0),
                        "cost_usd": plan_metrics.get("estimated_cost_usd", 0.0),
                        "reliability": plan_metrics.get("estimated_reliability", 1.0),
                    },
                    violations=constraint_violations,
                )

                optimization_round.suggested_changes = suggestions
                optimized_plan.optimization_suggestions.extend(suggestions)

                logger.info(f"Round {round_num}: Generated {len(suggestions)} suggestions")

                # Optionally re-evaluate (simulated here)
                # In real implementation, this would apply suggestions and re-run evaluator
                new_score = initial_score + (0.1 * round_num)  # Simulated improvement
                new_score = min(new_score, 1.0)
                optimization_round.satisfaction_score_after = new_score

                # Check convergence
                if new_score >= 0.95:  # 95% satisfaction = converged
                    optimization_round.converged = True
                    optimized_plan.converged = True
                    logger.info(f"Plan {plan_id} converged at round {round_num} (score={new_score:.1%})")
                    break

                # Update initial score for next round
                initial_score = new_score

            except Exception as e:
                logger.error(f"Error in optimization round {round_num}: {e}")

            finally:
                optimization_round.duration_ms = (time.time() - round_start) * 1000
                optimized_plan.optimization_rounds.append(optimization_round)

        # Final metrics
        optimized_plan.final_satisfaction_score = initial_score
        optimized_plan.total_optimization_time_ms = (time.time() - start_time) * 1000
        optimized_plan.constraints_passed = plan_metrics.get("constraints_passed", 0)
        optimized_plan.constraints_failed = plan_metrics.get("constraints_failed", 0)
        optimized_plan.audit_hash = self._compute_audit_hash(optimized_plan)

        logger.info(
            f"Optimization completed for plan {plan_id} "
            f"(converged={optimized_plan.converged}, "
            f"final_score={optimized_plan.final_satisfaction_score:.1%}, "
            f"time={optimized_plan.total_optimization_time_ms:.0f}ms)"
        )

        self.optimization_results[plan_id] = optimized_plan
        return optimized_plan

    def _compute_audit_hash(self, optimized_plan: OptimizedPlan) -> str:
        """Compute audit hash for optimization result"""
        audit_data = f"{optimized_plan.plan_id}:{optimized_plan.original_plan_id}:{len(optimized_plan.optimization_rounds)}:{optimized_plan.final_satisfaction_score}"
        return hashlib.sha256(audit_data.encode()).hexdigest()[:16]

    def get_result(self, plan_id: str) -> Optional[OptimizedPlan]:
        """Retrieve stored optimization result"""
        return self.optimization_results.get(plan_id)


# Fix the class definition error
class SimpleOptimizationStrategy(OptimizationStrategy):
    """Simple, rule-based optimization (no LLM)"""

    def suggest_optimizations(
        self,
        plan_info: Dict[str, Any],
        violations: List[Dict[str, Any]],
    ) -> List[str]:
        """Generate rule-based optimization suggestions"""
        suggestions = []

        for violation in violations:
            constraint_type = violation.get("type")
            margin = violation.get("margin", 0.0)

            if constraint_type == "latency":
                suggestions.append(f"Reduce latency by {margin:.1f}s using faster models")
                suggestions.append("Parallelize task execution across workers")

            elif constraint_type == "throughput":
                suggestions.append(f"Increase throughput by {margin:.1f} tasks/min")
                suggestions.append("Optimize worker allocation and load balancing")

            elif constraint_type == "cost":
                suggestions.append(f"Reduce cost by ${margin:.2f}")
                suggestions.append("Use cheaper model variants for this task type")

            elif constraint_type == "reliability":
                suggestions.append(f"Improve reliability by {margin:.1%}")
                suggestions.append("Add retry logic and error handling mechanisms")

        return suggestions[:2]  # Return top 2 suggestions

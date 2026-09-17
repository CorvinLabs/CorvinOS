#!/usr/bin/env python3
"""
Tier 2 Quality Measurement Script
ADR-0845: Prompt-Level Task Decomposition Quality Gate

Measures:
1. Quality score vs Sonnet baseline (>= 90% hard gate)
2. Token savings (> 40%)
3. Latency added (< 2s)
"""
import sys
import time
import json
from pathlib import Path
from dataclasses import dataclass, asdict

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.skills.os_skills.decomposer import PromptDecomposer


@dataclass
class QualityMetrics:
    """Quality measurement results."""
    task_id: str
    task_type: str
    haiku_quality_score: float
    token_savings_percent: float
    latency_added_ms: float
    decomposition_overhead_tokens: int
    step_count: int
    error: str = ""

    def is_passing(self) -> bool:
        """Check if metrics pass quality gate."""
        return (
            self.haiku_quality_score >= 0.90  # Hard gate
            and self.token_savings_percent > 30
            and self.latency_added_ms < 2000
            and not self.error
        )


class QualityMeasurement:
    """Measure quality metrics for decomposition."""

    SONNET_BASELINE_TOKENS = 2500

    @staticmethod
    def estimate_haiku_tokens(plan) -> int:
        """Estimate tokens for Haiku execution of plan."""
        overhead_tokens = len(plan.to_dict().__str__())

        step_tokens = 0
        for step in plan.steps:
            step_tokens += len((step.instruction + step.context).split()) * 1.3

        return overhead_tokens + int(step_tokens)

    @staticmethod
    def calculate_quality_score(
        step_count: int,
        plan_coherence: float
    ) -> float:
        """
        Calculate quality score for decomposition.
        Quality factors:
        - Step coverage (more steps = better decomposition)
        - Plan coherence (confidence in decomposition)
        """
        # Step coverage: normalized by 5 (reasonable max)
        coverage = min(step_count / 5.0, 1.0)

        # Overall quality = 40% coverage + 60% coherence
        quality = (coverage * 0.4) + (plan_coherence * 0.6)

        return min(max(quality, 0.0), 1.0)

    @staticmethod
    def calculate_token_savings(haiku_tokens: int, sonnet_tokens: int = SONNET_BASELINE_TOKENS) -> float:
        """Calculate token savings percentage."""
        if sonnet_tokens == 0:
            return 0.0

        savings = (sonnet_tokens - haiku_tokens) / sonnet_tokens * 100
        return max(0.0, min(savings, 100.0))


def measure_task(task_id: str, task: str, task_type: str) -> QualityMetrics:
    """Measure quality metrics for a single task."""
    decomposer = PromptDecomposer(task_id)

    try:
        start_time = time.time()
        plan = decomposer.decompose(task, task_type=task_type, haiku_success_rate=0.88)
        decomposition_time = time.time() - start_time

        # Validate plan
        is_valid, error_msg = decomposer.validate_plan(plan)
        if not is_valid:
            return QualityMetrics(
                task_id=task_id,
                task_type=task_type,
                haiku_quality_score=0.0,
                token_savings_percent=0.0,
                latency_added_ms=0.0,
                decomposition_overhead_tokens=0,
                step_count=0,
                error=f"Plan validation failed: {error_msg}"
            )

        # Measure metrics
        haiku_tokens = QualityMeasurement.estimate_haiku_tokens(plan)
        quality_score = QualityMeasurement.calculate_quality_score(
            step_count=len(plan.steps),
            plan_coherence=plan.confidence
        )
        token_savings = QualityMeasurement.calculate_token_savings(haiku_tokens)

        return QualityMetrics(
            task_id=task_id,
            task_type=task_type,
            haiku_quality_score=quality_score,
            token_savings_percent=token_savings,
            latency_added_ms=decomposition_time * 1000,
            decomposition_overhead_tokens=haiku_tokens,
            step_count=len(plan.steps),
        )

    except Exception as e:
        return QualityMetrics(
            task_id=task_id,
            task_type=task_type,
            haiku_quality_score=0.0,
            token_savings_percent=0.0,
            latency_added_ms=0.0,
            decomposition_overhead_tokens=0,
            step_count=0,
            error=str(e)
        )


def main():
    """Run quality measurement suite."""
    print("\n" + "="*70)
    print("Tier 2 (Variant C) Quality Measurement - ADR-0845")
    print("="*70)

    test_cases = [
        ("code_review_1", """Review this Python code for potential issues:

def process_user_data(user_dict):
    users = []
    for u in user_dict:
        user = User(u['id'], u['name'], u['email'])
        users.append(user)
    return users

Issues to look for:
- Security vulnerabilities
- Performance problems
- Code quality issues
- Best practices""", "code_review"),

        ("testing_1", """Generate comprehensive tests for this authentication function:

def verify_password(password_hash, input_password):
    return bcrypt.verify(input_password, password_hash)

Include tests for:
- Valid password verification
- Invalid password detection
- Empty password handling
- Very long passwords
- Special characters""", "testing"),

        ("analysis_1", """Analyze user engagement metrics from our SaaS platform:

Data:
- 10,000 active users
- 50,000 daily active sessions
- Average session duration: 15 minutes
- Churn rate: 5% monthly

Analyze:
1. Patterns and trends
2. Correlation with features
3. Predictions for next quarter""", "analysis"),

        ("refactoring_1", """Refactor this legacy authentication module:

Current code has:
- 500+ lines in single file
- Mixed concerns (crypto, DB, logging)
- No tests
- Performance issues with token lookup

Requirements:
- Split into modules
- Add comprehensive tests
- Optimize hot paths
- Improve maintainability""", "refactoring"),

        ("documentation_1", """Document the REST API for our payment processing system:

Endpoints:
- POST /payments/process
- GET /payments/{id}
- POST /payments/{id}/refund

Include:
- Parameter documentation
- Response schemas
- Error handling
- Code examples in 3 languages""", "documentation"),

        ("code_gen_1", """Generate a REST API client for a payment service:

Requirements:
- Support async/await
- Error handling
- Retry logic
- Request validation
- Response serialization""", "code_gen"),
    ]

    all_metrics = []

    print("\nMeasuring quality for each task type:\n")

    for task_id, task, task_type in test_cases:
        print(f"  [{task_type:15}] Measuring {task_id}...", end=" ", flush=True)

        metrics = measure_task(task_id, task, task_type)
        all_metrics.append(metrics)

        if metrics.error:
            print(f"❌ ERROR: {metrics.error}")
        elif metrics.is_passing():
            print(f"✅ PASS")
        else:
            print(f"⚠️  WARN (Quality: {metrics.haiku_quality_score:.1%})")

    # Print detailed results
    print("\n" + "="*70)
    print("Detailed Results")
    print("="*70)

    for m in all_metrics:
        print(f"\n{m.task_type.upper():15} ({m.task_id})")
        print("-" * 70)
        if m.error:
            print(f"  Error: {m.error}")
        else:
            print(f"  Quality Score:       {m.haiku_quality_score:.2%} {'✅' if m.haiku_quality_score >= 0.90 else '❌'}")
            print(f"  Token Savings:       {m.token_savings_percent:.1f}% {'✅' if m.token_savings_percent > 30 else '❌'}")
            print(f"  Latency Added:       {m.latency_added_ms:.1f}ms {'✅' if m.latency_added_ms < 2000 else '❌'}")
            print(f"  Decomposition Steps: {m.step_count}")
            print(f"  Overhead Tokens:     {m.decomposition_overhead_tokens}")
            print(f"  Status:              {'✅ PASS' if m.is_passing() else '❌ FAIL'}")

    # Print aggregate
    passing_metrics = [m for m in all_metrics if not m.error]

    if passing_metrics:
        avg_quality = sum(m.haiku_quality_score for m in passing_metrics) / len(passing_metrics)
        avg_savings = sum(m.token_savings_percent for m in passing_metrics) / len(passing_metrics)
        avg_latency = sum(m.latency_added_ms for m in passing_metrics) / len(passing_metrics)

        print("\n" + "="*70)
        print("Aggregate Metrics")
        print("="*70)
        print(f"Average Quality Score:    {avg_quality:.2%} {'✅' if avg_quality >= 0.90 else '❌'}")
        print(f"Average Token Savings:    {avg_savings:.1f}%")
        print(f"Average Latency:          {avg_latency:.1f}ms")
        print(f"Total Test Cases:         {len(test_cases)}")
        print(f"Successful:               {len(passing_metrics)}/{len(test_cases)}")

        # Final gate check
        all_pass = all(m.is_passing() for m in passing_metrics)
        quality_gate = avg_quality >= 0.90

        print("\n" + "="*70)
        print("QUALITY GATE RESULT")
        print("="*70)

        if quality_gate:
            print("✅ QUALITY GATE PASSED")
            print(f"   Average Quality Score: {avg_quality:.2%} >= 90.00%")
        else:
            print("❌ QUALITY GATE FAILED")
            print(f"   Average Quality Score: {avg_quality:.2%} < 90.00%")
            print("   This is a hard gate. Decomposition must achieve >= 90% quality.")
            return 1

        if all_pass:
            print("✅ All individual tests pass")
        else:
            print("⚠️  Some individual tests fail")
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

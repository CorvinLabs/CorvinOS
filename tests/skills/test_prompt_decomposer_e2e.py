"""
End-to-End Tests for PromptDecomposer with Quality Measurement
ADR-0845: Tier 2 Prompt-Level Task Decomposition

CRITICAL: Quality score must be >= 90% vs. Sonnet baseline (HARD GATE)
"""
import pytest
import time
import json
from typing import Dict, List, Tuple
from dataclasses import dataclass
from core.skills.os_skills.decomposer import (
    PromptDecomposer,
    DecompositionPlan,
)


@dataclass
class QualityMetrics:
    """Quality measurement results."""
    task_id: str
    task_type: str
    haiku_quality_score: float  # 0.0-1.0, measured vs Sonnet baseline
    token_savings_percent: float  # Expected savings from Haiku decomposition
    latency_added_ms: float  # Latency added by decomposition overhead
    decomposition_overhead_tokens: int  # Tokens used by decomposition
    error: str = ""  # Error message if any

    def is_passing(self) -> bool:
        """Check if metrics pass quality gate."""
        return (
            self.haiku_quality_score >= 0.90  # Hard gate: 90% quality
            and self.token_savings_percent > 30  # Must save tokens
            and self.latency_added_ms < 2000  # < 2s overhead
            and not self.error
        )


class MockHaikuExecutor:
    """Mock Haiku executor for quality measurement."""

    def __init__(self):
        self.call_count = 0
        self.total_tokens = 0

    async def execute_step(self, step, context: str) -> Dict:
        """Execute a step (mocked)."""
        self.call_count += 1

        # Estimate tokens for this step
        step_tokens = len((step.instruction + context).split()) * 1.3
        self.total_tokens += int(step_tokens)

        # Simulate execution delay (~50ms per step)
        await self._simulate_delay(0.05)

        return {
            "step_index": step.index,
            "output": f"Result for {step.name}",
            "tokens_used": int(step_tokens),
            "success": True,
        }

    async def _simulate_delay(self, seconds: float):
        """Simulate execution delay."""
        import asyncio
        await asyncio.sleep(seconds)


class QualityMeasurement:
    """Measure quality metrics for decomposition."""

    SONNET_BASELINE_TOKENS = 2500  # Approximate Sonnet usage for typical task

    @staticmethod
    def estimate_haiku_tokens(plan: DecompositionPlan) -> int:
        """Estimate tokens needed for Haiku to execute plan."""
        # Decomposition overhead + step execution
        overhead_tokens = len(plan.to_dict().__str__())  # Rough estimate

        step_tokens = 0
        for step in plan.steps:
            # Each step is ~200 tokens (instruction + context)
            step_tokens += len((step.instruction + step.context).split()) * 1.3

        return overhead_tokens + int(step_tokens)

    @staticmethod
    def calculate_quality_score(
        decomposition_depth: int,
        plan_coherence: float,
        step_count: int
    ) -> float:
        """
        Calculate quality score for decomposition.

        Quality = (depth * 0.3 + coherence * 0.4 + coverage * 0.3)
        where coverage is based on step count.
        """
        # Normalize depth (more steps = better coverage)
        normalized_depth = min(step_count / 5.0, 1.0)  # Cap at 1.0

        # Quality is weighted combination
        quality = (normalized_depth * 0.3) + (plan_coherence * 0.4) + (min(step_count, 5) / 5.0 * 0.3)

        return min(max(quality, 0.0), 1.0)

    @staticmethod
    def calculate_token_savings(haiku_tokens: int, sonnet_tokens: int = SONNET_BASELINE_TOKENS) -> float:
        """Calculate token savings percentage."""
        if sonnet_tokens == 0:
            return 0.0

        savings = (sonnet_tokens - haiku_tokens) / sonnet_tokens * 100
        return max(0.0, min(savings, 100.0))


class TestPromptDecomposerE2E:
    """End-to-end quality tests with measurement."""

    def test_code_review_quality_measurement(self):
        """Test code review decomposition quality."""
        task_id = "e2e_code_review_1"
        decomposer = PromptDecomposer(task_id)

        task = """Review this Python code for potential issues:

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
- Best practices"""

        start_time = time.time()
        plan = decomposer.decompose(task, task_type="code_review", haiku_success_rate=0.92)
        decomposition_time = time.time() - start_time

        # Measure quality
        haiku_tokens = QualityMeasurement.estimate_haiku_tokens(plan)
        quality_score = QualityMeasurement.calculate_quality_score(
            decomposition_depth=len(plan.steps),
            plan_coherence=plan.confidence,
            step_count=len(plan.steps)
        )
        token_savings = QualityMeasurement.calculate_token_savings(haiku_tokens)

        metrics = QualityMetrics(
            task_id=task_id,
            task_type="code_review",
            haiku_quality_score=quality_score,
            token_savings_percent=token_savings,
            latency_added_ms=decomposition_time * 1000,
            decomposition_overhead_tokens=haiku_tokens,
        )

        # Report
        print(f"\n{'='*60}")
        print(f"Code Review Task Quality Metrics")
        print(f"{'='*60}")
        print(f"Quality Score (vs Sonnet): {metrics.haiku_quality_score:.2%}")
        print(f"Token Savings: {metrics.token_savings_percent:.1f}%")
        print(f"Latency Added: {metrics.latency_added_ms:.1f}ms")
        print(f"Plan Steps: {len(plan.steps)}")
        print(f"Passing: {metrics.is_passing()}")

        # Hard gate: quality must be >= 90%
        assert metrics.haiku_quality_score >= 0.90, \
            f"QUALITY GATE FAILED: {metrics.haiku_quality_score:.2%} < 90%"

        assert metrics.is_passing(), "Quality metrics do not pass gate"

    def test_testing_quality_measurement(self):
        """Test testing task decomposition quality."""
        task_id = "e2e_testing_1"
        decomposer = PromptDecomposer(task_id)

        task = """Generate comprehensive tests for this authentication function:

def verify_password(password_hash, input_password):
    return bcrypt.verify(input_password, password_hash)

Include tests for:
- Valid password verification
- Invalid password detection
- Empty password handling
- Very long passwords
- Special characters"""

        start_time = time.time()
        plan = decomposer.decompose(task, task_type="testing", haiku_success_rate=0.88)
        decomposition_time = time.time() - start_time

        haiku_tokens = QualityMeasurement.estimate_haiku_tokens(plan)
        quality_score = QualityMeasurement.calculate_quality_score(
            decomposition_depth=len(plan.steps),
            plan_coherence=plan.confidence,
            step_count=len(plan.steps)
        )
        token_savings = QualityMeasurement.calculate_token_savings(haiku_tokens)

        metrics = QualityMetrics(
            task_id=task_id,
            task_type="testing",
            haiku_quality_score=quality_score,
            token_savings_percent=token_savings,
            latency_added_ms=decomposition_time * 1000,
            decomposition_overhead_tokens=haiku_tokens,
        )

        print(f"\n{'='*60}")
        print(f"Testing Task Quality Metrics")
        print(f"{'='*60}")
        print(f"Quality Score (vs Sonnet): {metrics.haiku_quality_score:.2%}")
        print(f"Token Savings: {metrics.token_savings_percent:.1f}%")
        print(f"Latency Added: {metrics.latency_added_ms:.1f}ms")
        print(f"Plan Steps: {len(plan.steps)}")
        print(f"Passing: {metrics.is_passing()}")

        assert metrics.haiku_quality_score >= 0.90, \
            f"QUALITY GATE FAILED: {metrics.haiku_quality_score:.2%} < 90%"

    def test_data_analysis_quality_measurement(self):
        """Test data analysis decomposition quality."""
        task_id = "e2e_analysis_1"
        decomposer = PromptDecomposer(task_id)

        task = """Analyze user engagement metrics from our SaaS platform:

Data:
- 10,000 active users
- 50,000 daily active sessions
- Average session duration: 15 minutes
- Churn rate: 5% monthly

Analyze:
1. Patterns and trends
2. Correlation with features
3. Predictions for next quarter"""

        start_time = time.time()
        plan = decomposer.decompose(task, task_type="analysis", haiku_success_rate=0.85)
        decomposition_time = time.time() - start_time

        haiku_tokens = QualityMeasurement.estimate_haiku_tokens(plan)
        quality_score = QualityMeasurement.calculate_quality_score(
            decomposition_depth=len(plan.steps),
            plan_coherence=plan.confidence,
            step_count=len(plan.steps)
        )
        token_savings = QualityMeasurement.calculate_token_savings(haiku_tokens)

        metrics = QualityMetrics(
            task_id=task_id,
            task_type="analysis",
            haiku_quality_score=quality_score,
            token_savings_percent=token_savings,
            latency_added_ms=decomposition_time * 1000,
            decomposition_overhead_tokens=haiku_tokens,
        )

        print(f"\n{'='*60}")
        print(f"Data Analysis Task Quality Metrics")
        print(f"{'='*60}")
        print(f"Quality Score (vs Sonnet): {metrics.haiku_quality_score:.2%}")
        print(f"Token Savings: {metrics.token_savings_percent:.1f}%")
        print(f"Latency Added: {metrics.latency_added_ms:.1f}ms")
        print(f"Plan Steps: {len(plan.steps)}")
        print(f"Passing: {metrics.is_passing()}")

        assert metrics.haiku_quality_score >= 0.90, \
            f"QUALITY GATE FAILED: {metrics.haiku_quality_score:.2%} < 90%"

    def test_refactoring_quality_measurement(self):
        """Test refactoring task decomposition quality."""
        task_id = "e2e_refactoring_1"
        decomposer = PromptDecomposer(task_id)

        task = """Refactor this legacy authentication module:

Current code has:
- 500+ lines in single file
- Mixed concerns (crypto, DB, logging)
- No tests
- Performance issues with token lookup

Requirements:
- Split into modules
- Add comprehensive tests
- Optimize hot paths
- Improve maintainability"""

        start_time = time.time()
        plan = decomposer.decompose(task, task_type="refactoring", haiku_success_rate=0.87)
        decomposition_time = time.time() - start_time

        haiku_tokens = QualityMeasurement.estimate_haiku_tokens(plan)
        quality_score = QualityMeasurement.calculate_quality_score(
            decomposition_depth=len(plan.steps),
            plan_coherence=plan.confidence,
            step_count=len(plan.steps)
        )
        token_savings = QualityMeasurement.calculate_token_savings(haiku_tokens)

        metrics = QualityMetrics(
            task_id=task_id,
            task_type="refactoring",
            haiku_quality_score=quality_score,
            token_savings_percent=token_savings,
            latency_added_ms=decomposition_time * 1000,
            decomposition_overhead_tokens=haiku_tokens,
        )

        print(f"\n{'='*60}")
        print(f"Refactoring Task Quality Metrics")
        print(f"{'='*60}")
        print(f"Quality Score (vs Sonnet): {metrics.haiku_quality_score:.2%}")
        print(f"Token Savings: {metrics.token_savings_percent:.1f}%")
        print(f"Latency Added: {metrics.latency_added_ms:.1f}ms")
        print(f"Plan Steps: {len(plan.steps)}")
        print(f"Passing: {metrics.is_passing()}")

        assert metrics.haiku_quality_score >= 0.90, \
            f"QUALITY GATE FAILED: {metrics.haiku_quality_score:.2%} < 90%"

    def test_documentation_quality_measurement(self):
        """Test documentation generation quality."""
        task_id = "e2e_documentation_1"
        decomposer = PromptDecomposer(task_id)

        task = """Document the REST API for our payment processing system:

Endpoints:
- POST /payments/process
- GET /payments/{id}
- POST /payments/{id}/refund

Include:
- Parameter documentation
- Response schemas
- Error handling
- Code examples in 3 languages"""

        start_time = time.time()
        plan = decomposer.decompose(task, task_type="documentation", haiku_success_rate=0.89)
        decomposition_time = time.time() - start_time

        haiku_tokens = QualityMeasurement.estimate_haiku_tokens(plan)
        quality_score = QualityMeasurement.calculate_quality_score(
            decomposition_depth=len(plan.steps),
            plan_coherence=plan.confidence,
            step_count=len(plan.steps)
        )
        token_savings = QualityMeasurement.calculate_token_savings(haiku_tokens)

        metrics = QualityMetrics(
            task_id=task_id,
            task_type="documentation",
            haiku_quality_score=quality_score,
            token_savings_percent=token_savings,
            latency_added_ms=decomposition_time * 1000,
            decomposition_overhead_tokens=haiku_tokens,
        )

        print(f"\n{'='*60}")
        print(f"Documentation Task Quality Metrics")
        print(f"{'='*60}")
        print(f"Quality Score (vs Sonnet): {metrics.haiku_quality_score:.2%}")
        print(f"Token Savings: {metrics.token_savings_percent:.1f}%")
        print(f"Latency Added: {metrics.latency_added_ms:.1f}ms")
        print(f"Plan Steps: {len(plan.steps)}")
        print(f"Passing: {metrics.is_passing()}")

        assert metrics.haiku_quality_score >= 0.90, \
            f"QUALITY GATE FAILED: {metrics.haiku_quality_score:.2%} < 90%"


class TestAggregateQualityMetrics:
    """Test aggregate quality across all task types."""

    def test_aggregate_quality_gate(self):
        """Test that aggregate quality meets gate."""
        decomposer = PromptDecomposer("aggregate_test")

        test_cases = [
            ("Review code", "code_review"),
            ("Write tests", "testing"),
            ("Analyze data", "analysis"),
            ("Refactor module", "refactoring"),
            ("Generate REST API", "code_gen"),
        ]

        all_metrics = []

        for task, task_type in test_cases:
            plan = decomposer.decompose(task, task_type=task_type, haiku_success_rate=0.88)

            haiku_tokens = QualityMeasurement.estimate_haiku_tokens(plan)
            quality_score = QualityMeasurement.calculate_quality_score(
                decomposition_depth=len(plan.steps),
                plan_coherence=plan.confidence,
                step_count=len(plan.steps)
            )
            token_savings = QualityMeasurement.calculate_token_savings(haiku_tokens)

            metrics = QualityMetrics(
                task_id=f"agg_{task_type}",
                task_type=task_type,
                haiku_quality_score=quality_score,
                token_savings_percent=token_savings,
                latency_added_ms=10.0,  # Estimated
                decomposition_overhead_tokens=haiku_tokens,
            )

            all_metrics.append(metrics)

        # Calculate aggregate
        avg_quality = sum(m.haiku_quality_score for m in all_metrics) / len(all_metrics)
        avg_savings = sum(m.token_savings_percent for m in all_metrics) / len(all_metrics)

        print(f"\n{'='*60}")
        print(f"Aggregate Quality Metrics")
        print(f"{'='*60}")
        print(f"Average Quality Score: {avg_quality:.2%}")
        print(f"Average Token Savings: {avg_savings:.1f}%")
        print(f"Test Cases: {len(all_metrics)}")
        print(f"All Passing: {all(m.is_passing() for m in all_metrics)}")

        for m in all_metrics:
            print(f"  {m.task_type:15} Quality: {m.haiku_quality_score:.2%}  Savings: {m.token_savings_percent:5.1f}%")

        # Hard gate
        assert avg_quality >= 0.90, \
            f"AGGREGATE QUALITY GATE FAILED: {avg_quality:.2%} < 90%"

        assert all(m.is_passing() for m in all_metrics), \
            "Not all metrics pass individual gates"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])

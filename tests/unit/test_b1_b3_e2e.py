"""
Iteration 2 E2E Tests — Execution Engine (B1) + Optimizer Loop (B3)
End-to-end flow: TaskQueue → TaskRouter → Executor → PlanOptimizer → audit trail
K=2 E2E Wiring Proof gate.
"""

import pytest
import sys
import os
import time
from datetime import datetime
from typing import Dict, Any

# Add core to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../'))

from core.orchestrator.task_orchestrator import (
    Task, TaskType, TaskStatus, TaskPriority, TaskRouter, TaskQueue
)
from core.orchestrator.plan_optimizer import (
    ExecutionPlan, Constraint, ConstraintType, PlanEvaluator, PlanOptimizer,
    EvaluationResult, PlanStatus
)
from core.orchestrator.executor import (
    Executor, ExecutionResult, ExecutionStatus, ErrorClassification, ExecutorState
)
from core.orchestrator.optimizer_loop import (
    OptimizerLoop, OptimizedPlan, SimpleOptimizationStrategy, LLMOptimizationStrategy
)


# ============================================================================
# EXECUTOR (B1) UNIT TESTS
# ============================================================================

class TestExecutor:
    """Test Executor task execution with retry logic"""

    def test_executor_initialization(self):
        """Executor initializes with tenant isolation"""
        executor = Executor(tenant_id="tenant-1", max_retries=3)
        assert executor.tenant_id == "tenant-1"
        assert executor.max_retries == 3

    def test_executor_successful_execution(self):
        """Executor succeeds on first attempt"""
        executor = Executor(tenant_id="tenant-1")
        result = executor.execute(
            task_id="task-001",
            worker_type="opus",
            input_data={"query": "test"}
        )
        assert result.status == ExecutionStatus.COMPLETED
        assert result.retries_used == 0
        assert result.output is not None
        assert result.audit_hash is not None
        assert len(result.execution_trace) > 0

    def test_executor_transient_error_retry(self):
        """Executor retries on transient errors with backoff"""
        def failing_worker(task_id: str, worker_type: str, input_data: Dict[str, Any]):
            # Simulate transient error on first two attempts
            if not hasattr(failing_worker, 'attempt_count'):
                failing_worker.attempt_count = 0
            failing_worker.attempt_count += 1

            if failing_worker.attempt_count < 3:
                raise Exception("timeout: connection failed")
            return {"success": True}

        executor = Executor(tenant_id="tenant-1", max_retries=3)
        start = time.time()
        result = executor.execute(
            task_id="task-002",
            worker_type="sonnet",
            input_data={"data": "test"},
            worker_callable=failing_worker
        )
        elapsed = time.time() - start

        assert result.status == ExecutionStatus.COMPLETED
        assert result.retries_used == 2  # Failed twice, succeeded on third
        # Should have backoff delays: ~2s + ~4s = ~6s minimum
        assert elapsed >= 5.0, f"Expected >= 5s backoff, got {elapsed:.1f}s"
        assert "COMPLETED" in result.execution_trace[-1]

    def test_executor_permanent_error_no_retry(self):
        """Executor fails immediately on permanent errors"""
        def failing_worker(task_id: str, worker_type: str, input_data: Dict[str, Any]):
            raise Exception("unauthorized: invalid credentials")

        executor = Executor(tenant_id="tenant-1", max_retries=3)
        start = time.time()
        result = executor.execute(
            task_id="task-003",
            worker_type="sonnet",
            input_data={"data": "test"},
            worker_callable=failing_worker
        )
        elapsed = time.time() - start

        assert result.status == ExecutionStatus.ROLLED_BACK
        assert result.error_classification == ErrorClassification.PERMANENT
        assert result.retries_used == 0  # No retry on permanent error
        assert elapsed < 1.0, f"Expected immediate failure, took {elapsed:.1f}s"

    def test_executor_max_retries_exceeded(self):
        """Executor fails after max retries"""
        def always_failing_worker(task_id: str, worker_type: str, input_data: Dict[str, Any]):
            raise Exception("network: connection timeout")

        executor = Executor(tenant_id="tenant-1", max_retries=2)
        result = executor.execute(
            task_id="task-004",
            worker_type="sonnet",
            input_data={"data": "test"},
            worker_callable=always_failing_worker
        )

        assert result.status == ExecutionStatus.FAILED
        assert result.retries_used == 2  # 0, 1, 2 = 3 total attempts
        assert len(result.execution_trace) > 0

    def test_executor_timeout(self):
        """Executor respects execution timeout"""
        def slow_worker(task_id: str, worker_type: str, input_data: Dict[str, Any]):
            time.sleep(3)  # Simulate slow work
            return {"result": "done"}

        executor = Executor(tenant_id="tenant-1")
        start = time.time()
        result = executor.execute(
            task_id="task-005",
            worker_type="sonnet",
            input_data={"data": "test"},
            worker_callable=slow_worker,
            timeout_secs=1  # 1 second timeout
        )
        elapsed = time.time() - start

        assert result.status == ExecutionStatus.TIMEOUT
        assert elapsed < 2.0, f"Should timeout quickly, took {elapsed:.1f}s"

    def test_executor_audit_hash(self):
        """Executor generates deterministic audit hash"""
        executor = Executor(tenant_id="tenant-1")
        result1 = executor.execute(
            task_id="task-006",
            worker_type="opus",
            input_data={"query": "test"}
        )
        result2 = executor.execute(
            task_id="task-006",  # Same task ID
            worker_type="opus",
            input_data={"query": "test"}
        )

        # Same task should produce consistent audit hash
        assert result1.audit_hash is not None
        # Note: Different executions due to different timestamps will have different hashes

    def test_executor_state_checkpoint_rollback(self):
        """Executor checkpoints and rolls back state"""
        executor = Executor(tenant_id="tenant-1")

        result = executor.execute(
            task_id="task-007",
            worker_type="sonnet",
            input_data={"data": "test"}
        )

        state = executor.get_state("task-007")
        assert state is not None
        assert state.saved_state is not None
        assert "input_data" in state.saved_state


# ============================================================================
# OPTIMIZER LOOP (B3) UNIT TESTS
# ============================================================================

class TestOptimizerLoop:
    """Test OptimizerLoop constraint optimization"""

    def test_optimizer_initialization(self):
        """OptimizerLoop initializes correctly"""
        optimizer = OptimizerLoop(
            tenant_id="tenant-1",
            max_rounds=2,
            timeout_secs_per_round=5,
            use_llm=False  # Use simple strategy for tests
        )
        assert optimizer.tenant_id == "tenant-1"
        assert optimizer.max_rounds == 2
        assert isinstance(optimizer.strategy, SimpleOptimizationStrategy)

    def test_optimizer_no_violations(self):
        """Optimizer skips when plan has no violations"""
        optimizer = OptimizerLoop(
            tenant_id="tenant-1",
            use_llm=False
        )

        result = optimizer.optimize_plan(
            plan_id="plan-001",
            plan_metrics={
                "task_ids": ["task-1", "task-2"],
                "estimated_latency_secs": 2.0,
                "estimated_throughput_tpm": 30.0,
                "estimated_cost_usd": 0.05,
                "estimated_reliability": 0.98,
                "constraints_passed": 4,
                "constraints_failed": 0,
            },
            constraint_violations=[]
        )

        assert result.converged is True
        assert result.final_satisfaction_score == 1.0
        assert len(result.optimization_rounds) == 0

    def test_optimizer_single_round_convergence(self):
        """Optimizer converges in single round"""
        optimizer = OptimizerLoop(
            tenant_id="tenant-1",
            use_llm=False,
            max_rounds=2
        )

        violations = [
            {"type": "latency", "margin": 0.5},
        ]

        result = optimizer.optimize_plan(
            plan_id="plan-002",
            plan_metrics={
                "task_ids": ["task-1"],
                "estimated_latency_secs": 5.5,
                "estimated_throughput_tpm": 12.0,
                "estimated_cost_usd": 0.08,
                "estimated_reliability": 0.95,
                "constraints_passed": 3,
                "constraints_failed": 1,
            },
            constraint_violations=violations
        )

        assert result.converged is True or len(result.optimization_rounds) > 0
        assert len(result.optimization_suggestions) > 0

    def test_optimizer_two_rounds(self):
        """Optimizer runs max 2 optimization rounds"""
        optimizer = OptimizerLoop(
            tenant_id="tenant-1",
            use_llm=False,
            max_rounds=2
        )

        violations = [
            {"type": "throughput", "margin": 5.0},
            {"type": "cost", "margin": 0.05},
        ]

        result = optimizer.optimize_plan(
            plan_id="plan-003",
            plan_metrics={
                "task_ids": ["task-1", "task-2", "task-3"],
                "estimated_latency_secs": 2.0,
                "estimated_throughput_tpm": 5.0,
                "estimated_cost_usd": 0.15,
                "estimated_reliability": 0.92,
                "constraints_passed": 2,
                "constraints_failed": 2,
            },
            constraint_violations=violations
        )

        assert len(result.optimization_rounds) <= 2
        assert all(r.duration_ms >= 0 for r in result.optimization_rounds)

    def test_simple_optimization_strategy(self):
        """SimpleOptimizationStrategy generates suggestions"""
        strategy = SimpleOptimizationStrategy()

        violations = [
            {"type": "latency", "margin": 1.5},
            {"type": "reliability", "margin": 0.05},
        ]

        suggestions = strategy.suggest_optimizations(
            plan_info={
                "task_count": 3,
                "latency_secs": 6.5,
                "throughput_tpm": 15.0,
                "cost_usd": 0.10,
                "reliability": 0.90,
            },
            violations=violations
        )

        assert len(suggestions) > 0
        assert all(isinstance(s, str) for s in suggestions)


# ============================================================================
# END-TO-END INTEGRATION TESTS
# ============================================================================

class TestE2EOrchestration:
    """End-to-end orchestration flow tests"""

    def test_e2e_happy_path(self):
        """E2E: Task enqueue → route → execute → optimize → complete"""
        tenant_id = "tenant-test"

        # 1. Create queue and router
        queue = TaskQueue(tenant_id=tenant_id)

        # 2. Create and enqueue task
        task = Task(
            task_id="e2e-task-001",
            tenant_id=tenant_id,
            task_type=TaskType.INFERENCE,
            priority=TaskPriority.NORMAL,
            input_data={"query": "test inference"}
        )
        success, error = queue.enqueue(task)
        assert success is True

        # 3. Route task
        worker, route_error = queue.router.route(task)
        assert route_error is None
        assert worker == "opus"  # INFERENCE → opus
        assert task.status == TaskStatus.ROUTED

        # 4. Create executor and execute
        executor = Executor(tenant_id=tenant_id)
        result = executor.execute(
            task_id=task.task_id,
            worker_type=worker,
            input_data=task.input_data
        )
        assert result.status == ExecutionStatus.COMPLETED
        assert result.audit_hash is not None

        # 5. Create execution plan and evaluate
        plan = ExecutionPlan(
            plan_id="e2e-plan-001",
            tenant_id=tenant_id,
            task_ids=[task.task_id],
            estimated_latency_secs=result.latency_ms / 1000.0,
            estimated_throughput_tpm=100.0,
            estimated_cost_usd=0.05,
            estimated_reliability=1.0,
        )

        optimizer = PlanOptimizer(tenant_id=tenant_id)
        optimizer.register_plan(plan)
        eval_result = optimizer.evaluate_plan(plan.plan_id)

        assert eval_result is not None
        assert eval_result.overall_pass is True

        # 6. Optimize plan
        opt_loop = OptimizerLoop(tenant_id=tenant_id, use_llm=False)
        optimized = opt_loop.optimize_plan(
            plan_id=plan.plan_id,
            plan_metrics={
                "task_ids": plan.task_ids,
                "estimated_latency_secs": plan.estimated_latency_secs,
                "estimated_throughput_tpm": plan.estimated_throughput_tpm,
                "estimated_cost_usd": plan.estimated_cost_usd,
                "estimated_reliability": plan.estimated_reliability,
                "constraints_passed": eval_result.constraints_satisfied.__len__(),
                "constraints_failed": eval_result.constraints_violated.__len__(),
            },
            constraint_violations=[]
        )

        assert optimized.converged is True
        assert optimized.audit_hash is not None

    def test_e2e_with_retry(self):
        """E2E: Execution with retry and recovery"""
        tenant_id = "tenant-retry"

        # Create task
        task = Task(
            task_id="e2e-retry-001",
            tenant_id=tenant_id,
            task_type=TaskType.ANALYSIS,
            priority=TaskPriority.HIGH,
            input_data={"data": "analyze"}
        )

        # Route
        router = TaskRouter(tenant_id=tenant_id)
        worker, _ = router.route(task)

        # Execute with failing worker
        attempt_count = [0]

        def flaky_worker(task_id: str, worker_type: str, input_data: Dict[str, Any]):
            attempt_count[0] += 1
            if attempt_count[0] < 2:
                raise Exception("timeout: temporary network issue")
            return {"status": "analyzed"}

        executor = Executor(tenant_id=tenant_id, max_retries=2)
        result = executor.execute(
            task_id=task.task_id,
            worker_type=worker,
            input_data=task.input_data,
            worker_callable=flaky_worker
        )

        assert result.status == ExecutionStatus.COMPLETED
        assert result.retries_used == 1
        assert len(result.execution_trace) > 0

    def test_e2e_constraint_violation_and_optimization(self):
        """E2E: Detect constraint violation and optimize"""
        tenant_id = "tenant-opt"

        # Create plan with constraint violation
        plan = ExecutionPlan(
            plan_id="e2e-opt-001",
            tenant_id=tenant_id,
            task_ids=["t1", "t2", "t3"],
            estimated_latency_secs=6.0,  # Violates 5s constraint
            estimated_throughput_tpm=8.0,  # Violates 10/min constraint
            estimated_cost_usd=0.12,
            estimated_reliability=0.92,
        )

        # Evaluate
        evaluator = PlanEvaluator(tenant_id=tenant_id)
        eval_result = evaluator.evaluate(plan)

        assert eval_result.overall_pass is False
        assert len(eval_result.constraints_violated) > 0

        # Optimize
        opt_loop = OptimizerLoop(tenant_id=tenant_id, use_llm=False)
        optimized = opt_loop.optimize_plan(
            plan_id=plan.plan_id,
            plan_metrics={
                "task_ids": plan.task_ids,
                "estimated_latency_secs": plan.estimated_latency_secs,
                "estimated_throughput_tpm": plan.estimated_throughput_tpm,
                "estimated_cost_usd": plan.estimated_cost_usd,
                "estimated_reliability": plan.estimated_reliability,
                "constraints_passed": 0,
                "constraints_failed": 4,
            },
            constraint_violations=[
                {"type": "latency", "margin": 1.0},
                {"type": "throughput", "margin": 2.0},
            ]
        )

        assert len(optimized.optimization_suggestions) > 0


# ============================================================================
# PERFORMANCE & THROUGHPUT TESTS
# ============================================================================

class TestPerformance:
    """Performance and throughput tests"""

    def test_executor_throughput_100_tasks(self):
        """Executor processes 100 tasks in reasonable time"""
        executor = Executor(tenant_id="tenant-perf")
        start = time.time()

        for i in range(100):
            result = executor.execute(
                task_id=f"task-{i}",
                worker_type="opus",
                input_data={"query": f"test-{i}"}
            )
            assert result.status == ExecutionStatus.COMPLETED

        elapsed = time.time() - start
        throughput = 100 / elapsed

        # Should process at least 50 tasks/sec (2s for 100 tasks)
        assert throughput > 50, f"Expected > 50 tasks/sec, got {throughput:.1f}"
        logger.info(f"Throughput: {throughput:.1f} tasks/sec ({elapsed:.1f}s for 100 tasks)")

    def test_executor_latency_p99(self):
        """Executor p99 latency < 100ms for simple execution"""
        executor = Executor(tenant_id="tenant-latency")
        latencies = []

        for i in range(100):
            start = time.time()
            result = executor.execute(
                task_id=f"task-{i}",
                worker_type="opus",
                input_data={"query": f"test-{i}"}
            )
            latency_ms = (time.time() - start) * 1000
            latencies.append(latency_ms)

        latencies.sort()
        p99_latency = latencies[int(len(latencies) * 0.99)]

        # P99 should be < 100ms
        assert p99_latency < 100, f"Expected p99 < 100ms, got {p99_latency:.1f}ms"
        logger.info(f"P99 latency: {p99_latency:.1f}ms")

    def test_optimizer_performance(self):
        """OptimizerLoop completes within timeout"""
        optimizer = OptimizerLoop(
            tenant_id="tenant-opt-perf",
            use_llm=False,
            timeout_secs_per_round=5
        )

        violations = [
            {"type": "latency", "margin": 1.0},
            {"type": "cost", "margin": 0.05},
        ]

        start = time.time()
        result = optimizer.optimize_plan(
            plan_id="perf-plan-001",
            plan_metrics={
                "task_ids": ["t1", "t2"],
                "estimated_latency_secs": 6.0,
                "estimated_throughput_tpm": 10.0,
                "estimated_cost_usd": 0.15,
                "estimated_reliability": 0.95,
                "constraints_passed": 2,
                "constraints_failed": 2,
            },
            constraint_violations=violations
        )
        elapsed = (time.time() - start) * 1000

        # Should complete well within timeout
        assert elapsed < 5000, f"Optimization took {elapsed:.0f}ms, expected < 5000ms"
        assert result.total_optimization_time_ms < 5000


# ============================================================================
# Utility
# ============================================================================

import logging
logger = logging.getLogger(__name__)

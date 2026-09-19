"""
Iteration 1 Unit Tests — Task Orchestrator (B1) + Plan Optimizer (B3)
Tests routing logic and constraint evaluation (no execution, no LLM).
K=1 RED/GREEN gate.
"""

import pytest
from datetime import datetime, timedelta
import sys
import os

# Add core to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../'))

from core.orchestrator.task_orchestrator import (
    Task, TaskType, TaskStatus, TaskPriority, TaskRouter, TaskQueue, RoutingRule
)
from core.orchestrator.plan_optimizer import (
    ExecutionPlan, Constraint, ConstraintType, PlanEvaluator, PlanOptimizer,
    EvaluationResult, PlanStatus
)


# ============================================================================
# B1 TASK ORCHESTRATOR TESTS
# ============================================================================

class TestTaskCreation:
    """Test Task dataclass creation and validation"""

    def test_task_creation_with_defaults(self):
        """Task can be created with minimal arguments"""
        task = Task(
            task_id="task-001",
            tenant_id="tenant-1",
            task_type=TaskType.INFERENCE,
            priority=TaskPriority.NORMAL,
            input_data={"query": "test"}
        )
        assert task.task_id == "task-001"
        assert task.tenant_id == "tenant-1"
        assert task.status == TaskStatus.QUEUED
        assert task.retries == 0

    def test_task_to_dict(self):
        """Task can be serialized to dict"""
        task = Task(
            task_id="task-002",
            tenant_id="tenant-1",
            task_type=TaskType.ANALYSIS,
            priority=TaskPriority.HIGH,
            input_data={"data": "value"}
        )
        task_dict = task.to_dict()
        assert task_dict["task_id"] == "task-002"
        assert task_dict["task_type"] == "analysis"
        assert task_dict["priority"] == 3  # HIGH

    def test_task_status_transitions(self):
        """Task status can be updated through workflow"""
        task = Task(
            task_id="task-003",
            tenant_id="tenant-1",
            task_type=TaskType.PLANNING,
            priority=TaskPriority.NORMAL,
            input_data={"plan": "test"}
        )
        assert task.status == TaskStatus.QUEUED

        task.status = TaskStatus.ROUTED
        assert task.status == TaskStatus.ROUTED

        task.status = TaskStatus.ASSIGNED
        assert task.status == TaskStatus.ASSIGNED


class TestTaskRouter:
    """Test TaskRouter routing logic (ADR-0296, ADR-0007)"""

    def test_router_initialization(self):
        """Router initializes with default rules"""
        router = TaskRouter("tenant-1")
        assert router.tenant_id == "tenant-1"
        assert len(router.routing_rules) == 5  # 5 default rules
        assert TaskType.INFERENCE in router.routing_rules

    def test_task_validation_success(self):
        """Valid task passes validation"""
        router = TaskRouter("tenant-1")
        task = Task(
            task_id="task-001",
            tenant_id="tenant-1",
            task_type=TaskType.INFERENCE,
            priority=TaskPriority.NORMAL,
            input_data={"query": "test"}
        )
        is_valid, error = router.validate_task(task)
        assert is_valid is True
        assert error is None

    def test_task_validation_missing_tenant(self):
        """Task without tenant_id fails validation"""
        router = TaskRouter("tenant-1")
        task = Task(
            task_id="task-001",
            tenant_id="",  # Empty
            task_type=TaskType.INFERENCE,
            priority=TaskPriority.NORMAL,
            input_data={"query": "test"}
        )
        is_valid, error = router.validate_task(task)
        assert is_valid is False
        assert "tenant_id required" in error

    def test_task_validation_tenant_mismatch(self):
        """Task with mismatched tenant fails validation"""
        router = TaskRouter("tenant-1")
        task = Task(
            task_id="task-001",
            tenant_id="tenant-2",  # Mismatch
            task_type=TaskType.INFERENCE,
            priority=TaskPriority.NORMAL,
            input_data={"query": "test"}
        )
        is_valid, error = router.validate_task(task)
        assert is_valid is False
        assert "tenant mismatch" in error

    def test_task_validation_invalid_type(self):
        """Task with invalid type fails validation"""
        router = TaskRouter("tenant-1")
        task = Task(
            task_id="task-001",
            tenant_id="tenant-1",
            task_type="invalid_type",  # Invalid
            priority=TaskPriority.NORMAL,
            input_data={"query": "test"}
        )
        # This would normally fail at the enum level, but test the validation logic
        # by passing a task through validation after manual type setting
        task.task_type = "invalid"  # Override
        is_valid, error = router.validate_task(task)
        assert is_valid is False

    def test_task_validation_empty_input(self):
        """Task with empty input data fails validation"""
        router = TaskRouter("tenant-1")
        task = Task(
            task_id="task-001",
            tenant_id="tenant-1",
            task_type=TaskType.INFERENCE,
            priority=TaskPriority.NORMAL,
            input_data={}  # Empty
        )
        is_valid, error = router.validate_task(task)
        assert is_valid is False
        assert "empty" in error

    def test_routing_success_inference(self):
        """Inference task routes to opus worker"""
        router = TaskRouter("tenant-1")
        task = Task(
            task_id="task-001",
            tenant_id="tenant-1",
            task_type=TaskType.INFERENCE,
            priority=TaskPriority.NORMAL,
            input_data={"query": "test"}
        )
        worker, error = router.route(task)
        assert worker == "opus"
        assert error is None
        assert task.status == TaskStatus.ROUTED
        assert task.assigned_worker == "opus"
        assert task.audit_hash is not None

    def test_routing_success_analysis(self):
        """Analysis task routes to sonnet worker"""
        router = TaskRouter("tenant-1")
        task = Task(
            task_id="task-002",
            tenant_id="tenant-1",
            task_type=TaskType.ANALYSIS,
            priority=TaskPriority.NORMAL,
            input_data={"data": "test"}
        )
        worker, error = router.route(task)
        assert worker == "sonnet"
        assert error is None

    def test_routing_fails_invalid_task(self):
        """Invalid task fails routing"""
        router = TaskRouter("tenant-1")
        task = Task(
            task_id="task-001",
            tenant_id="tenant-2",  # Mismatch
            task_type=TaskType.INFERENCE,
            priority=TaskPriority.NORMAL,
            input_data={"query": "test"}
        )
        worker, error = router.route(task)
        assert worker is None
        assert error is not None
        assert "tenant mismatch" in error

    def test_routing_priority_check(self):
        """Task below minimum priority fails routing"""
        router = TaskRouter("tenant-1")

        # Set minimum priority to HIGH for ANALYSIS
        rule = router.routing_rules[TaskType.ANALYSIS]
        rule.min_priority = TaskPriority.HIGH

        task = Task(
            task_id="task-001",
            tenant_id="tenant-1",
            task_type=TaskType.ANALYSIS,
            priority=TaskPriority.LOW,  # Below minimum
            input_data={"data": "test"}
        )
        worker, error = router.route(task)
        assert worker is None
        assert error is not None
        assert "priority" in error.lower()

    def test_register_custom_rule(self):
        """Custom routing rule can be registered"""
        router = TaskRouter("tenant-1")
        custom_rule = RoutingRule(
            task_type=TaskType.LEARNING,
            target_worker_type="haiku",
            max_concurrent=5
        )
        router.register_rule(custom_rule)
        assert router.get_rule(TaskType.LEARNING).target_worker_type == "haiku"

    def test_audit_hash_generation(self):
        """Audit hash is generated for routing decisions"""
        router = TaskRouter("tenant-1")
        task = Task(
            task_id="task-001",
            tenant_id="tenant-1",
            task_type=TaskType.INFERENCE,
            priority=TaskPriority.NORMAL,
            input_data={"query": "test"}
        )
        worker, error = router.route(task)
        assert task.audit_hash is not None
        assert len(task.audit_hash) == 16  # SHA256 truncated


class TestTaskQueue:
    """Test TaskQueue queueing logic (ADR-0007 tenant isolation)"""

    def test_queue_initialization(self):
        """Queue initializes for a tenant"""
        queue = TaskQueue("tenant-1")
        assert queue.tenant_id == "tenant-1"
        assert queue.queue_size() == 0

    def test_enqueue_valid_task(self):
        """Valid task can be enqueued"""
        queue = TaskQueue("tenant-1")
        task = Task(
            task_id="task-001",
            tenant_id="tenant-1",
            task_type=TaskType.INFERENCE,
            priority=TaskPriority.NORMAL,
            input_data={"query": "test"}
        )
        success, error = queue.enqueue(task)
        assert success is True
        assert error is None
        assert queue.queue_size() == 1

    def test_enqueue_tenant_mismatch(self):
        """Task with mismatched tenant cannot be enqueued"""
        queue = TaskQueue("tenant-1")
        task = Task(
            task_id="task-001",
            tenant_id="tenant-2",  # Mismatch
            task_type=TaskType.INFERENCE,
            priority=TaskPriority.NORMAL,
            input_data={"query": "test"}
        )
        success, error = queue.enqueue(task)
        assert success is False
        assert error is not None
        assert queue.queue_size() == 0

    def test_dequeue_priority_ordering(self):
        """Tasks are dequeued by priority (high first)"""
        queue = TaskQueue("tenant-1")

        # Enqueue in random order
        task_low = Task(
            task_id="task-low",
            tenant_id="tenant-1",
            task_type=TaskType.INFERENCE,
            priority=TaskPriority.LOW,
            input_data={"q": "1"}
        )
        task_high = Task(
            task_id="task-high",
            tenant_id="tenant-1",
            task_type=TaskType.INFERENCE,
            priority=TaskPriority.HIGH,
            input_data={"q": "2"}
        )

        queue.enqueue(task_low)
        queue.enqueue(task_high)

        # Dequeue should get high priority first
        dequeued = queue.dequeue_next()
        assert dequeued.task_id == "task-high"
        assert dequeued.priority == TaskPriority.HIGH

    def test_dequeue_fifo_within_priority(self):
        """Tasks at same priority are dequeued FIFO"""
        queue = TaskQueue("tenant-1")

        task1 = Task(
            task_id="task-1",
            tenant_id="tenant-1",
            task_type=TaskType.INFERENCE,
            priority=TaskPriority.NORMAL,
            input_data={"q": "1"}
        )
        task2 = Task(
            task_id="task-2",
            tenant_id="tenant-1",
            task_type=TaskType.INFERENCE,
            priority=TaskPriority.NORMAL,
            input_data={"q": "2"}
        )

        # Task1 enqueued first
        queue.enqueue(task1)
        import time
        time.sleep(0.01)  # Ensure different timestamps
        queue.enqueue(task2)

        # Should dequeue task1 first (FIFO)
        dequeued = queue.dequeue_next()
        assert dequeued.task_id == "task-1"

    def test_dequeue_empty_queue(self):
        """Dequeuing from empty queue returns None"""
        queue = TaskQueue("tenant-1")
        task = queue.dequeue_next()
        assert task is None

    def test_get_task(self):
        """Tasks can be looked up by ID"""
        queue = TaskQueue("tenant-1")
        task = Task(
            task_id="task-001",
            tenant_id="tenant-1",
            task_type=TaskType.INFERENCE,
            priority=TaskPriority.NORMAL,
            input_data={"query": "test"}
        )
        queue.enqueue(task)

        found = queue.get_task("task-001")
        assert found is not None
        assert found.task_id == "task-001"

    def test_queue_stats(self):
        """Queue statistics are accurate"""
        queue = TaskQueue("tenant-1")

        task1 = Task(
            task_id="task-1",
            tenant_id="tenant-1",
            task_type=TaskType.INFERENCE,
            priority=TaskPriority.NORMAL,
            input_data={"q": "1"}
        )
        queue.enqueue(task1)

        stats = queue.stats()
        assert stats["queue_size"] == 1
        assert stats["queued"] == 1


# ============================================================================
# B3 PLAN OPTIMIZER TESTS
# ============================================================================

class TestConstraintEvaluation:
    """Test Constraint evaluation logic"""

    def test_constraint_less_than_or_equal(self):
        """Constraint with <= operator works"""
        constraint = Constraint(
            constraint_type=ConstraintType.LATENCY,
            name="max_latency",
            target_value=5.0,
            operator="<="
        )
        assert constraint.evaluate(4.0) is True
        assert constraint.evaluate(5.0) is True
        assert constraint.evaluate(6.0) is False

    def test_constraint_greater_than_or_equal(self):
        """Constraint with >= operator works"""
        constraint = Constraint(
            constraint_type=ConstraintType.THROUGHPUT,
            name="min_throughput",
            target_value=10.0,
            operator=">="
        )
        assert constraint.evaluate(11.0) is True
        assert constraint.evaluate(10.0) is True
        assert constraint.evaluate(9.0) is False

    def test_constraint_violation_margin_less_than(self):
        """Violation margin is calculated for <= constraints"""
        constraint = Constraint(
            constraint_type=ConstraintType.LATENCY,
            name="max_latency",
            target_value=5.0,
            operator="<="
        )
        # Actual=7.0, target=5.0 → margin should be 2.0
        margin = constraint.violation_margin(7.0)
        assert margin == 2.0

    def test_constraint_violation_margin_greater_than(self):
        """Violation margin is calculated for >= constraints"""
        constraint = Constraint(
            constraint_type=ConstraintType.THROUGHPUT,
            name="min_throughput",
            target_value=10.0,
            operator=">="
        )
        # Actual=8.0, target=10.0 → margin should be 2.0
        margin = constraint.violation_margin(8.0)
        assert margin == 2.0


class TestPlanEvaluation:
    """Test PlanEvaluator constraint checking"""

    def test_evaluator_initialization(self):
        """Evaluator initializes with default constraints"""
        evaluator = PlanEvaluator("tenant-1")
        assert evaluator.tenant_id == "tenant-1"
        assert len(evaluator.constraints) > 0

    def test_evaluate_plan_all_pass(self):
        """Plan passes evaluation when all constraints satisfied"""
        evaluator = PlanEvaluator("tenant-1")
        plan = ExecutionPlan(
            plan_id="plan-001",
            tenant_id="tenant-1",
            task_ids=["task-1", "task-2"]
        )

        # Set metrics that pass all constraints
        plan.estimated_latency_secs = 2.0  # < 5.0
        plan.estimated_throughput_tpm = 15.0  # > 10.0
        plan.estimated_reliability = 0.98  # > 0.95
        plan.estimated_cost_usd = 0.05  # < 0.1 margin

        result = evaluator.evaluate(plan)
        assert result.overall_pass is True
        assert len(result.constraints_violated) == 0
        assert result.satisfaction_score > 0.8

    def test_evaluate_plan_some_violations(self):
        """Plan fails evaluation when constraints violated"""
        evaluator = PlanEvaluator("tenant-1")
        plan = ExecutionPlan(
            plan_id="plan-001",
            tenant_id="tenant-1",
            task_ids=["task-1", "task-2"]
        )

        # Set metrics that violate some constraints
        plan.estimated_latency_secs = 10.0  # > 5.0 (VIOLATES)
        plan.estimated_throughput_tpm = 5.0  # < 10.0 (VIOLATES)
        plan.estimated_reliability = 0.98  # OK
        plan.estimated_cost_usd = 0.05  # OK

        result = evaluator.evaluate(plan)
        assert result.overall_pass is False
        assert len(result.constraints_violated) > 0
        assert result.satisfaction_score < 1.0

    def test_evaluate_plan_tenant_mismatch(self):
        """Plan with mismatched tenant fails evaluation"""
        evaluator = PlanEvaluator("tenant-1")
        plan = ExecutionPlan(
            plan_id="plan-001",
            tenant_id="tenant-2",  # Mismatch
            task_ids=["task-1"]
        )

        result = evaluator.evaluate(plan)
        assert result.overall_pass is False
        assert result.satisfaction_score == 0.0

    def test_add_custom_constraint(self):
        """Custom constraints can be added"""
        evaluator = PlanEvaluator("tenant-1")
        custom = Constraint(
            constraint_type=ConstraintType.CUSTOM,
            name="custom_metric",
            target_value=100.0,
            operator=">="
        )
        evaluator.add_constraint(custom)

        assert len(evaluator.constraints[ConstraintType.CUSTOM]) > 0


class TestPlanOptimizer:
    """Test PlanOptimizer orchestration"""

    def test_optimizer_initialization(self):
        """Optimizer initializes for a tenant"""
        optimizer = PlanOptimizer("tenant-1", max_optimization_rounds=2)
        assert optimizer.tenant_id == "tenant-1"
        assert optimizer.max_optimization_rounds == 2

    def test_register_plan(self):
        """Plans can be registered for optimization"""
        optimizer = PlanOptimizer("tenant-1")
        plan = ExecutionPlan(
            plan_id="plan-001",
            tenant_id="tenant-1",
            task_ids=["task-1", "task-2"]
        )

        success, error = optimizer.register_plan(plan)
        assert success is True
        assert error is None
        assert plan.plan_hash is not None

    def test_register_plan_tenant_mismatch(self):
        """Plan with mismatched tenant cannot be registered"""
        optimizer = PlanOptimizer("tenant-1")
        plan = ExecutionPlan(
            plan_id="plan-001",
            tenant_id="tenant-2",  # Mismatch
            task_ids=["task-1"]
        )

        success, error = optimizer.register_plan(plan)
        assert success is False
        assert error is not None

    def test_evaluate_plan_through_optimizer(self):
        """Plans can be evaluated through optimizer"""
        optimizer = PlanOptimizer("tenant-1")
        plan = ExecutionPlan(
            plan_id="plan-001",
            tenant_id="tenant-1",
            task_ids=["task-1"]
        )
        plan.estimated_latency_secs = 2.0
        plan.estimated_throughput_tpm = 15.0
        plan.estimated_reliability = 0.98

        optimizer.register_plan(plan)
        result = optimizer.evaluate_plan("plan-001")

        assert result is not None
        assert result.overall_pass is True

    def test_optimization_history(self):
        """Optimization history is tracked"""
        optimizer = PlanOptimizer("tenant-1")
        plan = ExecutionPlan(
            plan_id="plan-001",
            tenant_id="tenant-1",
            task_ids=["task-1"]
        )
        plan.estimated_latency_secs = 2.0
        plan.estimated_throughput_tpm = 15.0
        plan.estimated_reliability = 0.98

        optimizer.register_plan(plan)

        # First evaluation
        optimizer.evaluate_plan("plan-001")
        history = optimizer.get_optimization_history("plan-001")
        assert len(history) == 1

        # Second evaluation
        optimizer.evaluate_plan("plan-001")
        history = optimizer.get_optimization_history("plan-001")
        assert len(history) == 2


# ============================================================================
# INTEGRATION TESTS (Iteration 1 baseline)
# ============================================================================

class TestIntegrationBaseline:
    """Test basic integration between B1 and B3"""

    def test_task_router_and_queue_together(self):
        """TaskRouter and TaskQueue work together"""
        queue = TaskQueue("tenant-1")
        task = Task(
            task_id="task-001",
            tenant_id="tenant-1",
            task_type=TaskType.INFERENCE,
            priority=TaskPriority.NORMAL,
            input_data={"query": "test"}
        )

        # Enqueue → router validates
        success, error = queue.enqueue(task)
        assert success is True

        # Route the task
        worker, route_error = queue.router.route(task)
        assert worker is not None
        assert route_error is None

    def test_plan_creation_and_evaluation(self):
        """ExecutionPlan creation and evaluation"""
        plan = ExecutionPlan(
            plan_id="plan-001",
            tenant_id="tenant-1",
            task_ids=["task-1", "task-2", "task-3"]
        )
        plan.estimated_latency_secs = 3.0
        plan.estimated_throughput_tpm = 12.0
        plan.estimated_reliability = 0.96

        evaluator = PlanEvaluator("tenant-1")
        result = evaluator.evaluate(plan)

        assert result.overall_pass is True
        assert plan.status == PlanStatus.EVALUATED


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

"""
B1 Task Orchestrator — Routing & Queueing
Routes tasks to workers based on type, tenant, priority.
ADR-0007 (multi-tenant), ADR-0296 (input validation), ADR-0256 (audit trail).
"""

from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field
from enum import Enum
import logging
from datetime import datetime
import uuid
import hashlib

logger = logging.getLogger(__name__)


class TaskPriority(Enum):
    """Task priority levels"""
    LOW = 1
    NORMAL = 2
    HIGH = 3
    CRITICAL = 4


class TaskType(Enum):
    """Task type classification"""
    INFERENCE = "inference"
    ANALYSIS = "analysis"
    PLANNING = "planning"
    OPTIMIZATION = "optimization"
    LEARNING = "learning"


class TaskStatus(Enum):
    """Task execution status"""
    QUEUED = "queued"
    ROUTED = "routed"
    ASSIGNED = "assigned"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Task:
    """Represents a single task in the queue"""
    task_id: str
    tenant_id: str
    task_type: TaskType
    priority: TaskPriority
    input_data: Dict[str, Any]
    created_at: datetime = field(default_factory=datetime.utcnow)
    status: TaskStatus = TaskStatus.QUEUED
    assigned_worker: Optional[str] = None
    audit_hash: Optional[str] = None
    retries: int = 0
    max_retries: int = 3

    def to_dict(self) -> Dict[str, Any]:
        """Convert task to dict for serialization"""
        return {
            "task_id": self.task_id,
            "tenant_id": self.tenant_id,
            "task_type": self.task_type.value,
            "priority": self.priority.value,
            "input_data": self.input_data,
            "created_at": self.created_at.isoformat(),
            "status": self.status.value,
            "assigned_worker": self.assigned_worker,
            "audit_hash": self.audit_hash,
            "retries": self.retries,
        }


@dataclass
class RoutingRule:
    """Routing rule for task assignment"""
    task_type: TaskType
    target_worker_type: str  # e.g., "opus", "sonnet", "standard"
    min_priority: TaskPriority = TaskPriority.LOW
    max_concurrent: int = 100
    timeout_secs: int = 300
    metadata: Dict[str, Any] = field(default_factory=dict)


class TaskRouter:
    """Routes tasks to appropriate workers based on type, priority, tenant"""

    def __init__(self, tenant_id: str):
        """Initialize TaskRouter for a specific tenant (ADR-0007)"""
        self.tenant_id = tenant_id
        self.routing_rules: Dict[TaskType, RoutingRule] = {}
        self._setup_default_rules()
        logger.info(f"TaskRouter initialized for tenant: {tenant_id}")

    def _setup_default_rules(self) -> None:
        """Set up default routing rules"""
        self.routing_rules[TaskType.INFERENCE] = RoutingRule(
            task_type=TaskType.INFERENCE,
            target_worker_type="opus",
            min_priority=TaskPriority.LOW,
            max_concurrent=50,
            timeout_secs=120,
        )
        self.routing_rules[TaskType.ANALYSIS] = RoutingRule(
            task_type=TaskType.ANALYSIS,
            target_worker_type="sonnet",
            min_priority=TaskPriority.NORMAL,
            max_concurrent=30,
            timeout_secs=300,
        )
        self.routing_rules[TaskType.PLANNING] = RoutingRule(
            task_type=TaskType.PLANNING,
            target_worker_type="opus",
            min_priority=TaskPriority.HIGH,
            max_concurrent=20,
            timeout_secs=600,
        )
        self.routing_rules[TaskType.OPTIMIZATION] = RoutingRule(
            task_type=TaskType.OPTIMIZATION,
            target_worker_type="sonnet",
            min_priority=TaskPriority.NORMAL,
            max_concurrent=25,
            timeout_secs=300,
        )
        self.routing_rules[TaskType.LEARNING] = RoutingRule(
            task_type=TaskType.LEARNING,
            target_worker_type="sonnet",
            min_priority=TaskPriority.LOW,
            max_concurrent=10,
            timeout_secs=180,
        )

    def validate_task(self, task: Task) -> Tuple[bool, Optional[str]]:
        """
        Validate task using ADR-0296 input validation strategy.
        Returns (is_valid, error_message)
        """
        # Tenant validation (ADR-0007 isolation)
        if not task.tenant_id:
            return False, "tenant_id required"
        if task.tenant_id != self.tenant_id:
            return False, f"tenant mismatch: {task.tenant_id} != {self.tenant_id}"

        # Task ID validation
        if not task.task_id or len(task.task_id) == 0:
            return False, "task_id required"

        # Task type validation
        if task.task_type not in TaskType:
            return False, f"invalid task_type: {task.task_type}"

        # Priority validation
        if task.priority not in TaskPriority:
            return False, f"invalid priority: {task.priority}"

        # Input data validation
        if not isinstance(task.input_data, dict):
            return False, "input_data must be dict"
        if len(task.input_data) == 0:
            return False, "input_data cannot be empty"

        return True, None

    def route(self, task: Task) -> Tuple[Optional[str], Optional[str]]:
        """
        Route a task to a worker.
        Returns (target_worker_type, routing_error) or (worker_type, None) on success
        ADR-0296: deny-by-default routing strategy
        """
        # Validate task first (deny-by-default)
        is_valid, error = self.validate_task(task)
        if not is_valid:
            logger.warning(f"Task validation failed: {error} (task_id={task.task_id})")
            return None, error

        # Look up routing rule for task type
        rule = self.routing_rules.get(task.task_type)
        if not rule:
            logger.warning(f"No routing rule for task_type: {task.task_type}")
            return None, f"No routing rule for {task.task_type.value}"

        # Check priority meets minimum
        if task.priority.value < rule.min_priority.value:
            logger.warning(
                f"Task priority {task.priority} below minimum {rule.min_priority} "
                f"(task_id={task.task_id})"
            )
            return None, f"Priority below minimum for this task type"

        # Determine target worker (this is the routing decision)
        target_worker = rule.target_worker_type

        # Update task metadata
        task.assigned_worker = target_worker
        task.status = TaskStatus.ROUTED
        task.audit_hash = self._compute_audit_hash(task, target_worker)

        logger.info(
            f"Task routed: {task.task_id} → {target_worker} "
            f"(tenant={task.tenant_id}, type={task.task_type.value}, "
            f"priority={task.priority.name})"
        )

        return target_worker, None

    def _compute_audit_hash(self, task: Task, target_worker: str) -> str:
        """
        Compute audit hash for the routing decision.
        Used for audit trail integrity (ADR-0256).
        """
        audit_data = f"{task.task_id}:{task.tenant_id}:{task.task_type.value}:{target_worker}:{task.created_at.isoformat()}"
        return hashlib.sha256(audit_data.encode()).hexdigest()[:16]

    def register_rule(self, rule: RoutingRule) -> None:
        """Register or update a routing rule"""
        self.routing_rules[rule.task_type] = rule
        logger.info(f"Routing rule registered: {rule.task_type} → {rule.target_worker_type}")

    def get_rule(self, task_type: TaskType) -> Optional[RoutingRule]:
        """Get routing rule for task type"""
        return self.routing_rules.get(task_type)


class TaskQueue:
    """Tenant-scoped task queue (ADR-0007 multi-tenant isolation)"""

    def __init__(self, tenant_id: str):
        """Initialize queue for a tenant"""
        self.tenant_id = tenant_id
        self.queue: List[Task] = []
        self.router = TaskRouter(tenant_id)
        self.task_index: Dict[str, Task] = {}  # Fast lookup
        logger.info(f"TaskQueue initialized for tenant: {tenant_id}")

    def enqueue(self, task: Task) -> Tuple[bool, Optional[str]]:
        """
        Enqueue a task. Returns (success, error_message)
        Validates tenant isolation (ADR-0007).
        """
        # Tenant isolation check
        if task.tenant_id != self.tenant_id:
            error = f"Tenant mismatch: {task.tenant_id} != {self.tenant_id}"
            logger.error(f"Enqueue failed: {error}")
            return False, error

        # Validate task via router
        is_valid, error = self.router.validate_task(task)
        if not is_valid:
            logger.warning(f"Task validation failed: {error}")
            return False, error

        # Add to queue
        self.queue.append(task)
        self.task_index[task.task_id] = task
        logger.info(f"Task enqueued: {task.task_id} (queue_size={len(self.queue)})")

        return True, None

    def dequeue_next(self) -> Optional[Task]:
        """
        Dequeue the next task by priority and FIFO.
        Returns None if queue is empty.
        """
        if not self.queue:
            return None

        # Sort by priority (descending), then by creation time (ascending)
        self.queue.sort(
            key=lambda t: (-t.priority.value, t.created_at)
        )

        task = self.queue.pop(0)
        logger.info(f"Task dequeued: {task.task_id} (queue_size={len(self.queue)})")
        return task

    def get_task(self, task_id: str) -> Optional[Task]:
        """Look up a task by ID"""
        return self.task_index.get(task_id)

    def mark_completed(self, task_id: str) -> Tuple[bool, Optional[str]]:
        """Mark a task as completed"""
        task = self.task_index.get(task_id)
        if not task:
            return False, f"Task not found: {task_id}"

        task.status = TaskStatus.COMPLETED
        logger.info(f"Task marked completed: {task_id}")
        return True, None

    def mark_failed(self, task_id: str, error: str) -> Tuple[bool, Optional[str]]:
        """Mark a task as failed"""
        task = self.task_index.get(task_id)
        if not task:
            return False, f"Task not found: {task_id}"

        task.status = TaskStatus.FAILED
        task.retries += 1
        logger.error(f"Task marked failed: {task_id} (retries={task.retries}, error={error})")
        return True, None

    def queue_size(self) -> int:
        """Get current queue size"""
        return len(self.queue)

    def stats(self) -> Dict[str, Any]:
        """Get queue statistics"""
        return {
            "queue_size": len(self.queue),
            "total_tasks_seen": len(self.task_index),
            "queued": sum(1 for t in self.queue if t.status == TaskStatus.QUEUED),
            "routed": sum(1 for t in self.queue if t.status == TaskStatus.ROUTED),
            "assigned": sum(1 for t in self.queue if t.status == TaskStatus.ASSIGNED),
            "executing": sum(1 for t in self.queue if t.status == TaskStatus.EXECUTING),
        }

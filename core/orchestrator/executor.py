"""
B1 Execution Engine — Task Execution with Retry & State Management
Executes routed tasks with exponential backoff, state checkpointing, and error recovery.
ADR-0007 (multi-tenant), ADR-0296 (input validation), ADR-0256 (audit trail).
"""

from typing import Optional, Dict, Any, Tuple, List, Callable
from dataclasses import dataclass, field
from enum import Enum
import logging
import time
from datetime import datetime
import hashlib
import json
import copy

logger = logging.getLogger(__name__)


class ExecutionStatus(Enum):
    """Execution status"""
    PENDING = "pending"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    ROLLED_BACK = "rolled_back"


class ErrorClassification(Enum):
    """Error classification for retry strategy"""
    TRANSIENT = "transient"  # Network, timeout → retry with backoff
    PERMANENT = "permanent"  # Auth, invalid input → fail immediately
    UNKNOWN = "unknown"  # Unknown error → assume transient, log carefully


@dataclass
class ExecutionResult:
    """Result of task execution"""
    task_id: str
    tenant_id: str
    status: ExecutionStatus
    output: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    error_classification: Optional[ErrorClassification] = None
    latency_ms: float = 0.0
    retries_used: int = 0
    max_retries: int = 3
    executed_at: datetime = field(default_factory=datetime.utcnow)
    audit_hash: Optional[str] = None
    execution_trace: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert result to dict for serialization"""
        return {
            "task_id": self.task_id,
            "tenant_id": self.tenant_id,
            "status": self.status.value,
            "output": self.output,
            "error_message": self.error_message,
            "error_classification": self.error_classification.value if self.error_classification else None,
            "latency_ms": self.latency_ms,
            "retries_used": self.retries_used,
            "executed_at": self.executed_at.isoformat(),
            "audit_hash": self.audit_hash,
        }


@dataclass
class ExecutorState:
    """Represents executor state for checkpointing and rollback"""
    task_id: str
    tenant_id: str
    attempt: int = 0
    saved_state: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_checkpoint: Optional[datetime] = None

    def checkpoint(self, state_data: Dict[str, Any]) -> None:
        """Save current state to checkpoint"""
        self.saved_state = copy.deepcopy(state_data)
        self.last_checkpoint = datetime.utcnow()
        logger.debug(f"Checkpoint saved for {self.task_id} (attempt {self.attempt})")

    def rollback(self) -> Dict[str, Any]:
        """Restore state from checkpoint"""
        logger.info(f"Rolling back state for {self.task_id} to checkpoint")
        return copy.deepcopy(self.saved_state)


class Executor:
    """Executes tasks with retry logic, state management, and error recovery"""

    # Retry backoff configuration
    BACKOFF_MULTIPLIER = 2.0  # Exponential backoff: 2x per attempt
    INITIAL_BACKOFF_MS = 2000  # Start with 2 seconds
    MAX_BACKOFF_MS = 8000  # Cap at 8 seconds

    # Transient error patterns
    TRANSIENT_ERROR_PATTERNS = [
        "timeout", "connection", "network", "unreachable",
        "temporarily unavailable", "try again", "rate limit"
    ]

    # Permanent error patterns
    PERMANENT_ERROR_PATTERNS = [
        "unauthorized", "forbidden", "invalid", "malformed",
        "authentication", "bad request", "not found"
    ]

    def __init__(self, tenant_id: str, max_retries: int = 3):
        """Initialize executor for a tenant (ADR-0007)"""
        self.tenant_id = tenant_id
        self.max_retries = max_retries
        self.execution_states: Dict[str, ExecutorState] = {}
        self.execution_results: Dict[str, ExecutionResult] = {}
        logger.info(f"Executor initialized for tenant: {tenant_id} (max_retries={max_retries})")

    def execute(
        self,
        task_id: str,
        worker_type: str,
        input_data: Dict[str, Any],
        worker_callable: Optional[Callable] = None,
        timeout_secs: int = 300,
    ) -> ExecutionResult:
        """
        Execute a task on a worker with retry logic.

        Args:
            task_id: Unique task identifier
            worker_type: Type of worker (opus, sonnet, etc.)
            input_data: Input to pass to worker
            worker_callable: Optional callable to simulate worker execution
            timeout_secs: Execution timeout in seconds

        Returns:
            ExecutionResult with status, output, and audit information
        """
        # Validate tenant and task
        if not task_id or not worker_type or not input_data:
            return ExecutionResult(
                task_id=task_id or "unknown",
                tenant_id=self.tenant_id,
                status=ExecutionStatus.FAILED,
                error_message="Invalid input: task_id, worker_type, input_data required",
                error_classification=ErrorClassification.PERMANENT,
            )

        # Initialize execution state
        result = ExecutionResult(
            task_id=task_id,
            tenant_id=self.tenant_id,
            status=ExecutionStatus.PENDING,
            max_retries=self.max_retries,
        )

        executor_state = ExecutorState(task_id=task_id, tenant_id=self.tenant_id)
        self.execution_states[task_id] = executor_state

        # Save initial state checkpoint
        initial_state = {"input_data": copy.deepcopy(input_data)}
        executor_state.checkpoint(initial_state)

        start_time = time.time()
        result.execution_trace.append(f"[0s] Execution started (worker={worker_type}, timeout={timeout_secs}s)")

        # Retry loop
        for attempt in range(self.max_retries + 1):
            executor_state.attempt = attempt
            result.retries_used = attempt

            # Check timeout
            elapsed_ms = (time.time() - start_time) * 1000
            if elapsed_ms > timeout_secs * 1000:
                result.status = ExecutionStatus.TIMEOUT
                result.error_message = f"Execution timeout after {elapsed_ms:.0f}ms"
                result.latency_ms = elapsed_ms
                result.execution_trace.append(f"[{elapsed_ms/1000:.1f}s] TIMEOUT after {attempt} attempts")
                logger.warning(f"Task {task_id} timed out after {attempt} attempts")
                return result

            result.status = ExecutionStatus.EXECUTING
            result.execution_trace.append(f"[{elapsed_ms/1000:.1f}s] Attempt {attempt + 1} starting")

            try:
                # Execute task (simulated or real)
                if worker_callable:
                    output = worker_callable(task_id, worker_type, input_data)
                else:
                    output = self._simulate_worker_execution(task_id, worker_type, input_data)

                # Success
                result.status = ExecutionStatus.COMPLETED
                result.output = output
                result.latency_ms = (time.time() - start_time) * 1000
                result.execution_trace.append(f"[{result.latency_ms/1000:.1f}s] COMPLETED (attempt {attempt + 1})")
                logger.info(f"Task {task_id} completed successfully on attempt {attempt + 1} ({result.latency_ms:.1f}ms)")
                break

            except Exception as e:
                elapsed_ms = (time.time() - start_time) * 1000
                error_msg = str(e)
                error_class = self._classify_error(error_msg)
                result.error_classification = error_class
                result.error_message = error_msg
                result.execution_trace.append(f"[{elapsed_ms/1000:.1f}s] Error (attempt {attempt + 1}): {error_class.value} - {error_msg}")

                logger.warning(f"Task {task_id} failed on attempt {attempt + 1}: {error_class.value} - {error_msg}")

                # Rollback state on permanent error
                if error_class == ErrorClassification.PERMANENT:
                    result.status = ExecutionStatus.ROLLED_BACK
                    executor_state.rollback()
                    result.error_message = f"Permanent error, rolled back: {error_msg}"
                    result.latency_ms = (time.time() - start_time) * 1000
                    result.execution_trace.append(f"[{result.latency_ms/1000:.1f}s] ROLLED_BACK (permanent error)")
                    logger.error(f"Task {task_id} rolled back due to permanent error")
                    break

                # For transient errors, retry with backoff (unless last attempt)
                if attempt < self.max_retries:
                    backoff_ms = self._calculate_backoff(attempt)
                    elapsed_ms = (time.time() - start_time) * 1000
                    result.execution_trace.append(f"[{elapsed_ms/1000:.1f}s] Backing off {backoff_ms}ms before retry")
                    time.sleep(backoff_ms / 1000.0)
                else:
                    result.status = ExecutionStatus.FAILED
                    result.latency_ms = (time.time() - start_time) * 1000
                    result.execution_trace.append(f"[{result.latency_ms/1000:.1f}s] FAILED (max retries exceeded)")
                    logger.error(f"Task {task_id} failed after {self.max_retries + 1} attempts")

        # Compute audit hash
        result.audit_hash = self._compute_audit_hash(result)
        self.execution_results[task_id] = result

        return result

    def _simulate_worker_execution(
        self,
        task_id: str,
        worker_type: str,
        input_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Simulate worker execution.
        In production, this would call the actual worker service.
        """
        # Simple simulation: echo back input with worker type
        return {
            "task_id": task_id,
            "worker_type": worker_type,
            "processed_input": input_data,
            "timestamp": datetime.utcnow().isoformat(),
        }

    def _classify_error(self, error_msg: str) -> ErrorClassification:
        """Classify error as transient or permanent"""
        error_lower = error_msg.lower()

        # Check permanent patterns first (fail-fast)
        for pattern in self.PERMANENT_ERROR_PATTERNS:
            if pattern in error_lower:
                return ErrorClassification.PERMANENT

        # Check transient patterns
        for pattern in self.TRANSIENT_ERROR_PATTERNS:
            if pattern in error_lower:
                return ErrorClassification.TRANSIENT

        # Default to unknown (log carefully)
        logger.warning(f"Unknown error classification: {error_msg}")
        return ErrorClassification.UNKNOWN

    def _calculate_backoff(self, attempt: int) -> int:
        """
        Calculate exponential backoff in milliseconds.
        attempt 0 → 2s, attempt 1 → 4s, attempt 2 → 8s (capped)
        """
        backoff_ms = self.INITIAL_BACKOFF_MS * (self.BACKOFF_MULTIPLIER ** attempt)
        return min(int(backoff_ms), self.MAX_BACKOFF_MS)

    def _compute_audit_hash(self, result: ExecutionResult) -> str:
        """Compute audit hash for execution result"""
        audit_data = f"{result.task_id}:{result.tenant_id}:{result.status.value}:{result.latency_ms}:{result.executed_at.isoformat()}"
        return hashlib.sha256(audit_data.encode()).hexdigest()[:16]

    def get_result(self, task_id: str) -> Optional[ExecutionResult]:
        """Retrieve stored execution result"""
        return self.execution_results.get(task_id)

    def get_state(self, task_id: str) -> Optional[ExecutorState]:
        """Retrieve executor state for a task"""
        return self.execution_states.get(task_id)

"""
Tier 3: Brain Core — Decision engine, metrics collection, tier orchestration.

Responsibilities:
- Decide what task to execute next (simple heuristics in pilot)
- Collect metrics (latency, success rate, cost)
- Orchestrate full tier-1/2/3 pipeline
- Provide feedback for optimization (tier-4 learning, future)

Compliance:
- All decisions logged to audit trail
- Metrics anonymized (no PII)
- Deterministic (reproducible for testing)
"""

import json
import sqlite3
import time
from datetime import datetime
from typing import Any, Dict, Optional
from enum import Enum

try:
    from .tier2_task_engine import TaskType, TaskPayload, TaskStatus
    from .tier1_session import SessionStatus
except ImportError:
    from tier2_task_engine import TaskType, TaskPayload, TaskStatus
    from tier1_session import SessionStatus


class DecisionStrategy(str, Enum):
    """Decision-making strategies."""

    FIFO = "fifo"  # Execute tasks in order (pilot)
    PRIORITY = "priority"  # Execute by priority (future)
    ADAPTIVE = "adaptive"  # Learn from metrics (future)


class MetricsCollector:
    """Collect and aggregate metrics on task execution."""

    def __init__(self, db_conn: sqlite3.Connection):
        """Initialize metrics collector.

        Args:
            db_conn: SQLite connection (from bootstrap tier-0)
        """
        self.db = db_conn

    def record_metric(
        self, session_id: str, metric_name: str, value: float
    ):
        """Record single metric value.

        Args:
            session_id: Associated session
            metric_name: Metric name (e.g., 'task.latency_ms', 'task.success_rate')
            value: Metric value
        """
        from uuid import uuid4

        metric_id = str(uuid4())
        now = datetime.utcnow().isoformat() + "Z"

        self.db.execute(
            """
            INSERT INTO metrics (metric_id, session_id, timestamp, metric_name, value)
            VALUES (?, ?, ?, ?, ?)
            """,
            (metric_id, session_id, now, metric_name, value),
        )
        self.db.commit()

    def get_metric_summary(
        self, session_id: str, metric_name: str
    ) -> Dict[str, float]:
        """Get aggregated metric stats for session.

        Args:
            session_id: Session to query
            metric_name: Metric name

        Returns:
            Dict with min, max, avg, count
        """
        cursor = self.db.execute(
            """
            SELECT COUNT(*), MIN(value), MAX(value), AVG(value)
            FROM metrics
            WHERE session_id = ? AND metric_name = ?
            """,
            (session_id, metric_name),
        )
        row = cursor.fetchone()
        if not row or row[0] == 0:
            return {"count": 0, "min": None, "max": None, "avg": None}

        count, min_val, max_val, avg_val = row
        return {
            "count": int(count),
            "min": float(min_val),
            "max": float(max_val),
            "avg": float(avg_val),
        }

    def list_all_metrics(self, session_id: str) -> Dict[str, list]:
        """List all metrics collected for session.

        Returns:
            Dict mapping metric_name -> list of (timestamp, value) tuples
        """
        cursor = self.db.execute(
            """
            SELECT metric_name, timestamp, value
            FROM metrics
            WHERE session_id = ?
            ORDER BY timestamp ASC
            """,
            (session_id,),
        )
        metrics = {}
        for metric_name, timestamp, value in cursor.fetchall():
            if metric_name not in metrics:
                metrics[metric_name] = []
            metrics[metric_name].append((timestamp, float(value)))

        return metrics


class DecisionEngine:
    """Simple decision engine for task prioritization."""

    def __init__(self, strategy: DecisionStrategy = DecisionStrategy.FIFO):
        """Initialize decision engine.

        Args:
            strategy: Decision strategy (default FIFO for pilot)
        """
        self.strategy = strategy
        self.decisions_made = 0

    def decide(self, pending_tasks: list) -> Optional[str]:
        """Decide which task to execute next.

        Args:
            pending_tasks: List of (task_id, task_type, created_at) tuples

        Returns:
            task_id to execute, or None if no decision
        """
        if not pending_tasks:
            return None

        if self.strategy == DecisionStrategy.FIFO:
            # Execute oldest first (deterministic, reproducible)
            oldest = min(pending_tasks, key=lambda x: x[2])
            self.decisions_made += 1
            return oldest[0]

        # TODO: Implement PRIORITY and ADAPTIVE strategies
        return None


class BrainCore:
    """High-level orchestration of all tiers."""

    def __init__(
        self,
        task_engine,
        session_manager,
        audit_chain,
        strategy: DecisionStrategy = DecisionStrategy.FIFO,
    ):
        """Initialize brain core.

        Args:
            task_engine: TaskEngine instance (tier-2)
            session_manager: SessionManager instance (tier-1)
            audit_chain: AuditChain instance (tier-0)
            strategy: Decision strategy
        """
        self.task_engine = task_engine
        self.session_manager = session_manager
        self.audit = audit_chain
        self.decision_engine = DecisionEngine(strategy)
        self.metrics = None

    def set_metrics_collector(self, metrics_collector: MetricsCollector):
        """Inject metrics collector dependency.

        Args:
            metrics_collector: MetricsCollector instance
        """
        self.metrics = metrics_collector

    def submit_and_wait(
        self, session_id: str, payload: TaskPayload, timeout_seconds: float = 30.0
    ) -> Dict[str, Any]:
        """Submit task and wait for completion (blocking).

        Args:
            session_id: Associated session
            payload: TaskPayload
            timeout_seconds: Max time to wait

        Returns:
            Result dict with success and details

        Raises:
            TimeoutError: If task doesn't complete within timeout
        """
        # 1. Submit task
        task_id = self.task_engine.submit_task(session_id, payload)
        self.audit.write_event(
            event_type="brain.task_submitted",
            actor="brain_core",
            action="submit_and_wait",
            details={"task_id": task_id, "session_id": session_id},
        )

        # 2. Process tasks until complete
        start_time = time.time()
        while time.time() - start_time < timeout_seconds:
            self.task_engine.process_next_task()

            # Check if our task is done
            status = self.task_engine.get_task_status(task_id)
            if status in (TaskStatus.COMPLETED.value, TaskStatus.FAILED.value):
                result = self.task_engine.get_task_result(task_id)

                # Record metrics
                elapsed_ms = (time.time() - start_time) * 1000
                if self.metrics:
                    self.metrics.record_metric(
                        session_id, "task.latency_ms", elapsed_ms
                    )
                    success = result.get("success", False) if result else False
                    self.metrics.record_metric(
                        session_id,
                        "task.success_rate",
                        1.0 if success else 0.0,
                    )

                self.audit.write_event(
                    event_type="brain.task_completed",
                    actor="brain_core",
                    action="submit_and_wait",
                    details={
                        "task_id": task_id,
                        "session_id": session_id,
                        "latency_ms": elapsed_ms,
                        "success": result.get("success") if result else False,
                    },
                )

                return result or {"success": False, "task_id": task_id}

            time.sleep(0.01)  # Prevent busy-loop

        raise TimeoutError(
            f"Task {task_id} did not complete within {timeout_seconds}s"
        )

    def run_session(
        self, tasks: list, timeout_seconds: float = 60.0
    ) -> Dict[str, Any]:
        """Run complete session with multiple tasks.

        Args:
            tasks: List of TaskPayload objects
            timeout_seconds: Session timeout

        Returns:
            Session result with summary statistics
        """
        # 1. Begin session
        session_id = self.session_manager.begin_session(
            details={"task_count": len(tasks), "strategy": self.decision_engine.strategy.value}
        )
        self.session_manager.activate_session(session_id)

        # 2. Submit all tasks
        task_ids = []
        for task_payload in tasks:
            task_id = self.task_engine.submit_task(session_id, task_payload)
            task_ids.append(task_id)

        # 3. Process all tasks
        start_time = time.time()
        results = {}
        while time.time() - start_time < timeout_seconds:
            processed_id = self.task_engine.process_next_task()
            if processed_id:
                result = self.task_engine.get_task_result(processed_id)
                results[processed_id] = result

            # Check if all tasks done
            all_done = all(
                self.task_engine.get_task_status(tid) in (
                    TaskStatus.COMPLETED.value,
                    TaskStatus.FAILED.value,
                )
                for tid in task_ids
            )
            if all_done:
                break

            time.sleep(0.01)

        # 4. Collect session metrics
        elapsed = time.time() - start_time
        success_count = sum(
            1 for r in results.values() if r and r.get("success")
        )
        failed_count = len(results) - success_count

        session_summary = {
            "session_id": session_id,
            "task_count": len(tasks),
            "completed_count": len(results),
            "success_count": success_count,
            "failed_count": failed_count,
            "latency_seconds": elapsed,
            "success_rate": success_count / len(tasks) if tasks else 0.0,
        }

        # 5. Record metrics
        if self.metrics:
            self.metrics.record_metric(session_id, "session.latency_s", elapsed)
            self.metrics.record_metric(
                session_id,
                "session.success_rate",
                session_summary["success_rate"],
            )

        # 6. End session
        final_status = SessionStatus.COMPLETED if failed_count == 0 else SessionStatus.FAILED
        self.session_manager.end_session(session_id, status=final_status)

        self.audit.write_event(
            event_type="brain.session_completed",
            actor="brain_core",
            action="run_session",
            details=session_summary,
        )

        return session_summary

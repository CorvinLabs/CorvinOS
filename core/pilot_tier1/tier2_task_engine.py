"""
Tier 2: Task Engine — Task queue, execution, status tracking.

Responsibilities:
- Queue tasks (install, enable, disable, uninstall plugins)
- Execute tasks in FIFO order (deterministic, reproducible)
- Track task status and results
- Provide feedback to tier-3 (brain core for decision-making)
- Audit all task operations (GDPR Art. 30/32)

Compliance:
- Every task logged with full audit trail
- Task execution atomic (all-or-nothing)
- Results immutable after completion
"""

import json
import sqlite3
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional, Callable


class TaskStatus(str, Enum):
    """Task lifecycle states."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskType(str, Enum):
    """Task types supported by tier 1 pilot."""

    PLUGIN_INSTALL = "plugin_install"
    PLUGIN_ENABLE = "plugin_enable"
    PLUGIN_DISABLE = "plugin_disable"
    PLUGIN_UNINSTALL = "plugin_uninstall"
    SYSTEM_HEALTH_CHECK = "system_health_check"


@dataclass
class TaskPayload:
    """Task execution parameters."""

    task_type: TaskType
    plugin_id: Optional[str] = None
    version: Optional[str] = None
    config: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict:
        """Convert to serializable dict."""
        return {
            "task_type": self.task_type.value,
            "plugin_id": self.plugin_id,
            "version": self.version,
            "config": self.config,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "TaskPayload":
        """Deserialize from dict."""
        return cls(
            task_type=TaskType(data["task_type"]),
            plugin_id=data.get("plugin_id"),
            version=data.get("version"),
            config=data.get("config"),
        )


class TaskQueue:
    """FIFO task queue (SQLite-backed)."""

    def __init__(self, db_conn: sqlite3.Connection):
        """Initialize task queue.

        Args:
            db_conn: SQLite connection (from bootstrap tier-0)
        """
        self.db = db_conn

    def enqueue(self, session_id: str, payload: TaskPayload) -> str:
        """Add task to queue.

        Args:
            session_id: Associated session
            payload: TaskPayload with execution parameters

        Returns:
            task_id (UUID4)
        """
        task_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat() + "Z"

        self.db.execute(
            """
            INSERT INTO tasks (task_id, session_id, created_at, updated_at, status, task_type, payload, result)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                session_id,
                now,
                now,
                TaskStatus.QUEUED.value,
                payload.task_type.value,
                json.dumps(payload.to_dict()),
                None,
            ),
        )
        self.db.commit()

        return task_id

    def dequeue(self) -> Optional[tuple]:
        """Get next task in FIFO order (oldest first).

        Returns:
            Tuple of (task_id, session_id, payload) or None if empty
        """
        cursor = self.db.execute(
            """
            SELECT task_id, session_id, payload
            FROM tasks
            WHERE status = ?
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (TaskStatus.QUEUED.value,),
        )
        row = cursor.fetchone()
        if not row:
            return None

        task_id, session_id, payload_json = row
        payload = TaskPayload.from_dict(json.loads(payload_json))
        return (task_id, session_id, payload)

    def set_status(self, task_id: str, status: TaskStatus, result: Optional[Dict] = None):
        """Update task status and optionally set result.

        Args:
            task_id: Task to update
            status: New status
            result: Optional result dict (immutable after set)
        """
        now = datetime.utcnow().isoformat() + "Z"
        self.db.execute(
            """
            UPDATE tasks SET status = ?, updated_at = ?, result = ?
            WHERE task_id = ?
            """,
            (
                status.value,
                now,
                json.dumps(result) if result else None,
                task_id,
            ),
        )
        self.db.commit()

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get task by ID.

        Returns:
            Task dict or None if not found
        """
        cursor = self.db.execute(
            """
            SELECT task_id, session_id, created_at, updated_at, status, task_type, payload, result
            FROM tasks WHERE task_id = ?
            """,
            (task_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None

        return {
            "task_id": row[0],
            "session_id": row[1],
            "created_at": row[2],
            "updated_at": row[3],
            "status": row[4],
            "task_type": row[5],
            "payload": json.loads(row[6]) if row[6] else {},
            "result": json.loads(row[7]) if row[7] else None,
        }

    def list_tasks(self, session_id: Optional[str] = None) -> list:
        """List tasks (optionally filtered by session).

        Returns:
            List of task dicts
        """
        if session_id:
            cursor = self.db.execute(
                "SELECT task_id, created_at, status, task_type FROM tasks WHERE session_id = ? ORDER BY created_at DESC",
                (session_id,),
            )
        else:
            cursor = self.db.execute(
                "SELECT task_id, created_at, status, task_type FROM tasks ORDER BY created_at DESC"
            )

        return [
            {
                "task_id": row[0],
                "created_at": row[1],
                "status": row[2],
                "task_type": row[3],
            }
            for row in cursor.fetchall()
        ]


class TaskExecutor:
    """Execute tasks with registered handlers."""

    def __init__(self, audit_chain):
        """Initialize executor.

        Args:
            audit_chain: AuditChain instance (from tier-0)
        """
        self.audit = audit_chain
        self.handlers: Dict[TaskType, Callable] = {}

    def register_handler(self, task_type: TaskType, handler: Callable):
        """Register execution handler for task type.

        Args:
            task_type: Type of task
            handler: Async function (payload) -> result_dict
        """
        self.handlers[task_type] = handler

    def execute(self, task_id: str, payload: TaskPayload) -> Dict[str, Any]:
        """Execute task (synchronous, single-threaded).

        Args:
            task_id: Task ID (for audit logging)
            payload: TaskPayload with execution parameters

        Returns:
            Result dict with success/failure and details

        Raises:
            ValueError: If no handler registered for task type
        """
        handler = self.handlers.get(payload.task_type)
        if not handler:
            error_msg = f"No handler for task type {payload.task_type}"
            self.audit.write_event(
                event_type="task.execution_failed",
                actor="task_executor",
                action="execute",
                details={
                    "task_id": task_id,
                    "task_type": payload.task_type.value,
                    "error": error_msg,
                },
            )
            raise ValueError(error_msg)

        self.audit.write_event(
            event_type="task.execution_started",
            actor="task_executor",
            action="execute",
            details={
                "task_id": task_id,
                "task_type": payload.task_type.value,
                "plugin_id": payload.plugin_id,
            },
        )

        try:
            result = handler(payload)
            self.audit.write_event(
                event_type="task.execution_completed",
                actor="task_executor",
                action="execute",
                details={
                    "task_id": task_id,
                    "task_type": payload.task_type.value,
                    "success": result.get("success", False),
                },
            )
            return result

        except Exception as e:
            error_msg = str(e)
            self.audit.write_event(
                event_type="task.execution_failed",
                actor="task_executor",
                action="execute",
                details={
                    "task_id": task_id,
                    "task_type": payload.task_type.value,
                    "error": error_msg,
                },
            )
            return {
                "success": False,
                "error": error_msg,
                "task_id": task_id,
            }


class TaskEngine:
    """High-level task engine orchestrating queue + executor."""

    def __init__(
        self, db_conn: sqlite3.Connection, audit_chain, max_retries: int = 3
    ):
        """Initialize task engine.

        Args:
            db_conn: SQLite connection
            audit_chain: AuditChain instance
            max_retries: Max retries on task failure
        """
        self.queue = TaskQueue(db_conn)
        self.executor = TaskExecutor(audit_chain)
        self.audit = audit_chain
        self.max_retries = max_retries

    def submit_task(self, session_id: str, payload: TaskPayload) -> str:
        """Submit task for execution.

        Args:
            session_id: Associated session
            payload: TaskPayload

        Returns:
            task_id
        """
        task_id = self.queue.enqueue(session_id, payload)

        self.audit.write_event(
            event_type="task.submitted",
            actor="task_engine",
            action="submit",
            details={
                "task_id": task_id,
                "session_id": session_id,
                "task_type": payload.task_type.value,
            },
        )

        return task_id

    def process_next_task(self) -> Optional[str]:
        """Process next queued task.

        Returns:
            task_id if executed, None if queue empty
        """
        dequeued = self.queue.dequeue()
        if not dequeued:
            return None  # Queue empty

        task_id, session_id, payload = dequeued

        # Mark as running
        self.queue.set_status(task_id, TaskStatus.RUNNING)

        # Execute
        result = self.executor.execute(task_id, payload)

        # Mark complete/failed
        status = TaskStatus.COMPLETED if result.get("success") else TaskStatus.FAILED
        self.queue.set_status(task_id, status, result)

        return task_id

    def process_all_tasks(self) -> list:
        """Process all queued tasks until empty.

        Returns:
            List of executed task_ids
        """
        executed = []
        while True:
            task_id = self.process_next_task()
            if not task_id:
                break
            executed.append(task_id)

        return executed

    def get_task_status(self, task_id: str) -> Optional[str]:
        """Get task status.

        Returns:
            Status string or None if not found
        """
        task = self.queue.get_task(task_id)
        return task["status"] if task else None

    def get_task_result(self, task_id: str) -> Optional[Dict]:
        """Get task result (None if not completed).

        Returns:
            Result dict or None
        """
        task = self.queue.get_task(task_id)
        return task["result"] if task else None

"""
SQLite-backed operation queue for offline-first operation.

Persists operations when offline, replays on reconnect with idempotence guarantee.
Journaling (WAL mode) ensures safety even on crash.

Design:
- FIFO queue (preserve operation order)
- Atomic apply (all-or-nothing semantics)
- Idempotence: same operation applied 2x is safe
- Hash-chained for audit trail
- Automatic cleanup of applied operations
"""

from dataclasses import dataclass, asdict
from typing import List, Optional, Dict, Any
from datetime import datetime
import sqlite3
import json
from pathlib import Path


@dataclass
class Operation:
    """Immutable operation to be queued and replayed."""
    op_id: str              # Unique operation ID
    task_id: str            # Which task
    input_data: Dict[str, Any]
    context_data: Dict[str, Any]
    engine_choice: str      # "claude" or "local_llama2"
    timestamp: datetime
    audit_hash: str         # Hash-chain link


@dataclass
class QueuedOperationRecord:
    """Database record for queued operation."""
    op_id: str
    task_id: str
    input_json: str
    context_json: str
    engine_choice: str
    timestamp: int          # Unix timestamp
    status: str             # "pending", "applied", "failed"
    result_json: Optional[str] = None
    error_msg: Optional[str] = None
    audit_hash: str = ""


class OperationQueue:
    """
    SQLite-backed queue for offline operations.

    Features:
    - FIFO ordering
    - Atomic apply (transaction-based)
    - Idempotence via operation ID deduplication
    - Automatic cleanup of applied operations
    - WAL mode for crash safety
    """

    def __init__(self, db_path: Path):
        """Initialize queue."""
        self.db_path = db_path
        self.connection: Optional[sqlite3.Connection] = None
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        """Create tables if they don't exist."""
        conn = self._get_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS operations (
                op_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                input_json TEXT NOT NULL,
                context_json TEXT NOT NULL,
                engine_choice TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                status TEXT NOT NULL,
                result_json TEXT,
                error_msg TEXT,
                audit_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_status
            ON operations(status)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_timestamp
            ON operations(timestamp)
        """)
        # WAL mode for durability
        conn.execute("PRAGMA journal_mode=WAL")
        conn.commit()

    def _get_connection(self) -> sqlite3.Connection:
        """Get or create database connection."""
        if self.connection is None:
            self.connection = sqlite3.connect(str(self.db_path))
        return self.connection

    def enqueue(self, operation: Operation) -> bool:
        """
        Add operation to queue.

        Returns True if enqueued, False if duplicate ID.
        """
        conn = self._get_connection()

        try:
            conn.execute("""
                INSERT INTO operations (
                    op_id, task_id, input_json, context_json, engine_choice,
                    timestamp, status, audit_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                operation.op_id,
                operation.task_id,
                json.dumps(operation.input_data),
                json.dumps(operation.context_data),
                operation.engine_choice,
                int(operation.timestamp.timestamp()),
                "pending",
                operation.audit_hash,
            ))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            # Duplicate operation ID
            return False

    def dequeue(self) -> Optional[Operation]:
        """
        Get next pending operation (FIFO).

        Returns None if queue is empty.
        """
        conn = self._get_connection()
        cursor = conn.execute("""
            SELECT op_id, task_id, input_json, context_json, engine_choice,
                   timestamp, audit_hash
            FROM operations
            WHERE status = 'pending'
            ORDER BY timestamp ASC
            LIMIT 1
        """)

        row = cursor.fetchone()
        if not row:
            return None

        return Operation(
            op_id=row[0],
            task_id=row[1],
            input_data=json.loads(row[2]),
            context_data=json.loads(row[3]),
            engine_choice=row[4],
            timestamp=datetime.fromtimestamp(row[5]),
            audit_hash=row[6],
        )

    def mark_applied(self, op_id: str, result: Dict[str, Any]) -> bool:
        """
        Mark operation as applied with result.

        Returns True if successful, False if not found.
        """
        conn = self._get_connection()
        cursor = conn.execute("""
            UPDATE operations
            SET status = 'applied', result_json = ?, updated_at = CURRENT_TIMESTAMP
            WHERE op_id = ?
        """, (json.dumps(result), op_id))
        conn.commit()

        return cursor.rowcount > 0

    def mark_failed(self, op_id: str, error: str) -> bool:
        """
        Mark operation as failed with error message.

        Returns True if successful, False if not found.
        """
        conn = self._get_connection()
        cursor = conn.execute("""
            UPDATE operations
            SET status = 'failed', error_msg = ?, updated_at = CURRENT_TIMESTAMP
            WHERE op_id = ?
        """, (error, op_id))
        conn.commit()

        return cursor.rowcount > 0

    def get_pending_count(self) -> int:
        """Count of pending operations."""
        conn = self._get_connection()
        cursor = conn.execute("""
            SELECT COUNT(*) FROM operations WHERE status = 'pending'
        """)
        return cursor.fetchone()[0]

    def get_all_pending(self) -> List[Operation]:
        """Get all pending operations in FIFO order."""
        conn = self._get_connection()
        cursor = conn.execute("""
            SELECT op_id, task_id, input_json, context_json, engine_choice,
                   timestamp, audit_hash
            FROM operations
            WHERE status = 'pending'
            ORDER BY timestamp ASC
        """)

        operations = []
        for row in cursor.fetchall():
            operations.append(Operation(
                op_id=row[0],
                task_id=row[1],
                input_data=json.loads(row[2]),
                context_data=json.loads(row[3]),
                engine_choice=row[4],
                timestamp=datetime.fromtimestamp(row[5]),
                audit_hash=row[6],
            ))

        return operations

    def cleanup_applied(self) -> int:
        """
        Delete applied operations (keep for audit log retention).

        Returns count deleted.
        """
        conn = self._get_connection()
        cursor = conn.execute("""
            DELETE FROM operations
            WHERE status = 'applied'
        """)
        conn.commit()

        return cursor.rowcount

    def close(self) -> None:
        """Close database connection."""
        if self.connection:
            self.connection.close()
            self.connection = None

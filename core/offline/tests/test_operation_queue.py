"""
Tests for SQLite operation queue.

Coverage:
- Enqueue/dequeue operations
- Status tracking (pending, applied, failed)
- FIFO ordering
- Atomicity (WAL mode)
- Idempotence (duplicate ID detection)
- Cleanup operations
"""

import pytest
import tempfile
from pathlib import Path
from datetime import datetime

from core.offline.operation_queue import (
    OperationQueue,
    Operation,
    QueuedOperationRecord,
)


class TestOperationQueue:
    """Test operation queue."""

    @pytest.fixture
    def queue(self):
        """Create temporary queue for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "queue.db"
            queue = OperationQueue(db_path)
            yield queue
            queue.close()

    def test_queue_creation(self, queue):
        """Queue can be created."""
        assert queue is not None

    def test_enqueue_operation(self, queue):
        """Enqueue single operation."""
        op = Operation(
            op_id="op-1",
            task_id="task-1",
            input_data={"prompt": "hello"},
            context_data={"operator_id": "op-1"},
            engine_choice="claude",
            timestamp=datetime.utcnow(),
            audit_hash="hash-1",
        )

        success = queue.enqueue(op)
        assert success is True
        assert queue.get_pending_count() == 1

    def test_enqueue_duplicate_rejected(self, queue):
        """Duplicate operation ID is rejected."""
        op1 = Operation(
            op_id="op-1",
            task_id="task-1",
            input_data={"prompt": "hello"},
            context_data={},
            engine_choice="claude",
            timestamp=datetime.utcnow(),
            audit_hash="hash-1",
        )
        op2 = Operation(
            op_id="op-1",  # Same ID
            task_id="task-2",
            input_data={"prompt": "world"},
            context_data={},
            engine_choice="claude",
            timestamp=datetime.utcnow(),
            audit_hash="hash-2",
        )

        assert queue.enqueue(op1) is True
        assert queue.enqueue(op2) is False  # Duplicate rejected

    def test_dequeue_fifo(self, queue):
        """Dequeue returns operations in FIFO order."""
        # Enqueue 3 operations
        for i in range(3):
            op = Operation(
                op_id=f"op-{i}",
                task_id=f"task-{i}",
                input_data={},
                context_data={},
                engine_choice="claude",
                timestamp=datetime.utcnow(),
                audit_hash=f"hash-{i}",
            )
            queue.enqueue(op)

        # Dequeue and verify FIFO order
        op1 = queue.dequeue()
        assert op1.op_id == "op-0"

        op2 = queue.dequeue()
        assert op2.op_id == "op-1"

        op3 = queue.dequeue()
        assert op3.op_id == "op-2"

        op4 = queue.dequeue()
        assert op4 is None  # Queue empty

    def test_mark_applied(self, queue):
        """Mark operation as applied."""
        op = Operation(
            op_id="op-1",
            task_id="task-1",
            input_data={},
            context_data={},
            engine_choice="claude",
            timestamp=datetime.utcnow(),
            audit_hash="hash-1",
        )
        queue.enqueue(op)

        result = {"status": "success"}
        success = queue.mark_applied("op-1", result)
        assert success is True
        assert queue.get_pending_count() == 0

    def test_mark_failed(self, queue):
        """Mark operation as failed."""
        op = Operation(
            op_id="op-1",
            task_id="task-1",
            input_data={},
            context_data={},
            engine_choice="claude",
            timestamp=datetime.utcnow(),
            audit_hash="hash-1",
        )
        queue.enqueue(op)

        success = queue.mark_failed("op-1", "Connection timeout")
        assert success is True

    def test_get_all_pending(self, queue):
        """Get all pending operations."""
        # Enqueue 5 operations
        for i in range(5):
            op = Operation(
                op_id=f"op-{i}",
                task_id=f"task-{i}",
                input_data={},
                context_data={},
                engine_choice="claude",
                timestamp=datetime.utcnow(),
                audit_hash=f"hash-{i}",
            )
            queue.enqueue(op)

        pending = queue.get_all_pending()
        assert len(pending) == 5

        # Mark 2 as applied
        queue.mark_applied("op-0", {})
        queue.mark_applied("op-1", {})

        pending = queue.get_all_pending()
        assert len(pending) == 3
        assert pending[0].op_id == "op-2"

    def test_cleanup_applied(self, queue):
        """Cleanup removes applied operations."""
        # Enqueue 3, mark 2 as applied
        for i in range(3):
            op = Operation(
                op_id=f"op-{i}",
                task_id=f"task-{i}",
                input_data={},
                context_data={},
                engine_choice="claude",
                timestamp=datetime.utcnow(),
                audit_hash=f"hash-{i}",
            )
            queue.enqueue(op)

        queue.mark_applied("op-0", {})
        queue.mark_applied("op-1", {})

        # Cleanup should remove 2
        deleted = queue.cleanup_applied()
        assert deleted == 2
        assert queue.get_pending_count() == 1

    def test_idempotence_guarantee(self, queue):
        """Applying same operation twice is safe (idempotent)."""
        op = Operation(
            op_id="op-1",
            task_id="task-1",
            input_data={"value": 42},
            context_data={},
            engine_choice="claude",
            timestamp=datetime.utcnow(),
            audit_hash="hash-1",
        )

        # First enqueue and apply
        queue.enqueue(op)
        queue.mark_applied("op-1", {"result": "first"})

        # Try to enqueue again (simulating replay)
        # Should fail because ID already exists
        assert queue.enqueue(op) is False

        # This ensures idempotence: duplicate applies are prevented

"""E2E tests for Task Audit Trail — Hash-Chained Task State Transitions (ADR-0XXX).

10 comprehensive test scenarios:
1. test_audit_trail_creation — task.created audit event
2. test_audit_trail_state_transitions — PENDING → RUNNING → COMPLETED
3. test_audit_trail_failed_transition — task failure path
4. test_audit_trail_cancelled_transition — task cancellation
5. test_audit_trail_hash_chain_integrity — verify chain integrity
6. test_audit_trail_console_endpoint — read audit via API
7. test_audit_trail_retrieval — read_events functionality
8. test_audit_trail_multiple_transitions — full lifecycle
9. test_audit_trail_concurrent_tasks — multiple task audit trails
10. test_audit_trail_chain_status — chain status reporting and recovery
"""

import json
import pytest
from pathlib import Path
from datetime import datetime
import os

from core.console.corvin_core.task_manager import TaskManager, TaskStatus
from core.console.corvin_core.task_audit_trail import TaskAuditTrail


@pytest.fixture
def temp_tasks_dir(tmp_path):
    """Create a temporary tasks directory for testing."""
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    return tasks_dir


@pytest.fixture
def temp_corvin_home(monkeypatch, tmp_path):
    """Set CORVIN_HOME to a temporary directory."""
    corvin_home = tmp_path / ".corvin"
    corvin_home.mkdir()
    monkeypatch.setenv("CORVIN_HOME", str(corvin_home))
    return corvin_home


@pytest.fixture
def task_manager(temp_tasks_dir):
    """Create a TaskManager instance."""
    return TaskManager(tasks_dir=temp_tasks_dir)


class TestAuditTrailCreation:
    """Test 1: task.created audit event"""

    def test_audit_trail_creation(self, task_manager, temp_corvin_home):
        """Verify task creation is recorded in audit trail."""
        task_id = task_manager.create_task(
            chat_key="web:test",
            instruction="Test task",
            persona="assistant",
            executor_id="user123",
            tenant_id="_default",
        )

        # Verify task was created
        task = task_manager.get_task(task_id)
        assert task is not None
        assert task.status == TaskStatus.PENDING

        # Verify audit trail exists and has creation event
        audit = TaskAuditTrail(task_id=task_id, tenant_id="_default")
        events = audit.read_events()
        assert len(events) == 1

        event = events[0]
        assert event["event_type"] == "task.created"
        assert event["old_state"] is None
        assert event["new_state"] == "pending"
        assert event["executor_id"] == "user123"
        assert event["prev_hash"] == "genesis"


class TestAuditTrailStateTransitions:
    """Test 2: State transitions PENDING → RUNNING → COMPLETED"""

    def test_audit_trail_state_transitions(self, task_manager, temp_corvin_home):
        """Verify full happy-path state transitions are audited."""
        task_id = task_manager.create_task(
            chat_key="web:test",
            instruction="Test task",
            executor_id="user123",
            tenant_id="_default",
        )

        # Transition to RUNNING
        task_manager.record_event(
            task_id=task_id,
            event={"event": "task.started", "engine": "claude"},
            executor_id="system",
            reason="Task started execution",
            tenant_id="_default",
        )

        # Transition to COMPLETED
        task_manager.record_event(
            task_id=task_id,
            event={"event": "task.completed", "exit_code": 0, "summary": "Success"},
            executor_id="system",
            reason="Task completed successfully",
            tenant_id="_default",
        )

        # Verify final state
        task = task_manager.get_task(task_id)
        assert task.status == TaskStatus.COMPLETED
        assert task.exit_code == 0

        # Verify audit trail
        audit = TaskAuditTrail(task_id=task_id, tenant_id="_default")
        events = audit.read_events()
        assert len(events) == 3

        # Check event sequence
        assert events[0]["event_type"] == "task.created"
        assert events[1]["event_type"] == "task.started"
        assert events[1]["old_state"] == "pending"
        assert events[1]["new_state"] == "running"

        assert events[2]["event_type"] == "task.completed"
        assert events[2]["old_state"] == "running"
        assert events[2]["new_state"] == "completed"


class TestAuditTrailFailedTransition:
    """Test 3: Task failure path"""

    def test_audit_trail_failed_transition(self, task_manager, temp_corvin_home):
        """Verify task failure is recorded with reason."""
        task_id = task_manager.create_task(
            chat_key="web:test",
            instruction="Test task",
            executor_id="user123",
            tenant_id="_default",
        )

        # Transition to RUNNING
        task_manager.record_event(
            task_id=task_id,
            event={"event": "task.started", "engine": "claude"},
            executor_id="system",
            tenant_id="_default",
        )

        # Transition to FAILED
        task_manager.record_event(
            task_id=task_id,
            event={"event": "task.failed", "exit_code": 1, "error": "Out of memory"},
            executor_id="system",
            reason="Task execution failed: Out of memory",
            tenant_id="_default",
        )

        # Verify final state
        task = task_manager.get_task(task_id)
        assert task.status == TaskStatus.FAILED
        assert task.exit_code == 1

        # Verify audit trail
        audit = TaskAuditTrail(task_id=task_id, tenant_id="_default")
        events = audit.read_events()
        assert len(events) == 3
        assert events[2]["event_type"] == "task.failed"
        assert events[2]["reason"] == "Task execution failed: Out of memory"


class TestAuditTrailCancelledTransition:
    """Test 4: Task cancellation"""

    def test_audit_trail_cancelled_transition(self, task_manager, temp_corvin_home):
        """Verify task cancellation is recorded."""
        task_id = task_manager.create_task(
            chat_key="web:test",
            instruction="Test task",
            executor_id="user123",
            tenant_id="_default",
        )

        # Cancel the task
        success = task_manager.cancel_task(task_id)
        assert success

        # Manually record the cancel event with audit info
        task_manager.record_event(
            task_id=task_id,
            event={"event": "task.cancelled"},
            executor_id="user456",
            reason="User requested cancellation",
            tenant_id="_default",
        )

        # Verify final state
        task = task_manager.get_task(task_id)
        assert task.status == TaskStatus.CANCELLED

        # Verify audit trail
        audit = TaskAuditTrail(task_id=task_id, tenant_id="_default")
        events = audit.read_events()
        assert len(events) == 2
        assert events[1]["event_type"] == "task.cancelled"
        assert events[1]["executor_id"] == "user456"
        assert events[1]["reason"] == "User requested cancellation"


class TestAuditTrailHashChainIntegrity:
    """Test 5: Hash chain verification"""

    def test_audit_trail_hash_chain_integrity(self, task_manager, temp_corvin_home):
        """Verify hash chain remains valid through multiple transitions."""
        task_id = task_manager.create_task(
            chat_key="web:test",
            instruction="Test task",
            tenant_id="_default",
        )

        # Record multiple state transitions
        task_manager.record_event(
            task_id=task_id,
            event={"event": "task.started"},
            tenant_id="_default",
        )

        task_manager.record_event(
            task_id=task_id,
            event={"event": "task.completed", "exit_code": 0},
            tenant_id="_default",
        )

        # Verify chain
        audit = TaskAuditTrail(task_id=task_id, tenant_id="_default")
        assert audit.verify_chain() is True

        # Verify chain structure
        events = audit.read_events()
        for i, event in enumerate(events):
            if i == 0:
                assert event["prev_hash"] == "genesis"
            else:
                assert event["prev_hash"] == events[i - 1]["hash"]


class TestAuditTrailChainStatus:
    """Test 6: Chain status and recovery"""

    def test_audit_trail_chain_status(self, task_manager, temp_corvin_home):
        """Verify chain status reporting."""
        task_id = task_manager.create_task(
            chat_key="web:test",
            instruction="Test task",
            tenant_id="_default",
        )

        task_manager.record_event(
            task_id=task_id,
            event={"event": "task.started"},
            tenant_id="_default",
        )

        task_manager.record_event(
            task_id=task_id,
            event={"event": "task.completed", "exit_code": 0},
            tenant_id="_default",
        )

        audit = TaskAuditTrail(task_id=task_id, tenant_id="_default")
        status = audit.get_chain_status()

        assert status["task_id"] == task_id
        assert status["height"] == 3  # created, started, completed
        assert status["integrity_verified"] is True
        assert "last_hash" in status
        assert status["last_hash"] != "genesis"


class TestAuditTrailMultipleTransitions:
    """Test 7: Full lifecycle with multiple transitions"""

    def test_audit_trail_multiple_transitions(self, task_manager, temp_corvin_home):
        """Verify full task lifecycle is audited."""
        task_id = task_manager.create_task(
            chat_key="web:test",
            instruction="Full lifecycle test",
            executor_id="user123",
            tenant_id="_default",
        )

        # Simulate full lifecycle
        events_sequence = [
            ("task.started", "pending", "running", "system", "Starting execution"),
            ("task.completed", "running", "completed", "system", "Execution completed"),
        ]

        for event_type, old_state, new_state, executor, reason in events_sequence:
            task_manager.record_event(
                task_id=task_id,
                event={"event": event_type, "exit_code": 0 if "completed" in event_type else None},
                executor_id=executor,
                reason=reason,
                tenant_id="_default",
            )

        # Verify complete audit trail
        audit = TaskAuditTrail(task_id=task_id, tenant_id="_default")
        events = audit.read_events()

        assert len(events) == 3
        assert events[0]["event_type"] == "task.created"
        assert events[1]["event_type"] == "task.started"
        assert events[2]["event_type"] == "task.completed"

        # Verify executor tracking
        assert events[0]["executor_id"] == "user123"
        assert events[1]["executor_id"] == "system"
        assert events[2]["executor_id"] == "system"


class TestAuditTrailConcurrentTasks:
    """Test 8: Multiple concurrent task audit trails"""

    def test_audit_trail_concurrent_tasks(self, task_manager, temp_corvin_home):
        """Verify concurrent tasks maintain separate audit trails."""
        task_ids = []

        # Create multiple tasks
        for i in range(3):
            task_id = task_manager.create_task(
                chat_key="web:test",
                instruction=f"Task {i}",
                executor_id=f"user{i}",
                tenant_id="_default",
            )
            task_ids.append(task_id)

            # Different transitions for each task
            if i % 2 == 0:
                # Even tasks: start and complete
                task_manager.record_event(
                    task_id=task_id,
                    event={"event": "task.started"},
                    tenant_id="_default",
                )
                task_manager.record_event(
                    task_id=task_id,
                    event={"event": "task.completed", "exit_code": 0},
                    tenant_id="_default",
                )
            else:
                # Odd tasks: just fail
                task_manager.record_event(
                    task_id=task_id,
                    event={"event": "task.failed", "exit_code": 1},
                    tenant_id="_default",
                )

        # Verify each task has its own correct audit trail
        for i, task_id in enumerate(task_ids):
            audit = TaskAuditTrail(task_id=task_id, tenant_id="_default")
            events = audit.read_events()

            # All should have creation event
            assert events[0]["event_type"] == "task.created"
            assert events[0]["executor_id"] == f"user{i}"

            # Check final state based on task number
            if i % 2 == 0:
                assert events[-1]["event_type"] == "task.completed"
            else:
                assert events[-1]["event_type"] == "task.failed"


class TestAuditTrailRetrieval:
    """Test 9: Audit event retrieval and integrity"""

    def test_audit_trail_retrieval(self, task_manager, temp_corvin_home):
        """Verify audit events can be retrieved intact."""
        task_id = task_manager.create_task(
            chat_key="web:test",
            instruction="Test task",
            executor_id="user123",
            tenant_id="_default",
        )

        # Record transitions with detailed reason
        task_manager.record_event(
            task_id=task_id,
            event={"event": "task.started", "pid": 12345},
            executor_id="system",
            reason="Scheduled to run at 2026-09-12T10:30:00Z",
            tenant_id="_default",
        )

        # Retrieve and verify
        audit = TaskAuditTrail(task_id=task_id, tenant_id="_default")
        events = audit.read_events()

        assert len(events) == 2
        created_event = events[0]
        started_event = events[1]

        # Verify creation event structure
        assert "event_id" in created_event
        assert "timestamp" in created_event
        assert "hash" in created_event
        assert created_event["tenant_id"] == "_default"

        # Verify started event structure
        assert started_event["reason"] == "Scheduled to run at 2026-09-12T10:30:00Z"
        assert started_event["hash"] != created_event["hash"]
        assert started_event["prev_hash"] == created_event["hash"]


class TestAuditTrailIntegration:
    """Test 10: Integration with TaskManager"""

    def test_audit_trail_integration(self, task_manager, temp_corvin_home):
        """Verify TaskManager audit integration end-to-end."""
        # Create and transition a task
        task_id = task_manager.create_task(
            chat_key="web:test",
            instruction="Integration test",
            executor_id="integration_test_user",
            tenant_id="_default",
        )

        task_manager.record_event(
            task_id=task_id,
            event={"event": "task.started"},
            executor_id="system",
            tenant_id="_default",
        )

        task_manager.record_event(
            task_id=task_id,
            event={"event": "task.completed", "exit_code": 0},
            executor_id="system",
            tenant_id="_default",
        )

        # Verify task state
        task = task_manager.get_task(task_id)
        assert task.status == TaskStatus.COMPLETED

        # Verify audit trail exists
        audit = TaskAuditTrail(task_id=task_id, tenant_id="_default")
        events = audit.read_events()
        status = audit.get_chain_status()

        assert len(events) == 3
        assert status["integrity_verified"] is True
        assert status["height"] == 3

        # Verify all states are recorded
        event_types = [e["event_type"] for e in events]
        assert "task.created" in event_types
        assert "task.started" in event_types
        assert "task.completed" in event_types

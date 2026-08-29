"""E2E tests: Extract & install slack-notifier via full Tier 1-3 pipeline."""

import pytest
import tempfile
import json
from pathlib import Path
from unittest.mock import Mock, patch

from ..tier0_bootstrap import BootstrapManager, Config, AuditChain
from ..tier1_session import SessionManager
from ..tier2_task_engine import TaskPayload, TaskType, TaskEngine
from ..tier3_brain_core import BrainCore, MetricsCollector


@pytest.fixture
def temp_dir():
    """Create temporary directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def bootstrap_manager(temp_dir):
    """Initialize bootstrap manager."""
    config = Config(tenant_id="test_e2e", corvin_home=temp_dir)
    manager = BootstrapManager(config)
    manager.boot()
    return manager


@pytest.fixture
def session_manager(bootstrap_manager):
    """Initialize session manager."""
    db_conn = bootstrap_manager.get_database_connection()
    return SessionManager(db_conn, bootstrap_manager.audit)


@pytest.fixture
def task_engine(bootstrap_manager):
    """Initialize task engine."""
    db_conn = bootstrap_manager.get_database_connection()
    return TaskEngine(db_conn, bootstrap_manager.audit)


@pytest.fixture
def brain_core(task_engine, session_manager, bootstrap_manager):
    """Initialize brain core."""
    brain = BrainCore(
        task_engine=task_engine,
        session_manager=session_manager,
        audit_chain=bootstrap_manager.audit,
    )
    db_conn = bootstrap_manager.get_database_connection()
    brain.set_metrics_collector(MetricsCollector(db_conn))
    return brain


class TestSlackNotifierE2E:
    """E2E tests for slack-notifier extraction and installation."""

    def test_bootstrap_and_prepare_session(self, bootstrap_manager, session_manager):
        """Bootstrap completes and session can be created."""
        assert bootstrap_manager.is_initialized

        session_id = session_manager.begin_session(
            details={"purpose": "slack_notifier_test"}
        )
        assert session_id is not None

        session = session_manager.load_session(session_id)
        assert session["status"] == "created"

    def test_slack_notifier_install_task(self, task_engine):
        """Create slack-notifier install task."""
        session_id = "test_session_001"

        payload = TaskPayload(
            task_type=TaskType.PLUGIN_INSTALL,
            plugin_id="slack-notifier",
            version="1.0.0",
            config={"webhook_url": "https://hooks.slack.com/test"},
        )

        task_id = task_engine.submit_task(session_id, payload)
        assert task_id is not None

        # Verify task in queue
        task = task_engine.queue.get_task(task_id)
        assert task["task_type"] == "plugin_install"
        assert task["status"] == "queued"

    def test_mock_handler_registration(self, task_engine):
        """Register mock handler for testing."""

        def mock_install_handler(payload):
            """Mock installation handler."""
            if payload.plugin_id == "slack-notifier":
                return {
                    "success": True,
                    "plugin_id": "slack-notifier",
                    "installed_version": "1.0.0",
                    "webhook_verified": True,
                }
            return {"success": False, "error": "Unknown plugin"}

        task_engine.executor.register_handler(
            TaskType.PLUGIN_INSTALL, mock_install_handler
        )

        # Submit and execute task
        payload = TaskPayload(
            task_type=TaskType.PLUGIN_INSTALL,
            plugin_id="slack-notifier",
            version="1.0.0",
        )
        task_id = task_engine.submit_task("test_session", payload)
        task_engine.process_next_task()

        result = task_engine.get_task_result(task_id)
        assert result["success"] is True
        assert result["plugin_id"] == "slack-notifier"

    def test_full_pipeline_install_enable_disable(
        self, brain_core, session_manager
    ):
        """Full pipeline: create session, install, enable, disable."""

        # 1. Register handlers
        def mock_install(payload):
            return {
                "success": True,
                "plugin_id": payload.plugin_id,
                "action": "installed",
            }

        def mock_enable(payload):
            return {
                "success": True,
                "plugin_id": payload.plugin_id,
                "action": "enabled",
            }

        def mock_disable(payload):
            return {
                "success": True,
                "plugin_id": payload.plugin_id,
                "action": "disabled",
            }

        brain_core.task_engine.executor.register_handler(
            TaskType.PLUGIN_INSTALL, mock_install
        )
        brain_core.task_engine.executor.register_handler(
            TaskType.PLUGIN_ENABLE, mock_enable
        )
        brain_core.task_engine.executor.register_handler(
            TaskType.PLUGIN_DISABLE, mock_disable
        )

        # 2. Begin session
        session_id = session_manager.begin_session(
            details={"feature": "slack_notifier_full_test"}
        )
        session_manager.activate_session(session_id)

        # 3. Create task sequence
        tasks = [
            TaskPayload(
                task_type=TaskType.PLUGIN_INSTALL,
                plugin_id="slack-notifier",
                version="1.0.0",
            ),
            TaskPayload(
                task_type=TaskType.PLUGIN_ENABLE,
                plugin_id="slack-notifier",
            ),
            TaskPayload(
                task_type=TaskType.PLUGIN_DISABLE,
                plugin_id="slack-notifier",
            ),
        ]

        # 4. Run session
        result = brain_core.run_session(tasks, timeout_seconds=10.0)

        assert result["session_id"] == session_id
        assert result["task_count"] == 3
        assert result["success_count"] == 3
        assert result["failed_count"] == 0
        assert result["success_rate"] == 1.0

    def test_reproducibility_sqlite_state(self, bootstrap_manager):
        """Verify session/task state is reproducible from SQLite."""
        config = bootstrap_manager.config

        # 1. Create first manager and populate data
        manager1 = BootstrapManager(config)
        manager1.boot()
        db1 = manager1.get_database_connection()
        session_mgr1 = SessionManager(db1, manager1.audit)

        session_id = session_mgr1.begin_session(details={"run": 1})
        session_mgr1.activate_session(session_id)

        # 2. Close and reopen (simulating process restart)
        db1.close()

        # 3. Create second manager on same config
        manager2 = BootstrapManager(config)
        manager2.boot()
        db2 = manager2.get_database_connection()
        session_mgr2 = SessionManager(db2, manager2.audit)

        # 4. Verify session is recoverable
        session = session_mgr2.load_session(session_id)
        assert session is not None
        assert session["session_id"] == session_id
        assert session["status"] == "active"

    def test_audit_trail_completeness(self, brain_core, session_manager):
        """Verify all operations are audit-logged."""

        # Register mock handler
        def mock_handler(payload):
            return {"success": True}

        brain_core.task_engine.executor.register_handler(
            TaskType.PLUGIN_INSTALL, mock_handler
        )

        # Run operation
        session_id = session_manager.begin_session()
        session_manager.activate_session(session_id)

        task = TaskPayload(
            task_type=TaskType.PLUGIN_INSTALL,
            plugin_id="test-plugin",
        )
        brain_core.submit_and_wait(session_id, task, timeout_seconds=5.0)

        # Check audit trail
        audit_log_path = brain_core.audit.log_path
        with open(audit_log_path, "r") as f:
            events = [json.loads(line) for line in f.readlines()]

        event_types = [e["event_type"] for e in events]
        assert "session.created" in event_types
        assert "session.activated" in event_types
        assert "task.submitted" in event_types
        assert any("execution" in et for et in event_types)

    def test_metrics_collection(self, brain_core, session_manager):
        """Verify metrics are collected during execution."""

        # Register mock handler
        def mock_handler(payload):
            return {"success": True}

        brain_core.task_engine.executor.register_handler(
            TaskType.PLUGIN_INSTALL, mock_handler
        )

        # Run operation
        session_id = session_manager.begin_session()
        session_manager.activate_session(session_id)

        task = TaskPayload(
            task_type=TaskType.PLUGIN_INSTALL,
            plugin_id="test-plugin",
        )
        brain_core.submit_and_wait(session_id, task, timeout_seconds=5.0)

        # Verify metrics were recorded
        if brain_core.metrics:
            latency_summary = brain_core.metrics.get_metric_summary(
                session_id, "task.latency_ms"
            )
            assert latency_summary["count"] > 0
            assert latency_summary["avg"] > 0

            success_summary = brain_core.metrics.get_metric_summary(
                session_id, "task.success_rate"
            )
            assert success_summary["count"] > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

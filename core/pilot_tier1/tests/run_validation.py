#!/usr/bin/env python3
"""Direct validation of Tier 1 Pilot (no pytest required)."""

import sys
import tempfile
import json
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from tier0_bootstrap import BootstrapManager, Config, AuditChain, CoreRegistry
from tier1_session import SessionManager, SessionStatus
from tier2_task_engine import TaskPayload, TaskType, TaskEngine
from tier3_brain_core import BrainCore, MetricsCollector


def test_audit_chain():
    """Test: Audit hash-chaining."""
    print("\n✓ TEST: Audit Chain Hash-Linking")
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "audit.jsonl"
        chain = AuditChain(log_path)

        # Write events
        hash1 = chain.write_event(
            event_type="test.event1",
            actor="test_actor",
            action="action1",
            details={"key": "value1"},
        )
        hash2 = chain.write_event(
            event_type="test.event2",
            actor="test_actor",
            action="action2",
        )

        # Verify linking
        with open(log_path, "r") as f:
            lines = f.readlines()
            event1 = json.loads(lines[0])
            event2 = json.loads(lines[1])

        assert event1["previous_hash"] == "genesis", "First event should have genesis link"
        assert event2["previous_hash"] == hash1, "Second event should link to first"
        assert chain.verify_chain(), "Chain verification should pass"

        print(f"  • Event 1 hash: {hash1[:16]}...")
        print(f"  • Event 2 hash: {hash2[:16]}... (links to event 1)")
        print(f"  • Chain verification: PASS")


def test_core_registry():
    """Test: Core plugin interface registry."""
    print("\n✓ TEST: Core Registry")
    registry = CoreRegistry()

    interfaces = registry.list_interfaces()
    print(f"  • Registered interfaces: {len(interfaces)}")
    for iface in interfaces:
        print(f"    - {iface}")

    audit_backend = registry.get_interface("audit_backend")
    assert audit_backend is not None, "audit_backend should be registered"
    print(f"  • audit_backend methods: {audit_backend['methods']}")


def test_bootstrap():
    """Test: Bootstrap manager (full Tier 0)."""
    print("\n✓ TEST: Bootstrap Manager (Tier 0)")
    with tempfile.TemporaryDirectory() as tmpdir:
        config = Config(tenant_id="test_bootstrap", corvin_home=Path(tmpdir))
        manager = BootstrapManager(config)

        # Boot
        result = manager.boot()
        assert result is True, "Bootstrap boot() should succeed"
        assert manager.is_initialized, "Bootstrap should be marked initialized"

        print(f"  • Bootstrap completed successfully")
        print(f"  • Database created: {config.db_path}")
        print(f"  • Audit log created: {config.audit_log_path}")

        # Verify audit trail
        with open(config.audit_log_path, "r") as f:
            events = [json.loads(line) for line in f.readlines()]
        print(f"  • Audit events logged: {len(events)}")
        print(f"    - First: {events[0]['event_type']}")
        print(f"    - Last: {events[-1]['event_type']}")


def test_session_manager():
    """Test: Session manager (Tier 1)."""
    print("\n✓ TEST: Session Manager (Tier 1)")
    with tempfile.TemporaryDirectory() as tmpdir:
        config = Config(tenant_id="test_session", corvin_home=Path(tmpdir))
        manager = BootstrapManager(config)
        manager.boot()

        db_conn = manager.get_database_connection()
        session_mgr = SessionManager(db_conn, manager.audit)

        # Create session
        session_id = session_mgr.begin_session(
            details={"purpose": "test_session"}
        )
        print(f"  • Session created: {session_id[:16]}...")

        # Load session
        session = session_mgr.load_session(session_id)
        assert session["status"] == "created", "Status should be 'created'"
        print(f"  • Session status: {session['status']}")

        # Activate session
        session_mgr.activate_session(session_id)
        session = session_mgr.load_session(session_id)
        assert session["status"] == "active", "Status should be 'active'"
        print(f"  • Session activated: status = {session['status']}")


def test_task_engine():
    """Test: Task engine (Tier 2)."""
    print("\n✓ TEST: Task Engine (Tier 2)")
    with tempfile.TemporaryDirectory() as tmpdir:
        config = Config(tenant_id="test_task", corvin_home=Path(tmpdir))
        manager = BootstrapManager(config)
        manager.boot()

        db_conn = manager.get_database_connection()
        task_engine = TaskEngine(db_conn, manager.audit)

        # Register handler
        def mock_install(payload):
            return {
                "success": True,
                "plugin_id": payload.plugin_id,
                "action": "installed",
            }

        task_engine.executor.register_handler(
            TaskType.PLUGIN_INSTALL, mock_install
        )

        # Submit task
        payload = TaskPayload(
            task_type=TaskType.PLUGIN_INSTALL,
            plugin_id="slack-notifier",
            version="1.0.0",
        )
        task_id = task_engine.submit_task("session_001", payload)
        print(f"  • Task submitted: {task_id[:16]}...")

        # Process task
        task_engine.process_next_task()
        status = task_engine.get_task_status(task_id)
        result = task_engine.get_task_result(task_id)

        assert status == "completed", f"Task status should be 'completed', got {status}"
        assert result["success"], "Task result should succeed"
        print(f"  • Task processed: status = {status}")
        print(f"  • Task result: {result}")


def test_brain_core():
    """Test: Brain core orchestration (Tier 3)."""
    print("\n✓ TEST: Brain Core (Tier 3)")
    with tempfile.TemporaryDirectory() as tmpdir:
        config = Config(tenant_id="test_brain", corvin_home=Path(tmpdir))
        manager = BootstrapManager(config)
        manager.boot()

        db_conn = manager.get_database_connection()
        task_engine = TaskEngine(db_conn, manager.audit)
        session_mgr = SessionManager(db_conn, manager.audit)
        brain = BrainCore(
            task_engine=task_engine,
            session_manager=session_mgr,
            audit_chain=manager.audit,
        )
        brain.set_metrics_collector(MetricsCollector(db_conn))

        # Register handler
        def mock_install(payload):
            return {"success": True, "plugin_id": payload.plugin_id}

        def mock_enable(payload):
            return {"success": True, "plugin_id": payload.plugin_id}

        brain.task_engine.executor.register_handler(
            TaskType.PLUGIN_INSTALL, mock_install
        )
        brain.task_engine.executor.register_handler(
            TaskType.PLUGIN_ENABLE, mock_enable
        )

        # Run session with tasks
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
        ]

        result = brain.run_session(tasks, timeout_seconds=10.0)

        assert result["success_count"] == 2, "Both tasks should succeed"
        assert result["success_rate"] == 1.0, "Success rate should be 100%"
        print(f"  • Session completed")
        print(f"    - Tasks: {result['task_count']}")
        print(f"    - Succeeded: {result['success_count']}")
        print(f"    - Failed: {result['failed_count']}")
        print(f"    - Success rate: {result['success_rate']*100:.1f}%")
        print(f"    - Latency: {result['latency_seconds']:.3f}s")


def test_reproducibility():
    """Test: SQLite-based reproducibility (session recovery)."""
    print("\n✓ TEST: Reproducibility (Session Recovery from SQLite)")
    with tempfile.TemporaryDirectory() as tmpdir:
        config = Config(tenant_id="test_repro", corvin_home=Path(tmpdir))

        # 1. Create first instance
        manager1 = BootstrapManager(config)
        manager1.boot()
        db1 = manager1.get_database_connection()
        session_mgr1 = SessionManager(db1, manager1.audit)

        session_id = session_mgr1.begin_session(details={"run": 1})
        session_mgr1.activate_session(session_id)
        original_session = session_mgr1.load_session(session_id)

        # Close connection (simulating process shutdown)
        db1.close()

        # 2. Create second instance on same config
        manager2 = BootstrapManager(config)
        manager2.boot()
        db2 = manager2.get_database_connection()
        session_mgr2 = SessionManager(db2, manager2.audit)

        # 3. Recover session
        recovered_session = session_mgr2.load_session(session_id)

        assert recovered_session is not None, "Session should be recoverable"
        assert recovered_session["session_id"] == original_session["session_id"]
        assert recovered_session["status"] == original_session["status"]

        print(f"  • Session created: {session_id[:16]}...")
        print(f"  • Process shutdown/restart simulated")
        print(f"  • Session recovered from SQLite")
        print(f"  • Session status: {recovered_session['status']}")
        print(f"  • ✓ Reproducibility VERIFIED")


def main():
    """Run all validation tests."""
    print("=" * 70)
    print("TIER 1 PILOT — REPRODUCIBILITY VALIDATION")
    print("=" * 70)

    try:
        test_audit_chain()
        test_core_registry()
        test_bootstrap()
        test_session_manager()
        test_task_engine()
        test_brain_core()
        test_reproducibility()

        print("\n" + "=" * 70)
        print("✓ ALL TESTS PASSED — TIER 1 PILOT READY FOR E2E")
        print("=" * 70)
        return 0

    except Exception as e:
        print(f"\n✗ TEST FAILED: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())

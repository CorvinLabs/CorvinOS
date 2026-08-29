"""Tests for Tier 0: Bootstrap."""

import pytest
import tempfile
import json
from pathlib import Path
from datetime import datetime

from ..tier0_bootstrap import (
    Config,
    AuditChain,
    CoreRegistry,
    BootstrapManager,
)


@pytest.fixture
def temp_dir():
    """Create temporary directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def config(temp_dir):
    """Create test config."""
    return Config(
        tenant_id="test_tenant",
        corvin_home=temp_dir,
    )


class TestAuditChain:
    """Test audit hash-chaining (GDPR Art. 30/32)."""

    def test_audit_chain_initialization(self, temp_dir):
        """Audit chain initializes without errors."""
        log_path = temp_dir / "audit.jsonl"
        chain = AuditChain(log_path)
        assert log_path.exists()

    def test_audit_event_write(self, temp_dir):
        """Single event written to chain."""
        log_path = temp_dir / "audit.jsonl"
        chain = AuditChain(log_path)

        event_hash = chain.write_event(
            event_type="test.event",
            actor="test_actor",
            action="test_action",
            details={"key": "value"},
        )

        assert event_hash is not None
        assert len(event_hash) == 64  # SHA256 hex

        # Verify event in log
        with open(log_path, "r") as f:
            event = json.loads(f.readline())
            assert event["event_type"] == "test.event"
            assert event["actor"] == "test_actor"
            assert event["hash"] == event_hash
            assert event["previous_hash"] == "genesis"

    def test_audit_chain_linking(self, temp_dir):
        """Events are linked by previous_hash."""
        log_path = temp_dir / "audit.jsonl"
        chain = AuditChain(log_path)

        hash1 = chain.write_event("test.event1", "actor", "action1")
        hash2 = chain.write_event("test.event2", "actor", "action2")

        with open(log_path, "r") as f:
            lines = f.readlines()
            event1 = json.loads(lines[0])
            event2 = json.loads(lines[1])

            assert event1["hash"] == hash1
            assert event2["previous_hash"] == hash1
            assert event2["hash"] == hash2

    def test_audit_chain_verification(self, temp_dir):
        """Chain verification succeeds for intact chain."""
        log_path = temp_dir / "audit.jsonl"
        chain = AuditChain(log_path)

        # Write events
        chain.write_event("event1", "actor", "action")
        chain.write_event("event2", "actor", "action")

        # Verify
        assert chain.verify_chain() is True

    def test_audit_chain_tampering_detection(self, temp_dir):
        """Tampered chain fails verification."""
        log_path = temp_dir / "audit.jsonl"
        chain = AuditChain(log_path)

        chain.write_event("event1", "actor", "action")

        # Tamper with event
        with open(log_path, "r+") as f:
            event = json.loads(f.readline())
            event["details"]["tampered"] = True
            f.seek(0)
            f.write(json.dumps(event) + "\n")

        # Verification should fail
        assert chain.verify_chain() is False


class TestCoreRegistry:
    """Test core plugin interface registry."""

    def test_registry_initialization(self):
        """Registry initializes with interfaces."""
        registry = CoreRegistry()
        assert len(registry.list_interfaces()) >= 4

    def test_get_interface(self):
        """Retrieve interface definition by name."""
        registry = CoreRegistry()
        interface = registry.get_interface("audit_backend")

        assert interface is not None
        assert interface["base_class"] == "AuditBackend"
        assert "write" in interface["methods"]

    def test_list_interfaces(self):
        """List all registered interfaces."""
        registry = CoreRegistry()
        interfaces = registry.list_interfaces()

        assert "audit_backend" in interfaces
        assert "notification_backend" in interfaces
        assert "recall_backend" in interfaces


class TestBootstrapManager:
    """Test bootstrap manager (full tier-0)."""

    def test_bootstrap_initialization(self, config):
        """Bootstrap initializes without errors."""
        manager = BootstrapManager(config)
        assert manager.config == config
        assert manager.core_registry is not None

    def test_bootstrap_boot_success(self, config):
        """Bootstrap boot() succeeds."""
        manager = BootstrapManager(config)
        assert manager.boot() is True
        assert manager.is_initialized is True

    def test_bootstrap_creates_database(self, config):
        """Bootstrap creates SQLite database."""
        manager = BootstrapManager(config)
        manager.boot()

        assert config.db_path.exists()

        # Verify tables exist
        conn = manager.get_database_connection()
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = [row[0] for row in cursor.fetchall()]
        assert "sessions" in tables
        assert "tasks" in tables
        assert "metrics" in tables

    def test_bootstrap_audit_trail(self, config):
        """Bootstrap logs events to audit trail."""
        manager = BootstrapManager(config)
        manager.boot()

        with open(config.audit_log_path, "r") as f:
            lines = f.readlines()
            assert len(lines) >= 2  # At least started + completed

            events = [json.loads(line) for line in lines]
            assert events[0]["event_type"] == "bootstrap.started"
            assert events[-1]["event_type"] == "bootstrap.completed"

    def test_bootstrap_boot_tripwire(self, config):
        """Bootstrap tripwire verifies audit chain before proceeding."""
        manager = BootstrapManager(config)

        # First boot should succeed
        assert manager.boot() is True

        # Tamper with audit log
        with open(config.audit_log_path, "r+") as f:
            lines = f.readlines()
            event = json.loads(lines[0])
            event["details"]["tampered"] = True
            f.seek(0)
            f.write(json.dumps(event) + "\n")
            for line in lines[1:]:
                f.write(line)

        # Second boot should fail (tripwire)
        manager2 = BootstrapManager(config)
        with pytest.raises(RuntimeError):
            manager2.boot()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

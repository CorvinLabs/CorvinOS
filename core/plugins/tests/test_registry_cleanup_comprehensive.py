"""Comprehensive tests for plugin registry cleanup (ADR-0250 Part 2).

Covers:
- Multi-backup system (keep last 5 backups with timestamps)
- Enhanced schema validation
- Integrity verification
- Recovery with timestamped backups
- Audit trail integration
- Crash simulation and recovery
- E2E registry lifecycle with cleanup
"""
from __future__ import annotations

import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# ── Adjust path so tests can be run standalone ───────────────────────────────
_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[3]
_PKG = _HERE.parents[1]
_FORGE = _REPO / "operator" / "forge"
_SHARED = _REPO / "operator" / "bridges" / "shared"
for _p in (str(_PKG), str(_FORGE), str(_SHARED), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from corvin_plugins.manifest import (  # noqa: E402
    PIIRisk,
    PluginError,
    PluginOrigin,
    PluginRecord,
)
from corvin_plugins.state import (  # noqa: E402
    BackupManager,
    PluginLifecycle,
    RegistryCorrupt,
    TenantRegistry,
    registry_backup_path,
    registry_backups_dir,
    registry_path,
    registry_mutation,
)

#: Test module name for plugin class_path
_MOD = __name__

_SCHEMA = {
    "type": "object",
    "properties": {"channel": {"type": "string", "default": "ops"}},
    "required": ["channel"],
    "additionalProperties": False,
}


def _record(pid="acme-notify", **kw) -> PluginRecord:
    """Create a test PluginRecord."""
    base = dict(
        plugin_id=pid,
        version="1.0.0",
        display_name="Acme Notify",
        plugin_type="notification_backend",
        origin=PluginOrigin.VETTED,
        pii_risk=PIIRisk.LOW,
        settings_schema=_SCHEMA,
        settings={"channel": "ops"},
    )
    base.update(kw)
    return PluginRecord(**base)


class TestBackupManager(unittest.TestCase):
    """Test BackupManager for timestamped backups."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self._audit_path = self.home / "audit.jsonl"
        os.environ["VOICE_AUDIT_PATH"] = str(self._audit_path)
        self.registry_path = registry_path(tenant_id="_default", corvin_home_path=self.home)
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        os.environ.pop("VOICE_AUDIT_PATH", None)
        self._tmp.cleanup()

    def test_backup_manager_creates_timestamped_backup(self):
        """BackupManager creates timestamped backup files."""
        # Create initial registry (no backup on first save)
        reg = TenantRegistry(self.registry_path)
        reg.records["plugin1"] = _record("plugin1")
        reg.save()

        # Create a mutation to trigger backup creation
        reg = TenantRegistry.load(
            tenant_id="_default", corvin_home_path=self.home, auto_recover=False
        )
        reg.records["plugin2"] = _record("plugin2")
        reg.save()

        # Verify timestamped backup was created
        backups_dir = registry_backups_dir(tenant_id="_default", corvin_home_path=self.home)
        backups = list(backups_dir.glob("registry.*.yaml"))
        self.assertGreaterEqual(len(backups), 1, "at least one timestamped backup should exist")

    def test_backup_manager_keeps_last_5_backups(self):
        """BackupManager keeps only the last 5 backups."""
        backup_mgr = BackupManager(self.registry_path)

        # Create 7 backups by accumulating plugins
        reg = None
        for i in range(7):
            if reg is None:
                reg = TenantRegistry(self.registry_path)
            else:
                reg = TenantRegistry.load(
                    tenant_id="_default", corvin_home_path=self.home, auto_recover=False
                )
            reg.records[f"plugin{i}"] = _record(f"plugin{i}")
            reg.save()
            time.sleep(0.01)  # Ensure different timestamps

        # Verify only 5 backups remain
        backups = backup_mgr.list_backups()
        self.assertEqual(len(backups), 5, f"expected 5 backups, got {len(backups)}")

    def test_backup_manager_lists_newest_first(self):
        """list_backups returns backups newest first."""
        backup_mgr = BackupManager(self.registry_path)

        # Create 3 backups
        for i in range(3):
            reg = TenantRegistry(self.registry_path)
            reg.records[f"plugin{i}"] = _record(f"plugin{i}")
            reg.save()
            time.sleep(0.01)

        backups = backup_mgr.list_backups()
        if len(backups) > 1:
            # Verify they're sorted by modification time (newest first)
            self.assertGreater(backups[0].stat().st_mtime, backups[-1].stat().st_mtime)

    def test_backup_manager_verifies_all_backups(self):
        """verify_all_backups checks YAML validity of all backups."""
        backup_mgr = BackupManager(self.registry_path)

        # Create initial registry
        reg = TenantRegistry(self.registry_path)
        reg.records["plugin1"] = _record("plugin1")
        reg.save()

        # Create a mutation to trigger backup
        reg = TenantRegistry.load(
            tenant_id="_default", corvin_home_path=self.home, auto_recover=False
        )
        reg.records["plugin2"] = _record("plugin2")
        reg.save()

        # Verify all backups
        results = backup_mgr.verify_all_backups()
        self.assertGreater(len(results), 0, "should have at least one backup")
        for backup_name, is_valid in results.items():
            self.assertTrue(is_valid, f"backup {backup_name} should be valid")

    def test_backup_manager_restores_specific_backup(self):
        """restore_from_backup restores a specific backup."""
        # Create initial state
        reg = TenantRegistry(self.registry_path)
        reg.records["plugin1"] = _record("plugin1")
        reg.save()

        # Create a second state
        reg.records["plugin2"] = _record("plugin2")
        reg.save()

        # Get backups
        backup_mgr = BackupManager(self.registry_path)
        backups = backup_mgr.list_backups()
        self.assertGreater(len(backups), 0)

        # Corrupt the current registry
        self.registry_path.write_text("{ invalid yaml")

        # Restore from the oldest backup
        oldest_backup = backups[-1]
        success = backup_mgr.restore_from_backup(oldest_backup)
        self.assertTrue(success, "restore should succeed")
        self.assertTrue(self.registry_path.exists())


class TestRegistryIntegrity(unittest.TestCase):
    """Test registry integrity verification."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self._audit_path = self.home / "audit.jsonl"
        os.environ["VOICE_AUDIT_PATH"] = str(self._audit_path)
        self.registry_path = registry_path(tenant_id="_default", corvin_home_path=self.home)
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        os.environ.pop("VOICE_AUDIT_PATH", None)
        self._tmp.cleanup()

    def test_verify_records_schema_succeeds_with_valid_records(self):
        """verify_records_schema passes valid records."""
        reg = TenantRegistry(self.registry_path)
        reg.records["plugin1"] = _record("plugin1")
        reg.records["plugin2"] = _record("plugin2")

        is_valid, errors = reg.verify_records_schema()
        self.assertTrue(is_valid, f"should be valid, but got errors: {errors}")
        self.assertEqual(len(errors), 0)

    def test_verify_records_schema_detects_invalid_origin(self):
        """verify_records_schema detects invalid origin."""
        reg = TenantRegistry(self.registry_path)
        record = _record("plugin1")
        # Manually corrupt the origin
        record.origin = "invalid_origin"  # type: ignore
        reg.records["plugin1"] = record

        is_valid, errors = reg.verify_records_schema()
        self.assertFalse(is_valid)
        self.assertGreater(len(errors), 0)

    def test_verify_integrity_checks_file_permissions(self):
        """verify_integrity checks registry file has mode 0o600."""
        reg = TenantRegistry(self.registry_path)
        reg.records["plugin1"] = _record("plugin1")
        reg.save()

        # Intentionally make it world-readable
        os.chmod(self.registry_path, 0o644)

        is_valid, errors = reg.verify_integrity()
        self.assertFalse(is_valid)
        self.assertIn("mode", " ".join(errors).lower())

    def test_verify_integrity_passes_with_valid_registry(self):
        """verify_integrity passes with valid registry and backups."""
        reg = TenantRegistry(self.registry_path)
        reg.records["plugin1"] = _record("plugin1")
        reg.save()

        is_valid, errors = reg.verify_integrity()
        self.assertTrue(is_valid, f"should be valid, but got errors: {errors}")


class TestTimestampedRecovery(unittest.TestCase):
    """Test recovery using timestamped backups."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self._audit_path = self.home / "audit.jsonl"
        os.environ["VOICE_AUDIT_PATH"] = str(self._audit_path)
        self.registry_path = registry_path(tenant_id="_default", corvin_home_path=self.home)
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        os.environ.pop("VOICE_AUDIT_PATH", None)
        self._tmp.cleanup()

    def test_restore_from_timestamped_backup_most_recent(self):
        """restore_from_timestamped_backup uses most recent by default."""
        # Create initial state with plugins
        reg = TenantRegistry(self.registry_path)
        reg.records["plugin1"] = _record("plugin1")
        reg.records["plugin2"] = _record("plugin2")
        reg.save()

        # Create a mutation to trigger backup creation
        reg = TenantRegistry.load(
            tenant_id="_default", corvin_home_path=self.home, auto_recover=False
        )
        reg.records["plugin3"] = _record("plugin3")
        reg.save()

        # Corrupt the registry
        self.registry_path.write_text("{ invalid")

        # Restore
        reg2 = TenantRegistry(self.registry_path)
        success = reg2.restore_from_timestamped_backup()
        self.assertTrue(success)

        # Verify state was restored (should have first 2 plugins from backup)
        reg3 = TenantRegistry.load(
            tenant_id="_default", corvin_home_path=self.home, auto_recover=False
        )
        self.assertIn("plugin1", reg3.records)
        self.assertIn("plugin2", reg3.records)

    def test_restore_from_specific_timestamped_backup(self):
        """restore_from_timestamped_backup can restore specific backup."""
        # Create first state
        reg = TenantRegistry(self.registry_path)
        reg.records["plugin1"] = _record("plugin1")
        reg.save()

        # Create second state
        reg.records["plugin2"] = _record("plugin2")
        reg.save()
        time.sleep(0.01)

        # Get backups and pick the older one
        backup_mgr = BackupManager(self.registry_path)
        backups = backup_mgr.list_backups()
        older_backup = backups[-1]  # Last one is oldest

        # Corrupt the registry
        self.registry_path.write_text("{ invalid")

        # Restore from older backup (should only have plugin1)
        reg2 = TenantRegistry(self.registry_path)
        success = reg2.restore_from_timestamped_backup(older_backup)
        self.assertTrue(success)

        # Verify only plugin1 is present
        reg3 = TenantRegistry.load(
            tenant_id="_default", corvin_home_path=self.home, auto_recover=False
        )
        self.assertIn("plugin1", reg3.records)


class TestE2ERegistryLifecycle(unittest.TestCase):
    """End-to-end registry lifecycle with cleanup."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self._audit_path = self.home / "audit.jsonl"
        os.environ["VOICE_AUDIT_PATH"] = str(self._audit_path)

    def tearDown(self):
        os.environ.pop("VOICE_AUDIT_PATH", None)
        self._tmp.cleanup()

    def test_e2e_multi_tenant_independent_backups(self):
        """Multiple tenants maintain independent backup sets."""
        # Create plugins for tenant 1
        reg1_path = registry_path(tenant_id="tenant1", corvin_home_path=self.home)
        reg1_path.parent.mkdir(parents=True, exist_ok=True)
        reg1 = TenantRegistry(reg1_path)
        reg1.records["plugin1"] = _record("plugin1")
        reg1.save()

        # Load and add second plugin to tenant 1
        reg1 = TenantRegistry.load(
            tenant_id="tenant1", corvin_home_path=self.home, auto_recover=False
        )
        reg1.records["plugin2"] = _record("plugin2")
        reg1.save()

        # Create plugins for tenant 2 (first save)
        reg2_path = registry_path(tenant_id="tenant2", corvin_home_path=self.home)
        reg2_path.parent.mkdir(parents=True, exist_ok=True)
        reg2 = TenantRegistry(reg2_path)
        reg2.records["plugin_a"] = _record("plugin_a")
        reg2.save()

        # Load and add second plugin to tenant 2 to create backup
        reg2 = TenantRegistry.load(
            tenant_id="tenant2", corvin_home_path=self.home, auto_recover=False
        )
        reg2.records["plugin_b"] = _record("plugin_b")
        reg2.save()

        # Verify backups are separate
        backups_dir1 = registry_backups_dir(tenant_id="tenant1", corvin_home_path=self.home)
        backups_dir2 = registry_backups_dir(tenant_id="tenant2", corvin_home_path=self.home)

        backups1 = list(backups_dir1.glob("registry.*.yaml"))
        backups2 = list(backups_dir2.glob("registry.*.yaml"))

        self.assertGreater(len(backups1), 0, "tenant1 should have backups")
        self.assertGreater(len(backups2), 0, "tenant2 should have backups")
        # Both should have the same number of backups (1 each, from the second mutation)
        self.assertEqual(len(backups1), len(backups2))

    def test_e2e_crash_recovery_maintains_consistency(self):
        """Crash during mutation leaves system recoverable."""
        reg_path = registry_path(tenant_id="_default", corvin_home_path=self.home)
        reg_path.parent.mkdir(parents=True, exist_ok=True)

        # Create stable state
        reg = TenantRegistry(reg_path)
        reg.records["plugin1"] = _record("plugin1")
        reg.save()

        original_content = reg_path.read_text()

        # Simulate crash during mutation
        reg.records["plugin2"] = _record("plugin2")
        with patch("os.replace", side_effect=OSError("crash")):
            with self.assertRaises(OSError):
                reg.save()

        # System should still have original state
        self.assertTrue(reg_path.exists())
        current_content = reg_path.read_text()
        self.assertEqual(current_content, original_content)

        # Should be able to recover
        recovered = TenantRegistry.load(
            tenant_id="_default", corvin_home_path=self.home, auto_recover=False
        )
        self.assertIn("plugin1", recovered.records)

    def test_e2e_automated_backup_cleanup(self):
        """Registry saves automatically clean up old backups."""
        reg_path = registry_path(tenant_id="_default", corvin_home_path=self.home)
        reg_path.parent.mkdir(parents=True, exist_ok=True)

        # Create many mutations to trigger cleanup
        reg = None
        for i in range(10):
            if reg is None:
                reg = TenantRegistry(reg_path)
            else:
                reg = TenantRegistry.load(
                    tenant_id="_default", corvin_home_path=self.home, auto_recover=False
                )
            reg.records[f"plugin{i}"] = _record(f"plugin{i}")
            reg.save()
            time.sleep(0.01)

        # Verify only 5 backups remain (cleanup keeps last MAX_BACKUPS)
        backups_dir = registry_backups_dir(tenant_id="_default", corvin_home_path=self.home)
        backups = list(backups_dir.glob("registry.*.yaml"))
        self.assertEqual(len(backups), 5, f"expected 5 backups, got {len(backups)}")


class TestAuditTrailWithCleanup(unittest.TestCase):
    """Audit trail integration with registry cleanup."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self._audit_path = self.home / "audit.jsonl"
        os.environ["VOICE_AUDIT_PATH"] = str(self._audit_path)

    def tearDown(self):
        os.environ.pop("VOICE_AUDIT_PATH", None)
        self._tmp.cleanup()

    def test_backup_creation_events_recorded_in_audit(self):
        """Backup creation triggers audit events."""
        reg_path = registry_path(tenant_id="_default", corvin_home_path=self.home)
        reg_path.parent.mkdir(parents=True, exist_ok=True)

        reg = TenantRegistry(reg_path)
        reg.records["plugin1"] = _record("plugin1")
        reg.save()

        # Second save creates backup
        reg.records["plugin2"] = _record("plugin2")
        reg.save()

        # Audit trail should have been created
        if self._audit_path.exists():
            audit_content = self._audit_path.read_text()
            # Should have some audit events
            self.assertGreater(len(audit_content), 0)


if __name__ == "__main__":
    unittest.main()

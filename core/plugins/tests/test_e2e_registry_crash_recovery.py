"""End-to-end test: simulate registry crash and verify recovery.

This test validates the complete crash → recovery cycle as documented in ADR-0250.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

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
    PluginOrigin,
    PluginRecord,
)
from corvin_plugins.state import (  # noqa: E402
    TenantRegistry,
    RegistryCorrupt,
    registry_backup_path,
    registry_path,
)

_SCHEMA = {
    "type": "object",
    "properties": {"channel": {"type": "string", "default": "ops"}},
    "required": ["channel"],
    "additionalProperties": False,
}


def _record(pid="plugin1", **kw) -> PluginRecord:
    """Create a test PluginRecord."""
    base = dict(
        plugin_id=pid,
        version="1.0.0",
        display_name="Test Plugin",
        plugin_type="notification_backend",
        origin=PluginOrigin.VETTED,
        pii_risk=PIIRisk.LOW,
        settings_schema=_SCHEMA,
        settings={"channel": "ops"},
    )
    base.update(kw)
    return PluginRecord(**base)


class TestE2ECrashRecovery(unittest.TestCase):
    """End-to-end crash → recovery scenario."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self._audit_path = self.home / "audit.jsonl"
        os.environ["VOICE_AUDIT_PATH"] = str(self._audit_path)

    def tearDown(self):
        os.environ.pop("VOICE_AUDIT_PATH", None)
        self._tmp.cleanup()

    def test_complete_crash_recovery_scenario(self):
        """
        Scenario:
        1. Operator installs plugins A, B, C (three saves)
        2. Process crashes during save of plugin D
        3. On restart, auto-recovery restores to state with A, B, C
        4. Operator can then install plugin D again
        """
        reg_path = registry_path(tenant_id="_default", corvin_home_path=self.home)
        bak_path = registry_backup_path(tenant_id="_default", corvin_home_path=self.home)

        # ─── Phase 1: Install plugins A, B, C ──────────────────────────────────
        print("\n[Phase 1] Installing plugins A, B, C...")
        reg = TenantRegistry(reg_path)

        for pid in ["plugin-a", "plugin-b", "plugin-c"]:
            reg.records[pid] = _record(pid)
            reg.save()
            print(f"  ✓ Saved {pid}")

        # Verify saved state
        self.assertEqual(len(reg.records), 3)
        self.assertIn("plugin-a", reg.records)
        self.assertIn("plugin-b", reg.records)
        self.assertIn("plugin-c", reg.records)
        print(f"  State: 3 plugins saved")

        # Verify backup exists from the last save
        self.assertTrue(bak_path.exists(), "backup should exist after saves")

        # ─── Phase 2: Simulate crash during plugin D installation ──────────────
        print("\n[Phase 2] Simulating crash during plugin D save...")
        reg.records["plugin-d"] = _record("plugin-d")

        # Corrupt the file to simulate incomplete write
        original_registry = reg_path.read_text() if reg_path.exists() else ""
        print(f"  Original registry size: {len(original_registry)} bytes")

        try:
            reg.save()
        except Exception as e:
            print(f"  Save error (expected in crash simulation): {e}")

        # Now simulate the actual crash by corrupting the file
        if reg_path.exists():
            reg_path.write_text("{ corrupted yaml: [")
            print(f"  Corrupted registry file (simulating crash)")

        # ─── Phase 3: Process restart with auto-recovery ─────────────────────
        print("\n[Phase 3] Process restart - auto-recovery triggered...")

        # Try to load registry (auto_recover=True by default)
        recovered_reg = TenantRegistry.load(
            tenant_id="_default", corvin_home_path=self.home, auto_recover=True
        )

        print(f"  Recovered {len(recovered_reg.records)} plugins")

        # Verify state is restored to before plugin D
        self.assertEqual(len(recovered_reg.records), 3, "should have 3 plugins from backup")
        self.assertIn("plugin-a", recovered_reg.records)
        self.assertIn("plugin-b", recovered_reg.records)
        self.assertIn("plugin-c", recovered_reg.records)
        self.assertNotIn("plugin-d", recovered_reg.records, "plugin D was not saved before crash")

        print("  ✓ Recovery successful - state restored to A, B, C")

        # ─── Phase 4: Resume operation (install D again) ────────────────────
        print("\n[Phase 4] Resuming operation - installing plugin D again...")

        # Add plugin D and save
        recovered_reg.records["plugin-d"] = _record("plugin-d")
        recovered_reg.save()

        print(f"  ✓ Plugin D installed")

        # Verify final state
        final_reg = TenantRegistry.load(
            tenant_id="_default", corvin_home_path=self.home, auto_recover=False
        )
        self.assertEqual(len(final_reg.records), 4)
        self.assertIn("plugin-d", final_reg.records)

        print(f"  Final state: 4 plugins (A, B, C, D)")
        print(f"  Backup exists: {bak_path.exists()}")

        # ─── Summary ──────────────────────────────────────────────────────────
        print("\n[Summary]")
        print("  ✓ Phase 1: Initial save of A, B, C")
        print("  ✓ Phase 2: Crash during D save (corrupted file)")
        print("  ✓ Phase 3: Auto-recovery to A, B, C")
        print("  ✓ Phase 4: Resumed operation, installed D")
        print("  ✓ All phases completed successfully")

    def test_double_corruption_scenario(self):
        """
        Scenario:
        1. Registry and backup both become corrupted
        2. Recovery should fail with clear error
        3. Operator sees RegistryCorrupt exception
        """
        print("\n[Double Corruption Test]")
        reg_path = registry_path(tenant_id="_default", corvin_home_path=self.home)
        bak_path = registry_backup_path(tenant_id="_default", corvin_home_path=self.home)

        # Create both files as corrupted
        reg_path.parent.mkdir(parents=True, exist_ok=True)
        reg_path.write_text("{ main registry is corrupted")
        bak_path.write_text("{ backup is also corrupted")

        print(f"  Main file corrupted: {reg_path.exists()}")
        print(f"  Backup file corrupted: {bak_path.exists()}")

        # Try to load with auto-recovery
        with self.assertRaises(RegistryCorrupt) as cm:
            TenantRegistry.load(
                tenant_id="_default", corvin_home_path=self.home, auto_recover=True
            )

        error_msg = str(cm.exception)
        self.assertIn("backup", error_msg)
        self.assertIn("corrupted", error_msg)

        print(f"  ✓ Correctly raised RegistryCorrupt:")
        print(f"    {error_msg}")
        print(f"  ✓ Fail-closed behavior verified")

    def test_no_backup_scenario(self):
        """
        Scenario:
        1. Registry corrupted on first save (no backup exists yet)
        2. Recovery should fail with clear error
        3. Operator can check audit trail for reconstruction
        """
        print("\n[No Backup Test]")
        reg_path = registry_path(tenant_id="_default", corvin_home_path=self.home)
        bak_path = registry_backup_path(tenant_id="_default", corvin_home_path=self.home)

        # Create corrupted registry with no backup
        reg_path.parent.mkdir(parents=True, exist_ok=True)
        reg_path.write_text("{ corrupted")

        print(f"  Registry corrupted: {reg_path.exists()}")
        print(f"  Backup exists: {bak_path.exists()}")

        # Try to load with auto-recovery
        with self.assertRaises(RegistryCorrupt) as cm:
            TenantRegistry.load(
                tenant_id="_default", corvin_home_path=self.home, auto_recover=True
            )

        error_msg = str(cm.exception)
        self.assertIn("no backup", error_msg)

        print(f"  ✓ Correctly raised RegistryCorrupt:")
        print(f"    {error_msg}")
        print(f"  ✓ Fail-closed behavior verified")


if __name__ == "__main__":
    # Run with verbose output to see the scenario play-by-play
    unittest.main(verbosity=2)

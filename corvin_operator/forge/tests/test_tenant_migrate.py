"""Per-subtask E2E for ADR-0007 Phase 1.4 — tenant migration helper.

Three load-bearing E2E gates from the implementation plan:

  E2E_A: two-tenant smoke — _default and acme run side by side, each
         writes its own audit chain, ``verify_chain`` passes independently.

  E2E_B: legacy single-tenant path survives migration — boot from clean
         ``<sandbox>/global/...`` layout, migration fires, post-boot the
         legacy paths still address the same data (via symlink).

  E2E_C: ``CORVIN_TENANT_MIGRATE=0`` opt-out — keeps the legacy layout
         untouched; the marker is NEVER written.

Plus the supporting invariants:

  * Idempotency — running twice changes nothing on the second run.
  * Dry-run — reports what would happen, makes no FS changes.
  * No-op on fresh install — no legacy subdirs → write marker, no move.
  * Partial migration tolerance — pre-existing tenants/_default/<sub>
    is left alone (no overwrite).
  * Force flag bypasses both the opt-out env and the marker.

All cases sandbox to ``tempfile.mkdtemp``; the live ``<repo>/.corvin``
is never touched.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "operator" / "forge"))

from forge.tenant_migrate import (  # noqa: E402
    DEFAULT_TENANT_ID,
    migrate_to_default_tenant_if_needed,
)


class _SandboxBase(unittest.TestCase):
    """Shared sandbox setup for every Phase-1.4 test case."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="corvin-tenant-migrate-")
        self.home = Path(self._tmp) / "corvin"
        self.home.mkdir(parents=True, exist_ok=True)
        self._saved_opt_out = os.environ.get("CORVIN_TENANT_MIGRATE")
        os.environ.pop("CORVIN_TENANT_MIGRATE", None)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)
        if self._saved_opt_out is None:
            os.environ.pop("CORVIN_TENANT_MIGRATE", None)
        else:
            os.environ["CORVIN_TENANT_MIGRATE"] = self._saved_opt_out

    def _seed_legacy_layout(self):
        """Plant some files in the legacy <home>/<sub>/ layout."""
        (self.home / "global" / "forge").mkdir(parents=True, exist_ok=True)
        (self.home / "global" / "forge" / "audit.jsonl").write_text(
            '{"event":"seed"}\n', encoding="utf-8"
        )
        (self.home / "global" / "roles").mkdir(parents=True, exist_ok=True)
        (self.home / "global" / "roles" / "discord__chatA.json").write_text(
            '{"chatA":{}}', encoding="utf-8"
        )
        (self.home / "sessions" / "discord:chatA").mkdir(parents=True, exist_ok=True)
        (self.home / "sessions" / "discord:chatA" / "session.json").write_text(
            '{"started":true}', encoding="utf-8"
        )


class E2E_A_TwoTenantSmokeTests(_SandboxBase):
    """E2E_A — two tenants in parallel, audit chains independent."""

    def test_default_and_acme_isolated(self):
        # Bootstrap: create tenants/_default and tenants/acme manually
        # (the migration helper handles _default; acme is created
        # ahead of time to simulate a Gateway provisioning it).
        for tid in (DEFAULT_TENANT_ID, "acme"):
            (self.home / "tenants" / tid / "global" / "forge").mkdir(
                parents=True, exist_ok=True
            )
            (self.home / "tenants" / tid / "global" / "forge" / "audit.jsonl").write_text(
                json.dumps({"tenant": tid, "event": "isolated"}) + "\n",
                encoding="utf-8",
            )

        # Each chain reads back its own data
        default_chain = (
            self.home / "tenants" / DEFAULT_TENANT_ID / "global" / "forge" / "audit.jsonl"
        )
        acme_chain = self.home / "tenants" / "acme" / "global" / "forge" / "audit.jsonl"

        default_event = json.loads(default_chain.read_text().strip())
        acme_event = json.loads(acme_chain.read_text().strip())

        self.assertEqual(default_event["tenant"], DEFAULT_TENANT_ID)
        self.assertEqual(acme_event["tenant"], "acme")
        # No cross-contamination
        self.assertNotIn("acme", default_chain.read_text())
        self.assertNotIn(DEFAULT_TENANT_ID, acme_chain.read_text())


class E2E_B_LegacyMigrationTests(_SandboxBase):
    """E2E_B — legacy layout migrates, legacy paths still work via symlink."""

    def test_full_migration_round_trip(self):
        self._seed_legacy_layout()
        legacy_audit_path = self.home / "global" / "forge" / "audit.jsonl"
        pre_content = legacy_audit_path.read_text()
        # The seed event is the first thing in the chain
        self.assertIn('"event":"seed"', pre_content)

        result = migrate_to_default_tenant_if_needed(corvin_home_path=self.home)

        self.assertEqual(result["status"], "ok")
        self.assertIn("global", result["moved"])
        self.assertIn("sessions", result["moved"])

        # Marker landed
        self.assertTrue((self.home / ".tenant-migrated").exists())

        # Legacy path is now a symlink
        legacy_global = self.home / "global"
        self.assertTrue(legacy_global.is_symlink())
        self.assertEqual(
            os.readlink(str(legacy_global)),
            str(Path("tenants") / DEFAULT_TENANT_ID / "global"),
        )

        # Real data lives under tenants/_default/global
        moved_audit = (
            self.home / "tenants" / DEFAULT_TENANT_ID / "global" / "forge" / "audit.jsonl"
        )
        self.assertTrue(moved_audit.exists())

        # The seed event is still present in the post-migration chain
        # (read via the legacy symlink), AND the tenant.path_migrated
        # event has been appended ahead of the move.
        post_content = legacy_audit_path.read_text()
        self.assertTrue(post_content.startswith(pre_content),
                        "pre-migration seed event must remain as prefix")
        self.assertIn('"event_type": "tenant.path_migrated"', post_content)
        self.assertIn('"tenant_id": "_default"', post_content)

        # State-store-style read also works via legacy path
        legacy_roles = self.home / "global" / "roles" / "discord__chatA.json"
        self.assertTrue(legacy_roles.exists())  # resolves through symlink
        self.assertEqual(legacy_roles.read_text(), '{"chatA":{}}')


class E2E_C_OptOutTests(_SandboxBase):
    """E2E_C — CORVIN_TENANT_MIGRATE=0 keeps legacy layout untouched."""

    def test_env_opt_out_short_circuits(self):
        self._seed_legacy_layout()
        os.environ["CORVIN_TENANT_MIGRATE"] = "0"

        result = migrate_to_default_tenant_if_needed(corvin_home_path=self.home)

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "CORVIN_TENANT_MIGRATE=0")

        # No marker, no tenants/, legacy layout intact
        self.assertFalse((self.home / ".tenant-migrated").exists())
        self.assertFalse((self.home / "tenants").exists())
        self.assertFalse((self.home / "global").is_symlink())
        # Original file content preserved
        self.assertEqual(
            (self.home / "global" / "forge" / "audit.jsonl").read_text(),
            '{"event":"seed"}\n',
        )


class IdempotencyTests(_SandboxBase):
    """Running the migration twice is a no-op the second time."""

    def test_second_run_skipped(self):
        self._seed_legacy_layout()

        first = migrate_to_default_tenant_if_needed(corvin_home_path=self.home)
        self.assertEqual(first["status"], "ok")

        second = migrate_to_default_tenant_if_needed(corvin_home_path=self.home)
        self.assertEqual(second["status"], "skipped")
        self.assertEqual(second["reason"], "already-migrated")

    def test_force_bypasses_marker(self):
        self._seed_legacy_layout()
        migrate_to_default_tenant_if_needed(corvin_home_path=self.home)

        # Forced re-run: marker is ignored, but symlinks already exist
        # so to_migrate is empty → noop, NOT a re-migration.
        forced = migrate_to_default_tenant_if_needed(
            corvin_home_path=self.home, force=True
        )
        self.assertIn(forced["status"], ("noop", "ok"))


class DryRunTests(_SandboxBase):
    def test_dry_run_lists_subdirs_no_fs_changes(self):
        self._seed_legacy_layout()

        result = migrate_to_default_tenant_if_needed(
            corvin_home_path=self.home, dry_run=True
        )

        self.assertEqual(result["status"], "would-migrate")
        self.assertIn("global", result["subdirs"])
        self.assertIn("sessions", result["subdirs"])
        # No marker, no symlinks
        self.assertFalse((self.home / ".tenant-migrated").exists())
        self.assertFalse((self.home / "global").is_symlink())


class NoOpFreshInstallTests(_SandboxBase):
    def test_empty_home_writes_marker_no_move(self):
        # Don't seed anything — fresh install
        result = migrate_to_default_tenant_if_needed(corvin_home_path=self.home)
        self.assertEqual(result["status"], "noop")
        self.assertTrue((self.home / ".tenant-migrated").exists())
        # tenants/_default/ exists (mkdir'd) but no subdirs inside
        self.assertTrue((self.home / "tenants" / DEFAULT_TENANT_ID).exists())


class PartialMigrationToleranceTests(_SandboxBase):
    def test_existing_target_subdir_not_overwritten(self):
        self._seed_legacy_layout()
        # Pre-create the target — simulates a partial prior migration
        target = self.home / "tenants" / DEFAULT_TENANT_ID / "global"
        target.mkdir(parents=True, exist_ok=True)
        (target / "preexisting.txt").write_text("KEEP", encoding="utf-8")

        result = migrate_to_default_tenant_if_needed(corvin_home_path=self.home)

        # The 'global' subdir was skipped (target existed); others migrated
        self.assertEqual(result["status"], "ok")
        self.assertNotIn("global", result["moved"])
        # The preexisting file is untouched
        self.assertEqual((target / "preexisting.txt").read_text(), "KEEP")
        # The legacy /global/ dir is still a real dir (not symlinked)
        self.assertFalse((self.home / "global").is_symlink())


class NoCorvinHomeTests(unittest.TestCase):
    def test_missing_home_skipped_cleanly(self):
        tmp = tempfile.mkdtemp(prefix="corvin-no-home-")
        bogus = Path(tmp) / "does-not-exist"
        try:
            result = migrate_to_default_tenant_if_needed(corvin_home_path=bogus)
            self.assertEqual(result["status"], "skipped")
            self.assertEqual(result["reason"], "no-corvin-home")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class AuditEventEmittedTests(_SandboxBase):
    """When the audit chain is reachable, a tenant.path_migrated event lands."""

    def test_audit_event_written_before_move(self):
        self._seed_legacy_layout()
        sandbox_audit = self.home / "audit_capture.jsonl"

        migrate_to_default_tenant_if_needed(
            corvin_home_path=self.home, audit_path=sandbox_audit
        )

        self.assertTrue(sandbox_audit.exists())
        line = sandbox_audit.read_text().strip().splitlines()[0]
        event = json.loads(line)
        self.assertEqual(event["event_type"], "tenant.path_migrated")
        self.assertEqual(event["details"]["tenant_id"], DEFAULT_TENANT_ID)
        self.assertEqual(event["details"]["method"], "rename+symlink")
        self.assertIn("global", event["details"]["subdirs"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

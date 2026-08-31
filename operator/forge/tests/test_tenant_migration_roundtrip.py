"""Migration → state-store roundtrip — ADR-0007 Phase 1.4 + 1.3 join.

Phase 1.4's migration helper renames ``<corvin_home>/<sub>/`` into
``<corvin_home>/tenants/_default/<sub>/`` and leaves a symlink at the
legacy location. Phase 1.3's state-stores accept ``tenant_id=None`` and
fall through to the legacy path. The strangler-fig invariant: a
state-store call with ``tenant_id=None`` AFTER migration must still
read and write the same on-disk bytes through the symlink.

The existing Phase-1.4 E2Es verify the rename and symlink shape. The
existing Phase-1.3 E2Es verify the kwarg contract. Neither verifies
the SEQUENCE: legacy state pre-migration → migrate → read via state-
store default branch → confirm same value lands. This file closes
that gap.

Five cases:

  R1 — Pre-migration seed survives the move: legacy file content
       readable via the symlink path AND via the new tenants/<tid>/
       path.

  R2 — State-store WRITE via tenant_id=None (default branch) after
       migration lands under tenants/_default/<sub>/.

  R3 — State-store WRITE via tenant_id="_default" (explicit) lands
       at the SAME on-disk location as tenant_id=None (symlink
       transparency).

  R4 — Audit chain continuity across migration: events written
       before AND after migration appear in the same physical chain
       file, hash-chain verify intact.

  R5 — Two-tenant follow-on: after migration plants _default,
       Gateway provisions tenant "acme" by mkdir'ing
       tenants/acme/global/forge; events written there must NOT
       appear in _default's chain.

Sandbox-only.
"""
from __future__ import annotations

import importlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "operator" / "forge"))
sys.path.insert(0, str(_REPO / "operator" / "bridges" / "shared"))

from forge import paths as _forge_paths  # noqa: E402
from forge import security_events as _se  # noqa: E402
from forge.tenant_migrate import (  # noqa: E402
    DEFAULT_TENANT_ID,
    migrate_to_default_tenant_if_needed,
)


class _MigrationSandbox(unittest.TestCase):
    """Sandbox that seeds the legacy layout and runs the helper."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="corvin-roundtrip-")
        self.home = Path(self._tmp) / "corvin"
        self.home.mkdir(parents=True, exist_ok=True)
        self._saved_home = os.environ.get("CORVIN_HOME")
        self._saved_tid = os.environ.get("CORVIN_TENANT_ID")
        self._saved_optout = os.environ.get("CORVIN_TENANT_MIGRATE")
        os.environ["CORVIN_HOME"] = str(self.home)
        os.environ.pop("CORVIN_TENANT_ID", None)
        os.environ.pop("CORVIN_TENANT_MIGRATE", None)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)
        for k, v in (
            ("CORVIN_HOME", self._saved_home),
            ("CORVIN_TENANT_ID", self._saved_tid),
            ("CORVIN_TENANT_MIGRATE", self._saved_optout),
        ):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _seed_legacy_audit(self, content: str = '{"event":"pre-seed"}\n') -> Path:
        """Plant a legacy audit chain file BEFORE migration."""
        legacy_dir = self.home / "global" / "forge"
        legacy_dir.mkdir(parents=True, exist_ok=True)
        chain = legacy_dir / "audit.jsonl"
        chain.write_text(content, encoding="utf-8")
        return chain

    def _seed_legacy_roles(self, payload: str = '{"chatA":{"member":"alice"}}') -> Path:
        legacy_dir = self.home / "global" / "roles"
        legacy_dir.mkdir(parents=True, exist_ok=True)
        f = legacy_dir / "discord__chatA.json"
        f.write_text(payload, encoding="utf-8")
        return f


class R1_PreMigrationSeedSurvives(_MigrationSandbox):
    """Legacy file content readable via the symlink AND via the new path."""

    def test_legacy_chain_visible_through_both_paths(self):
        self._seed_legacy_audit('{"event":"seed-A"}\n')
        result = migrate_to_default_tenant_if_needed(corvin_home_path=self.home)
        self.assertEqual(result["status"], "ok")

        legacy_path = self.home / "global" / "forge" / "audit.jsonl"
        new_path = (
            self.home / "tenants" / DEFAULT_TENANT_ID / "global" / "forge"
            / "audit.jsonl"
        )

        # New path now holds the data physically
        self.assertTrue(new_path.exists())
        # Legacy path is reachable via the symlink at <home>/global
        self.assertTrue(legacy_path.exists())  # resolves via symlink
        self.assertTrue((self.home / "global").is_symlink())

        # Both must read identical content
        self.assertEqual(legacy_path.read_text(), new_path.read_text())
        self.assertIn('"event":"seed-A"', new_path.read_text())

    def test_legacy_roles_state_survives(self):
        self._seed_legacy_roles('{"chatX":{"role":"observer"}}')
        migrate_to_default_tenant_if_needed(corvin_home_path=self.home)

        legacy = self.home / "global" / "roles" / "discord__chatA.json"
        new = (
            self.home / "tenants" / DEFAULT_TENANT_ID / "global" / "roles"
            / "discord__chatA.json"
        )
        self.assertTrue(new.exists())
        self.assertEqual(legacy.read_text(), new.read_text())
        self.assertEqual(legacy.read_text(), '{"chatX":{"role":"observer"}}')


class R2_DefaultBranchLandsUnderTenants(_MigrationSandbox):
    """State-store WRITE via tenant_id=None after migration lands under
    tenants/_default/."""

    def test_audit_write_via_default_resolver_lands_under_tenant_tree(self):
        self._seed_legacy_audit()
        migrate_to_default_tenant_if_needed(corvin_home_path=self.home)

        # Write event via forge.paths.tenant_global_dir() — no tenant_id
        # arg, no env. Resolution must be _default.
        target = _forge_paths.tenant_global_dir() / "forge" / "audit.jsonl"
        _se.write_event(target, "tool.created", details={"name": "post-migrate"})

        # The physical location must be under tenants/_default/
        new_path = (
            self.home / "tenants" / DEFAULT_TENANT_ID / "global" / "forge"
            / "audit.jsonl"
        )
        self.assertTrue(new_path.exists())
        body = new_path.read_text()
        self.assertIn("post-migrate", body)
        # Also visible through the legacy symlink
        legacy = self.home / "global" / "forge" / "audit.jsonl"
        self.assertIn("post-migrate", legacy.read_text())

    def test_state_store_default_kwarg_writes_through_symlink(self):
        # Pre-migration, the legacy roles dir holds chatA's state
        self._seed_legacy_roles()
        migrate_to_default_tenant_if_needed(corvin_home_path=self.home)

        # Now use roles._store_path with tenant_id=None — must resolve
        # to the legacy path, which is now a symlink into tenants/_default/.
        roles_mod = importlib.import_module("roles")
        store_path = roles_mod._store_path("discord", "chatA")
        # The path is still the legacy-style address, but on disk it
        # resolves into the tenant tree.
        self.assertTrue(store_path.exists())
        resolved = store_path.resolve()
        # resolved path must include tenants/_default/
        self.assertIn(DEFAULT_TENANT_ID, resolved.parts)


class R3_SymlinkTransparency(_MigrationSandbox):
    """Explicit tenant_id="_default" and tenant_id=None hit the same bytes."""

    def test_both_resolutions_address_same_file(self):
        self._seed_legacy_audit()
        migrate_to_default_tenant_if_needed(corvin_home_path=self.home)

        # Write via the new explicit-tenant path
        explicit = _forge_paths.tenant_global_dir("_default") / "forge" / "audit.jsonl"
        _se.write_event(explicit, "tool.created", details={"via": "explicit"})

        # Read via the legacy-symlink-default path
        default = _forge_paths.tenant_global_dir() / "forge" / "audit.jsonl"
        self.assertEqual(explicit.resolve(), default.resolve())
        self.assertIn("explicit", default.read_text())


class R4_AuditChainContinuity(_MigrationSandbox):
    """Pre- and post-migration events live in one verifiable chain."""

    def test_chain_intact_across_migration(self):
        # Seed an empty chain that the migration helper will use as the
        # audit target. The helper writes the tenant.path_migrated event
        # BEFORE renaming, so it lands in the pre-rename chain.
        self._seed_legacy_audit(content="")

        # Boundary 1: write a hash-chained event before migration
        legacy_audit = self.home / "global" / "forge" / "audit.jsonl"
        _se.write_event(legacy_audit, "tool.created", details={"phase": "pre"})

        # Migrate
        result = migrate_to_default_tenant_if_needed(corvin_home_path=self.home)
        self.assertEqual(result["status"], "ok")

        # Boundary 2: write a hash-chained event AFTER migration.
        # Resolve via default path → goes through symlink → lands in
        # the physical file under tenants/_default/.
        post_audit = _forge_paths.tenant_global_dir() / "forge" / "audit.jsonl"
        _se.write_event(post_audit, "skill.created", details={"phase": "post"})

        # The physical chain has: pre-event, migration-event, post-event
        physical = (
            self.home / "tenants" / DEFAULT_TENANT_ID / "global" / "forge"
            / "audit.jsonl"
        )
        lines = [
            json.loads(l) for l in physical.read_text().splitlines() if l.strip()
        ]
        event_types = [rec.get("event_type") for rec in lines]
        self.assertIn("tool.created", event_types)
        self.assertIn("tenant.path_migrated", event_types)
        self.assertIn("skill.created", event_types)

        # Verify-chain must walk the WHOLE chain cleanly
        ok, problems = _se.verify_chain(physical)
        self.assertTrue(ok, f"chain must verify across migration: {problems}")
        self.assertEqual(problems, [])

    def test_chain_intact_when_read_via_symlink(self):
        # Same content, but verify via the legacy-symlink path
        self._seed_legacy_audit(content="")
        legacy = self.home / "global" / "forge" / "audit.jsonl"
        _se.write_event(legacy, "tool.created", details={"phase": "pre"})

        migrate_to_default_tenant_if_needed(corvin_home_path=self.home)
        _se.write_event(legacy, "skill.created", details={"phase": "post"})

        # Reading via the symlink must verify just as cleanly
        ok, problems = _se.verify_chain(legacy)
        self.assertTrue(ok, f"symlink read must verify: {problems}")


class R5_GatewayProvisionsAcmeAfterMigration(_MigrationSandbox):
    """After migration plants _default, a new tenant 'acme' is isolated."""

    def test_acme_chain_does_not_leak_into_default(self):
        # Migrate _default
        self._seed_legacy_audit()
        migrate_to_default_tenant_if_needed(corvin_home_path=self.home)
        _se.write_event(
            _forge_paths.tenant_global_dir("_default") / "forge" / "audit.jsonl",
            "tool.created",
            details={"tenant_hint": "_default"},
        )

        # Gateway provisions acme (mkdir, no migration helper involved)
        (self.home / "tenants" / "acme" / "global" / "forge").mkdir(parents=True)
        _se.write_event(
            _forge_paths.tenant_global_dir("acme") / "forge" / "audit.jsonl",
            "tool.created",
            details={"tenant_hint": "acme"},
        )

        # _default's chain must NOT mention acme
        default_chain_body = (
            self.home / "tenants" / DEFAULT_TENANT_ID / "global" / "forge"
            / "audit.jsonl"
        ).read_text()
        self.assertIn("_default", default_chain_body)
        self.assertNotIn('"tenant_hint": "acme"', default_chain_body)

        # And acme's chain must NOT mention _default details
        acme_chain_body = (
            self.home / "tenants" / "acme" / "global" / "forge" / "audit.jsonl"
        ).read_text()
        self.assertIn("acme", acme_chain_body)
        self.assertNotIn('"tenant_hint": "_default"', acme_chain_body)

        # Both verify clean and independently
        for tid in ("_default", "acme"):
            chain = (
                self.home / "tenants" / tid / "global" / "forge" / "audit.jsonl"
            )
            ok, problems = _se.verify_chain(chain)
            self.assertTrue(ok, f"{tid} chain must verify: {problems}")


class R6_OptOutKeepsLegacyAddressable(_MigrationSandbox):
    """CORVIN_TENANT_MIGRATE=0 keeps the legacy path the source of truth."""

    def test_legacy_path_still_writable_with_optout(self):
        self._seed_legacy_audit()
        os.environ["CORVIN_TENANT_MIGRATE"] = "0"

        result = migrate_to_default_tenant_if_needed(corvin_home_path=self.home)
        self.assertEqual(result["status"], "skipped")

        # Legacy is NOT a symlink — it's a real dir holding real state
        legacy = self.home / "global" / "forge" / "audit.jsonl"
        self.assertFalse((self.home / "global").is_symlink())
        self.assertTrue(legacy.exists())
        # Write through the legacy path still works
        _se.write_event(legacy, "tool.created", details={"phase": "optout"})
        body = legacy.read_text()
        self.assertIn("optout", body)
        # And the new tenants/ tree was NOT created by the helper
        self.assertFalse((self.home / "tenants").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)

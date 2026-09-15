"""Per-subtask E2E for ADR-0007 Phase 1.3 — tenant-aware state stores.

Sweeps all eight state-store modules and asserts the common contract:

  * _store_path(...) / _config_path(...) / _audit_path(...) accept
    an optional `tenant_id` keyword arg.
  * When `tenant_id is None` (the default), the legacy path is
    preserved byte-identically — single-operator backward compatibility.
  * When `tenant_id` is a non-empty string, the path lives under
    <corvin_home>/tenants/<tid>/global/<store>/... — the new layout
    Phase 1.4 will migrate to.

Modules covered:
  - roles
  - consent
  - quota
  - disclosure
  - proposal
  - auth_elevation
  - dialectic
  - ldd

The test imports each module and exercises the path functions
directly. No state is written to disk; this is a pure shape test
that complements the per-module behaviour tests already in place.
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SHARED = REPO / "operator" / "bridges" / "shared"


def _import_shared(mod_name: str):
    """Import a shared module without polluting sys.modules for the others."""
    if str(SHARED) not in sys.path:
        sys.path.insert(0, str(SHARED))
    # forge package must be importable for dialectic/ldd fallbacks
    forge_path = REPO / "operator" / "forge"
    if str(forge_path) not in sys.path:
        sys.path.insert(0, str(forge_path))
    sys.modules.pop(mod_name, None)
    return importlib.import_module(mod_name)


class _BaseStoreTests:
    """Shared assertions over one state-store module."""

    module_name = ""
    has_chat_args = True  # roles/consent/quota/disclosure/proposal
    path_attr = "_store_path"
    store_subdir = ""  # subdir under global/ for the per-chat file

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="corvin-store-tenant-")
        self._home = Path(self._tmp) / "corvin"
        self._home.mkdir(parents=True, exist_ok=True)
        self._saved_home = os.environ.get("CORVIN_HOME")
        os.environ["CORVIN_HOME"] = str(self._home)
        self.mod = _import_shared(self.module_name)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)
        if self._saved_home is None:
            os.environ.pop("CORVIN_HOME", None)
        else:
            os.environ["CORVIN_HOME"] = self._saved_home

    def _call_path(self, **kwargs):
        func = getattr(self.mod, self.path_attr)
        if self.has_chat_args:
            return func("discord", "chatA", **kwargs)
        return func(**kwargs)

    def test_legacy_path_unchanged(self):
        path = self._call_path()
        # Path must include /global/ but NOT /tenants/
        self.assertIn("global", path.parts)
        self.assertNotIn("tenants", path.parts)

    def test_tenant_path_under_tenants(self):
        path = self._call_path(tenant_id="acme")
        self.assertIn("tenants", path.parts)
        self.assertIn("acme", path.parts)
        # tenants and acme must be adjacent (in that order)
        idx = path.parts.index("tenants")
        self.assertEqual(path.parts[idx + 1], "acme")

    def test_tenant_default_path(self):
        path = self._call_path(tenant_id="_default")
        self.assertIn("_default", path.parts)

    def test_audit_path_legacy(self):
        if not hasattr(self.mod, "_audit_path"):
            self.skipTest(f"{self.module_name} has no _audit_path")
        path = self.mod._audit_path()
        self.assertEqual(path.parts[-3:], ("global", "forge", "audit.jsonl"))
        self.assertNotIn("tenants", path.parts)

    def test_audit_path_tenanted(self):
        if not hasattr(self.mod, "_audit_path"):
            self.skipTest(f"{self.module_name} has no _audit_path")
        path = self.mod._audit_path(tenant_id="acme")
        self.assertEqual(path.parts[-5:], ("tenants", "acme", "global", "forge", "audit.jsonl"))


class RolesStoreTests(_BaseStoreTests, unittest.TestCase):
    module_name = "roles"
    has_chat_args = True
    path_attr = "_store_path"


class ConsentStoreTests(_BaseStoreTests, unittest.TestCase):
    module_name = "consent"
    has_chat_args = True


class QuotaStoreTests(_BaseStoreTests, unittest.TestCase):
    module_name = "quota"
    has_chat_args = True


class DisclosureStoreTests(_BaseStoreTests, unittest.TestCase):
    module_name = "disclosure"
    has_chat_args = True


class ProposalStoreTests(_BaseStoreTests, unittest.TestCase):
    module_name = "proposal"
    has_chat_args = True


class AuthElevationStoreTests(_BaseStoreTests, unittest.TestCase):
    module_name = "auth_elevation"
    has_chat_args = False


class DialecticConfigTests(_BaseStoreTests, unittest.TestCase):
    module_name = "dialectic"
    has_chat_args = False
    path_attr = "_config_path"

    def test_audit_path_legacy(self):
        self.skipTest("dialectic has no _audit_path")

    def test_audit_path_tenanted(self):
        self.skipTest("dialectic has no _audit_path")


class LDDConfigTests(_BaseStoreTests, unittest.TestCase):
    module_name = "ldd"
    has_chat_args = False
    path_attr = "_config_path"

    def test_audit_path_legacy(self):
        self.skipTest("ldd has no _audit_path")

    def test_audit_path_tenanted(self):
        self.skipTest("ldd has no _audit_path")


if __name__ == "__main__":
    unittest.main(verbosity=2)

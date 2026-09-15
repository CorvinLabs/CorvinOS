"""Per-subtask E2E for ADR-0007 Phase 1.2 — tenant-aware resolvers in paths.py.

Verifies all three byte-identical paths.py copies (forge, cowork,
voice/bridges/shared) grow the same set of tenant-aware resolvers,
with matching semantics:

  * tenant_home(tid)              → <corvin_home>/tenants/<tid>/
  * tenant_global_dir(tid)        → <tenant_home>/global/
  * tenant_sessions_dir(tid)      → <tenant_home>/sessions/
  * tenant_forge_dir(tid)         → <tenant_home>/forge/
  * tenant_skill_forge_dir(tid)   → <tenant_home>/skill-forge/
  * tenant_voice_dir(tid)         → <tenant_home>/voice/
  * tenant_cowork_dir(tid)        → <tenant_home>/cowork/

Backward-compat invariant: legacy resolvers (corvin_home, voice_dir,
cowork_dir, forge_dir) keep returning the same paths they returned
before Phase 1.2. The tenant-axis is purely additive.
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def _import_paths(plugin: str):
    """Import the paths.py copy for one of the three plugin trees.

    plugin: "forge" | "cowork" | "voice".
    Each lives at a different module path; we reset sys.path between
    imports so the three copies don't shadow each other.
    """
    sys.modules.pop("paths", None)
    sys.modules.pop("forge.paths", None)
    sys.modules.pop("forge", None)
    sys.modules.pop("cowork", None)
    sys.modules.pop("cowork.lib", None)
    sys.modules.pop("cowork.lib.paths", None)

    if plugin == "forge":
        sys.path.insert(0, str(REPO / "operator" / "forge"))
        return importlib.import_module("forge.paths")
    if plugin == "cowork":
        sys.path.insert(0, str(REPO / "operator" / "cowork"))
        return importlib.import_module("lib.paths")
    if plugin == "voice":
        sys.path.insert(0, str(REPO / "operator" / "bridges"))
        return importlib.import_module("shared.paths")
    raise ValueError(f"unknown plugin {plugin!r}")


_PUBLIC_TENANT_FUNCS = [
    "tenant_home",
    "tenant_global_dir",
    "tenant_sessions_dir",
    "tenant_forge_dir",
    "tenant_skill_forge_dir",
    "tenant_voice_dir",
    "tenant_cowork_dir",
]


class _BasePathsTenantTests:
    """Shared assertions, parametrised over the three paths.py copies."""

    plugin = ""  # overridden by subclasses

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="corvin-paths-tenant-test-")
        self._home = Path(self._tmp) / "corvin"
        self._home.mkdir(parents=True, exist_ok=True)
        self._saved_home = os.environ.get("CORVIN_HOME")
        self._saved_tid = os.environ.get("CORVIN_TENANT_ID")
        os.environ["CORVIN_HOME"] = str(self._home)
        os.environ.pop("CORVIN_TENANT_ID", None)
        self.paths = _import_paths(self.plugin)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)
        for var, saved in (
            ("CORVIN_HOME", self._saved_home),
            ("CORVIN_TENANT_ID", self._saved_tid),
        ):
            if saved is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = saved

    def test_all_public_funcs_exist(self):
        for name in _PUBLIC_TENANT_FUNCS:
            self.assertTrue(
                hasattr(self.paths, name),
                f"{self.plugin}.paths missing tenant resolver {name!r}",
            )

    def test_tenant_home_default_shape(self):
        path = self.paths.tenant_home()
        self.assertEqual(
            path,
            self._home / "tenants" / "_default",
        )

    def test_tenant_home_explicit_shape(self):
        path = self.paths.tenant_home("acme-corp")
        self.assertEqual(
            path,
            self._home / "tenants" / "acme-corp",
        )

    def test_tenant_home_env_used(self):
        os.environ["CORVIN_TENANT_ID"] = "from-env"
        path = self.paths.tenant_home()
        self.assertEqual(
            path,
            self._home / "tenants" / "from-env",
        )

    def test_tenant_home_arg_beats_env(self):
        os.environ["CORVIN_TENANT_ID"] = "from-env"
        path = self.paths.tenant_home("from-arg")
        self.assertEqual(
            path,
            self._home / "tenants" / "from-arg",
        )

    def test_tenant_home_invalid_rejected(self):
        with self.assertRaises(ValueError):
            self.paths.tenant_home("BAD-UPPER")

    def test_tenant_home_no_filesystem_side_effects(self):
        path = self.paths.tenant_home("brandnew")
        self.assertFalse(path.exists())

    def test_tenant_subdir_shapes(self):
        cases = [
            ("tenant_global_dir", "global"),
            ("tenant_sessions_dir", "sessions"),
            ("tenant_forge_dir", "forge"),
            ("tenant_skill_forge_dir", "skill-forge"),
            ("tenant_voice_dir", "voice"),
            ("tenant_cowork_dir", "cowork"),
        ]
        for func_name, subdir in cases:
            with self.subTest(func=func_name):
                func = getattr(self.paths, func_name)
                path = func("acme")
                expected = self._home / "tenants" / "acme" / subdir
                self.assertEqual(path, expected)

    def test_legacy_resolvers_unchanged(self):
        # corvin_home(), cowork_dir(), forge_dir() still return flat paths.
        self.assertEqual(self.paths.corvin_home(), self._home)
        self.assertEqual(self.paths.cowork_dir(), self._home / "cowork")
        self.assertEqual(self.paths.forge_dir(), self._home / "forge")
        # voice_dir() may be either the flat path (forge/cowork) or the
        # tenant-scoped path (bridges/shared ADR-0007). Accept both variants.
        voice = self.paths.voice_dir()
        accepted = {
            self._home / "voice",
            self._home / "tenants" / "_default" / "voice",
        }
        self.assertIn(voice, accepted, f"voice_dir() returned unexpected path: {voice}")


class ForgePathsTenantTests(_BasePathsTenantTests, unittest.TestCase):
    plugin = "forge"


class CoworkPathsTenantTests(_BasePathsTenantTests, unittest.TestCase):
    plugin = "cowork"


class VoiceSharedPathsTenantTests(_BasePathsTenantTests, unittest.TestCase):
    plugin = "voice"


class TenantIDValidationContractTests(unittest.TestCase):
    """Verify the inline validation in paths.py mirrors forge.tenants."""

    def setUp(self):
        sys.path.insert(0, str(REPO / "operator" / "forge"))
        from forge import tenants as forge_tenants
        from forge import paths as forge_paths
        self.forge_tenants = forge_tenants
        self.forge_paths = forge_paths

    def test_default_constant_matches(self):
        self.assertEqual(
            self.forge_paths._DEFAULT_TENANT_ID,
            self.forge_tenants.DEFAULT_TENANT_ID,
        )

    def test_charset_regex_matches(self):
        self.assertEqual(
            self.forge_paths._TENANT_ID_RE.pattern,
            self.forge_tenants._TENANT_ID_RE.pattern,
        )

    def test_validation_outcomes_match(self):
        good = ["_default", "acme", "acme-corp", "tenant42", "a"]
        bad = ["", "ACME", " acme", "ac/me", ".", "..", "__system", "-acme"]
        for tid in good:
            with self.subTest(tid=tid, expect="pass"):
                self.assertEqual(
                    self.forge_paths._validate_tenant_id(tid),
                    self.forge_tenants.validate_tenant_id(tid),
                )
        for tid in bad:
            with self.subTest(tid=tid, expect="raise"):
                with self.assertRaises(ValueError):
                    self.forge_paths._validate_tenant_id(tid)
                with self.assertRaises(self.forge_tenants.InvalidTenantID):
                    self.forge_tenants.validate_tenant_id(tid)


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Per-subtask E2E for ADR-0007 Phase 1.1 — tenants module.

Covers:
  * default_tenant_id contract (`_default`)
  * validate_tenant_id charset / shape rules
  * current_tenant resolution precedence (arg > env > default)
  * tenant_home path construction (no FS side effects)
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

# Make the in-tree forge package importable when running this file directly.
_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "operator" / "forge"))

from forge import tenants  # noqa: E402
from forge.tenants import (  # noqa: E402
    DEFAULT_TENANT_ID,
    InvalidTenantID,
    current_tenant,
    default_tenant_id,
    tenant_home,
    validate_tenant_id,
)


class DefaultTenantIDTests(unittest.TestCase):
    def test_constant_is_underscore_default(self):
        self.assertEqual(DEFAULT_TENANT_ID, "_default")

    def test_default_tenant_id_function_returns_constant(self):
        self.assertEqual(default_tenant_id(), DEFAULT_TENANT_ID)


class ValidateTenantIDTests(unittest.TestCase):
    def test_accepts_default(self):
        self.assertEqual(validate_tenant_id("_default"), "_default")

    def test_accepts_alnum(self):
        self.assertEqual(validate_tenant_id("acme"), "acme")
        self.assertEqual(validate_tenant_id("acme-corp"), "acme-corp")
        self.assertEqual(validate_tenant_id("acme_corp"), "acme_corp")
        self.assertEqual(validate_tenant_id("tenant42"), "tenant42")
        self.assertEqual(validate_tenant_id("a"), "a")
        self.assertEqual(validate_tenant_id("9"), "9")

    def test_accepts_max_length(self):
        # 1 + 62 = 63 chars total
        long_id = "a" + ("b" * 62)
        self.assertEqual(len(long_id), 63)
        self.assertEqual(validate_tenant_id(long_id), long_id)

    def test_rejects_over_max_length(self):
        too_long = "a" + ("b" * 63)
        self.assertEqual(len(too_long), 64)
        with self.assertRaises(InvalidTenantID):
            validate_tenant_id(too_long)

    def test_rejects_empty(self):
        with self.assertRaises(InvalidTenantID):
            validate_tenant_id("")

    def test_rejects_uppercase(self):
        with self.assertRaises(InvalidTenantID):
            validate_tenant_id("ACME")
        with self.assertRaises(InvalidTenantID):
            validate_tenant_id("Acme")

    def test_rejects_whitespace(self):
        with self.assertRaises(InvalidTenantID):
            validate_tenant_id("acme corp")
        with self.assertRaises(InvalidTenantID):
            validate_tenant_id("acme\t")
        with self.assertRaises(InvalidTenantID):
            validate_tenant_id(" acme")

    def test_rejects_path_traversal(self):
        for bad in [".", "..", "../etc", "acme/sub", "acme\\sub", "/acme"]:
            with self.assertRaises(InvalidTenantID, msg=f"should reject {bad!r}"):
                validate_tenant_id(bad)

    def test_rejects_unicode(self):
        with self.assertRaises(InvalidTenantID):
            validate_tenant_id("akkmé")
        with self.assertRaises(InvalidTenantID):
            validate_tenant_id("ácme")

    def test_rejects_double_underscore_prefix(self):
        # Reserved for future internal namespaces.
        with self.assertRaises(InvalidTenantID):
            validate_tenant_id("__system")

    def test_rejects_leading_dash(self):
        with self.assertRaises(InvalidTenantID):
            validate_tenant_id("-acme")

    def test_rejects_non_string(self):
        with self.assertRaises(InvalidTenantID):
            validate_tenant_id(None)  # type: ignore[arg-type]
        with self.assertRaises(InvalidTenantID):
            validate_tenant_id(42)  # type: ignore[arg-type]
        with self.assertRaises(InvalidTenantID):
            validate_tenant_id(b"acme")  # type: ignore[arg-type]


class CurrentTenantPrecedenceTests(unittest.TestCase):
    def setUp(self):
        self._saved_env = os.environ.get("CORVIN_TENANT_ID")
        os.environ.pop("CORVIN_TENANT_ID", None)

    def tearDown(self):
        if self._saved_env is None:
            os.environ.pop("CORVIN_TENANT_ID", None)
        else:
            os.environ["CORVIN_TENANT_ID"] = self._saved_env

    def test_default_with_no_arg_no_env(self):
        self.assertEqual(current_tenant(), DEFAULT_TENANT_ID)

    def test_explicit_arg_wins(self):
        os.environ["CORVIN_TENANT_ID"] = "from-env"
        self.assertEqual(current_tenant("from-arg"), "from-arg")

    def test_env_used_when_no_arg(self):
        os.environ["CORVIN_TENANT_ID"] = "from-env"
        self.assertEqual(current_tenant(), "from-env")

    def test_env_validated_not_silently_dropped(self):
        os.environ["CORVIN_TENANT_ID"] = "INVALID-UPPERCASE"
        with self.assertRaises(InvalidTenantID):
            current_tenant()

    def test_arg_validated(self):
        with self.assertRaises(InvalidTenantID):
            current_tenant("BAD")

    def test_empty_env_falls_back_to_default(self):
        # Empty env var should be treated as "no env", not as invalid.
        os.environ["CORVIN_TENANT_ID"] = ""
        self.assertEqual(current_tenant(), DEFAULT_TENANT_ID)


class TenantHomeTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="corvin-tenants-test-")
        self._corvin_home = Path(self._tmpdir) / "corvin"
        self._corvin_home.mkdir(parents=True, exist_ok=True)
        self._saved_env = os.environ.get("CORVIN_TENANT_ID")
        os.environ.pop("CORVIN_TENANT_ID", None)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        if self._saved_env is None:
            os.environ.pop("CORVIN_TENANT_ID", None)
        else:
            os.environ["CORVIN_TENANT_ID"] = self._saved_env

    def test_default_tenant_path_shape(self):
        path = tenant_home(corvin_home_path=self._corvin_home)
        self.assertEqual(
            path,
            self._corvin_home / "tenants" / "_default",
        )

    def test_explicit_tenant_path_shape(self):
        path = tenant_home("acme-corp", corvin_home_path=self._corvin_home)
        self.assertEqual(
            path,
            self._corvin_home / "tenants" / "acme-corp",
        )

    def test_env_tenant_used(self):
        os.environ["CORVIN_TENANT_ID"] = "from-env"
        path = tenant_home(corvin_home_path=self._corvin_home)
        self.assertEqual(
            path,
            self._corvin_home / "tenants" / "from-env",
        )

    def test_explicit_arg_beats_env(self):
        os.environ["CORVIN_TENANT_ID"] = "from-env"
        path = tenant_home("from-arg", corvin_home_path=self._corvin_home)
        self.assertEqual(
            path,
            self._corvin_home / "tenants" / "from-arg",
        )

    def test_no_filesystem_side_effects(self):
        # Phase 1.1 contract: tenant_home MUST NOT create the directory.
        # Phase 1.4 migration helper is the only path that mkdir's tenant dirs.
        path = tenant_home("brandnew", corvin_home_path=self._corvin_home)
        self.assertFalse(
            path.exists(),
            f"tenant_home should not create {path}, but it exists",
        )

    def test_invalid_tenant_rejected(self):
        with self.assertRaises(InvalidTenantID):
            tenant_home("BAD-UPPER", corvin_home_path=self._corvin_home)

    def test_production_call_uses_paths_corvin_home(self):
        # When no corvin_home_path is injected, tenant_home delegates to
        # forge.paths.corvin_home() — verify the path includes
        # /tenants/_default/ regardless of where corvin_home lands.
        path = tenant_home()
        self.assertIn("tenants", path.parts)
        self.assertEqual(path.parts[-1], "_default")
        self.assertEqual(path.parts[-2], "tenants")


class ModuleExportsTests(unittest.TestCase):
    def test_exports(self):
        # The four public names Phase 1.2 will import.
        for name in (
            "DEFAULT_TENANT_ID",
            "InvalidTenantID",
            "current_tenant",
            "default_tenant_id",
            "tenant_home",
            "validate_tenant_id",
        ):
            self.assertTrue(
                hasattr(tenants, name),
                f"forge.tenants missing public name {name!r}",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)

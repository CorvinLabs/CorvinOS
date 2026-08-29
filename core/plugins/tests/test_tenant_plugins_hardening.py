"""Regression tests for the tenant_plugins security hardening (session 2026-08-28, plan F3).

Locks the two scariest fixes, both verified red->green when introduced:
  * M2 — path-traversal guard: a crafted plugin_id (e.g. '../../x') must NOT let rmtree/copytree
    escape the tenant's installed dir. Before the fix, `uninstall '../../SECRET'` deleted a
    directory outside the tenant.
  * M1 — fail-closed registry load: a present-but-wrong-shape registry file (e.g. a state.py
    dict-format `plugins:` mapping on the same path) must RAISE, never silently reset to [] and
    then overwrite the real records on the next save.

Runnable standalone via unittest (no pytest dependency) and pytest-discoverable for CI.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[3]
for _p in (_REPO / "core" / "plugins", _REPO / "operator" / "forge",
           _REPO / "operator" / "bridges" / "shared", _REPO):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import yaml  # noqa: E402
from corvin_plugins.manifest import InvalidPluginID  # noqa: E402
from corvin_plugins.tenant_plugins import TenantPluginRegistry  # noqa: E402


class _TmpHome(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self._prev = os.environ.get("CORVIN_HOME")
        os.environ["CORVIN_HOME"] = str(self.home)
        self.reg = TenantPluginRegistry(tenant_id="_test")
        self.reg._ensure_dirs()

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("CORVIN_HOME", None)
        else:
            os.environ["CORVIN_HOME"] = self._prev
        self._tmp.cleanup()


class TestPathTraversalGuard(_TmpHome):
    def test_uninstall_traversal_id_raises_and_does_not_delete_outside_tenant(self):
        # sentinel two levels above the installed dir — must survive
        sentinel = (self.reg.installed_dir / ".." / ".." / "SECRET").resolve()
        sentinel.mkdir(parents=True, exist_ok=True)
        (sentinel / "important.txt").write_text("keep")
        # craft a registry entry with a traversal id
        self.reg.registry_path.write_text(
            yaml.dump({"schema_version": "1.0", "tenant_id": "_test",
                       "plugins": [{"plugin_id": "../../SECRET", "version": "1.0.0"}]})
        )
        with self.assertRaises((InvalidPluginID, ValueError)):
            self.reg.unregister_plugin("../../SECRET")
        self.assertTrue(sentinel.exists(), "traversal id must not delete outside the tenant")

    def test_get_plugin_path_rejects_traversal(self):
        with self.assertRaises((InvalidPluginID, ValueError)):
            self.reg.get_plugin_path("../../etc")


class TestFailClosedRegistryLoad(_TmpHome):
    def test_dict_format_registry_raises_and_is_not_reset(self):
        # a state.py-style DICT-format registry (plugins as a mapping) on the same path
        original = yaml.dump({"schema_version": "1.0",
                              "plugins": {"real-a": {"version": "1.0.0"},
                                          "real-b": {"version": "2.0.0"}}})
        self.reg.registry_path.write_text(original)
        with self.assertRaises(ValueError):
            self.reg.list_plugins()  # calls load_registry — must fail closed, not reset
        self.assertEqual(self.reg.registry_path.read_text(), original, "file must be untouched")

    def test_normal_list_format_registry_loads(self):
        self.reg.registry_path.write_text(
            yaml.dump({"schema_version": "1.0",
                       "plugins": [{"plugin_id": "good-plugin", "version": "1.0.0"}]})
        )
        got = self.reg.list_plugins()
        self.assertEqual([p.plugin_id for p in got], ["good-plugin"])

    def test_missing_file_is_empty_not_an_error(self):
        self.reg.registry_path.unlink(missing_ok=True)
        self.assertEqual(self.reg.list_plugins(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

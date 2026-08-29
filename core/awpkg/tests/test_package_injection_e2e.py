"""E2E regression tests for the package->skill-injection bridge (ADR-0459/0460, plan A2/F3).

Locks the behaviour that was verified red->green during the 2026-08-28 implementation session:
  * a `package` workspace scope exists and resolves (ADR-0459)
  * register_components() registers a package skill into the MANIFEST-DRIVEN registry (visible to
    skill_inject), in the `package` scope, with a bootstrap grade that clears the injection gate
  * hyphenated skill names are normalised (`-` -> `_`) with a collision guard (A2)
  * the awpkg CLI `pkg install` activates the package (calls register_components) end-to-end

Runs standalone via unittest (no pytest dependency) AND is discoverable by pytest in CI — the two
verification tiers the plan's F1/F3 require. The skill body passes the SkillForge linter.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[3]
for _p in (_REPO / "core" / "awpkg", _REPO / "operator" / "skill-forge",
           _REPO / "operator" / "forge", _REPO, _HERE.parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import forge.scope as _scope  # noqa: E402
from awpkg.installer import (  # noqa: E402
    InstalledPackage,
    register_components,
    _sanitize_skill_name,
)
from skill_forge.multi_registry import MultiSkillRegistry  # noqa: E402


_SKILL_BODY = """---
name: {name}
type: learned-experience
description: A demo package skill for the injection E2E.
---
# {name}
Reusable demo guidance delivered by an installed package.
"""


class _TmpHome(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self._prev = {k: os.environ.get(k) for k in ("CORVIN_HOME", "CORVIN_TENANT_ID")}
        os.environ["CORVIN_HOME"] = str(self.home)
        os.environ["CORVIN_TENANT_ID"] = "_default"

    def tearDown(self):
        for k, v in self._prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def _write_skill(self, dir_name: str, name: str) -> None:
        d = self.home / "pkgs" / "demo" / "skills" / dir_name
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(_SKILL_BODY.format(name=name), encoding="utf-8")

    def _install(self, components):
        inst = InstalledPackage(
            id="demo.pkg", name="Demo", version="1.0.0", scope="package",
            install_dir=self.home / "pkgs" / "demo", components=components, permissions={},
        )
        return register_components(inst, corvin_home=self.home, tenant_id="_default")

    @staticmethod
    def _registry_names():
        return {sp.name: (sc, sp) for sc, sp in MultiSkillRegistry(tenant_id="_default").list_with_scope()}


class TestPackageScope(unittest.TestCase):
    def test_package_is_a_valid_scope(self):
        self.assertIn("package", _scope.VALID_SCOPES)

    def test_scope_root_resolves_package_and_leaves_others_unchanged(self):
        pkg = _scope.scope_root("package", tenant_id="_test")
        self.assertEqual(pkg.parts[-2:], ("packages", "forge"))
        # existing scopes unchanged
        self.assertEqual(_scope.scope_root("user", tenant_id="_test").name, "forge")


class TestRegisterComponents(_TmpHome):
    def test_skill_registered_visible_and_passes_injection_gate(self):
        self._write_skill("testskill", "testskill")
        summ = self._install({"skills": ["skills/testskill/SKILL.md"]})
        self.assertEqual(summ.get("skills"), ["testskill"])
        reg = self._registry_names()
        self.assertIn("testskill", reg)
        scope, spec = reg["testskill"]
        self.assertEqual(scope, "package")
        # injection gate proxy: skill_inject excludes n_grades<1 or mean<=0
        self.assertGreaterEqual(len(spec.grades), 1)
        self.assertGreater(spec.mean_score, 0.0)

    def test_hyphen_name_normalised_with_collision_guard(self):
        self.assertEqual(_sanitize_skill_name("my-cool-skill"), "my_cool_skill")
        self._write_skill("hyphenskill", "my-hyphen-skill")
        self._write_skill("collide", "my_hyphen_skill")
        summ = self._install(
            {"skills": ["skills/hyphenskill/SKILL.md", "skills/collide/SKILL.md"]}
        )
        self.assertEqual(summ.get("skills"), ["my_hyphen_skill"])  # first, normalised
        self.assertEqual(len(summ.get("skills_skipped", [])), 1)  # second, collision-guarded
        self.assertIn("my_hyphen_skill", self._registry_names())


class TestCliActivatesPackage(_TmpHome):
    def _load_cli(self):
        spec = importlib.util.spec_from_file_location("awpkg_cli", str(_REPO / "core/awpkg/awpkg.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_cli_install_registers_the_skill_end_to_end(self):
        try:
            from helpers import make_awpkg  # awpkg test helper
        except Exception as exc:  # pragma: no cover
            self.skipTest(f"awpkg test helper unavailable: {exc}")
        tmp = Path(tempfile.mkdtemp())
        manifest = {
            "awpkg": "1.0", "id": "demo.clipkg", "name": "CLI Demo", "version": "1.0.0",
            "description": "demo", "components": {"skills": ["skills/clidemoskill/SKILL.md"]},
        }
        pkg = make_awpkg(manifest, {"skills/clidemoskill/SKILL.md": _SKILL_BODY.format(name="clidemoskill")}, tmp)
        cli = self._load_cli()
        rc = cli._cmd_install(argparse.Namespace(file=str(pkg), scope="user"))
        self.assertEqual(rc, 0)
        self.assertIn("clidemoskill", self._registry_names())


if __name__ == "__main__":
    unittest.main(verbosity=2)

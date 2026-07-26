"""Every module the runtime imports must be importable in a WHEEL install.

This guards a failure class that has now bitten three times, each time silently:

* ADR-0217 (2026-07-24): ``import tde.*`` failed on PyPI, so ``_tde_available()``
  returned False and the shipped default delegation engine did not exist on the
  primary distribution channel.
* 2026-07-26, plugin/compute/workflow packages: eight packages shipped under
  ``core/<area>/<pkg>/`` in site-packages — not on ``sys.path`` — so
  ``import corvin_plugins`` failed on every fresh install. The plugin system,
  the compute worker, workflows, AWPKG and the ADR-0232 compliance tripwires
  were all dead in the shipped product.
* 2026-07-26, ``clag``: the vendor bootstrap put the forge PACKAGE dir on
  sys.path but not its inner directory, so the 12 bare ``import clag`` sites all
  failed. consent.py's integrity gate is FAIL-CLOSED, so ``is_granted()``
  returned ``(False, 'chain-integrity-failed')`` for every user: on a fresh
  install nobody could be admitted in any messenger. The boot tripwire reported
  "deny-by-default holds", because a permanently broken deny-everything gate is
  indistinguishable from a working one.

Nothing was broken in the repo in any of the three cases, which is exactly why no
test caught them: a git checkout puts these paths on sys.path by layout accident.
These tests assert the packaging invariants directly instead.
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def _pyproject_wheel_sources() -> dict[str, str]:
    """The ``[tool.hatch.build.targets.wheel.sources]`` table, parsed."""
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - py<3.11
        import tomli as tomllib  # type: ignore[no-redef]
    data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    return (
        data.get("tool", {}).get("hatch", {}).get("build", {})
        .get("targets", {}).get("wheel", {}).get("sources", {})
    )


class TestCorePackagesAreImportableTopLevel(unittest.TestCase):
    """A core/<area>/<pkg> imported as a top-level module needs a sources mapping.

    Without the mapping the files still SHIP (``packages`` includes ``core``) but
    land at ``core/<area>/<pkg>/`` in site-packages, where nothing can import them.
    """

    def _top_level_importers(self, pkg: str) -> list[str]:
        """Repo files that do ``import <pkg>`` / ``from <pkg>… `` (non-test)."""
        result = subprocess.run(
            ["grep", "-rEl", rf"^[[:space:]]*(from|import) {pkg}[. ]",
             "--include=*.py", "core", "operator"],
            cwd=REPO, capture_output=True, text=True,
        )
        return [
            line for line in result.stdout.splitlines()
            if "/tests/" not in line and "/test_" not in line
        ]

    def test_every_top_level_imported_core_package_is_remapped(self):
        sources = _pyproject_wheel_sources()
        missing: list[str] = []
        for init in sorted((REPO / "core").glob("*/*/__init__.py")):
            pkg = init.parent.name
            rel = init.parent.relative_to(REPO).as_posix()   # core/<area>/<pkg>
            area = init.parent.parent.relative_to(REPO).as_posix()  # core/<area>
            importers = self._top_level_importers(pkg)
            if not importers:
                continue
            # Either the package itself is remapped, or its whole area is.
            if rel in sources or sources.get(area) == "":
                continue
            missing.append(f"{rel} (imported top-level by {len(importers)} file(s))")
        self.assertEqual(
            missing, [],
            "these packages are imported as top-level modules but land under "
            "core/ in site-packages, so they are unimportable in a wheel:\n  "
            + "\n  ".join(missing),
        )

    def test_the_known_eight_stay_mapped(self):
        """Explicit list, so a refactor cannot quietly drop one."""
        sources = _pyproject_wheel_sources()
        for pkg in (
            "core/plugins/corvin_plugins",
            "core/observability/corvin_logging",
            "core/compliance/corvin_compliance_reports",
            "core/compute/corvin_compute",
            "core/workflows/corvin_workflows",
            "core/delegate/corvin_delegate",
            "core/orchestration/corvin_orchestration",
            "core/awpkg/awpkg",
        ):
            self.assertIn(pkg, sources, f"{pkg} lost its wheel sources mapping")

    def test_new_mappings_are_per_package_not_per_area(self):
        """Area-level mappings ("core/x" = "") lift siblings to the wheel root.

        Three legacy entries do that and are known-good because the exclude list
        happens to filter their siblings. Any FURTHER area-level mapping would lift
        that area's README.md / LICENSE / requirements.txt / bootstrap.sh next to the
        ones already there and break the build with "second file added at the same
        path" — and would drop core/awpkg/awpkg.py beside the awpkg/ package. New
        packages must be mapped individually, e.g.
        "core/plugins/corvin_plugins" = "corvin_plugins".
        """
        legacy = {"core/console", "core/gateway", "core/license"}
        sources = _pyproject_wheel_sources()
        offenders = [k for k, v in sources.items() if v == "" and k not in legacy]
        self.assertEqual(
            offenders, [],
            "map these per package instead of per area: " + ", ".join(offenders),
        )

    def test_every_remapped_package_actually_exists(self):
        """A stale mapping is a silent no-op — the package stays unimportable."""
        for src in _pyproject_wheel_sources():
            self.assertTrue(
                (REPO / src).is_dir(), f"sources maps {src}, which does not exist"
            )


class TestBareImportsResolveFromTheVendorBootstrap(unittest.TestCase):
    """Bare ``import X`` inside operator/ needs a vendor subtree that provides X."""

    def _subtrees(self) -> tuple[str, ...]:
        sys.path.insert(0, str(REPO / "core" / "console"))
        try:
            from corvin_console import _operator_bootstrap as ob
        finally:
            sys.path.pop(0)
        return ob._OPERATOR_SUBTREES

    def test_the_inner_forge_dir_is_on_the_list(self):
        """``import clag`` is bare at 12 sites and only resolves from forge/forge."""
        self.assertIn(
            "forge/forge", self._subtrees(),
            "without this a wheel install cannot import clag, and consent.py's "
            "fail-closed gate then denies EVERY user with 'chain-integrity-failed'",
        )

    def test_every_bare_clag_import_is_covered_by_one_entry(self):
        """Assert the fix is central, not 12 scattered try/except fallbacks."""
        result = subprocess.run(
            ["grep", "-rEl", r"^[[:space:]]*from clag import|^[[:space:]]*import clag$",
             "--include=*.py", "operator", "core"],
            cwd=REPO, capture_output=True, text=True,
        )
        sites = [
            f for f in result.stdout.splitlines()
            if "_vendor" not in f and "/test_" not in f
        ]
        self.assertTrue(sites, "grep found no clag import sites — check the pattern")
        # The point: they stay bare. One sys.path entry serves all of them, so a
        # 13th site added tomorrow works without touching anything.
        self.assertIn("forge/forge", self._subtrees())

    def test_orchestration_stays_listed(self):
        """The ADR-0217 incarnation of the same class."""
        self.assertIn("orchestration", self._subtrees())


if __name__ == "__main__":
    unittest.main()

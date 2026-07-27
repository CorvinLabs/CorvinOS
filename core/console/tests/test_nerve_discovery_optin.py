"""What the nervous system is allowed to IMPORT and EXECUTE at boot (ADR-0177).

``NerveRegistry.discover()`` runs on the boot path (``boot_healer._heal_cycle``
→ ``scan_all()``).  Two of its three tiers used to reach outside the repo with
no declaration at all:

* Tier 1 called ``ep.load()`` on *every* installed ``corvinOS.nerve_fibers``
  entry point — importing any package that happens to declare one, including a
  transitive dependency the operator never chose;
* Tier 2 called ``exec_module()`` on every ``~/.corvin/nerve_fibers/*.py`` with
  no check that those files could only have been written by the operator.

The plugin loader closed exactly this hole with
``spec.plugins.auto_discover_entry_points`` (default false).  This file holds
the nervous system to the same line, and the assertion is deliberately about
*side effects of the import*, not about the registry: a fiber that failed to
instantiate would be missing from the registry while its module had already
run.  So the probe module writes a marker file at import time and the test asks
whether that file exists.

The patch sits at the stdlib boundary — ``importlib.metadata.entry_points`` —
rather than on an internal seam, so a refactor that keeps the seam but resumes
importing everything still fails here.  The entry points are real
``EntryPoint`` objects whose ``load()`` performs a real import.

Tier 2 keeps loading: dropping a file into the tenant's own directory IS the
explicit operator act (the counterpart of ``spec.plugins.installed``).  What it
does not keep is trusting the path — the tests below cover a symlink escape, a
group/world-writable directory and a group/world-writable file, i.e. the three
ways a file in that directory can fail to be the operator's.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
import uuid
from contextlib import contextmanager
from importlib.metadata import EntryPoint
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[3]

for _p in (
    str(_REPO / "core" / "console"),
    str(_REPO / "operator"),
    str(_REPO / "operator" / "forge"),
    str(_REPO),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_GROUP = "corvinOS.nerve_fibers"

#: Written by the probe module at import time.  The marker is the whole point:
#: it records that the module body RAN, which is the act under test.
_PROBE_SOURCE = """\
import os
from pathlib import Path

Path(os.environ["NERVE_PROBE_MARKER"]).write_text("imported", encoding="utf-8")

from corvin_console.aco.nerve import NerveFiber


class ProbeFiber(NerveFiber):
    fiber_id = "probe.entry_point"
    fiber_version = "1.0.0"

    def scan(self):
        return []
"""


@contextmanager
def _tenant_home(spec: dict | None):
    """A CORVIN_HOME whose tenant.corvin.yaml carries *spec* (or has no file)."""
    with tempfile.TemporaryDirectory() as td:
        home = Path(td) / "corvin_home"
        global_dir = home / "tenants" / "_default" / "global"
        global_dir.mkdir(parents=True)
        if spec is not None:
            import yaml

            (global_dir / "tenant.corvin.yaml").write_text(
                yaml.safe_dump({"apiVersion": "corvin/v1", "kind": "Tenant", "spec": spec}),
                encoding="utf-8",
            )
        keys = ("CORVIN_HOME", "CORVIN_TENANT_ID", "VOICE_AUDIT_PATH")
        prev = {k: os.environ.get(k) for k in keys}
        os.environ["CORVIN_HOME"] = str(home)
        os.environ["CORVIN_TENANT_ID"] = "_default"
        # Keep the real GDPR chain out of the run (tests/conftest.py convention).
        os.environ["VOICE_AUDIT_PATH"] = str(home / "audit.jsonl")
        try:
            yield home
        finally:
            for k, v in prev.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


@contextmanager
def _installed_probe_entry_point(name: str = "probe"):
    """Make a real, importable entry point out of a throwaway module.

    Yields the marker path.  It must not exist unless the module was imported,
    so the module name is unique per call — a name already in ``sys.modules``
    would make ``load()`` a no-op and the test vacuously green.
    """
    with tempfile.TemporaryDirectory() as td:
        mod_name = f"nerve_probe_{uuid.uuid4().hex}"
        Path(td, f"{mod_name}.py").write_text(_PROBE_SOURCE, encoding="utf-8")
        marker = Path(td) / "imported.marker"

        ep = EntryPoint(name=name, value=f"{mod_name}:ProbeFiber", group=_GROUP)

        def _fake_entry_points(*, group=None, **_kw):
            return [ep] if group == _GROUP else []

        sys.path.insert(0, td)
        os.environ["NERVE_PROBE_MARKER"] = str(marker)
        patcher = mock.patch("importlib.metadata.entry_points", _fake_entry_points)
        patcher.start()
        try:
            yield marker
        finally:
            patcher.stop()
            os.environ.pop("NERVE_PROBE_MARKER", None)
            sys.path.remove(td)
            sys.modules.pop(mod_name, None)


def _run_entry_point_discovery():
    from corvin_console.aco.nerve import NerveRegistry

    NerveRegistry.reset()
    try:
        NerveRegistry._discover_entry_points()
        return {f["fiber_id"] for f in NerveRegistry.list_fibers()}
    finally:
        NerveRegistry.reset()


# ── Tier 1: entry points ──────────────────────────────────────────────────────


class TestEntryPointsNeedADeclaration(unittest.TestCase):
    def test_nothing_is_imported_without_an_opt_in(self):
        with _tenant_home(None), _installed_probe_entry_point() as marker:
            ids = _run_entry_point_discovery()
            self.assertFalse(
                marker.exists(),
                "an installed entry point was imported at boot with no "
                "declaration — that is the hole auto_discover_entry_points "
                "exists to close",
            )
            self.assertNotIn("probe.entry_point", ids)

    def test_an_empty_spec_is_not_an_opt_in(self):
        with _tenant_home({"engine": {"id": "hermes"}}), \
                _installed_probe_entry_point() as marker:
            _run_entry_point_discovery()
            self.assertFalse(marker.exists())

    def test_auto_discover_true_imports_it(self):
        with _tenant_home({"nerve": {"auto_discover_entry_points": True}}), \
                _installed_probe_entry_point() as marker:
            ids = _run_entry_point_discovery()
            self.assertTrue(
                marker.exists(),
                "the opt-in must still work — the goal is 'no unasked import', "
                "not 'no entry-point fibers'",
            )
            self.assertIn("probe.entry_point", ids)

    def test_a_named_fiber_is_imported(self):
        with _tenant_home({"nerve": {"fibers": ["probe"]}}), \
                _installed_probe_entry_point("probe") as marker:
            ids = _run_entry_point_discovery()
            self.assertTrue(marker.exists())
            self.assertIn("probe.entry_point", ids)

    def test_naming_a_different_fiber_imports_nothing(self):
        """A declaration is per name, not a blanket 'yes'."""
        with _tenant_home({"nerve": {"fibers": ["something_else"]}}), \
                _installed_probe_entry_point("probe") as marker:
            _run_entry_point_discovery()
            self.assertFalse(marker.exists())

    def test_the_plugin_opt_in_carries_over(self):
        """An operator who already said yes there is not asked twice."""
        with _tenant_home({"plugins": {"auto_discover_entry_points": True}}), \
                _installed_probe_entry_point() as marker:
            _run_entry_point_discovery()
            self.assertTrue(marker.exists())

    def test_an_explicit_nerve_false_beats_the_plugin_fallback(self):
        with _tenant_home({
            "nerve": {"auto_discover_entry_points": False},
            "plugins": {"auto_discover_entry_points": True},
        }), _installed_probe_entry_point() as marker:
            _run_entry_point_discovery()
            self.assertFalse(marker.exists())

    def test_an_unreadable_config_is_not_an_opt_in(self):
        """Deny-by-default in the failure direction too.

        The headless flag resolves a read failure to "serve as before"; this one
        must resolve it to "import nothing" — the safe answer is the opposite
        one because the risky act here is the import itself.
        """
        with _tenant_home({}) as home, _installed_probe_entry_point() as marker:
            cfg = home / "tenants" / "_default" / "global" / "tenant.corvin.yaml"
            cfg.write_text("{ this is not yaml: [", encoding="utf-8")
            _run_entry_point_discovery()
            self.assertFalse(marker.exists())

    def test_discovery_survives_a_broken_entry_point(self):
        with _tenant_home({"nerve": {"auto_discover_entry_points": True}}):
            ep = EntryPoint(name="bad", value="no_such_module_at_all:Nope", group=_GROUP)
            with mock.patch(
                "importlib.metadata.entry_points",
                lambda *, group=None, **_k: [ep] if group == _GROUP else [],
            ):
                self.assertEqual(_run_entry_point_discovery(), set())


# ── Tier 2: local files ───────────────────────────────────────────────────────


_LOCAL_FIBER_SOURCE = """\
import os
from pathlib import Path

Path(os.environ["NERVE_PROBE_MARKER"]).write_text("executed", encoding="utf-8")

from corvin_console.aco.nerve import NerveFiber


class LocalProbeFiber(NerveFiber):
    fiber_id = "probe.local"
    fiber_version = "1.0.0"

    def scan(self):
        return []
"""


class _LocalBase(unittest.TestCase):
    def _run(self, home: Path) -> set[str]:
        from corvin_console.aco.nerve import NerveRegistry

        NerveRegistry.reset()
        try:
            NerveRegistry._discover_local_plugins()
            return {f["fiber_id"] for f in NerveRegistry.list_fibers()}
        finally:
            NerveRegistry.reset()

    @contextmanager
    def _fibers_dir(self, spec=None):
        with _tenant_home(spec) as home:
            fibers = home / "nerve_fibers"
            fibers.mkdir(parents=True)
            fibers.chmod(0o700)
            marker = home / "local.marker"
            os.environ["NERVE_PROBE_MARKER"] = str(marker)
            try:
                yield home, fibers, marker
            finally:
                os.environ.pop("NERVE_PROBE_MARKER", None)


class TestLocalFibersStillLoad(_LocalBase):
    """The operator act must keep working — hardening is not switching off."""

    def test_an_operator_owned_file_is_executed(self):
        with self._fibers_dir() as (home, fibers, marker):
            (fibers / "my_connector.py").write_text(_LOCAL_FIBER_SOURCE, encoding="utf-8")
            ids = self._run(home)
            self.assertTrue(marker.exists(), "the operator's own fiber stopped loading")
            self.assertIn("probe.local", ids)

    def test_no_opt_in_config_is_required_for_local_files(self):
        """Tier 2 is not gated on spec.nerve — the file placement IS the act."""
        with self._fibers_dir(spec={"nerve": {"auto_discover_entry_points": False}}) as (
            home,
            fibers,
            marker,
        ):
            (fibers / "my_connector.py").write_text(_LOCAL_FIBER_SOURCE, encoding="utf-8")
            self.assertIn("probe.local", self._run(home))
            self.assertTrue(marker.exists())

    def test_a_broken_file_does_not_cost_the_good_one(self):
        with self._fibers_dir() as (home, fibers, marker):
            (fibers / "a_broken.py").write_text("this is not python {{{{", encoding="utf-8")
            (fibers / "b_good.py").write_text(_LOCAL_FIBER_SOURCE, encoding="utf-8")
            self.assertIn("probe.local", self._run(home))

    def test_a_missing_directory_is_quiet(self):
        with _tenant_home(None) as home:
            self.assertEqual(self._run(home), set())


@unittest.skipIf(os.name == "nt", "POSIX ownership/permission model")
class TestLocalFibersMustBeTheOperators(_LocalBase):
    def test_a_symlink_out_of_the_directory_is_not_executed(self):
        with self._fibers_dir() as (home, fibers, marker):
            outside = home / "not_the_fibers_dir"
            outside.mkdir()
            payload = outside / "payload.py"
            payload.write_text(_LOCAL_FIBER_SOURCE, encoding="utf-8")
            (fibers / "innocent.py").symlink_to(payload)

            ids = self._run(home)
            self.assertFalse(
                marker.exists(),
                "a symlink executed code from outside the fibers directory",
            )
            self.assertNotIn("probe.local", ids)

    def test_a_world_writable_file_is_not_executed(self):
        with self._fibers_dir() as (home, fibers, marker):
            f = fibers / "dropped.py"
            f.write_text(_LOCAL_FIBER_SOURCE, encoding="utf-8")
            f.chmod(0o666)
            self.assertNotIn("probe.local", self._run(home))
            self.assertFalse(marker.exists())

    def test_a_group_writable_file_is_still_executed(self):
        """The counter-direction, and it is the one that would silently hurt.

        umask 002 + user-private groups (the Debian/Ubuntu default) makes
        ``0o664`` the mode of a file the operator just wrote.  A rule that
        rejects group-writable would disable Tier 2 on a normal install while
        looking like hardening.
        """
        with self._fibers_dir() as (home, fibers, marker):
            f = fibers / "normal_umask.py"
            f.write_text(_LOCAL_FIBER_SOURCE, encoding="utf-8")
            f.chmod(0o664)
            self.assertIn("probe.local", self._run(home))
            self.assertTrue(marker.exists())

    def test_a_world_writable_directory_disables_the_whole_tier(self):
        """If anyone can drop a file in, no file in there is "the operator's"."""
        with self._fibers_dir() as (home, fibers, marker):
            (fibers / "fine.py").write_text(_LOCAL_FIBER_SOURCE, encoding="utf-8")
            fibers.chmod(0o777)
            try:
                self.assertNotIn("probe.local", self._run(home))
                self.assertFalse(marker.exists())
            finally:
                fibers.chmod(0o700)


class TestDiscoveryNeverBreaksBoot(unittest.TestCase):
    """``discover()`` runs on the boot path; it may degrade, never raise."""

    def test_an_exploding_walk_is_absorbed(self):
        from corvin_console.aco.nerve import NerveRegistry

        with _tenant_home(None):
            with mock.patch.object(
                NerveRegistry, "_load_local_plugin_dir",
                side_effect=PermissionError("nope"),
            ):
                NerveRegistry.reset()
                try:
                    NerveRegistry._discover_local_plugins()  # must not raise
                finally:
                    NerveRegistry.reset()

    def test_discover_registers_builtins_even_with_both_outer_tiers_hostile(self):
        from corvin_console.aco.nerve import NerveRegistry

        with _tenant_home(None):
            with mock.patch(
                "importlib.metadata.entry_points", side_effect=RuntimeError("boom")
            ), mock.patch.object(
                NerveRegistry, "_load_local_plugin_dir", side_effect=OSError("boom")
            ):
                NerveRegistry.reset()
                try:
                    NerveRegistry.discover()
                    self.assertTrue(
                        NerveRegistry.list_fibers(),
                        "the built-in fibers must survive a hostile Tier-1/Tier-2",
                    )
                finally:
                    NerveRegistry.reset()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

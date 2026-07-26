"""What the loader is allowed to IMPORT (ADR-0030, `auto_discover_entry_points`).

The property under test is not "the right class comes back" — `test_plugin_system`
covers that — but the side effect of getting there: importing a module runs its
top-level code, so every `ep.load()` the loader performs is third-party code
executed on this machine.

`spec.plugins.auto_discover_entry_points` defaults to false so that act needs an
operator decision. A probe module that writes a marker file on import is the only
way to test it: a loader that imported a package cannot claim afterwards that it
did not, whereas asserting on the returned class list would pass just as happily
for a loader that imported everything and then discarded most of it.
"""
from __future__ import annotations

import contextlib
import importlib.metadata
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Iterator
from unittest import mock

_HERE = Path(__file__).resolve()
_PKG = _HERE.parents[1]
_REPO = _HERE.parents[3]
for _p in (str(_PKG), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from corvin_plugins import loader as loader_module  # noqa: E402

GROUP = "corvin.plugins"

#: Written to a temp dir and imported through a real ``EntryPoint.load()``.
#: The marker write is at module scope on purpose — that IS the observation.
_PROBE_SOURCE = '''\
from pathlib import Path

Path(r"{marker}").write_text("imported", encoding="utf-8")


class Probe:
    plugin_id = "{plugin_id}"
    plugin_type = "notification_backend"
    version = "1.0.0"

    def on_load(self, ctx=None):
        pass

    def on_unload(self):
        pass
'''


class _Declared:
    """A plugin the operator listed with an explicit ``class_path``."""

    plugin_id = "declared-plugin"
    plugin_type = "notification_backend"
    version = "1.0.0"

    def on_load(self, ctx=None):
        pass

    def on_unload(self):
        pass


class _EntryPointProbes(unittest.TestCase):
    """Base: a temp dir on ``sys.path`` holding importable probe modules."""

    _counter = 0

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        sys.path.insert(0, str(self.home))
        self._probe_modules: list[str] = []

    def tearDown(self) -> None:
        with contextlib.suppress(ValueError):
            sys.path.remove(str(self.home))
        for name in self._probe_modules:
            sys.modules.pop(name, None)
        self._tmp.cleanup()

    def probe(
        self, ep_name: str, plugin_id: str | None = None
    ) -> tuple[importlib.metadata.EntryPoint, Path]:
        """A real EntryPoint plus the marker file its import would write."""
        type(self)._counter += 1
        mod = f"_ep_probe_{type(self)._counter}"
        marker = self.home / f"{mod}.imported"
        (self.home / f"{mod}.py").write_text(
            _PROBE_SOURCE.format(marker=marker, plugin_id=plugin_id or ep_name),
            encoding="utf-8",
        )
        self._probe_modules.append(mod)
        return importlib.metadata.EntryPoint(ep_name, f"{mod}:Probe", GROUP), marker

    @contextlib.contextmanager
    def installed_entry_points(
        self, *eps: importlib.metadata.EntryPoint
    ) -> Iterator[None]:
        """Pretend these entry points are installed, at the stdlib boundary.

        Patched on ``importlib.metadata`` rather than on a loader-internal seam:
        a test that stubbed the loader's own helper would prove the helper is
        called, not that no import happened.
        """
        with mock.patch.object(
            importlib.metadata, "entry_points", return_value=list(eps)
        ):
            yield

    @staticmethod
    def config(*installed: dict, auto: bool | None = None) -> dict:
        plugins: dict = {"installed": list(installed)}
        if auto is not None:
            plugins["auto_discover_entry_points"] = auto
        return {"spec": {"plugins": plugins}}


class TestDeclarationsDoNotImportEntryPoints(_EntryPointProbes):
    """A declaration is not a licence to import everything else."""

    def test_a_declared_class_path_imports_no_entry_point(self) -> None:
        # The regression this file exists for: `if auto_ep or installed:` made
        # one line in spec.plugins.installed — the commonest config there is —
        # import every corvin.plugins package on the machine, with
        # auto_discover_entry_points sitting at its default false.
        ep, marker = self.probe("unrelated-plugin")
        cfg = self.config(
            {"id": "declared-plugin", "class_path": f"{__name__}:_Declared"}
        )
        with self.installed_entry_points(ep):
            loaded = loader_module.discover_and_load(cfg, corvin_home=self.home)

        self.assertEqual(len(loaded), 1)
        self.assertIsInstance(loaded[0], _Declared)
        self.assertFalse(
            marker.exists(),
            "an entry point nobody declared was imported although "
            "auto_discover_entry_points is off",
        )

    def test_an_entry_point_no_declaration_needs_stays_unimported(self) -> None:
        needed, needed_marker = self.probe("wanted-plugin")
        other, other_marker = self.probe("unwanted-plugin")
        cfg = self.config({"id": "wanted-plugin"})

        with self.installed_entry_points(needed, other):
            loaded = loader_module.discover_and_load(cfg, corvin_home=self.home)

        self.assertEqual(len(loaded), 1)
        self.assertTrue(needed_marker.exists(), "the declared entry point must load")
        self.assertFalse(
            other_marker.exists(),
            "resolving one declaration must not import the neighbours",
        )

    def test_no_declaration_and_no_opt_in_imports_nothing(self) -> None:
        ep, marker = self.probe("some-plugin")
        with self.installed_entry_points(ep):
            self.assertEqual(
                loader_module.discover_and_load(self.config(), corvin_home=self.home),
                [],
            )
        self.assertFalse(marker.exists())

    def test_an_id_that_matches_nothing_still_imports_nothing(self) -> None:
        ep, marker = self.probe("some-plugin")
        failures: list[tuple] = []
        cfg = self.config({"id": "typo-plugin"})

        with self.installed_entry_points(ep):
            loaded = loader_module.discover_and_load(
                cfg,
                corvin_home=self.home,
                on_error=lambda pid, reason, et: failures.append((pid, reason)),
            )

        self.assertEqual(loaded, [])
        self.assertEqual(failures, [("typo-plugin", "no_class_path_or_entry_point")])
        self.assertFalse(
            marker.exists(),
            "a typo'd id must not become a reason to import the whole group",
        )


class TestOptInStillLoadsEverything(_EntryPointProbes):
    """The opt-in path is unchanged — that is the behaviour being preserved."""

    def test_auto_discovery_imports_and_instantiates_undeclared_packages(self) -> None:
        ep, marker = self.probe("auto-plugin")
        cfg = self.config(auto=True)

        with self.installed_entry_points(ep):
            loaded = loader_module.discover_and_load(cfg, corvin_home=self.home)

        self.assertTrue(marker.exists())
        self.assertEqual([p.plugin_id for p in loaded], ["auto-plugin"])

    def test_auto_discovery_alongside_a_declaration(self) -> None:
        ep, marker = self.probe("auto-plugin")
        cfg = self.config(
            {"id": "declared-plugin", "class_path": f"{__name__}:_Declared"},
            auto=True,
        )

        with self.installed_entry_points(ep):
            loaded = loader_module.discover_and_load(cfg, corvin_home=self.home)

        self.assertTrue(marker.exists())
        self.assertEqual(
            sorted(p.plugin_id for p in loaded), ["auto-plugin", "declared-plugin"]
        )

    def test_a_declaration_resolves_against_the_class_plugin_id_when_opted_in(
        self,
    ) -> None:
        # The one resolution the restricted path cannot do (it would have to
        # import every candidate to learn the attribute) still works here.
        ep, marker = self.probe("dist-name", plugin_id="the-plugin-id")
        cfg = self.config({"id": "the-plugin-id"}, auto=True)

        with self.installed_entry_points(ep):
            loaded = loader_module.discover_and_load(cfg, corvin_home=self.home)

        self.assertTrue(marker.exists())
        self.assertEqual([p.plugin_id for p in loaded], ["the-plugin-id"])


class TestLoadFromEntryPoints(_EntryPointProbes):
    """The primitive itself."""

    def test_names_none_loads_everything(self) -> None:
        a, marker_a = self.probe("a-plugin")
        b, marker_b = self.probe("b-plugin")
        with self.installed_entry_points(a, b):
            classes = loader_module.load_from_entry_points()
        self.assertEqual(len(classes), 2)
        self.assertTrue(marker_a.exists() and marker_b.exists())

    def test_names_restricts_which_modules_are_imported(self) -> None:
        a, marker_a = self.probe("a-plugin")
        b, marker_b = self.probe("b-plugin")
        with self.installed_entry_points(a, b):
            classes = loader_module.load_from_entry_points(names={"a-plugin"})
        self.assertEqual([c.plugin_id for c in classes], ["a-plugin"])
        self.assertTrue(marker_a.exists())
        self.assertFalse(marker_b.exists())

    def test_an_empty_name_set_loads_nothing(self) -> None:
        a, marker_a = self.probe("a-plugin")
        with self.installed_entry_points(a):
            self.assertEqual(loader_module.load_from_entry_points(names=set()), [])
        self.assertFalse(marker_a.exists())

    def test_a_broken_entry_point_does_not_block_the_others(self) -> None:
        good, marker = self.probe("good-plugin")
        broken = importlib.metadata.EntryPoint(
            "broken-plugin", "_no_such_probe_module:Probe", GROUP
        )
        with self.installed_entry_points(broken, good):
            classes = loader_module.load_from_entry_points()
        self.assertEqual([c.plugin_id for c in classes], ["good-plugin"])
        self.assertTrue(marker.exists())

    def test_unreadable_metadata_degrades_to_empty(self) -> None:
        with mock.patch.object(
            importlib.metadata, "entry_points", side_effect=RuntimeError("boom")
        ):
            self.assertEqual(loader_module.load_from_entry_points(), [])
            self.assertEqual(
                loader_module.resolve_declared_entry_points({"x"}), {}
            )


class TestResolveDeclaredEntryPoints(_EntryPointProbes):

    def test_an_empty_request_reads_no_metadata_at_all(self) -> None:
        with mock.patch.object(
            importlib.metadata, "entry_points", side_effect=AssertionError("read")
        ):
            self.assertEqual(loader_module.resolve_declared_entry_points([]), {})
            self.assertEqual(loader_module.resolve_declared_entry_points([""]), {})

    def test_the_class_is_keyed_by_both_names(self) -> None:
        ep, _ = self.probe("dist-name", plugin_id="the-plugin-id")
        with self.installed_entry_points(ep):
            resolved = loader_module.resolve_declared_entry_points({"dist-name"})
        self.assertEqual(set(resolved), {"dist-name", "the-plugin-id"})
        self.assertIs(resolved["dist-name"], resolved["the-plugin-id"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

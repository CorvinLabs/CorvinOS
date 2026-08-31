"""`compute_engine` plugins reach the process that dispatches them (Stage 4).

This surface was dead in a way the activation plan mis-diagnosed twice. The plan
said "pass `compute_registry` into the gateway's `bootstrap_all` — one line".
That could never have worked:

* `corvin_compute.engine_registry` had no reader — `WorkerServer` dispatches only
  through its own `_extra_engines`, and `register_engine()` has no production
  caller;
* and it is the wrong process — `WorkerServer` is constructed in the
  `corvin-compute worker` subprocess, while the gateway is where plugins were
  being loaded.

The fix loads `compute_engine` plugins in the worker instead, which gives the
registry a real reader in the process that owns dispatch. These tests pin both
halves: the engine arrives, and the type filter that makes it safe holds.

The filter is not tidiness. Without it the worker would also load the tenant's
bridge supervisors and start messenger daemons from the compute process — a
second set racing the real ones, which is the duplicate-start failure ADR-0238
names as its load-bearing invariant.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_REPO = Path(__file__).resolve().parents[3]
for _p in (_REPO / "core" / "compute",):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from corvin_plugins import bootstrap as bs  # noqa: E402


class _FakeEngine:
    """Minimal ComputeEngine shape — engine_id + job_id_prefix are what the
    registry keys and guards on."""

    def __init__(self, engine_id: str, prefix: str) -> None:
        self.engine_id = engine_id
        self.job_id_prefix = prefix


class _ComputePlugin:
    plugin_id = "com.example.compute"
    plugin_type = "compute_engine"
    version = "0.1.0"
    display_name = "Example Compute"

    #: Set by on_load so a test can assert the plugin was reached, separately
    #: from asserting the engine landed in the registry.
    loaded_with: object = None

    def on_load(self, ctx):
        type(self).loaded_with = ctx.compute_registry
        if ctx.compute_registry is not None:
            ctx.compute_registry.register(_FakeEngine("example", "ex-"))

    def on_unload(self):
        pass

    def health_check(self):
        from corvin_plugins.protocol import HealthStatus

        return HealthStatus(ok=True, message="ok", details={})


class _BridgePlugin:
    """A bridge supervisor, as far as the type filter is concerned."""

    plugin_id = "discord-bridge"
    plugin_type = "bridge_channel"
    version = "0.1.0"
    display_name = "Discord"
    started = False

    def on_load(self, ctx):
        type(self).started = True

    def on_unload(self):
        pass

    def health_check(self):
        from corvin_plugins.protocol import HealthStatus

        return HealthStatus(ok=True, message="ok", details={})


#: The module name the LOADER must use, taken from `__name__` rather than
#: written out.
#:
#: A hard-coded "core.plugins.tests.test_compute_engine_call_site" imports this
#: file a SECOND time under a different module name, so `on_load` sets
#: `loaded_with` on a different class object than the one the assertions read.
#: Every observation is then silently None while the load itself succeeds — the
#: test fails for a reason that has nothing to do with the code under test, and
#: (worse) an assertion written the other way round would have PASSED against a
#: plugin that never ran.
_HERE = __name__


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        _ComputePlugin.loaded_with = None
        _BridgePlugin.started = False
        # The bundled bridge injection is a separate mechanism; keep it out of
        # these assertions unless a test opts in.
        p = patch("corvin_plugins.bridges.supervisor._flag_enabled", return_value=False)
        p.start()
        self.addCleanup(p.stop)

        # `corvin_compute.engine_registry` is a MODULE-LEVEL singleton, so an
        # engine registered by one test is still there in the next one. That is
        # not a detail: `_load_compute_engine_plugins` reports what it added by
        # diffing `discover()` before and after, so a leaked engine makes the
        # diff empty and the next test reads "nothing registered" — a false
        # negative that looks exactly like a broken call site.
        from corvin_compute import engine_registry as reg

        self.registry = reg.get_registry()
        _before = set(self.registry.discover())
        self.addCleanup(
            lambda: [self.registry.unregister(e)
                     for e in self.registry.discover() if e not in _before] and None
        )

    def _cfg(self, *entries: dict) -> dict:
        return {"spec": {"plugins": {"installed": list(entries)}}}

    def _entry(self, cls) -> dict:
        return {"id": cls.plugin_id, "class_path": f"{_HERE}:{cls.__name__}"}

    def _load(self, cfg, **kw):
        from corvin_plugins.registry import get_registry

        loaded = bs.bootstrap_declared(
            tenant_id="_default", corvin_home=self.home, tenant_config=cfg, **kw
        )
        self.addCleanup(
            lambda: [get_registry().unregister(pid) for pid in loaded] and None
        )
        return loaded


class TestTheTypeFilter(_Base):
    def test_only_the_named_type_is_loaded(self):
        loaded = self._load(
            self._cfg(self._entry(_ComputePlugin), self._entry(_BridgePlugin)),
            only_types=frozenset({"compute_engine"}),
        )
        self.assertEqual(loaded, ["com.example.compute"])

    def test_a_filtered_plugin_never_runs_its_on_load(self):
        """The load-bearing half: filtering must happen BEFORE `on_load`.

        A bridge supervisor whose `on_load` ran inside the compute worker would
        start a messenger daemon there. Asserting only on the returned id list
        would not catch that — the plugin could have started a daemon and still
        been excluded from the result.
        """
        self._load(
            self._cfg(self._entry(_ComputePlugin), self._entry(_BridgePlugin)),
            only_types=frozenset({"compute_engine"}),
        )
        self.assertFalse(
            _BridgePlugin.started,
            "a filtered-out plugin ran on_load — in the compute worker that "
            "means a second messenger daemon racing the real one (ADR-0238)",
        )

    def test_the_type_is_read_off_the_class_not_the_declaration(self):
        """A declaration is operator-written YAML and may claim anything.

        Trusting `entry["plugin_type"]` would let a bridge supervisor into the
        worker by writing one word in a config file.
        """
        entry = self._entry(_BridgePlugin)
        entry["plugin_type"] = "compute_engine"  # a lie
        self._load(self._cfg(entry), only_types=frozenset({"compute_engine"}))
        self.assertFalse(_BridgePlugin.started)

    def test_no_filter_loads_everything(self):
        loaded = self._load(
            self._cfg(self._entry(_ComputePlugin), self._entry(_BridgePlugin))
        )
        self.assertEqual(len(loaded), 2)
        self.assertTrue(_BridgePlugin.started)


class TestTheEngineReachesTheWorkersRegistry(_Base):
    def test_the_plugin_is_handed_the_registry_it_will_be_dispatched_from(self):
        self._load(
            self._cfg(self._entry(_ComputePlugin)),
            only_types=frozenset({"compute_engine"}),
            compute_registry=self.registry,
        )
        self.assertIs(
            _ComputePlugin.loaded_with, self.registry,
            "ctx.compute_registry was not the registry the worker dispatches "
            "through — the handle is filled but points somewhere else",
        )

    def test_the_worker_helper_returns_what_actually_landed(self):
        """`_load_compute_engine_plugins` snapshots the registry, not the claim.

        It diffs `registry.discover()` before and after rather than trusting a
        plugin's word that it registered. Registration-is-not-invocation, applied
        to this fix's own plumbing.
        """
        from corvin_compute.cli import _load_compute_engine_plugins

        cfg_dir = self.home / "tenants" / "_default" / "global"
        cfg_dir.mkdir(parents=True)
        import json as _json

        (cfg_dir / "tenant.corvin.yaml").write_text(
            _json.dumps(self._cfg(self._entry(_ComputePlugin))), encoding="utf-8"
        )
        engines = _load_compute_engine_plugins(
            tenant_id="_default", corvin_home=self.home
        )
        self.assertEqual([e.engine_id for e in engines], ["example"])

    def test_a_broken_plugin_does_not_stop_the_worker(self):
        from corvin_compute.cli import _load_compute_engine_plugins

        with patch.object(bs, "bootstrap_declared", side_effect=RuntimeError("boom")):
            self.assertEqual(
                _load_compute_engine_plugins(
                    tenant_id="_default", corvin_home=self.home
                ),
                [],
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

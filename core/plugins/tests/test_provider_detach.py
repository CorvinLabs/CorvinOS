"""Unloading a plugin must release the provider slot it holds.

The defect this pins: `registry.unregister()` ran `on_unload()` and dropped the
plugin from its own maps, but the ADR-0033/0233 provider registries kept
pointing at the object. For a recall backend that means the next turn calls
`index_turn()` on a closed database handle; for an audit backend it means the
fan-out stream is silently dead while `get_active()` still answers.

Two properties, and the second is the one that is easy to get wrong:

* the slot is released when the plugin that holds it unloads;
* it is released **only** when the unloading plugin is still the one installed.
  Clearing by plugin_type alone evicts whatever is in the slot, so a plugin that
  had already been superseded would, on its way out, hand the slot back to the
  bundled default and leave the survivor believing it is active.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[3]
_PKG = _HERE.parents[1]
for _p in (str(_PKG), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from corvin_plugins.protocol import HealthStatus, PluginContext  # noqa: E402
from corvin_plugins.registry import PluginRegistry  # noqa: E402
from corvin_plugins.state import _PROVIDER_MODULES  # noqa: E402

#: plugin_type -> (module, the ctx handle a plugin registers itself through)
_CASES = (
    ("recall_backend", "recall_registry"),
    ("router_backend", "router_registry"),
    ("summary_provider", "summary_registry"),
    ("notification_backend", "notification_registry"),
    ("audit_backend", "audit_registry"),
    ("user_backend", "user_registry"),
    ("stt_provider", "stt_registry"),
    ("data_connector", "data_connector_registry"),
    ("context_retriever", "context_retriever_registry"),  # ADR-0599
)


def _module(name: str):
    from importlib import import_module

    return import_module(f"corvin_plugins.providers.{name}")


class _Provider:
    """A plugin that installs itself into its type's provider registry."""

    version = "1.0.0"
    display_name = "P"

    def __init__(self, plugin_id: str, plugin_type: str, handle: str) -> None:
        self.plugin_id = plugin_id
        self.plugin_type = plugin_type
        self._handle = handle
        self.closed = False

    def on_load(self, ctx: PluginContext) -> None:
        getattr(ctx, self._handle).set_active(self)

    def on_unload(self) -> None:
        self.closed = True

    def health_check(self) -> HealthStatus:
        return HealthStatus(ok=True)


def _ctx(plugin_id: str) -> PluginContext:
    from corvin_plugins.bootstrap import build_context

    return build_context(
        plugin_id=plugin_id, tenant_id="_default", corvin_home=Path("/tmp")
    )


class TestEveryProviderTypeIsCovered(unittest.TestCase):
    def test_all_eight_types_are_mapped(self):
        # Types with no provider registry (compute_engine, worker_engine,
        # bridge_channel) are legitimately absent; every type that HAS one must
        # be mapped, or its plugins leak their slot on unload. The map listed
        # four of eight for one commit.
        for plugin_type, _handle in _CASES:
            with self.subTest(plugin_type=plugin_type):
                self.assertIn(plugin_type, _PROVIDER_MODULES)
        self.assertEqual(len(_PROVIDER_MODULES), len(_CASES))

    def test_every_mapped_module_exposes_both_detach_calls(self):
        for plugin_type, module_name in _PROVIDER_MODULES.items():
            with self.subTest(plugin_type=plugin_type):
                mod = _module(module_name)
                self.assertTrue(callable(getattr(mod, "clear", None)))
                self.assertTrue(callable(getattr(mod, "clear_if_active", None)))


class TestSlotIsReleasedOnUnload(unittest.TestCase):
    def setUp(self):
        self.reg = PluginRegistry()

    def tearDown(self):
        for pid in list(self.reg.discover()):
            try:
                self.reg.unregister(pid)
            except Exception:  # noqa: BLE001
                pass

    def test_unload_releases_the_slot_for_every_provider_type(self):
        for plugin_type, handle in _CASES:
            with self.subTest(plugin_type=plugin_type):
                mod = _module(_PROVIDER_MODULES[plugin_type])
                before = mod.get_active()

                plugin = _Provider(f"p-{plugin_type}", plugin_type, handle)
                self.reg.register(plugin, _ctx(plugin.plugin_id))
                self.assertIs(
                    mod.get_active(), plugin,
                    f"{plugin_type}: plugin did not become active",
                )

                self.reg.unregister(plugin.plugin_id)
                self.assertIsNot(
                    mod.get_active(), plugin,
                    f"{plugin_type}: the registry still points at an unloaded "
                    f"plugin — its on_unload() has already run",
                )
                # Back to what was there before — by TYPE, not identity: the
                # four registries with a bundled default construct a fresh one
                # rather than reviving the previous object, which is right
                # (a recycled default could carry state from before the plugin).
                after = mod.get_active()
                if before is None:
                    self.assertIsNone(after)
                else:
                    self.assertIsInstance(after, type(before))


class TestDetachIsInstanceChecked(unittest.TestCase):
    """The survivor must keep the slot when a superseded plugin unloads."""

    def setUp(self):
        self.reg = PluginRegistry()

    def tearDown(self):
        for pid in list(self.reg.discover()):
            try:
                self.reg.unregister(pid)
            except Exception:  # noqa: BLE001
                pass

    def test_unloading_a_superseded_plugin_does_not_evict_the_survivor(self):
        for plugin_type, handle in _CASES:
            with self.subTest(plugin_type=plugin_type):
                mod = _module(_PROVIDER_MODULES[plugin_type])

                first = _Provider(f"first-{plugin_type}", plugin_type, handle)
                second = _Provider(f"second-{plugin_type}", plugin_type, handle)
                self.reg.register(first, _ctx(first.plugin_id))
                self.reg.register(second, _ctx(second.plugin_id))
                # `second` took the slot over on load.
                self.assertIs(mod.get_active(), second)

                self.reg.unregister(first.plugin_id)
                self.assertIs(
                    mod.get_active(), second,
                    f"{plugin_type}: unloading the superseded plugin evicted the "
                    f"one that actually holds the slot",
                )
                self.reg.unregister(second.plugin_id)

    def test_disabling_a_sibling_does_not_evict_a_compliance_provider(self):
        """The 403 is worthless if a 200 next door has the same effect.

        A compliance-boot-layer plugin holds the audit_backend slot; its own
        disable correctly answers 403. Disabling an ORDINARY audit_backend
        plugin used to call `_detach_providers(plugin_type)`, which clears by
        type and therefore evicted the compliance plugin's slot — same outcome
        as the refused request, one route over, with no audit record naming it.
        """
        mod = _module("audit_backend")
        protected = _Provider("core-audit-forwarder", "audit_backend", "audit_registry")
        self.reg.register(protected, _ctx(protected.plugin_id), boot_layer="compliance")
        self.assertIs(mod.get_active(), protected)

        # An ordinary plugin of the SAME type, loaded and then unloaded again.
        # It never took the slot over (it does not call set_active), which is
        # the realistic shape: most plugins of a type are not the active one.
        sibling = _Provider("my-audit-copy", "audit_backend", "audit_registry")
        sibling.on_load = lambda ctx: None  # type: ignore[assignment]
        self.reg.register(sibling, _ctx(sibling.plugin_id))
        self.reg.unregister(sibling.plugin_id)

        self.assertIs(
            mod.get_active(), protected,
            "unloading a sibling plugin evicted the compliance plugin's "
            "provider slot — the 403 on its own disable is then decorative",
        )

    def test_clear_if_active_reports_whether_it_acted(self):
        mod = _module("recall_backend")
        plugin = _Provider("solo", "recall_backend", "recall_registry")
        self.reg.register(plugin, _ctx("solo"))
        self.assertTrue(mod.clear_if_active(plugin))
        # Second call: no longer active, so nothing to do.
        self.assertFalse(mod.clear_if_active(plugin))


if __name__ == "__main__":
    unittest.main()

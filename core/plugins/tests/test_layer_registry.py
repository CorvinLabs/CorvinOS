"""Tests for the layer axis: classification, disable guard, replacement (ADR-0243).

The load-bearing properties under test are not "the field round-trips" but:

* a community plugin cannot promote itself into a privileged layer,
* a compliance-layer plugin cannot be switched off by an operator action,
* a replacement can only target a ``core`` reference implementation,
* an absent ``layer`` in a registry file written before ADR-0243 reads as
  ``installed`` and never as something privileged.
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

from corvin_plugins.manifest import (  # noqa: E402
    PluginError,
    BootLayer,
    PluginOrigin,
    PluginRecord,
)
from corvin_plugins.protocol import (  # noqa: E402
    HealthStatus,
    PluginAlreadyRegistered,
    PluginContext,
    PluginDisableRefused,
    PluginNotFound,
    PluginReplacementRefused,
)
from corvin_plugins.registry import PluginRegistry  # noqa: E402


class _StubPlugin:
    plugin_type = "compute_engine"
    version = "1.0.0"
    display_name = "Stub"

    def __init__(self, plugin_id: str = "stub", *, layer: str | None = None) -> None:
        self.plugin_id = plugin_id
        self.unloaded = False
        self.on_load_raises = False
        if layer is not None:
            self.layer = layer

    def on_load(self, ctx: PluginContext) -> None:
        if self.on_load_raises:
            raise RuntimeError("boom")

    def on_unload(self) -> None:
        self.unloaded = True

    def health_check(self) -> HealthStatus:
        return HealthStatus(ok=True)


def _ctx(plugin_id: str = "stub", *, sink: list | None = None) -> PluginContext:
    def emit(event_type: str, details: dict) -> None:
        if sink is not None:
            sink.append((event_type, details))

    return PluginContext(
        plugin_id=plugin_id,
        tenant_id="test",
        corvin_home=Path("/tmp"),
        config={},
        audit_emit=emit,
    )


def _record(**over) -> PluginRecord:
    base = dict(
        plugin_id="rec",
        version="1.0.0",
        display_name="Rec",
        plugin_type="compute_engine",
    )
    base.update(over)
    return PluginRecord(**base)


# ── 1. Record-level classification ────────────────────────────────────────────


class TestRecordLayer(unittest.TestCase):
    def test_default_layer_is_the_least_privileged(self):
        # A record that says nothing must not land anywhere privileged.
        self.assertIs(_record().layer, BootLayer.INSTALLED)

    def test_community_origin_cannot_claim_compliance(self):
        with self.assertRaises(PluginError) as ctx:
            _record(layer=BootLayer.COMPLIANCE, origin=PluginOrigin.COMMUNITY)
        self.assertIn("privileged", str(ctx.exception))

    def test_community_origin_cannot_claim_core(self):
        with self.assertRaises(PluginError):
            _record(layer=BootLayer.CORE, origin=PluginOrigin.COMMUNITY)

    def test_community_origin_may_claim_bundled_and_installed(self):
        for layer in (BootLayer.BUNDLED, BootLayer.INSTALLED):
            with self.subTest(layer=layer):
                rec = _record(layer=layer, origin=PluginOrigin.COMMUNITY)
                self.assertIs(rec.layer, layer)

    def test_builtin_and_vetted_may_claim_privileged_layers(self):
        for origin in (PluginOrigin.BUILTIN, PluginOrigin.VETTED):
            for layer in (BootLayer.COMPLIANCE, BootLayer.CORE):
                with self.subTest(origin=origin, layer=layer):
                    self.assertIs(_record(layer=layer, origin=origin).layer, layer)

    def test_can_disable_is_false_only_for_compliance(self):
        self.assertFalse(
            _record(layer=BootLayer.COMPLIANCE, origin=PluginOrigin.BUILTIN).can_disable()
        )
        for layer in (BootLayer.CORE, BootLayer.BUNDLED, BootLayer.INSTALLED):
            with self.subTest(layer=layer):
                self.assertTrue(
                    _record(layer=layer, origin=PluginOrigin.BUILTIN).can_disable()
                )


class TestReplacesField(unittest.TestCase):
    def test_replaces_defaults_to_none(self):
        self.assertIsNone(_record().replaces)

    def test_cannot_replace_itself(self):
        with self.assertRaises(PluginError):
            _record(plugin_id="rec", replaces="rec")

    def test_compliance_layer_may_not_declare_replaces(self):
        with self.assertRaises(PluginError) as ctx:
            _record(
                layer=BootLayer.COMPLIANCE,
                origin=PluginOrigin.BUILTIN,
                replaces="audit-writer",
            )
        self.assertIn("not replaceable", str(ctx.exception))

    def test_replaces_is_charset_validated(self):
        # Same path-segment rule as plugin_id — the value reaches audit details.
        with self.assertRaises(PluginError):
            _record(replaces="../../etc/passwd")


class TestRecordRoundTrip(unittest.TestCase):
    def test_layer_and_replaces_survive_to_dict_from_dict(self):
        rec = _record(
            layer=BootLayer.CORE, origin=PluginOrigin.BUILTIN, replaces="old-acs"
        )
        back = PluginRecord.from_dict(rec.to_dict())
        self.assertIs(back.layer, BootLayer.CORE)
        self.assertEqual(back.replaces, "old-acs")

    def test_missing_layer_reads_as_installed(self):
        # A registry.yaml written before ADR-0243 has no `layer` key at all.
        data = _record().to_dict()
        del data["layer"]
        del data["replaces"]
        self.assertIs(PluginRecord.from_dict(data).layer, BootLayer.INSTALLED)

    def test_empty_layer_string_reads_as_installed_not_crash(self):
        data = _record().to_dict()
        data["layer"] = ""
        self.assertIs(PluginRecord.from_dict(data).layer, BootLayer.INSTALLED)


# ── 2. Registry-level behaviour ───────────────────────────────────────────────


class TestRegistryLayerTracking(unittest.TestCase):
    def setUp(self):
        self.reg = PluginRegistry()

    def test_explicit_layer_argument_wins(self):
        p = _StubPlugin("a", layer="installed")
        self.reg.register(p, _ctx("a"), layer=BootLayer.CORE)
        self.assertIs(self.reg.boot_layer_of("a"), BootLayer.CORE)

    def test_plugin_self_declaration_is_used_when_no_argument(self):
        self.reg.register(_StubPlugin("a", layer="bundled"), _ctx("a"))
        self.assertIs(self.reg.boot_layer_of("a"), BootLayer.BUNDLED)

    def test_undeclared_plugin_is_installed(self):
        self.reg.register(_StubPlugin("a"), _ctx("a"))
        self.assertIs(self.reg.boot_layer_of("a"), BootLayer.INSTALLED)

    def test_unknown_layer_string_degrades_to_installed(self):
        self.reg.register(_StubPlugin("a", layer="superuser"), _ctx("a"))
        self.assertIs(self.reg.boot_layer_of("a"), BootLayer.INSTALLED)

    def test_layer_of_unregistered_raises(self):
        with self.assertRaises(PluginNotFound):
            self.reg.boot_layer_of("nope")

    def test_plugins_by_layer_filters(self):
        self.reg.register(_StubPlugin("a"), _ctx("a"), layer=BootLayer.CORE)
        self.reg.register(_StubPlugin("b"), _ctx("b"), layer=BootLayer.CORE)
        self.reg.register(_StubPlugin("c"), _ctx("c"), layer=BootLayer.BUNDLED)
        self.assertEqual(
            {p.plugin_id for p in self.reg.plugins_by_boot_layer(BootLayer.CORE)}, {"a", "b"}
        )
        self.assertEqual(
            {p.plugin_id for p in self.reg.plugins_by_boot_layer("bundled")}, {"c"}
        )

    def test_failed_on_load_leaves_no_layer_entry(self):
        p = _StubPlugin("a")
        p.on_load_raises = True
        with self.assertRaises(RuntimeError):
            self.reg.register(p, _ctx("a"), layer=BootLayer.CORE)
        # The rollback must clear the layer slot too, or a later re-register of
        # the same id would inherit a stale privileged classification.
        with self.assertRaises(PluginNotFound):
            self.reg.boot_layer_of("a")
        self.reg.register(_StubPlugin("a"), _ctx("a"))
        self.assertIs(self.reg.boot_layer_of("a"), BootLayer.INSTALLED)

    def test_load_audit_event_carries_the_layer(self):
        sink: list = []
        self.reg.register(_StubPlugin("a"), _ctx("a", sink=sink), layer=BootLayer.CORE)
        loaded = [d for e, d in sink if e == "plugin.loaded"]
        self.assertEqual(loaded[0]["layer"], "core")


class TestDisableGuard(unittest.TestCase):
    def setUp(self):
        self.reg = PluginRegistry()

    def test_operator_cannot_disable_compliance(self):
        self.reg.register(_StubPlugin("audit"), _ctx("audit"), layer=BootLayer.COMPLIANCE)
        with self.assertRaises(PluginDisableRefused):
            self.reg.disable("audit")
        # still registered and never unloaded
        self.assertIs(self.reg.boot_layer_of("audit"), BootLayer.COMPLIANCE)

    def test_operator_cannot_reach_past_disable_via_unregister(self):
        # The admin surface must not be able to bypass disable() by calling the
        # primitive with the operator flag set.
        self.reg.register(_StubPlugin("audit"), _ctx("audit"), layer=BootLayer.COMPLIANCE)
        with self.assertRaises(PluginDisableRefused):
            self.reg.unregister("audit", operator_initiated=True)

    def test_shutdown_path_may_unload_compliance(self):
        p = _StubPlugin("audit")
        self.reg.register(p, _ctx("audit"), layer=BootLayer.COMPLIANCE)
        self.reg.unregister("audit")  # machinery, not an operator action
        self.assertTrue(p.unloaded)

    def test_other_layers_are_disableable(self):
        for layer in (BootLayer.CORE, BootLayer.BUNDLED, BootLayer.INSTALLED):
            with self.subTest(layer=layer):
                reg = PluginRegistry()
                p = _StubPlugin("x")
                reg.register(p, _ctx("x"), layer=layer)
                self.assertTrue(reg.can_disable("x"))
                reg.disable("x")
                self.assertTrue(p.unloaded)

    def test_can_disable_of_unknown_id_is_true(self):
        self.assertTrue(self.reg.can_disable("never-registered"))

    def test_unload_audit_event_records_who_asked(self):
        sink: list = []
        self.reg.register(_StubPlugin("x"), _ctx("x", sink=sink), layer=BootLayer.BUNDLED)
        self.reg.disable("x")
        unloaded = [d for e, d in sink if e == "plugin.unloaded"]
        self.assertTrue(unloaded[0]["operator_initiated"])
        self.assertEqual(unloaded[0]["layer"], "bundled")


class TestReplacement(unittest.TestCase):
    def setUp(self):
        self.reg = PluginRegistry()

    def _install_core(self, pid: str = "acs-default"):
        p = _StubPlugin(pid)
        self.reg.register(p, _ctx(pid), layer=BootLayer.CORE)
        return p

    def test_replacing_a_core_plugin_swaps_it(self):
        old = self._install_core()
        new = _StubPlugin("acs-k8s")
        self.reg.replace(new, _ctx("acs-k8s"), replaces="acs-default")
        self.assertTrue(old.unloaded)
        self.assertIs(self.reg.get("acs-k8s"), new)
        self.assertIs(self.reg.boot_layer_of("acs-k8s"), BootLayer.CORE)
        with self.assertRaises(PluginNotFound):
            self.reg.get("acs-default")

    def test_replacing_compliance_is_refused(self):
        p = _StubPlugin("audit")
        self.reg.register(p, _ctx("audit"), layer=BootLayer.COMPLIANCE)
        with self.assertRaises(PluginReplacementRefused):
            self.reg.replace(_StubPlugin("my-audit"), _ctx("my-audit"), replaces="audit")
        # The target must survive the refusal untouched.
        self.assertFalse(p.unloaded)
        self.assertIs(self.reg.get("audit"), p)

    def test_replacing_bundled_is_refused(self):
        p = _StubPlugin("discord")
        self.reg.register(p, _ctx("discord"), layer=BootLayer.BUNDLED)
        with self.assertRaises(PluginReplacementRefused):
            self.reg.replace(_StubPlugin("d2"), _ctx("d2"), replaces="discord")
        self.assertFalse(p.unloaded)

    def test_replacing_an_unknown_target_is_refused(self):
        with self.assertRaises(PluginReplacementRefused):
            self.reg.replace(_StubPlugin("x"), _ctx("x"), replaces="ghost")

    def test_replacement_with_colliding_id_leaves_target_loaded(self):
        old = self._install_core()
        # A replacement whose own id is already taken must be refused BEFORE the
        # target is unloaded, or the collision would cost us both plugins.
        self.reg.register(_StubPlugin("taken"), _ctx("taken"), layer=BootLayer.INSTALLED)
        with self.assertRaises(PluginAlreadyRegistered):
            self.reg.replace(_StubPlugin("taken"), _ctx("taken"), replaces="acs-default")
        self.assertFalse(old.unloaded)
        self.assertIs(self.reg.get("acs-default"), old)

    def test_replacement_emits_an_audit_event(self):
        self._install_core()
        sink: list = []
        self.reg.replace(
            _StubPlugin("acs-k8s"), _ctx("acs-k8s", sink=sink), replaces="acs-default"
        )
        replaced = [d for e, d in sink if e == "plugin.replaced"]
        self.assertEqual(replaced[0]["replaces"], "acs-default")
        self.assertEqual(replaced[0]["plugin_id"], "acs-k8s")

    def test_failed_replacement_leaves_the_slot_empty_not_half_loaded(self):
        self._install_core()
        broken = _StubPlugin("acs-k8s")
        broken.on_load_raises = True
        with self.assertRaises(RuntimeError):
            self.reg.replace(broken, _ctx("acs-k8s"), replaces="acs-default")
        # Documented outcome: neither plugin is registered. The old one's
        # on_unload() already ran, so silently restoring it would hand callers a
        # torn-down object.
        with self.assertRaises(PluginNotFound):
            self.reg.get("acs-default")
        with self.assertRaises(PluginNotFound):
            self.reg.get("acs-k8s")


if __name__ == "__main__":
    unittest.main()

"""Tests for the boot-layer axis: classification, disable guard, replacement (ADR-0243).

The load-bearing properties under test are not "the field round-trips" but:

* a community plugin cannot promote itself into a privileged boot layer,
* a compliance-boot-layer plugin cannot be switched off by an operator action,
* a replacement can only target a ``core`` reference implementation,
* an absent ``boot_layer`` in a registry file written before ADR-0243 reads as
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
    BootLayer,
    PluginError,
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

    def __init__(
        self, plugin_id: str = "stub", *, boot_layer: str | None = None
    ) -> None:
        self.plugin_id = plugin_id
        self.unloaded = False
        self.on_load_raises = False
        if boot_layer is not None:
            self.boot_layer = boot_layer

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


class TestRecordBootLayer(unittest.TestCase):
    def test_default_boot_layer_is_the_least_privileged(self):
        # A record that says nothing must not land anywhere privileged.
        self.assertIs(_record().boot_layer, BootLayer.INSTALLED)

    def test_community_origin_cannot_claim_compliance(self):
        with self.assertRaises(PluginError) as ctx:
            _record(boot_layer=BootLayer.COMPLIANCE, origin=PluginOrigin.COMMUNITY)
        self.assertIn("privileged", str(ctx.exception))

    def test_community_origin_cannot_claim_core(self):
        with self.assertRaises(PluginError):
            _record(boot_layer=BootLayer.CORE, origin=PluginOrigin.COMMUNITY)

    def test_community_origin_may_claim_bundled_and_installed(self):
        for boot_layer in (BootLayer.BUNDLED, BootLayer.INSTALLED):
            with self.subTest(boot_layer=boot_layer):
                rec = _record(boot_layer=boot_layer, origin=PluginOrigin.COMMUNITY)
                self.assertIs(rec.boot_layer, boot_layer)

    def test_builtin_and_vetted_may_claim_privileged_boot_layers(self):
        for origin in (PluginOrigin.BUILTIN, PluginOrigin.VETTED):
            for boot_layer in (BootLayer.COMPLIANCE, BootLayer.CORE):
                with self.subTest(origin=origin, boot_layer=boot_layer):
                    self.assertIs(
                        _record(boot_layer=boot_layer, origin=origin).boot_layer,
                        boot_layer,
                    )

    def test_can_disable_is_false_only_for_compliance(self):
        self.assertFalse(
            _record(
                boot_layer=BootLayer.COMPLIANCE, origin=PluginOrigin.BUILTIN
            ).can_disable()
        )
        for boot_layer in (BootLayer.CORE, BootLayer.BUNDLED, BootLayer.INSTALLED):
            with self.subTest(boot_layer=boot_layer):
                self.assertTrue(
                    _record(boot_layer=boot_layer, origin=PluginOrigin.BUILTIN).can_disable()
                )


class TestReplacesField(unittest.TestCase):
    def test_replaces_defaults_to_none(self):
        self.assertIsNone(_record().replaces)

    def test_cannot_replace_itself(self):
        with self.assertRaises(PluginError):
            _record(plugin_id="rec", replaces="rec")

    def test_compliance_boot_layer_may_not_declare_replaces(self):
        with self.assertRaises(PluginError) as ctx:
            _record(
                boot_layer=BootLayer.COMPLIANCE,
                origin=PluginOrigin.BUILTIN,
                replaces="audit-writer",
            )
        self.assertIn("not replaceable", str(ctx.exception))

    def test_replaces_is_charset_validated(self):
        # Same path-segment rule as plugin_id — the value reaches audit details.
        with self.assertRaises(PluginError):
            _record(replaces="../../etc/passwd")


class TestRecordRoundTrip(unittest.TestCase):
    def test_boot_layer_and_replaces_survive_to_dict_from_dict(self):
        rec = _record(
            boot_layer=BootLayer.CORE, origin=PluginOrigin.BUILTIN, replaces="old-acs"
        )
        back = PluginRecord.from_dict(rec.to_dict())
        self.assertIs(back.boot_layer, BootLayer.CORE)
        self.assertEqual(back.replaces, "old-acs")

    def test_missing_boot_layer_reads_as_installed(self):
        # A registry.yaml written before ADR-0243 has no `boot_layer` key at all.
        data = _record().to_dict()
        del data["boot_layer"]
        del data["replaces"]
        self.assertIs(PluginRecord.from_dict(data).boot_layer, BootLayer.INSTALLED)

    def test_empty_boot_layer_string_reads_as_installed_not_crash(self):
        data = _record().to_dict()
        data["boot_layer"] = ""
        self.assertIs(PluginRecord.from_dict(data).boot_layer, BootLayer.INSTALLED)

    def test_the_axis_is_persisted_under_boot_layer_not_layer(self):
        # The key on disk is what an operator and the Console both read. `layer`
        # already means four other things in this repo (L1–L44, ADR-0124 audit
        # layers, the ADR-0142 extension API, quality layers), so the newest and
        # smallest of the five spells itself out.
        data = _record(boot_layer=BootLayer.BUNDLED).to_dict()
        self.assertEqual(data["boot_layer"], "bundled")
        self.assertNotIn("layer", data)

    def test_a_registry_written_with_the_legacy_layer_key_still_loads(self):
        # BACKWARD COMPATIBILITY: the axis shipped for a few hours under the bare
        # key `layer`. from_dict() rejects unknown fields fail-closed, so without
        # this path a registry.yaml from that window would not merely lose the
        # value — it would raise and take the whole tenant registry down.
        data = _record(boot_layer=BootLayer.BUNDLED).to_dict()
        data["layer"] = data.pop("boot_layer")

        with self.assertLogs("corvin.plugins.manifest", level="WARNING") as caught:
            back = PluginRecord.from_dict(data)

        self.assertIs(back.boot_layer, BootLayer.BUNDLED)
        self.assertIn("legacy key 'layer'", "\n".join(caught.output))
        # …and the rewrite is one save away: the round trip drops the old key.
        self.assertNotIn("layer", back.to_dict())

    def test_the_new_key_wins_when_a_file_carries_both(self):
        # Deterministic rather than order-dependent: a half-migrated file must
        # not resolve to whichever key the YAML loader happened to yield last.
        data = _record(boot_layer=BootLayer.BUNDLED).to_dict()
        data["layer"] = "compliance"
        self.assertIs(PluginRecord.from_dict(data).boot_layer, BootLayer.BUNDLED)

    def test_the_legacy_key_does_not_widen_the_unknown_field_guard(self):
        # The compat path exempts exactly one name. Everything else a newer
        # CorvinOS might have written must still fail closed, or an older
        # install would silently drop state and persist the truncation back.
        data = _record().to_dict()
        data["something_from_the_future"] = 1
        with self.assertRaises(PluginError):
            PluginRecord.from_dict(data)


# ── 2. Registry-level behaviour ───────────────────────────────────────────────


class TestRegistryBootLayerTracking(unittest.TestCase):
    def setUp(self):
        self.reg = PluginRegistry()

    def test_explicit_boot_layer_argument_wins(self):
        p = _StubPlugin("a", boot_layer="installed")
        self.reg.register(p, _ctx("a"), boot_layer=BootLayer.CORE)
        self.assertIs(self.reg.boot_layer_of("a"), BootLayer.CORE)

    def test_plugin_self_declaration_is_used_when_no_argument(self):
        self.reg.register(_StubPlugin("a", boot_layer="bundled"), _ctx("a"))
        self.assertIs(self.reg.boot_layer_of("a"), BootLayer.BUNDLED)

    def test_undeclared_plugin_is_installed(self):
        self.reg.register(_StubPlugin("a"), _ctx("a"))
        self.assertIs(self.reg.boot_layer_of("a"), BootLayer.INSTALLED)

    def test_unknown_boot_layer_string_degrades_to_installed(self):
        self.reg.register(_StubPlugin("a", boot_layer="superuser"), _ctx("a"))
        self.assertIs(self.reg.boot_layer_of("a"), BootLayer.INSTALLED)

    def test_boot_layer_of_unregistered_raises(self):
        with self.assertRaises(PluginNotFound):
            self.reg.boot_layer_of("nope")

    def test_plugins_by_boot_layer_filters(self):
        self.reg.register(_StubPlugin("a"), _ctx("a"), boot_layer=BootLayer.CORE)
        self.reg.register(_StubPlugin("b"), _ctx("b"), boot_layer=BootLayer.CORE)
        self.reg.register(_StubPlugin("c"), _ctx("c"), boot_layer=BootLayer.BUNDLED)
        self.assertEqual(
            {p.plugin_id for p in self.reg.plugins_by_boot_layer(BootLayer.CORE)},
            {"a", "b"},
        )
        self.assertEqual(
            {p.plugin_id for p in self.reg.plugins_by_boot_layer("bundled")}, {"c"}
        )

    def test_failed_on_load_leaves_no_boot_layer_entry(self):
        p = _StubPlugin("a")
        p.on_load_raises = True
        with self.assertRaises(RuntimeError):
            self.reg.register(p, _ctx("a"), boot_layer=BootLayer.CORE)
        # The rollback must clear the boot-layer slot too, or a later re-register
        # of the same id would inherit a stale privileged classification.
        with self.assertRaises(PluginNotFound):
            self.reg.boot_layer_of("a")
        self.reg.register(_StubPlugin("a"), _ctx("a"))
        self.assertIs(self.reg.boot_layer_of("a"), BootLayer.INSTALLED)

    def test_load_audit_event_carries_the_boot_layer(self):
        sink: list = []
        self.reg.register(
            _StubPlugin("a"), _ctx("a", sink=sink), boot_layer=BootLayer.CORE
        )
        loaded = [d for e, d in sink if e == "plugin.loaded"]
        self.assertEqual(loaded[0]["boot_layer"], "core")


class TestDisableGuard(unittest.TestCase):
    def setUp(self):
        self.reg = PluginRegistry()

    def test_operator_cannot_disable_compliance(self):
        self.reg.register(
            _StubPlugin("audit"), _ctx("audit"), boot_layer=BootLayer.COMPLIANCE
        )
        with self.assertRaises(PluginDisableRefused):
            self.reg.disable("audit")
        # still registered and never unloaded
        self.assertIs(self.reg.boot_layer_of("audit"), BootLayer.COMPLIANCE)

    def test_operator_cannot_reach_past_disable_via_unregister(self):
        # The admin surface must not be able to bypass disable() by calling the
        # primitive with the operator flag set.
        self.reg.register(
            _StubPlugin("audit"), _ctx("audit"), boot_layer=BootLayer.COMPLIANCE
        )
        with self.assertRaises(PluginDisableRefused):
            self.reg.unregister("audit", operator_initiated=True)

    def test_shutdown_path_may_unload_compliance(self):
        p = _StubPlugin("audit")
        self.reg.register(p, _ctx("audit"), boot_layer=BootLayer.COMPLIANCE)
        self.reg.unregister("audit")  # machinery, not an operator action
        self.assertTrue(p.unloaded)

    def test_other_boot_layers_are_disableable(self):
        for boot_layer in (BootLayer.CORE, BootLayer.BUNDLED, BootLayer.INSTALLED):
            with self.subTest(boot_layer=boot_layer):
                reg = PluginRegistry()
                p = _StubPlugin("x")
                reg.register(p, _ctx("x"), boot_layer=boot_layer)
                self.assertTrue(reg.can_disable("x"))
                reg.disable("x")
                self.assertTrue(p.unloaded)

    def test_can_disable_of_unknown_id_is_true(self):
        self.assertTrue(self.reg.can_disable("never-registered"))

    def test_unload_audit_event_records_who_asked(self):
        sink: list = []
        self.reg.register(
            _StubPlugin("x"), _ctx("x", sink=sink), boot_layer=BootLayer.BUNDLED
        )
        self.reg.disable("x")
        unloaded = [d for e, d in sink if e == "plugin.unloaded"]
        self.assertTrue(unloaded[0]["operator_initiated"])
        self.assertEqual(unloaded[0]["boot_layer"], "bundled")


class TestTheBreakerIsNotAnOffSwitchForCompliance(unittest.TestCase):
    """An open circuit breaker is functionally a disable.

    `health_check_all()` records a breaker failure on every `ok=False`, and the
    admin plane calls it on every read. Five ordinary page loads against a
    compliance plugin whose backend was briefly unreachable were therefore
    enough to contain it — no healing flag, no operator action, no audit entry
    saying the mechanism stopped running. That is the automatic off switch
    CLAUDE.md rules out for this layer.
    """

    def setUp(self):
        self.reg = PluginRegistry()

    def tearDown(self):
        import corvin_plugins.circuit_breaker as breakers

        for pid in list(self.reg.discover()):
            breakers.forget(pid)
            try:
                self.reg.unregister(pid)
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _unhealthy(plugin_id: str):
        p = _StubPlugin(plugin_id)
        p.health_check = lambda: HealthStatus(ok=False, message="backend unreachable")  # type: ignore[assignment]
        return p

    def test_repeated_unhealthy_reads_do_not_contain_a_compliance_plugin(self):
        import corvin_plugins.circuit_breaker as breakers

        self.reg.register(
            self._unhealthy("audit"), _ctx("audit"), boot_layer=BootLayer.COMPLIANCE
        )
        for _ in range(10):
            self.reg.health_check_all()

        stats = breakers.get_breaker("audit").stats().to_dict()
        self.assertNotEqual(
            stats.get("state"), "open",
            "ten health reads contained a compliance plugin — an open breaker "
            "stops it being called at all",
        )

    def test_an_ordinary_plugin_is_still_contained(self):
        # Counter-test: a fix that simply stopped opening breakers would pass
        # the test above and remove the containment the breaker exists for.
        import corvin_plugins.circuit_breaker as breakers

        self.reg.register(self._unhealthy("ordinary"), _ctx("ordinary"))
        for _ in range(10):
            self.reg.health_check_all()

        self.assertEqual(
            breakers.get_breaker("ordinary").stats().to_dict().get("state"), "open",
            "an unhealthy installed-layer plugin must still be contained",
        )

    def test_the_compliance_plugin_is_still_reported_unhealthy(self):
        # Not containing it must not mean hiding it: health stays visible.
        self.reg.register(
            self._unhealthy("audit"), _ctx("audit"), boot_layer=BootLayer.COMPLIANCE
        )
        result = self.reg.health_check_all()["audit"]
        self.assertFalse(result.ok)
        self.assertIn("unreachable", result.message)


class TestReplacement(unittest.TestCase):
    def setUp(self):
        self.reg = PluginRegistry()

    def _install_core(self, pid: str = "acs-default"):
        p = _StubPlugin(pid)
        self.reg.register(p, _ctx(pid), boot_layer=BootLayer.CORE)
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
        self.reg.register(p, _ctx("audit"), boot_layer=BootLayer.COMPLIANCE)
        with self.assertRaises(PluginReplacementRefused):
            self.reg.replace(_StubPlugin("my-audit"), _ctx("my-audit"), replaces="audit")
        # The target must survive the refusal untouched.
        self.assertFalse(p.unloaded)
        self.assertIs(self.reg.get("audit"), p)

    def test_replacing_bundled_is_refused(self):
        p = _StubPlugin("discord")
        self.reg.register(p, _ctx("discord"), boot_layer=BootLayer.BUNDLED)
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
        self.reg.register(
            _StubPlugin("taken"), _ctx("taken"), boot_layer=BootLayer.INSTALLED
        )
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

    def test_a_failed_replacement_is_not_audited_as_a_replacement(self):
        # The chain is append-only: "X replaced Y" written before the swap
        # succeeded is a permanent claim about a state the process never
        # reached, and nothing can correct it afterwards.
        self._install_core()
        broken = _StubPlugin("acs-k8s")
        broken.on_load_raises = True
        sink: list = []
        with self.assertRaises(RuntimeError):
            self.reg.replace(
                broken, _ctx("acs-k8s", sink=sink), replaces="acs-default"
            )
        events = [e for e, _ in sink]
        self.assertNotIn("plugin.replaced", events)
        failed = [d for e, d in sink if e == "plugin.replace_failed"]
        self.assertTrue(failed, "the gap must be recorded, just not as success")
        self.assertEqual(failed[0]["error_type"], "RuntimeError")
        self.assertNotIn("boom", str(failed[0]), "no exception message in audit")

    def test_a_replacement_may_not_claim_a_different_boot_layer(self):
        # Otherwise this path mints privilege: the target is proven core, but
        # the replacement could ask for compliance and become undisableable.
        target = self._install_core()
        with self.assertRaises(PluginReplacementRefused):
            self.reg.replace(
                _StubPlugin("acs-k8s"), _ctx("acs-k8s"),
                replaces="acs-default", boot_layer=BootLayer.COMPLIANCE,
            )
        # Refused BEFORE the target was touched.
        self.assertIs(self.reg.get("acs-default"), target)

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

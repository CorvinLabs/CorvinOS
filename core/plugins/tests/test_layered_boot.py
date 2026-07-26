"""Tests for the layered boot sequence (ADR-0240 / ADR-0243, Phase 2).

Two properties carry the weight here:

* **The trust boundary.** Global (compliance/core) plugins come from the wheel;
  tenant scope (bundled/installed) comes from operator-writable files. A tenant
  file claiming a privileged layer is downgraded and audited — otherwise any
  writable YAML could mint an undisableable plugin.
* **The call site.** ``bootstrap_global`` being correct is worth nothing if
  nothing calls it, so ``bootstrap_all`` is tested for actually invoking it and
  for invoking it FIRST.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[3]
_PKG = _HERE.parents[1]
for _p in (str(_PKG), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import corvin_plugins.bootstrap as boot  # noqa: E402
from corvin_plugins.manifest import PluginLayer, PluginOrigin, PluginRecord  # noqa: E402
from corvin_plugins.protocol import HealthStatus, PluginContext  # noqa: E402
from corvin_plugins.registry import get_registry  # noqa: E402


class _Plug:
    plugin_type = "compute_engine"
    version = "1.0.0"
    display_name = "P"

    def __init__(self, plugin_id: str = "g1") -> None:
        self.plugin_id = plugin_id

    def on_load(self, ctx: PluginContext) -> None:
        pass

    def on_unload(self) -> None:
        pass

    def health_check(self) -> HealthStatus:
        return HealthStatus(ok=True)


def _factory(plugin_id: str):
    return lambda: _Plug(plugin_id)


class _BootTestCase(unittest.TestCase):
    """Isolates the module-level global spec list and the process registry."""

    def setUp(self):
        self._saved_specs = list(boot._GLOBAL_SPECS)
        boot._GLOBAL_SPECS.clear()
        self.audited: list[tuple[str, dict]] = []
        self._audit_patch = mock.patch.object(
            boot,
            "_audit_degradation",
            lambda tid, event, details: self.audited.append((event, details)),
        )
        self._audit_patch.start()
        # Keep the real audit writer out of the test: register() emits through
        # the context's audit_emit, which build_context wires to the hash chain.
        self._emit_patch = mock.patch.object(
            boot, "_default_audit_emit", lambda tid: (lambda e, d: None)
        )
        self._emit_patch.start()

    def tearDown(self):
        self._emit_patch.stop()
        self._audit_patch.stop()
        boot._GLOBAL_SPECS.clear()
        boot._GLOBAL_SPECS.extend(self._saved_specs)
        reg = get_registry()
        for pid in list(reg.discover()):
            try:
                reg.unregister(pid)
            except Exception:  # noqa: BLE001
                pass

    def _events(self, name: str) -> list[dict]:
        return [d for e, d in self.audited if e == name]


# ── 1. Declaring a global plugin ──────────────────────────────────────────────


class TestRegisterGlobalPlugin(_BootTestCase):
    def test_compliance_and_core_are_accepted(self):
        boot.register_global_plugin("a:B", layer=PluginLayer.COMPLIANCE)
        boot.register_global_plugin("c:D", layer="core")
        self.assertEqual(len(boot._GLOBAL_SPECS), 2)

    def test_tenant_layers_are_refused(self):
        for layer in ("bundled", "installed"):
            with self.subTest(layer=layer):
                with self.assertRaises(ValueError):
                    boot.register_global_plugin("a:B", layer=layer)

    def test_duplicate_class_path_is_a_no_op(self):
        boot.register_global_plugin("a:B", layer="core")
        boot.register_global_plugin("a:B", layer="core")
        self.assertEqual(len(boot._GLOBAL_SPECS), 1)

    def test_specs_are_ordered_compliance_first_then_alphabetical(self):
        boot.register_global_plugin("z:Core", layer="core")
        boot.register_global_plugin("m:Comp", layer="compliance")
        boot.register_global_plugin("a:Core2", layer="core")
        self.assertEqual(
            [cp for cp, _ in boot._global_specs()], ["m:Comp", "a:Core2", "z:Core"]
        )


# ── 2. Failure semantics per layer ────────────────────────────────────────────


class TestGlobalBootFailureSemantics(_BootTestCase):
    def test_no_globals_is_a_quiet_no_op(self):
        self.assertEqual(
            boot.bootstrap_global(tenant_id="t", corvin_home=Path("/tmp")), []
        )
        self.assertEqual(self.audited, [])

    def test_core_failure_degrades_and_audits(self):
        boot.register_global_plugin("broken:Core", layer="core")
        with mock.patch(
            "corvin_plugins.loader.load_from_class_path",
            side_effect=ImportError("nope"),
        ):
            loaded = boot.bootstrap_global(tenant_id="t", corvin_home=Path("/tmp"))
        self.assertEqual(loaded, [])
        failures = self._events("plugin.global_load_failed")
        self.assertEqual(failures[0]["layer"], "core")
        self.assertEqual(failures[0]["error_type"], "ImportError")

    def test_compliance_failure_aborts_the_boot(self):
        boot.register_global_plugin("broken:Gate", layer="compliance")
        with mock.patch(
            "corvin_plugins.loader.load_from_class_path",
            side_effect=ImportError("nope"),
        ):
            with self.assertRaises(boot.GlobalComplianceLoadFailed):
                boot.bootstrap_global(tenant_id="t", corvin_home=Path("/tmp"))
        # The failure is on the chain before the exception leaves the function.
        self.assertEqual(self._events("plugin.global_load_failed")[0]["layer"], "compliance")

    def test_compliance_failure_is_not_swallowed_by_bootstrap_all(self):
        # bootstrap_all wraps three passes; the compliance abort must survive it,
        # or the fatal semantics would only hold for a caller nobody uses.
        boot.register_global_plugin("broken:Gate", layer="compliance")
        with mock.patch(
            "corvin_plugins.loader.load_from_class_path",
            side_effect=ImportError("nope"),
        ):
            with self.assertRaises(boot.GlobalComplianceLoadFailed):
                boot.bootstrap_all(
                    tenant_id="t", corvin_home=Path("/tmp"), tenant_config={}
                )

    def test_a_plugin_without_plugin_id_fails_its_layer_rule(self):
        boot.register_global_plugin("anon:Gate", layer="compliance")

        class _Anon:
            plugin_id = ""

        with mock.patch(
            "corvin_plugins.loader.load_from_class_path", return_value=_Anon
        ):
            with self.assertRaises(boot.GlobalComplianceLoadFailed):
                boot.bootstrap_global(tenant_id="t", corvin_home=Path("/tmp"))

    def test_successful_global_load_registers_on_the_right_layer(self):
        boot.register_global_plugin("ok:Gate", layer="compliance")
        with mock.patch(
            "corvin_plugins.loader.load_from_class_path", return_value=_factory("gate")
        ):
            loaded = boot.bootstrap_global(tenant_id="t", corvin_home=Path("/tmp"))
        self.assertEqual(loaded, ["gate"])
        self.assertIs(get_registry().layer_of("gate"), PluginLayer.COMPLIANCE)
        self.assertFalse(get_registry().can_disable("gate"))


# ── 3. The trust boundary ─────────────────────────────────────────────────────


class TestTenantCannotClaimPrivilegedLayers(_BootTestCase):
    def test_declared_entry_may_claim_bundled(self):
        layer = boot._declared_layer(
            {"layer": "bundled"}, plugin_id="p", tenant_id="t"
        )
        self.assertIs(layer, PluginLayer.BUNDLED)
        self.assertEqual(self._events("plugin.layer_rejected"), [])

    def test_declared_entry_claiming_compliance_is_downgraded_and_audited(self):
        layer = boot._declared_layer(
            {"layer": "compliance"}, plugin_id="evil", tenant_id="t"
        )
        self.assertIs(layer, PluginLayer.INSTALLED)
        rejected = self._events("plugin.layer_rejected")
        self.assertEqual(rejected[0]["reason"], "privileged_layer_from_tenant")
        self.assertEqual(rejected[0]["plugin_id"], "evil")

    def test_declared_entry_claiming_core_is_downgraded(self):
        self.assertIs(
            boot._declared_layer({"layer": "core"}, plugin_id="p", tenant_id="t"),
            PluginLayer.INSTALLED,
        )

    def test_unknown_layer_string_is_downgraded_and_audited(self):
        self.assertIs(
            boot._declared_layer({"layer": "root"}, plugin_id="p", tenant_id="t"),
            PluginLayer.INSTALLED,
        )
        self.assertEqual(
            self._events("plugin.layer_rejected")[0]["reason"], "unknown_layer"
        )

    def test_audited_layer_value_is_length_capped(self):
        boot._declared_layer({"layer": "x" * 500}, plugin_id="p", tenant_id="t")
        self.assertLessEqual(
            len(self._events("plugin.layer_rejected")[0]["declared_layer"]), 32
        )

    def test_absent_layer_defaults_to_installed_without_noise(self):
        self.assertIs(
            boot._declared_layer({}, plugin_id="p", tenant_id="t"),
            PluginLayer.INSTALLED,
        )
        self.assertEqual(self.audited, [])

    def test_registry_record_cannot_smuggle_a_core_layer(self):
        # A vetted record passes the manifest gate with layer=core, but arriving
        # through per-tenant registry.yaml it is still tenant scope.
        rec = PluginRecord(
            plugin_id="sneaky",
            version="1.0.0",
            display_name="S",
            plugin_type="compute_engine",
            layer=PluginLayer.CORE,
            origin=PluginOrigin.VETTED,
            class_path="x:Y",
        )
        with mock.patch(
            "corvin_plugins.loader.load_from_class_path", return_value=_factory("sneaky")
        ):
            ok = boot._load_one(rec, tenant_id="t", corvin_home=Path("/tmp"))
        self.assertTrue(ok)
        self.assertIs(get_registry().layer_of("sneaky"), PluginLayer.INSTALLED)
        self.assertEqual(
            self._events("plugin.layer_rejected")[0]["reason"],
            "privileged_layer_from_tenant",
        )


# ── 4. The call site ──────────────────────────────────────────────────────────


class TestBootstrapAllWiring(_BootTestCase):
    def test_bootstrap_all_calls_bootstrap_global(self):
        with mock.patch.object(boot, "bootstrap_global", return_value=[]) as spy:
            boot.bootstrap_all(tenant_id="t", corvin_home=Path("/tmp"), tenant_config={})
        spy.assert_called_once()

    def test_global_runs_before_the_tenant_passes(self):
        order: list[str] = []
        with mock.patch.object(
            boot, "bootstrap_global", side_effect=lambda **kw: order.append("global") or []
        ), mock.patch.object(
            boot, "bootstrap_declared",
            side_effect=lambda **kw: order.append("declared") or [],
        ), mock.patch.object(
            boot, "bootstrap_tenant",
            side_effect=lambda **kw: order.append("runtime") or [],
        ):
            boot.bootstrap_all(tenant_id="t", corvin_home=Path("/tmp"), tenant_config={})
        self.assertEqual(order, ["global", "declared", "runtime"])

    def test_returned_ids_are_globals_first_and_deduplicated(self):
        with mock.patch.object(boot, "bootstrap_global", return_value=["g"]), \
             mock.patch.object(boot, "bootstrap_declared", return_value=["d", "g"]), \
             mock.patch.object(boot, "bootstrap_tenant", return_value=["r", "d"]):
            result = boot.bootstrap_all(
                tenant_id="t", corvin_home=Path("/tmp"), tenant_config={}
            )
        self.assertEqual(result, ["g", "d", "r"])

    def test_gateway_boot_path_still_reaches_bootstrap_all(self):
        # The gateway is the real caller; if the import name ever drifts, the
        # whole layered boot becomes dead code with green unit tests.
        source = self._gateway_source()
        self.assertIn("from corvin_plugins.bootstrap import bootstrap_all", source)

    def test_gateway_does_not_swallow_the_compliance_abort(self):
        # The gateway wraps bootstrap_all in a broad `except Exception` so one
        # bad tenant plugin cannot cost the platform its boot. That is correct
        # for every failure EXCEPT the compliance abort — which the unit test
        # above proves bootstrap_all raises, and which this call site would
        # otherwise reduce to a warning. Green unit test, dead guarantee.
        source = self._gateway_source()
        self.assertIn("GlobalComplianceLoadFailed", source)
        # and it must re-raise, not merely mention the name
        idx = source.index("GlobalComplianceLoadFailed")
        self.assertIn("raise", source[idx:idx + 200])

    @staticmethod
    def _gateway_source() -> str:
        return (
            _REPO / "core" / "gateway" / "corvin_gateway" / "app.py"
        ).read_text(encoding="utf-8")


if __name__ == "__main__":
    unittest.main()

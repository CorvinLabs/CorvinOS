"""The post-boot tripwire: nothing on the compliance boot layer the wheel did not grant.

Five adversarial rounds concluded that in-process identity is not enforceable —
every guard that asks a plugin something can be lied to. This check asks nothing.
It compares the registry's own state against a list the wheel's boot code wrote,
at a moment when no plugin code has run since, and per ADR-0232/0233 it has no
override, no env var and no flag.

That makes it the one guard on this surface worth writing, and it is a check on
the RESULT rather than on the intent — which is why it survives the derivations
that the intent-side guards did not.

It is vacuously true on today's tree (`_GLOBAL_SPECS` is empty, so nothing is
granted and nothing should be on the layer). These tests therefore construct
both states explicitly instead of relying on the live one.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[3]
for _p in (str(_HERE.parents[1]), str(_REPO), str(_REPO / "core" / "compliance")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from corvin_compliance_reports import tripwire  # noqa: E402
from corvin_plugins.bootstrap import build_context  # noqa: E402
from corvin_plugins.manifest import BootLayer  # noqa: E402
from corvin_plugins.protocol import HealthStatus, PluginContext  # noqa: E402
from corvin_plugins.registry import get_registry  # noqa: E402


class _Plug:
    plugin_type = "compute_engine"
    version = "1.0.0"
    display_name = "P"

    def __init__(self, plugin_id: str) -> None:
        self.plugin_id = plugin_id

    def on_load(self, ctx: PluginContext) -> None:
        pass

    def on_unload(self) -> None:
        pass

    def health_check(self) -> HealthStatus:
        return HealthStatus(ok=True)


def _ctx(plugin_id: str) -> PluginContext:
    return build_context(
        plugin_id=plugin_id, tenant_id="_default", corvin_home=Path("/tmp")
    )


class TestPostBootTripwire(unittest.TestCase):
    def setUp(self):
        self.reg = get_registry()
        self._granted = set(tripwire._GRANTED_COMPLIANCE_IDS)

    def tearDown(self):
        tripwire._GRANTED_COMPLIANCE_IDS.clear()
        tripwire._GRANTED_COMPLIANCE_IDS.update(self._granted)
        for pid in list(self.reg.discover()):
            try:
                self.reg.unregister(pid)
            except Exception:  # noqa: BLE001
                pass

    def test_an_empty_layer_passes(self):
        result = tripwire.compliance_layer_is_wheel_granted()
        self.assertTrue(result.ok, result.detail)

    def test_a_granted_compliance_plugin_passes(self):
        self.reg.register(
            _Plug("audit-writer"), _ctx("audit-writer"),
            boot_layer=BootLayer.COMPLIANCE,
        )
        tripwire.record_granted_compliance_plugin("audit-writer")

        result = tripwire.compliance_layer_is_wheel_granted()
        self.assertTrue(result.ok, result.detail)

    def test_an_ungranted_compliance_plugin_fails_the_boot(self):
        """The case the whole check exists for.

        A plugin that reached the compliance layer some other way — a class
        attribute, a re-registration, a thread that escaped the load context —
        is in the registry and not in the granted set.
        """
        self.reg.register(
            _Plug("self-promoted"), _ctx("self-promoted"),
            boot_layer=BootLayer.COMPLIANCE,
        )
        # deliberately NOT recorded as granted

        result = tripwire.compliance_layer_is_wheel_granted()
        self.assertFalse(result.ok)
        self.assertIn("self-promoted", result.detail)

        with self.assertRaises(tripwire.TripwireError):
            tripwire.assert_post_boot()

    def test_the_detail_carries_ids_only(self):
        # The detail string reaches the hash-chained record. plugin_ids are
        # charset-validated identifiers; nothing else may go in there.
        self.reg.register(
            _Plug("sneaky"), _ctx("sneaky"), boot_layer=BootLayer.COMPLIANCE
        )
        detail = tripwire.compliance_layer_is_wheel_granted().detail
        self.assertIn("sneaky", detail)
        for forbidden in ("/", "\\", "@", "password", "token"):
            self.assertNotIn(forbidden, detail)

    def test_an_unreadable_registry_fails_closed(self):
        # Not being able to answer "who is on the compliance layer" is itself a
        # reason to refuse the boot, not to wave it through.
        from unittest import mock

        with mock.patch(
            "corvin_plugins.registry.PluginRegistry.plugins_by_boot_layer",
            side_effect=RuntimeError("registry broken"),
        ):
            result = tripwire.compliance_layer_is_wheel_granted()
        self.assertFalse(result.ok)
        self.assertIn("RuntimeError", result.detail)

    def test_a_bundled_plugin_does_not_trip_it(self):
        # Counter-test: a check that fired on any plugin would be useless.
        self.reg.register(
            _Plug("discord-bridge"), _ctx("discord-bridge"),
            boot_layer=BootLayer.BUNDLED,
        )
        self.assertTrue(tripwire.compliance_layer_is_wheel_granted().ok)

    def test_the_boot_sequence_actually_calls_it(self):
        # A tripwire nobody runs is the defect this repo has shipped repeatedly —
        # and this assertion was itself an instance of it. It read the GATEWAY's
        # source, which is one of two shipped hosts; the other
        # (corvin_console.standalone, launched by corvinos-serve and install.sh)
        # ran no tripwire at all and this test stayed green throughout.
        #
        # The sequence now lives once in bootstrap.boot_platform. WHICH hosts
        # must call it is pinned by test_boot_platform_call_site.py; this test
        # keeps the narrower question it was written for — that the post-boot
        # tripwire runs, and runs after the plugins are loaded.
        import ast

        path = _REPO / "core" / "plugins" / "corvin_plugins" / "bootstrap.py"
        text = path.read_text(encoding="utf-8")
        fn = next(
            n for n in ast.walk(ast.parse(text))
            if isinstance(n, ast.FunctionDef) and n.name == "boot_platform"
        )
        calls = sorted(
            (
                node.lineno,
                (node.func.id if isinstance(node.func, ast.Name)
                 else node.func.attr).lstrip("_"),
            )
            for node in ast.walk(fn)
            if isinstance(node, ast.Call)
            and isinstance(node.func, (ast.Name, ast.Attribute))
        )
        names = [name for _, name in calls]
        self.assertIn("assert_post_boot", names)
        # and AFTER the bootstrap, or it would be vacuously green
        self.assertLess(
            names.index("bootstrap_all"), names.index("assert_post_boot"),
            "the post-boot tripwire must run after the plugins are loaded",
        )

    def test_bootstrap_records_the_grant(self):
        # The recording side: without it every real compliance plugin would trip
        # the check it is supposed to pass.
        src = (
            _REPO / "core" / "plugins" / "corvin_plugins" / "bootstrap.py"
        ).read_text(encoding="utf-8")
        self.assertIn("_record_granted_compliance", src)


if __name__ == "__main__":
    unittest.main()

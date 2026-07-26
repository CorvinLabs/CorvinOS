"""Tests for boot-time plugin wiring (ADR-0233).

These exist because an adversarial pass over the Phase 1-4 work found two dead
mechanisms — the same defect class ADR-0233 called out in the retired prototype:

1. Nothing built a ``PluginContext``, so ``ctx.audit_registry`` and friends were
   always ``None`` and a plugin's ``on_load()`` had no registry to register with.
2. ``tripwire.assert_all()`` existed but was never called from any boot path.

Every case below pins one of those two against regression.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[3]
_PKG = _HERE.parents[1]
_COMPLIANCE = _REPO / "core" / "compliance"
_FORGE = _REPO / "operator" / "forge"
_SHARED = _REPO / "operator" / "bridges" / "shared"
for _p in (str(_PKG), str(_FORGE), str(_SHARED), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from corvin_plugins import bootstrap  # noqa: E402
from corvin_plugins import circuit_breaker as cb  # noqa: E402
from corvin_plugins.manifest import PIIRisk, PluginOrigin, PluginRecord  # noqa: E402
from corvin_plugins.protocol import HealthStatus  # noqa: E402
from corvin_plugins.providers import audit_backend, user_backend  # noqa: E402
from corvin_plugins.registry import get_registry  # noqa: E402
from corvin_plugins.state import PluginLifecycle  # noqa: E402


class TestBuildContext(unittest.TestCase):
    """The regression that matters: every provider handle must be populated."""

    def test_every_provider_registry_handle_is_attached(self):
        ctx = bootstrap.build_context(
            plugin_id="acme",
            tenant_id="_default",
            corvin_home=Path("/tmp"),
        )
        for handle in (
            "notification_registry",
            "recall_registry",
            "summary_registry",
            "router_registry",
            "audit_registry",
            "user_registry",
        ):
            self.assertIsNotNone(
                getattr(ctx, handle),
                f"{handle} is None — a plugin of that type could never register",
            )

    def test_audit_and_user_handles_are_the_real_registries(self):
        ctx = bootstrap.build_context(
            plugin_id="acme", tenant_id="_default", corvin_home=Path("/tmp")
        )
        self.assertIs(ctx.audit_registry, audit_backend._registry)
        self.assertIs(ctx.user_registry, user_backend._registry)

    def test_layer_registries_pass_through(self):
        sentinel = object()
        ctx = bootstrap.build_context(
            plugin_id="acme",
            tenant_id="_default",
            corvin_home=Path("/tmp"),
            engine_factory=sentinel,
        )
        self.assertIs(ctx.engine_factory, sentinel)

    def test_audit_emit_is_callable_and_never_raises(self):
        ctx = bootstrap.build_context(
            plugin_id="acme", tenant_id="_default", corvin_home=Path("/tmp")
        )
        # No audit path configured for this call — must not raise into the plugin.
        ctx.audit_emit("plugin.test", {"a": 1})

    def test_config_defaults_to_empty_dict(self):
        ctx = bootstrap.build_context(
            plugin_id="acme", tenant_id="_default", corvin_home=Path("/tmp")
        )
        self.assertEqual(ctx.config, {})


class TestAssertCompliance(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._prev = os.environ.get("VOICE_AUDIT_PATH")
        os.environ["VOICE_AUDIT_PATH"] = str(Path(self._tmp.name) / "audit.jsonl")

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("VOICE_AUDIT_PATH", None)
        else:
            os.environ["VOICE_AUDIT_PATH"] = self._prev
        self._tmp.cleanup()

    def test_passes_on_a_clean_install(self):
        self.assertIsInstance(bootstrap.assert_compliance(), list)

    def test_tripwire_module_is_importable_from_the_plugin_package(self):
        """The wiring bug was a ModuleNotFoundError, not a tripwire failure."""
        module = bootstrap._tripwire_module()
        self.assertTrue(hasattr(module, "assert_all"))

    def test_a_broken_chain_aborts_the_boot(self):
        import json

        import audit as _audit  # type: ignore[import-not-found]

        if _audit._se is None:
            self.skipTest("forge.security_events not importable in this layout")

        path = Path(os.environ["VOICE_AUDIT_PATH"])
        _audit.audit_event("bridge.login", channel="test", user="u1")
        _audit.audit_event("bridge.login", channel="test", user="u2")
        lines = path.read_text().splitlines()
        record = json.loads(lines[0])
        record["details"]["user"] = "tampered"
        lines[0] = json.dumps(record)
        path.write_text("\n".join(lines) + "\n")

        with self.assertRaises(Exception) as ctx:
            bootstrap.assert_compliance()
        self.assertIn("tripwire", type(ctx.exception).__name__.lower() + str(ctx.exception).lower())

    def test_missing_checker_falls_back_instead_of_passing_silently(self):
        """"The checker is missing" must not read as "the check passed"."""
        original = bootstrap._tripwire_module

        def boom():
            raise ImportError("no compliance package here")

        bootstrap._tripwire_module = boom  # type: ignore[assignment]
        try:
            with self.assertLogs("corvin.plugins.bootstrap", level="WARNING"):
                result = bootstrap.assert_compliance()
            self.assertEqual(result, [], "the inline fallback ran")
        finally:
            bootstrap._tripwire_module = original  # type: ignore[assignment]

    def test_inline_fallback_still_rejects_a_broken_chain(self):
        import json

        import audit as _audit  # type: ignore[import-not-found]

        if _audit._se is None:
            self.skipTest("forge.security_events not importable in this layout")

        path = Path(os.environ["VOICE_AUDIT_PATH"])
        _audit.audit_event("bridge.login", channel="test", user="u1")
        _audit.audit_event("bridge.login", channel="test", user="u2")
        lines = path.read_text().splitlines()
        record = json.loads(lines[0])
        record["details"]["user"] = "tampered"
        lines[0] = json.dumps(record)
        path.write_text("\n".join(lines) + "\n")

        with self.assertRaises(bootstrap.CoreAuditUnavailable):
            bootstrap._assert_core_audit_inline()


# ── Tenant bootstrap ──────────────────────────────────────────────────────────


class _Recorder:
    """A plugin that records whether it received working registry handles."""

    plugin_id = "test.bootstrap-notify"
    plugin_type = "notification_backend"
    version = "1.0.0"
    display_name = "Bootstrap Notify"

    loaded_with: dict = {}

    def on_load(self, ctx):
        type(self).loaded_with = {
            "tenant_id": ctx.tenant_id,
            "config": ctx.config,
            "notification_registry": ctx.notification_registry is not None,
            "audit_registry": ctx.audit_registry is not None,
        }
        ctx.notification_registry.set_active(self)

    def on_unload(self):
        type(self).loaded_with = {}

    def health_check(self):
        return HealthStatus(ok=True)

    def notify(self, event, payload, *, tenant_id="_default", severity="info"):
        pass


class TestBootstrapTenant(unittest.TestCase):
    def setUp(self):
        cb._registry = cb.BreakerRegistry()
        _Recorder.loaded_with = {}
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        os.environ["VOICE_AUDIT_PATH"] = str(self.home / "audit.jsonl")
        self.lc = PluginLifecycle(
            tenant_id="_default", corvin_home_path=self.home, lifecycle_enabled=True
        )

    def tearDown(self):
        os.environ.pop("VOICE_AUDIT_PATH", None)
        registry = get_registry()
        for pid in list(registry.discover()):
            try:
                registry.unregister(pid)
            except Exception:
                pass
        self._tmp.cleanup()

    def _install(self, *, enabled: bool) -> None:
        record = PluginRecord(
            plugin_id=_Recorder.plugin_id,
            version="1.0.0",
            display_name="Bootstrap Notify",
            plugin_type="notification_backend",
            origin=PluginOrigin.VETTED,
            pii_risk=PIIRisk.NONE,
            class_path="test_bootstrap:_Recorder",
            settings={},
        )
        self.lc.install(record, installed_by="test")
        if enabled:
            self.lc.enable(record.plugin_id)

    def test_flag_off_is_a_no_op(self):
        self._install(enabled=True)
        loaded = bootstrap.bootstrap_tenant(
            tenant_id="_default", corvin_home=self.home, lifecycle_enabled=False
        )
        self.assertEqual(loaded, [])
        self.assertEqual(_Recorder.loaded_with, {}, "no plugin may load while off")

    def test_enabled_plugin_loads_with_working_handles(self):
        self._install(enabled=True)
        loaded = bootstrap.bootstrap_tenant(
            tenant_id="_default", corvin_home=self.home, lifecycle_enabled=True
        )
        self.assertEqual(loaded, [_Recorder.plugin_id])
        self.assertTrue(_Recorder.loaded_with["notification_registry"])
        self.assertTrue(_Recorder.loaded_with["audit_registry"])
        self.assertEqual(_Recorder.loaded_with["tenant_id"], "_default")

    def test_disabled_plugin_is_not_loaded(self):
        self._install(enabled=False)
        loaded = bootstrap.bootstrap_tenant(
            tenant_id="_default", corvin_home=self.home, lifecycle_enabled=True
        )
        self.assertEqual(loaded, [])

    def test_a_broken_plugin_is_skipped_not_fatal(self):
        record = PluginRecord(
            plugin_id="test.does-not-exist",
            version="1.0.0",
            display_name="Missing",
            plugin_type="notification_backend",
            class_path="no_such_module:Nope",
            origin=PluginOrigin.VETTED,
        )
        self.lc.install(record, installed_by="test")
        self.lc.enable("test.does-not-exist", consent_granted_by="test")
        self._install(enabled=True)
        with self.assertLogs("corvin.plugins.bootstrap", level="ERROR"):
            loaded = bootstrap.bootstrap_tenant(
                tenant_id="_default", corvin_home=self.home, lifecycle_enabled=True
            )
        self.assertEqual(loaded, [_Recorder.plugin_id], "the good plugin still loads")

    def test_record_without_class_path_is_skipped(self):
        record = PluginRecord(
            plugin_id="test.no-class-path",
            version="1.0.0",
            display_name="No Class Path",
            plugin_type="notification_backend",
            origin=PluginOrigin.VETTED,
        )
        self.lc.install(record, installed_by="test")
        self.lc.enable("test.no-class-path", consent_granted_by="test")
        with self.assertLogs("corvin.plugins.bootstrap", level="ERROR"):
            self.assertEqual(
                bootstrap.bootstrap_tenant(
                    tenant_id="_default", corvin_home=self.home, lifecycle_enabled=True
                ),
                [],
            )

    def test_corrupt_registry_loads_nothing_but_does_not_raise(self):
        from corvin_plugins.state import registry_path

        path = registry_path(tenant_id="_default", corvin_home_path=self.home)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("plugins: {broken")
        with self.assertLogs("corvin.plugins.bootstrap", level="ERROR"):
            self.assertEqual(
                bootstrap.bootstrap_tenant(
                    tenant_id="_default", corvin_home=self.home, lifecycle_enabled=True
                ),
                [],
            )

    def test_shutdown_unregisters(self):
        self._install(enabled=True)
        loaded = bootstrap.bootstrap_tenant(
            tenant_id="_default", corvin_home=self.home, lifecycle_enabled=True
        )
        bootstrap.shutdown(loaded)
        self.assertNotIn(_Recorder.plugin_id, get_registry().discover())
        self.assertEqual(_Recorder.loaded_with, {}, "on_unload must have run")

    def test_shutdown_of_an_unknown_id_is_harmless(self):
        with self.assertLogs("corvin.plugins.bootstrap", level="ERROR"):
            bootstrap.shutdown(["never-registered"])


if __name__ == "__main__":
    unittest.main()

"""Tests for the per-tenant registry and runtime lifecycle (ADR-0233 Phase 3)."""
from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

# ── Adjust path so tests can be run standalone ───────────────────────────────
_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[3]
_PKG = _HERE.parents[1]
_FORGE = _REPO / "operator" / "forge"
_SHARED = _REPO / "operator" / "bridges" / "shared"
for _p in (str(_PKG), str(_FORGE), str(_SHARED), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from corvin_plugins.manifest import (  # noqa: E402
    Locality,
    NetworkEgress,
    PIIRisk,
    PluginError,
    PluginNotFound,
    PluginOrigin,
    PluginRecord,
    ValidationError,
)
from corvin_plugins.state import (  # noqa: E402
    ConsentRequired,
    LifecycleDisabled,
    PluginLifecycle,
    RegistryCorrupt,
    TenantRegistry,
    instance_dir,
    registry_path,
)

#: The module name a plugin ``class_path`` must use to reach the fakes below.
#:
#: NOT the literal "test_state_lifecycle". pytest's default (prepend) import mode
#: registers this file under its bare name, but ``--import-mode=importlib`` —
#: which .github/workflows/coverage.yml uses — registers it under a dotted,
#: rootdir-relative name. A hard-coded bare name then raises ModuleNotFoundError
#: inside the loader, the plugin is correctly skipped, and every assertion about
#: a LOADED plugin fails. That made this suite pass one way and fail the other:
#: 1040 green locally, 15 red under the mode CI actually runs — a harness
#: artefact that looks exactly like a product defect. ``__name__`` is right under
#: both modes.
_MOD = __name__

_SCHEMA = {
    "type": "object",
    "properties": {"channel": {"type": "string", "default": "ops"}},
    "required": ["channel"],
    "additionalProperties": False,
}


def _record(pid="acme-notify", **kw) -> PluginRecord:
    base = dict(
        plugin_id=pid,
        version="1.0.0",
        display_name="Acme Notify",
        plugin_type="notification_backend",
        origin=PluginOrigin.VETTED,
        pii_risk=PIIRisk.LOW,
        settings_schema=_SCHEMA,
        settings={"channel": "ops"},
    )
    base.update(kw)
    return PluginRecord(**base)


class _Base(unittest.TestCase):
    """Every test gets an isolated CORVIN_HOME and its own audit chain."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self._audit_path = self.home / "audit.jsonl"
        os.environ["VOICE_AUDIT_PATH"] = str(self._audit_path)
        self.lc = PluginLifecycle(
            tenant_id="_default",
            corvin_home_path=self.home,
            lifecycle_enabled=True,
        )

    def tearDown(self):
        os.environ.pop("VOICE_AUDIT_PATH", None)
        self._tmp.cleanup()

    def _reg(self) -> TenantRegistry:
        return TenantRegistry.load(tenant_id="_default", corvin_home_path=self.home)

    def _audit_text(self) -> str:
        return self._audit_path.read_text() if self._audit_path.exists() else ""


# ── Persistence ───────────────────────────────────────────────────────────────


class TestPersistence(_Base):
    def test_missing_registry_loads_empty(self):
        self.assertEqual(self._reg().records, {})

    def test_save_and_reload_round_trip(self):
        reg = self._reg()
        reg.records["acme-notify"] = _record()
        reg.save()
        again = self._reg()
        self.assertIn("acme-notify", again.records)
        self.assertEqual(again.records["acme-notify"].settings, {"channel": "ops"})

    def test_registry_is_mode_0600(self):
        reg = self._reg()
        reg.records["acme-notify"] = _record()
        reg.save()
        mode = stat.S_IMODE(reg.path.stat().st_mode)
        self.assertEqual(mode, 0o600, f"registry must not be world-readable, got {oct(mode)}")

    def test_save_leaves_no_temp_files(self):
        reg = self._reg()
        reg.records["a"] = _record("a")
        reg.save()
        leftovers = [p.name for p in reg.path.parent.iterdir() if p.name.startswith(".registry-")]
        self.assertEqual(leftovers, [])

    def test_corrupt_registry_fails_closed(self):
        path = registry_path(tenant_id="_default", corvin_home_path=self.home)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ this is not: valid: yaml: [")
        with self.assertRaises(RegistryCorrupt):
            self._reg()

    def test_non_mapping_registry_fails_closed(self):
        path = registry_path(tenant_id="_default", corvin_home_path=self.home)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("- just\n- a\n- list\n")
        with self.assertRaises(RegistryCorrupt):
            self._reg()

    def test_corrupt_registry_is_not_overwritten(self):
        """A read failure must never lead to an empty registry being persisted."""
        path = registry_path(tenant_id="_default", corvin_home_path=self.home)
        path.parent.mkdir(parents=True, exist_ok=True)
        original = "plugins: {broken"
        path.write_text(original)
        with self.assertRaises(RegistryCorrupt):
            self.lc.install(_record(), installed_by="operator")
        self.assertEqual(path.read_text(), original, "the bad file must be left alone")

    def test_record_from_a_newer_version_fails_closed(self):
        path = registry_path(tenant_id="_default", corvin_home_path=self.home)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "plugins:\n"
            "  acme:\n"
            "    plugin_id: acme\n"
            "    version: 1.0.0\n"
            "    display_name: Acme\n"
            "    plugin_type: notification_backend\n"
            "    future_field: 42\n"
        )
        with self.assertRaises(PluginError):
            self._reg()

    def test_instance_dir_rejects_a_path_traversal_id(self):
        with self.assertRaises(PluginError):
            instance_dir("../escape", tenant_id="_default", corvin_home_path=self.home)


# ── Install ───────────────────────────────────────────────────────────────────


class TestInstall(_Base):
    def test_install_adds_a_disabled_record(self):
        stored = self.lc.install(_record(), installed_by="operator")
        self.assertFalse(stored.enabled, "install must never enable")
        self.assertIsNotNone(stored.installed_at)
        self.assertEqual(stored.installed_by, "operator")
        self.assertTrue(self._reg().has("acme-notify"))

    def test_install_creates_the_state_dir(self):
        self.lc.install(_record(), installed_by="operator")
        self.assertTrue(
            instance_dir("acme-notify", tenant_id="_default", corvin_home_path=self.home).is_dir()
        )

    def test_install_fills_defaults_from_the_schema(self):
        stored = self.lc.install(_record(settings={}), installed_by="operator")
        self.assertEqual(stored.settings, {"channel": "ops"})

    def test_install_rejects_settings_that_violate_the_schema(self):
        with self.assertRaises(ValidationError):
            self.lc.install(_record(settings={"channel": 42}), installed_by="operator")
        self.assertFalse(self._reg().has("acme-notify"), "nothing must be persisted")

    def test_double_install_is_rejected(self):
        self.lc.install(_record(), installed_by="operator")
        with self.assertRaises(PluginError):
            self.lc.install(_record(), installed_by="operator")

    def test_install_is_audited_with_a_real_chained_event(self):
        self.lc.install(_record(), installed_by="operator")
        text = self._audit_text()
        if not text:
            self.skipTest("audit writer unavailable in this layout")
        self.assertIn("plugin.installed", text)
        self.assertIn("acme-notify/1.0.0", text)
        self.assertIn('"hash"', text, "the event must be part of the hash chain")

    def test_audit_chain_verifies_after_a_lifecycle_run(self):
        self.lc.install(_record(), installed_by="operator")
        self.lc.enable("acme-notify")
        self.lc.set_settings("acme-notify", {"channel": "alerts"})
        self.lc.disable("acme-notify")
        self.lc.uninstall("acme-notify")
        if not self._audit_text():
            self.skipTest("audit writer unavailable in this layout")
        import audit as _audit  # type: ignore[import-not-found]

        ok, problems = _audit.verify_audit(self._audit_path)
        self.assertTrue(ok, f"chain must verify after a full lifecycle: {problems}")


# ── Consent gate ──────────────────────────────────────────────────────────────


class TestConsentGate(_Base):
    def test_community_plugin_needs_consent(self):
        self.lc.install(_record(origin=PluginOrigin.COMMUNITY), installed_by="operator")
        with self.assertRaises(ConsentRequired):
            self.lc.enable("acme-notify")
        self.assertFalse(self._reg().get("acme-notify").enabled)

    def test_high_pii_needs_consent(self):
        self.lc.install(
            _record(origin=PluginOrigin.VETTED, pii_risk=PIIRisk.HIGH),
            installed_by="operator",
        )
        with self.assertRaises(ConsentRequired):
            self.lc.enable("acme-notify")

    def test_consent_grant_allows_enable_and_is_audited(self):
        # A community plugin must ALSO declare its egress (L35) before it can be
        # enabled — this one talks to nothing, which is the honest declaration for
        # a notification backend that only writes to the log.
        self.lc.install(
            _record(origin=PluginOrigin.COMMUNITY, network_egress=NetworkEgress.NONE),
            installed_by="operator",
        )
        enabled = self.lc.enable("acme-notify", consent_granted_by="operator")
        self.assertTrue(enabled.enabled)
        text = self._audit_text()
        if text:
            self.assertIn("consent_granted_by", text)

    def test_denied_enable_is_audited(self):
        self.lc.install(_record(origin=PluginOrigin.COMMUNITY), installed_by="operator")
        with self.assertRaises(ConsentRequired):
            self.lc.enable("acme-notify")
        text = self._audit_text()
        if text:
            self.assertIn("plugin.enable_denied", text)
            self.assertIn("consent_required", text)

    def test_vetted_low_risk_needs_no_consent(self):
        self.lc.install(_record(), installed_by="operator")
        self.assertTrue(self.lc.enable("acme-notify").enabled)


# ── Settings ──────────────────────────────────────────────────────────────────


class TestSettings(_Base):
    def setUp(self):
        super().setUp()
        self.lc.install(_record(), installed_by="operator")

    def test_valid_settings_are_persisted(self):
        self.lc.set_settings("acme-notify", {"channel": "alerts"})
        self.assertEqual(self._reg().get("acme-notify").settings, {"channel": "alerts"})

    def test_invalid_settings_leave_the_old_value_intact(self):
        with self.assertRaises(ValidationError):
            self.lc.set_settings("acme-notify", {"channel": 42})
        self.assertEqual(self._reg().get("acme-notify").settings, {"channel": "ops"})

    def test_unknown_key_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.lc.set_settings("acme-notify", {"channel": "ops", "surprise": 1})

    def test_audit_records_key_names_but_not_values(self):
        self.lc.set_settings("acme-notify", {"channel": "https://hooks.example/T0/B1/secret"})
        text = self._audit_text()
        if not text:
            self.skipTest("audit writer unavailable in this layout")
        self.assertIn("plugin.config_changed", text)
        self.assertIn("channel", text, "key names are fine")
        self.assertNotIn(
            "hooks.example", text, "setting VALUES must never reach the audit chain"
        )
        self.assertNotIn("secret", text)


# ── Disable / uninstall ───────────────────────────────────────────────────────


class TestDisableUninstall(_Base):
    def test_disable_flips_the_flag(self):
        self.lc.install(_record(), installed_by="operator")
        self.lc.enable("acme-notify")
        disabled = self.lc.disable("acme-notify")
        self.assertFalse(disabled.enabled)
        self.assertIsNone(disabled.enabled_at)

    def test_disable_is_blocked_by_an_enabled_dependent(self):
        self.lc.install(_record("base"), installed_by="operator")
        self.lc.install(
            _record("dependent", dependencies=["base>=1.0.0"]), installed_by="operator"
        )
        self.lc.enable("base")
        self.lc.enable("dependent")
        with self.assertRaises(PluginError) as ctx:
            self.lc.disable("base")
        self.assertIn("dependent", str(ctx.exception))
        self.assertTrue(self._reg().get("base").enabled, "must stay enabled")

    def test_disable_allowed_once_the_dependent_is_off(self):
        self.lc.install(_record("base"), installed_by="operator")
        self.lc.install(
            _record("dependent", dependencies=["base>=1.0.0"]), installed_by="operator"
        )
        self.lc.enable("base")
        self.lc.enable("dependent")
        self.lc.disable("dependent")
        self.assertFalse(self.lc.disable("base").enabled)

    def test_enable_is_rolled_back_when_dependencies_are_unsatisfiable(self):
        self.lc.install(
            _record("dependent", dependencies=["absent>=1.0.0"]), installed_by="operator"
        )
        with self.assertRaises(PluginError):
            self.lc.enable("dependent")
        self.assertFalse(
            self._reg().get("dependent").enabled,
            "a failed enable must not leave the record enabled",
        )

    def test_uninstall_requires_disabled(self):
        self.lc.install(_record(), installed_by="operator")
        self.lc.enable("acme-notify")
        with self.assertRaises(PluginError):
            self.lc.uninstall("acme-notify")

    def test_uninstall_removes_record_and_state(self):
        self.lc.install(_record(), installed_by="operator")
        state = instance_dir("acme-notify", tenant_id="_default", corvin_home_path=self.home)
        (state / "cache.json").write_text("{}")
        self.lc.uninstall("acme-notify")
        self.assertFalse(self._reg().has("acme-notify"))
        self.assertFalse(state.exists())

    def test_uninstall_keeps_the_audit_trail(self):
        self.lc.install(_record(), installed_by="operator")
        self.lc.uninstall("acme-notify")
        text = self._audit_text()
        if not text:
            self.skipTest("audit writer unavailable in this layout")
        self.assertIn("plugin.installed", text, "history must survive the uninstall")
        self.assertIn("plugin.uninstalled", text)

    def test_uninstall_can_keep_state(self):
        self.lc.install(_record(), installed_by="operator")
        state = instance_dir("acme-notify", tenant_id="_default", corvin_home_path=self.home)
        self.lc.uninstall("acme-notify", purge_state=False)
        self.assertTrue(state.exists())

    def test_unknown_plugin_raises_not_found(self):
        with self.assertRaises(PluginNotFound):
            self.lc.enable("nope")
        with self.assertRaises(PluginNotFound):
            self.lc.disable("nope")
        with self.assertRaises(PluginNotFound):
            self.lc.uninstall("nope")


# ── Feature-flag gate (both states) ───────────────────────────────────────────


class TestLifecycleFlag(_Base):
    def test_flag_off_refuses_every_mutation(self):
        off = PluginLifecycle(
            tenant_id="_default", corvin_home_path=self.home, lifecycle_enabled=False
        )
        with self.assertRaises(LifecycleDisabled):
            off.install(_record(), installed_by="operator")
        with self.assertRaises(LifecycleDisabled):
            off.enable("acme-notify")
        with self.assertRaises(LifecycleDisabled):
            off.set_settings("acme-notify", {})
        with self.assertRaises(LifecycleDisabled):
            off.disable("acme-notify")
        with self.assertRaises(LifecycleDisabled):
            off.uninstall("acme-notify")

    def test_flag_off_leaves_the_registry_untouched(self):
        off = PluginLifecycle(
            tenant_id="_default", corvin_home_path=self.home, lifecycle_enabled=False
        )
        with self.assertRaises(LifecycleDisabled):
            off.install(_record(), installed_by="operator")
        self.assertFalse(
            registry_path(tenant_id="_default", corvin_home_path=self.home).exists(),
            "a refused mutation must not even create the file",
        )

    def test_flag_is_read_per_call_not_cached(self):
        state = {"on": False}
        lc = PluginLifecycle(
            tenant_id="_default",
            corvin_home_path=self.home,
            lifecycle_enabled=lambda: state["on"],
        )
        with self.assertRaises(LifecycleDisabled):
            lc.install(_record(), installed_by="operator")
        state["on"] = True
        self.assertTrue(lc.install(_record(), installed_by="operator"))

    def test_reading_is_allowed_with_the_flag_off(self):
        """Off means read-only, not invisible."""
        self.lc.install(_record(), installed_by="operator")
        off = PluginLifecycle(
            tenant_id="_default", corvin_home_path=self.home, lifecycle_enabled=False
        )
        del off  # the registry itself is what a reader uses:
        self.assertTrue(self._reg().has("acme-notify"))


# ── Tenant isolation ──────────────────────────────────────────────────────────


class TestTenantIsolation(_Base):
    def test_two_tenants_have_separate_registries(self):
        other = PluginLifecycle(
            tenant_id="tenant-b", corvin_home_path=self.home, lifecycle_enabled=True
        )
        self.lc.install(_record("only-in-default"), installed_by="operator")
        other.install(_record("only-in-b"), installed_by="operator")

        default_reg = TenantRegistry.load(tenant_id="_default", corvin_home_path=self.home)
        b_reg = TenantRegistry.load(tenant_id="tenant-b", corvin_home_path=self.home)

        self.assertIn("only-in-default", default_reg.records)
        self.assertNotIn("only-in-b", default_reg.records)
        self.assertIn("only-in-b", b_reg.records)
        self.assertNotIn("only-in-default", b_reg.records)

    def test_registry_paths_do_not_collide(self):
        a = registry_path(tenant_id="_default", corvin_home_path=self.home)
        b = registry_path(tenant_id="tenant-b", corvin_home_path=self.home)
        self.assertNotEqual(a, b)
        self.assertIn("_default", str(a))
        self.assertIn("tenant-b", str(b))

    def test_enabling_in_one_tenant_does_not_enable_in_another(self):
        other = PluginLifecycle(
            tenant_id="tenant-b", corvin_home_path=self.home, lifecycle_enabled=True
        )
        self.lc.install(_record(), installed_by="operator")
        other.install(_record(), installed_by="operator")
        self.lc.enable("acme-notify")

        b_reg = TenantRegistry.load(tenant_id="tenant-b", corvin_home_path=self.home)
        self.assertFalse(b_reg.get("acme-notify").enabled)

    def test_invalid_tenant_id_is_rejected(self):
        from forge.tenants import InvalidTenantID  # type: ignore[import-not-found]

        with self.assertRaises(InvalidTenantID):
            PluginLifecycle(
                tenant_id="Nope Invalid!", corvin_home_path=self.home, lifecycle_enabled=True
            )

    def test_audit_events_carry_the_tenant(self):
        other = PluginLifecycle(
            tenant_id="tenant-b", corvin_home_path=self.home, lifecycle_enabled=True
        )
        other.install(_record(), installed_by="operator")
        text = self._audit_text()
        if not text:
            self.skipTest("audit writer unavailable in this layout")
        self.assertIn("tenant-b", text)


# ── Load order ────────────────────────────────────────────────────────────────


class TestLoadOrder(_Base):
    def test_enabled_records_are_dependency_ordered(self):
        self.lc.install(_record("base"), installed_by="operator")
        self.lc.install(
            _record("middle", dependencies=["base>=1.0.0"]), installed_by="operator"
        )
        self.lc.install(
            _record("top", dependencies=["middle>=1.0.0"]), installed_by="operator"
        )
        for pid in ("base", "middle", "top"):
            self.lc.enable(pid)
        self.assertEqual(self._reg().load_order(), ["base", "middle", "top"])

    def test_disabled_records_are_excluded_from_the_order(self):
        self.lc.install(_record("base"), installed_by="operator")
        self.lc.install(_record("other"), installed_by="operator")
        self.lc.enable("base")
        self.assertEqual(self._reg().load_order(), ["base"])


if __name__ == "__main__":
    unittest.main()


# ── Hot-reload (ADR-0124 Inv. 6) ──────────────────────────────────────────────


class _HotPlugin:
    """Registers itself and records what happened, so tests can see the effect."""

    plugin_id = "hot-notify"
    plugin_type = "notification_backend"
    version = "1.0.0"
    display_name = "Hot Notify"

    events: list = []

    def on_load(self, ctx):
        type(self).events.append("load")
        if ctx.notification_registry is not None:
            ctx.notification_registry.set_active(self)

    def on_unload(self):
        type(self).events.append("unload")

    def health_check(self):
        from corvin_plugins.protocol import HealthStatus

        return HealthStatus(ok=True)

    def notify(self, event, payload, *, tenant_id="_default", severity="info"):
        pass


class _RefusingPlugin(_HotPlugin):
    plugin_id = "refusing-notify"

    def on_load(self, ctx):
        raise RuntimeError("this plugin refuses to load")


class TestHotReload(_Base):
    """enable() used to write a flag only: the toggle showed on, the plugin was
    inert until the next boot. That is a silent lie in the UI, and ADR-0124 Inv. 6
    requires the change to take effect immediately."""

    def setUp(self):
        super().setUp()
        _HotPlugin.events = []
        from corvin_plugins.registry import get_registry

        self._registry = get_registry()
        for pid in list(self._registry.discover()):
            self._registry.unregister(pid)

    def tearDown(self):
        for pid in list(self._registry.discover()):
            try:
                self._registry.unregister(pid)
            except Exception:
                pass
        super().tearDown()

    def _install_hot(self, cls=_HotPlugin) -> None:
        self.lc.install(
            PluginRecord(
                plugin_id=cls.plugin_id,
                version="1.0.0",
                display_name=cls.display_name,
                plugin_type="notification_backend",
                origin=PluginOrigin.VETTED,
                pii_risk=PIIRisk.NONE,
                class_path=f"{_MOD}:{cls.__name__}",
            ),
            installed_by="test",
        )

    def test_enable_registers_the_plugin_immediately(self):
        self._install_hot()
        self.assertNotIn(_HotPlugin.plugin_id, self._registry.discover())
        self.lc.enable(_HotPlugin.plugin_id)
        self.assertIn(
            _HotPlugin.plugin_id,
            self._registry.discover(),
            "enable() must load the plugin, not just flip a flag",
        )
        self.assertIn("load", _HotPlugin.events)

    def test_enable_wires_the_provider_slot(self):
        from corvin_plugins.providers import notification_backend

        self._install_hot()
        self.lc.enable(_HotPlugin.plugin_id)
        self.assertIsInstance(notification_backend.get_active(), _HotPlugin)

    def test_disable_unloads_immediately(self):
        self._install_hot()
        self.lc.enable(_HotPlugin.plugin_id)
        self.lc.disable(_HotPlugin.plugin_id)
        self.assertNotIn(_HotPlugin.plugin_id, self._registry.discover())
        self.assertIn("unload", _HotPlugin.events)

    def test_a_refusing_plugin_stays_disabled_and_is_not_persisted(self):
        """Fail-closed: the registry must never claim an active plugin that isn't."""
        self._install_hot(_RefusingPlugin)
        with self.assertRaises(PluginError):
            self.lc.enable(_RefusingPlugin.plugin_id)
        self.assertFalse(
            self._reg().get(_RefusingPlugin.plugin_id).enabled,
            "a failed load must roll the enable back on disk",
        )
        self.assertNotIn(_RefusingPlugin.plugin_id, self._registry.discover())

    def test_failed_enable_is_audited(self):
        self._install_hot(_RefusingPlugin)
        with self.assertRaises(PluginError):
            self.lc.enable(_RefusingPlugin.plugin_id)
        text = self._audit_text()
        if text:
            self.assertIn("plugin.enable_failed", text)

    def test_record_without_class_path_enables_without_loading(self):
        """A record-only entry is legitimate; it must not be treated as a failure."""
        self.lc.install(_record("no-class-path"), installed_by="test")
        enabled = self.lc.enable("no-class-path")
        self.assertTrue(enabled.enabled)
        self.assertNotIn("no-class-path", self._registry.discover())

    def test_uninstall_unloads_a_stale_registration(self):
        self._install_hot()
        self.lc.enable(_HotPlugin.plugin_id)
        self.lc.disable(_HotPlugin.plugin_id)
        # Simulate a runtime registration that outlived its disable.
        from corvin_plugins.bootstrap import build_context
        from corvin_plugins.registry import register

        register(_HotPlugin(), build_context(
            plugin_id=_HotPlugin.plugin_id, tenant_id="_default", corvin_home=self.home
        ))
        self.lc.uninstall(_HotPlugin.plugin_id)
        self.assertNotIn(_HotPlugin.plugin_id, self._registry.discover())


# ── L34/L35 declarations (ADR-0124 Inv. 3) ────────────────────────────────────


class TestFlowDeclarations(_Base):
    """A plugin must say where it runs and what it talks to before it may run."""

    def test_defaults_are_the_least_trusted_combination(self):
        rec = _record()
        self.assertIs(rec.locality, Locality.UNKNOWN)
        self.assertIs(rec.network_egress, NetworkEgress.EXTERNAL)

    def test_community_plugin_needs_declared_egress_hosts(self):
        from corvin_plugins.state import EgressNotDeclared

        self.lc.install(_record(origin=PluginOrigin.COMMUNITY), installed_by="operator")
        with self.assertRaises(EgressNotDeclared):
            self.lc.enable("acme-notify", consent_granted_by="operator")
        self.assertFalse(self._reg().get("acme-notify").enabled)

    def test_declaring_hosts_unblocks_a_community_plugin(self):
        self.lc.install(
            _record(origin=PluginOrigin.COMMUNITY, egress_hosts=["hooks.example.com"]),
            installed_by="operator",
        )
        self.assertTrue(
            self.lc.enable("acme-notify", consent_granted_by="operator").enabled
        )

    def test_egress_none_also_unblocks_it(self):
        self.lc.install(
            _record(origin=PluginOrigin.COMMUNITY, network_egress=NetworkEgress.NONE),
            installed_by="operator",
        )
        self.assertTrue(
            self.lc.enable("acme-notify", consent_granted_by="operator").enabled
        )

    def test_vetted_plugin_may_leave_hosts_empty(self):
        """The maintainer reviewed it — that asymmetry is what `origin` is for."""
        self.lc.install(_record(origin=PluginOrigin.VETTED), installed_by="operator")
        self.assertTrue(self.lc.enable("acme-notify").enabled)

    def test_high_pii_with_unknown_locality_is_refused(self):
        self.lc.install(
            _record(
                origin=PluginOrigin.VETTED,
                pii_risk=PIIRisk.HIGH,
                locality=Locality.UNKNOWN,
            ),
            installed_by="operator",
        )
        with self.assertRaises(PluginError) as ctx:
            self.lc.enable("acme-notify", consent_granted_by="operator")
        self.assertIn("locality", str(ctx.exception))

    def test_high_pii_with_local_locality_is_allowed(self):
        self.lc.install(
            _record(
                origin=PluginOrigin.VETTED,
                pii_risk=PIIRisk.HIGH,
                locality=Locality.LOCAL,
                network_egress=NetworkEgress.NONE,
            ),
            installed_by="operator",
        )
        self.assertTrue(
            self.lc.enable("acme-notify", consent_granted_by="operator").enabled
        )

    def test_refusals_are_audited(self):
        from corvin_plugins.state import EgressNotDeclared

        self.lc.install(_record(origin=PluginOrigin.COMMUNITY), installed_by="operator")
        with self.assertRaises(EgressNotDeclared):
            self.lc.enable("acme-notify", consent_granted_by="operator")
        text = self._audit_text()
        if text:
            self.assertIn("egress_not_declared", text)

    def test_cloud_locality_with_no_egress_is_rejected_at_construction(self):
        with self.assertRaises(PluginError):
            _record(locality=Locality.EU_CLOUD, network_egress=NetworkEgress.NONE)

    def test_declarations_survive_the_round_trip(self):
        rec = _record(
            locality=Locality.EU_CLOUD,
            network_egress=NetworkEgress.EXTERNAL,
            egress_hosts=["a.example", "b.example"],
        )
        restored = PluginRecord.from_dict(rec.to_dict())
        self.assertIs(restored.locality, Locality.EU_CLOUD)
        self.assertEqual(restored.egress_hosts, ["a.example", "b.example"])


# ── Concurrency (adversarial review, iteration 1) ─────────────────────────────


class TestConcurrentMutations(_Base):
    """Registry writes must not lose each other.

    Found by review: every mutation did load → modify → save with no lock, so two
    Console requests raced and the last writer won. Measured before the fix: 12
    concurrent installs left 2 records on disk, with no error anywhere.
    """

    def _install(self, i: int) -> None:
        try:
            self.lc.install(
                _record(
                    f"p{i:02d}",
                    origin=PluginOrigin.VETTED,
                    network_egress=NetworkEgress.NONE,
                ),
                installed_by="race",
            )
        except PluginError:
            pass

    def test_concurrent_installs_all_survive(self):
        import threading

        threads = [threading.Thread(target=self._install, args=(i,)) for i in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        records = self._reg().records
        self.assertEqual(
            len(records), 12, f"writes were lost: only {sorted(records)} survived"
        )

    def test_concurrent_enable_and_settings_do_not_clobber(self):
        import threading

        for i in range(6):
            self._install(i)

        errors: list[str] = []

        def enable(i: int) -> None:
            try:
                self.lc.enable(f"p{i:02d}")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{type(exc).__name__}")

        def configure(i: int) -> None:
            try:
                self.lc.set_settings(f"p{i:02d}", {"channel": f"c{i}"})
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{type(exc).__name__}")

        threads = []
        for i in range(6):
            threads.append(threading.Thread(target=enable, args=(i,)))
            threads.append(threading.Thread(target=configure, args=(i,)))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], "no mutation may fail under contention")
        records = self._reg().records
        self.assertEqual(len(records), 6)
        self.assertTrue(all(r.enabled for r in records.values()), "every enable stuck")
        for i in range(6):
            self.assertEqual(records[f"p{i:02d}"].settings["channel"], f"c{i}")

    def test_a_failed_mutation_leaves_the_file_consistent(self):
        self._install(0)
        with self.assertRaises(PluginError):
            self.lc.install(
                _record("p00", origin=PluginOrigin.VETTED, network_egress=NetworkEgress.NONE),
                installed_by="race",
            )
        # The lock context re-raises without saving a partial state.
        self.assertEqual(len(self._reg().records), 1)

    def test_the_lock_file_is_not_world_readable(self):
        import stat

        self._install(0)
        lock = registry_path(
            tenant_id="_default", corvin_home_path=self.home
        ).with_name(".registry.lock")
        self.assertTrue(lock.exists())
        self.assertEqual(stat.S_IMODE(lock.stat().st_mode), 0o600)

    def test_mutation_helper_is_the_only_writer(self):
        """No method may call reg.save() directly — that is how the race returned."""
        import inspect

        from corvin_plugins import state as state_mod

        source = inspect.getsource(state_mod.PluginLifecycle)
        self.assertNotIn(
            "reg.save()", source, "mutations must go through registry_mutation()"
        )

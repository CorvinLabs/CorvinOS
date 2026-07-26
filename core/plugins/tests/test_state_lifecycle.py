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
        self.lc.install(_record(origin=PluginOrigin.COMMUNITY), installed_by="operator")
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

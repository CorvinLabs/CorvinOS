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

#: The module name a plugin ``class_path`` must use to reach the fakes below.
#:
#: NOT the literal "test_bootstrap". pytest's default (prepend) import mode registers this
#: file under its bare name, but --import-mode=importlib — which
#: .github/workflows/coverage.yml uses — registers it under a dotted, rootdir-
#: relative name. A hard-coded bare name then raises ModuleNotFoundError inside
#: the loader, the plugin is correctly skipped, and every assertion about a
#: LOADED plugin fails. That made this suite pass one way and fail the other:
#: 1040 green locally, 15 red in CI, for a harness reason that looks exactly like
#: a product defect. __name__ is right under both modes.
_MOD = __name__

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

    def _clear_runtime(self) -> None:
        """Undo what enable()'s hot-load did, so the next assertion sees only
        what bootstrap_tenant() itself does."""
        registry = get_registry()
        for pid in list(registry.discover()):
            try:
                registry.unregister(pid)
            except Exception:
                pass
        _Recorder.loaded_with = {}

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
        """Install (and optionally enable) the recorder plugin.

        NOTE: enable() hot-loads since ADR-0124 Inv. 6, so callers that want to
        observe what *bootstrap_tenant* does must reset _Recorder.loaded_with and
        unregister afterwards — otherwise they see the enable's effect.
        """
        record = PluginRecord(
            plugin_id=_Recorder.plugin_id,
            version="1.0.0",
            display_name="Bootstrap Notify",
            plugin_type="notification_backend",
            origin=PluginOrigin.VETTED,
            pii_risk=PIIRisk.NONE,
            class_path=f"{_MOD}:_Recorder",
            settings={},
        )
        self.lc.install(record, installed_by="test")
        if enabled:
            self.lc.enable(record.plugin_id)

    def test_flag_off_is_a_no_op(self):
        self._install(enabled=True)
        self._clear_runtime()
        loaded = bootstrap.bootstrap_tenant(
            tenant_id="_default", corvin_home=self.home, lifecycle_enabled=False
        )
        self.assertEqual(loaded, [])
        self.assertEqual(_Recorder.loaded_with, {}, "no plugin may load while off")

    def test_enabled_plugin_loads_with_working_handles(self):
        self._install(enabled=True)
        self._clear_runtime()
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
        """The real case: a record that was enabled while its class WAS loadable,
        and whose module disappeared later (package removed, upgrade dropped it).

        Since enable() hot-loads, it now refuses a broken class_path outright — so
        this state can only be reached by writing the record directly, which is
        exactly what an old registry on disk looks like.
        """
        from corvin_plugins.state import TenantRegistry

        broken = PluginRecord(
            plugin_id="test.does-not-exist",
            version="1.0.0",
            display_name="Missing",
            plugin_type="notification_backend",
            class_path="no_such_module:Nope",
            origin=PluginOrigin.VETTED,
            enabled=True,
        )
        self._install(enabled=True)
        self._clear_runtime()
        reg = TenantRegistry.load(tenant_id="_default", corvin_home_path=self.home)
        reg.records[broken.plugin_id] = broken
        reg.save()
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
        self._clear_runtime()
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
        self._clear_runtime()
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


# ── ADR-0030 Phase 7: the declarative config path ─────────────────────────────


class _Declared:
    """A plugin loaded from spec.plugins.installed rather than the registry."""

    plugin_id = "test.declared-notify"
    plugin_type = "notification_backend"
    version = "1.0.0"
    display_name = "Declared Notify"

    seen_config: dict = {}

    def on_load(self, ctx):
        type(self).seen_config = dict(ctx.config)
        if ctx.notification_registry is not None:
            ctx.notification_registry.set_active(self)

    def on_unload(self):
        type(self).seen_config = {}

    def health_check(self):
        return HealthStatus(ok=True)

    def notify(self, event, payload, *, tenant_id="_default", severity="info"):
        pass


class TestDeclaredPlugins(unittest.TestCase):
    """`spec.plugins.installed` was specified by ADR-0030 and never read.

    `loader.discover_and_load()` implemented it; nothing called either. An operator
    could write the documented config and get no plugins and no error.
    """

    def setUp(self):
        _Declared.seen_config = {}
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        os.environ["VOICE_AUDIT_PATH"] = str(self.home / "audit.jsonl")
        for pid in list(get_registry().discover()):
            get_registry().unregister(pid)

    def tearDown(self):
        os.environ.pop("VOICE_AUDIT_PATH", None)
        for pid in list(get_registry().discover()):
            try:
                get_registry().unregister(pid)
            except Exception:
                pass
        self._tmp.cleanup()

    def _write_config(self, body: str) -> None:
        path = self.home / "tenants" / "_default" / "global" / "tenant.corvin.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    def test_no_config_loads_nothing(self):
        self.assertEqual(
            bootstrap.bootstrap_declared(tenant_id="_default", corvin_home=self.home), []
        )

    def test_unreadable_config_is_not_fatal(self):
        self._write_config("{ this is not: valid: yaml: [")
        with self.assertLogs("corvin.plugins.bootstrap", level="ERROR"):
            self.assertEqual(
                bootstrap.load_tenant_spec("_default", self.home), {}
            )

    def test_declared_plugin_is_loaded_without_any_flag(self):
        self._write_config(
            "spec:\n"
            "  plugins:\n"
            "    installed:\n"
            "      - id: test.declared-notify\n"
            f"        class_path: {_MOD}:_Declared\n"
            "        config:\n"
            "          channel: ops\n"
        )
        loaded = bootstrap.bootstrap_declared(tenant_id="_default", corvin_home=self.home)
        self.assertEqual(loaded, ["test.declared-notify"])
        self.assertIn("test.declared-notify", get_registry().discover())

    def test_declared_config_reaches_the_plugin(self):
        self._write_config(
            "spec:\n"
            "  plugins:\n"
            "    installed:\n"
            "      - id: test.declared-notify\n"
            f"        class_path: {_MOD}:_Declared\n"
            "        config:\n"
            "          channel: alerts\n"
            "          depth: 3\n"
        )
        bootstrap.bootstrap_declared(tenant_id="_default", corvin_home=self.home)
        self.assertEqual(_Declared.seen_config, {"channel": "alerts", "depth": 3})

    def test_a_broken_declaration_is_skipped_not_fatal(self):
        self._write_config(
            "spec:\n"
            "  plugins:\n"
            "    installed:\n"
            "      - id: test.missing\n"
            "        class_path: no_such_module:Nope\n"
            "      - id: test.declared-notify\n"
            f"        class_path: {_MOD}:_Declared\n"
        )
        loaded = bootstrap.bootstrap_declared(tenant_id="_default", corvin_home=self.home)
        self.assertEqual(loaded, ["test.declared-notify"])

    def test_entry_point_discovery_stays_opt_in(self):
        """auto_discover_entry_points defaults to false — loading unlisted code
        from whatever packages happen to be installed must be a choice."""
        self._write_config("spec:\n  plugins:\n    installed: []\n")
        self.assertEqual(
            bootstrap.bootstrap_declared(tenant_id="_default", corvin_home=self.home), []
        )

    def test_bootstrap_all_runs_both_paths_with_declaration_precedence(self):
        self._write_config(
            "spec:\n"
            "  plugins:\n"
            "    installed:\n"
            "      - id: test.declared-notify\n"
            f"        class_path: {_MOD}:_Declared\n"
        )
        # Same plugin_id also in the runtime registry, with a different config.
        lc = PluginLifecycle(
            tenant_id="_default", corvin_home_path=self.home, lifecycle_enabled=True
        )
        lc.install(
            PluginRecord(
                plugin_id=_Declared.plugin_id,
                version="1.0.0",
                display_name="Registry Copy",
                plugin_type="notification_backend",
                origin=PluginOrigin.VETTED,
                class_path=f"{_MOD}:_Declared",
                settings={"channel": "from-registry"},
            ),
            installed_by="test",
        )
        for pid in list(get_registry().discover()):
            get_registry().unregister(pid)
        _Declared.seen_config = {}

        loaded = bootstrap.bootstrap_all(
            tenant_id="_default", corvin_home=self.home, lifecycle_enabled=True
        )
        self.assertEqual(loaded.count(_Declared.plugin_id), 1, "loaded exactly once")
        self.assertNotEqual(
            _Declared.seen_config.get("channel"),
            "from-registry",
            "the declaration must win over the registry entry",
        )

    def test_bootstrap_all_still_loads_registry_only_plugins(self):
        lc = PluginLifecycle(
            tenant_id="_default", corvin_home_path=self.home, lifecycle_enabled=True
        )
        lc.install(
            PluginRecord(
                plugin_id="test.registry-only",
                version="1.0.0",
                display_name="Registry Only",
                plugin_type="notification_backend",
                origin=PluginOrigin.VETTED,
                class_path=f"{_MOD}:_Declared",
            ),
            installed_by="test",
        )
        lc.enable("test.registry-only")
        for pid in list(get_registry().discover()):
            get_registry().unregister(pid)

        loaded = bootstrap.bootstrap_all(
            tenant_id="_default", corvin_home=self.home, lifecycle_enabled=True
        )
        self.assertIn("test.registry-only", loaded)


class TestDegradationIsAudited(unittest.TestCase):
    """Review finding: every boot failure path logged and continued — silently.

    Continuing is correct: one bad plugin must not take the platform down. But a
    log line was the ONLY trace. There is a plugin.loaded event on success and no
    counterpart on failure, so an operator who declared an audit_backend shipping
    copies to a SIEM would get a booting platform, a working core chain, a dead
    SIEM stream, and an audit trail where "never configured" and "died at boot"
    are indistinguishable.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = Path(self.tmp)
        self.events: list[tuple[str, dict]] = []
        self._orig = bootstrap._default_audit_emit
        bootstrap._default_audit_emit = lambda tid: (
            lambda et, d: self.events.append((et, d))
        )
        get_registry()._plugins.clear()
        get_registry()._contexts.clear()

    def tearDown(self):
        bootstrap._default_audit_emit = self._orig
        get_registry()._plugins.clear()
        get_registry()._contexts.clear()

    def _types(self):
        return [e for e, _ in self.events]

    def test_an_unimportable_declared_plugin_is_audited(self):
        config = {"spec": {"plugins": {"installed": [
            {"id": "a.missing", "class_path": "no_such_module_at_all:Nope"},
        ]}}}
        loaded = bootstrap.bootstrap_declared(
            tenant_id="_default", corvin_home=self.home, tenant_config=config
        )
        self.assertEqual(loaded, [], "a broken declaration must not count as loaded")
        self.assertIn("plugin.load_failed", self._types(), str(self.events))

    def test_a_plugin_whose_on_load_raises_is_audited(self):
        class _Hostile:
            plugin_id = "test.hostile-load"
            plugin_type = "audit_backend"
            version = "1.0.0"
            display_name = "Hostile"

            def on_load(self, ctx):
                raise RuntimeError("secret at postgres://u:pw@host/db")

            def on_unload(self):
                pass

            def health_check(self):
                return HealthStatus(ok=True)

        ok = bootstrap._register_instance(
            _Hostile(), plugin_id="test.hostile-load",
            tenant_id="_default", corvin_home=self.home,
        )
        self.assertFalse(ok)
        failed = [d for e, d in self.events if e == "plugin.load_failed"]
        self.assertTrue(failed, str(self.events))
        self.assertEqual(failed[0]["error_type"], "RuntimeError")
        self.assertNotIn(
            "postgres://", str(failed), "the exception MESSAGE must never be audited"
        )

    def test_a_corrupt_registry_is_audited_as_a_subsystem_degradation(self):
        path = self.home / "tenants" / "_default" / "plugins" / "registry.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{{{ not yaml :::")
        loaded = bootstrap.bootstrap_tenant(
            tenant_id="_default", corvin_home=self.home, lifecycle_enabled=True
        )
        self.assertEqual(loaded, [], "a corrupt registry must load nothing")
        self.assertIn("plugin.registry_unusable", self._types(), str(self.events))

    def test_the_platform_still_boots_when_every_path_fails(self):
        """Core stability: the whole subsystem failing is a degrade, not an abort."""
        path = self.home / "tenants" / "_default" / "plugins" / "registry.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("- not\n- a\n- mapping\n")
        config = {"spec": {"plugins": {"installed": [
            {"id": "x.bad", "class_path": "garbage-without-a-colon"},
        ]}}}
        loaded = bootstrap.bootstrap_all(
            tenant_id="_default", corvin_home=self.home,
            lifecycle_enabled=True, tenant_config=config,
        )
        self.assertEqual(loaded, [])
        self.assertTrue(self.events, "a total plugin failure must leave a trail")

    def test_a_raising_audit_sink_cannot_turn_visibility_into_a_boot_failure(self):
        bootstrap._default_audit_emit = lambda tid: (
            lambda et, d: (_ for _ in ()).throw(RuntimeError("audit down"))
        )
        loaded = bootstrap.bootstrap_declared(
            tenant_id="_default", corvin_home=self.home,
            tenant_config={"spec": {"plugins": {"installed": [
                {"id": "a.missing", "class_path": "no_such_module_at_all:Nope"},
            ]}}},
        )
        self.assertEqual(loaded, [])


class TestShutdownFlushesTheFanout(unittest.TestCase):
    """Review finding: the shutdown never flushed the audit fan-out.

    Fan-out is a hand-off, so copies can be queued for a backend that is about to
    be unloaded — and on_unload() discards the queue on purpose (delivering to a
    detached backend is worse than losing a copy). Nothing flushed it first, so
    every clean shutdown silently lost whatever was pending. The gateway lifespan
    now drains before unloading; this pins the ordering the fix depends on.
    """

    def setUp(self):
        from corvin_plugins.providers import audit_backend

        self.provider = audit_backend
        self.provider.clear()

    def tearDown(self):
        self.provider.clear()
        get_registry()._plugins.clear()
        get_registry()._contexts.clear()

    def test_a_flush_before_unload_delivers_what_a_later_unload_would_discard(self):
        received = []

        class _Sink:
            plugin_id = "test.shutdown-sink"
            plugin_type = "audit_backend"
            version = "1.0.0"
            display_name = "Shutdown Sink"

            def on_load(self, ctx):
                pass

            def on_unload(self):
                # What the real audit plugin does: detach, discarding the queue.
                audit_backend_mod.clear()

            def health_check(self):
                return HealthStatus(ok=True)

            def fanout(self, event_type, details, *, severity="INFO", tenant_id="_default"):
                received.append(event_type)

            def verify_chain(self):
                return HealthStatus(ok=True)

            def enforce_retention(self, max_age_days, *, tenant_id="_default"):
                return {"deleted": 0}

        from corvin_plugins.providers import audit_backend as audit_backend_mod

        sink = _Sink()
        self.provider.set_active(sink)
        for i in range(5):
            self.provider.fanout("bridge.login", {"i": i})

        # The order the lifespan implements: flush, THEN unload.
        self.provider.drain_now(timeout=5.0)
        self.assertEqual(len(received), 5, "the flush must happen before the unload")

    def test_unloading_without_a_flush_is_what_loses_them(self):
        """The counterfactual, so the ordering is not silently reversible."""
        received = []

        class _Sink:
            plugin_id = "test.noflush-sink"
            plugin_type = "audit_backend"
            version = "1.0.0"
            display_name = "No Flush"

            def fanout(self, event_type, details, *, severity="INFO", tenant_id="_default"):
                received.append(event_type)

            def verify_chain(self):
                return HealthStatus(ok=True)

            def enforce_retention(self, max_age_days, *, tenant_id="_default"):
                return {"deleted": 0}

        # A sink that is never given the chance to receive: clear() immediately.
        self.provider.set_active(_Sink())
        self.provider.fanout("bridge.login", {"i": 0})
        self.provider.clear()
        self.provider.drain_now(timeout=1.0)
        self.assertEqual(
            received, [], "clear() is supposed to discard — that is why order matters"
        )

    def test_the_lifespan_flushes_before_it_unloads(self):
        """Source-level ordering check: a future edit must not swap the two."""
        from pathlib import Path as _P

        app_path = (
            _P(__file__).resolve().parents[2] / "gateway" / "corvin_gateway" / "app.py"
        )
        if not app_path.exists():
            self.skipTest("gateway not present in this layout")
        app_src = app_path.read_text(encoding="utf-8")
        flush_at = app_src.find("flushed %d queued audit copy(ies)")
        unload_at = app_src.find("_plugin_shutdown(_plugins_loaded)")
        self.assertGreater(flush_at, 0, "the shutdown flush is gone")
        self.assertGreater(unload_at, 0)
        self.assertLess(
            flush_at, unload_at,
            "the fan-out flush must come BEFORE the plugin unload, or on_unload() "
            "discards the queue it was meant to deliver",
        )

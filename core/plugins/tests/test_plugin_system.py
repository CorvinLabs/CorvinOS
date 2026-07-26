"""Tests for the corvin_plugins plugin system (ADR-0030)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

# ── Adjust path so tests can be run standalone ───────────────────────────────
_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[3]            # CorvinOS repo root
_PKG = _HERE.parents[1]             # core/plugins (holds the corvin_plugins package)
for _p in (str(_PKG), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from corvin_plugins.protocol import (
    CorvinPlugin,
    HealthStatus,
    KNOWN_PLUGIN_TYPES,
    PluginAlreadyRegistered,
    PluginContext,
    PluginNotFound,
)
from corvin_plugins.registry import PluginRegistry
import corvin_plugins.registry as _reg_module
import corvin_plugins.loader as loader_module


# ──────────────────────────────────────────────────────────────────────────────
# Minimal stub plugin for tests
# ──────────────────────────────────────────────────────────────────────────────

class _StubPlugin:
    """Minimal CorvinPlugin-compatible stub for use in tests."""

    plugin_id    = "stub-plugin"
    plugin_type  = "compute_engine"
    version      = "0.0.1"
    display_name = "Stub Plugin"

    def __init__(self, *, plugin_id: str = "stub-plugin") -> None:
        self.plugin_id = plugin_id
        self.loaded = False
        self.unloaded = False
        self.load_ctx: PluginContext | None = None

    def on_load(self, ctx: PluginContext) -> None:
        self.loaded = True
        self.load_ctx = ctx

    def on_unload(self) -> None:
        self.unloaded = True

    def health_check(self) -> HealthStatus:
        return HealthStatus(ok=True, message="stub ok", details={"stub": True})


def _make_ctx(plugin_id: str = "stub-plugin") -> PluginContext:
    return PluginContext(
        plugin_id=plugin_id,
        tenant_id="test",
        corvin_home=Path("/tmp"),
        config={},
        audit_emit=lambda *_: None,
    )


# ──────────────────────────────────────────────────────────────────────────────
# 1. HealthStatus dataclass
# ──────────────────────────────────────────────────────────────────────────────

class TestHealthStatus(unittest.TestCase):

    def test_ok_true(self) -> None:
        hs = HealthStatus(ok=True)
        self.assertTrue(hs.ok)
        self.assertEqual(hs.message, "")
        self.assertEqual(hs.details, {})

    def test_ok_false_with_message(self) -> None:
        hs = HealthStatus(ok=False, message="timeout", details={"code": 408})
        self.assertFalse(hs.ok)
        self.assertEqual(hs.message, "timeout")
        self.assertEqual(hs.details["code"], 408)

    def test_defaults_are_independent(self) -> None:
        a = HealthStatus(ok=True)
        b = HealthStatus(ok=True)
        a.details["x"] = 1
        self.assertNotIn("x", b.details)


# ──────────────────────────────────────────────────────────────────────────────
# 2. PluginContext dataclass
# ──────────────────────────────────────────────────────────────────────────────

class TestPluginContext(unittest.TestCase):

    def test_required_fields(self) -> None:
        ctx = _make_ctx()
        self.assertEqual(ctx.plugin_id, "stub-plugin")
        self.assertEqual(ctx.tenant_id, "test")
        self.assertIsInstance(ctx.corvin_home, Path)
        self.assertIsNone(ctx.compute_registry)

    def test_extra_defaults_independent(self) -> None:
        a = _make_ctx()
        b = _make_ctx()
        a.extra["k"] = 1
        self.assertNotIn("k", b.extra)


# ──────────────────────────────────────────────────────────────────────────────
# 3. CorvinPlugin isinstance check
# ──────────────────────────────────────────────────────────────────────────────

class TestCorvinPluginProtocol(unittest.TestCase):

    def test_stub_isinstance(self) -> None:
        self.assertIsInstance(_StubPlugin(), CorvinPlugin)

    def test_missing_method_fails(self) -> None:
        class Incomplete:
            plugin_id = "x"
            plugin_type = "compute_engine"
            version = "1"
            display_name = "x"
            def on_load(self, ctx: PluginContext) -> None: pass
            def on_unload(self) -> None: pass
            # health_check is missing

        # @runtime_checkable only checks for methods/attrs present in Protocol
        # The Protocol has health_check so a class missing it fails isinstance.
        self.assertNotIsInstance(Incomplete(), CorvinPlugin)


# ──────────────────────────────────────────────────────────────────────────────
# 4. PluginRegistry.register() happy path
# ──────────────────────────────────────────────────────────────────────────────

class TestPluginRegistryRegister(unittest.TestCase):

    def setUp(self) -> None:
        self.registry = PluginRegistry()

    def test_register_calls_on_load(self) -> None:
        p = _StubPlugin()
        ctx = _make_ctx()
        self.registry.register(p, ctx)
        self.assertTrue(p.loaded)
        self.assertIs(p.load_ctx, ctx)

    def test_register_stores_plugin(self) -> None:
        p = _StubPlugin()
        self.registry.register(p, _make_ctx())
        self.assertEqual(len(self.registry), 1)

    def test_discover_returns_plugin_id(self) -> None:
        p = _StubPlugin()
        self.registry.register(p, _make_ctx())
        self.assertIn("stub-plugin", self.registry.discover())


# ──────────────────────────────────────────────────────────────────────────────
# 5. PluginRegistry.register() raises PluginAlreadyRegistered on duplicate
# ──────────────────────────────────────────────────────────────────────────────

class TestPluginRegistryDuplicate(unittest.TestCase):

    def test_duplicate_raises(self) -> None:
        registry = PluginRegistry()
        p1 = _StubPlugin()
        p2 = _StubPlugin()  # same plugin_id
        registry.register(p1, _make_ctx())
        with self.assertRaises(PluginAlreadyRegistered):
            registry.register(p2, _make_ctx())

    def test_duplicate_leaves_original(self) -> None:
        registry = PluginRegistry()
        p1 = _StubPlugin()
        p2 = _StubPlugin()
        registry.register(p1, _make_ctx())
        try:
            registry.register(p2, _make_ctx())
        except PluginAlreadyRegistered:
            pass
        self.assertIs(registry.get("stub-plugin"), p1)


# ──────────────────────────────────────────────────────────────────────────────
# 6. PluginRegistry.unregister() happy path
# ──────────────────────────────────────────────────────────────────────────────

class TestPluginRegistryUnregister(unittest.TestCase):

    def test_unregister_calls_on_unload(self) -> None:
        registry = PluginRegistry()
        p = _StubPlugin()
        registry.register(p, _make_ctx())
        registry.unregister("stub-plugin")
        self.assertTrue(p.unloaded)

    def test_unregister_removes_from_registry(self) -> None:
        registry = PluginRegistry()
        p = _StubPlugin()
        registry.register(p, _make_ctx())
        registry.unregister("stub-plugin")
        self.assertEqual(len(registry), 0)


# ──────────────────────────────────────────────────────────────────────────────
# 7. PluginRegistry.get() raises PluginNotFound for unknown id
# ──────────────────────────────────────────────────────────────────────────────

class TestPluginRegistryGet(unittest.TestCase):

    def test_get_raises_for_unknown(self) -> None:
        registry = PluginRegistry()
        with self.assertRaises(PluginNotFound):
            registry.get("nonexistent")

    def test_get_returns_plugin(self) -> None:
        registry = PluginRegistry()
        p = _StubPlugin()
        registry.register(p, _make_ctx())
        self.assertIs(registry.get("stub-plugin"), p)


# ──────────────────────────────────────────────────────────────────────────────
# 8. PluginRegistry.health_check_all() returns dict
# ──────────────────────────────────────────────────────────────────────────────

class TestHealthCheckAll(unittest.TestCase):

    def test_returns_dict_with_plugin_id_keys(self) -> None:
        registry = PluginRegistry()
        registry.register(_StubPlugin(plugin_id="a"), _make_ctx("a"))
        registry.register(_StubPlugin(plugin_id="b"), _make_ctx("b"))
        result = registry.health_check_all()
        self.assertIn("a", result)
        self.assertIn("b", result)
        self.assertIsInstance(result["a"], HealthStatus)
        self.assertTrue(result["a"].ok)

    def test_empty_registry_returns_empty_dict(self) -> None:
        registry = PluginRegistry()
        self.assertEqual(registry.health_check_all(), {})


# ──────────────────────────────────────────────────────────────────────────────
# 9. health_check_all() catches plugin exceptions and returns ok=False
# ──────────────────────────────────────────────────────────────────────────────

class TestHealthCheckAllCatchesExceptions(unittest.TestCase):

    def test_exception_in_health_check_returns_ok_false(self) -> None:
        """A raising health_check reports the exception CLASS, never its message.

        Changed by ADR-0233: this assertion used to require the message text
        ("exploded") to appear in HealthStatus.message. That surface now reaches
        the Console and the logs, and a plugin's exception message routinely
        carries a path, a hostname or a record fragment — so only the class name
        is propagated.
        """
        class BrokenPlugin(_StubPlugin):
            def health_check(self) -> HealthStatus:
                raise RuntimeError("health exploded for user jdoe at /home/secret")

        registry = PluginRegistry()
        bp = BrokenPlugin(plugin_id="broken")
        registry.register(bp, _make_ctx("broken"))
        result = registry.health_check_all()
        self.assertIn("broken", result)
        self.assertFalse(result["broken"].ok)
        self.assertIn("RuntimeError", result["broken"].message)
        self.assertNotIn("exploded", result["broken"].message)
        self.assertNotIn("jdoe", result["broken"].message)
        self.assertNotIn("/home/secret", result["broken"].message)
        # The breaker state travels with the status (ADR-0233 Phase 2).
        self.assertIn("breaker", result["broken"].details)

    def test_other_plugins_unaffected_by_exception(self) -> None:
        class BrokenPlugin(_StubPlugin):
            def health_check(self) -> HealthStatus:
                raise RuntimeError("boom")

        registry = PluginRegistry()
        registry.register(BrokenPlugin(plugin_id="broken"), _make_ctx("broken"))
        registry.register(_StubPlugin(plugin_id="good"), _make_ctx("good"))
        result = registry.health_check_all()
        self.assertFalse(result["broken"].ok)
        self.assertTrue(result["good"].ok)


# ──────────────────────────────────────────────────────────────────────────────
# 10. PluginRegistry.plugins_by_type() filters correctly
# ──────────────────────────────────────────────────────────────────────────────

class TestPluginsByType(unittest.TestCase):

    def test_filters_by_plugin_type(self) -> None:
        class WorkerPlugin(_StubPlugin):
            plugin_type = "worker_engine"

        registry = PluginRegistry()
        compute = _StubPlugin(plugin_id="compute1")
        worker = WorkerPlugin(plugin_id="worker1")
        registry.register(compute, _make_ctx("compute1"))
        registry.register(worker, _make_ctx("worker1"))

        compute_plugins = registry.plugins_by_type("compute_engine")
        worker_plugins = registry.plugins_by_type("worker_engine")

        self.assertEqual(len(compute_plugins), 1)
        self.assertIs(compute_plugins[0], compute)
        self.assertEqual(len(worker_plugins), 1)
        self.assertIs(worker_plugins[0], worker)

    def test_unknown_type_returns_empty_list(self) -> None:
        registry = PluginRegistry()
        registry.register(_StubPlugin(), _make_ctx())
        self.assertEqual(registry.plugins_by_type("nonexistent_type"), [])


# ──────────────────────────────────────────────────────────────────────────────
# 11. loader.load_from_class_path() with stdlib target
# ──────────────────────────────────────────────────────────────────────────────

class TestLoadFromClassPath(unittest.TestCase):

    def test_colon_separator(self) -> None:
        cls = loader_module.load_from_class_path("os.path:join")
        import os.path
        self.assertIs(cls, os.path.join)

    def test_dot_separator(self) -> None:
        cls = loader_module.load_from_class_path("os.path.join")
        import os.path
        self.assertIs(cls, os.path.join)

    def test_invalid_path_raises(self) -> None:
        with self.assertRaises((ImportError, ModuleNotFoundError, AttributeError)):
            loader_module.load_from_class_path("nonexistent.module:Foo")


# ──────────────────────────────────────────────────────────────────────────────
# 12. discover_and_load returns empty list for empty config
# ──────────────────────────────────────────────────────────────────────────────

class TestDiscoverAndLoad(unittest.TestCase):

    def test_empty_config_returns_empty_list(self) -> None:
        result = loader_module.discover_and_load({}, corvin_home=Path("/tmp"))
        self.assertEqual(result, [])

    def test_empty_installed_list(self) -> None:
        cfg = {"spec": {"plugins": {"installed": []}}}
        result = loader_module.discover_and_load(cfg, corvin_home=Path("/tmp"))
        self.assertEqual(result, [])

    def test_missing_spec_key(self) -> None:
        result = loader_module.discover_and_load({"other": 1}, corvin_home=Path("/tmp"))
        self.assertEqual(result, [])

    def test_load_via_class_path(self) -> None:
        cfg = {
            "spec": {
                "plugins": {
                    "installed": [
                        {
                            "id": "stub-plugin",
                            "class_path": f"{__name__}:_StubPlugin",
                        }
                    ]
                }
            }
        }
        result = loader_module.discover_and_load(cfg, corvin_home=Path("/tmp"))
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], _StubPlugin)

    def test_bad_class_path_skipped(self) -> None:
        cfg = {
            "spec": {
                "plugins": {
                    "installed": [
                        {"id": "bad", "class_path": "nonexistent.module:Foo"},
                    ]
                }
            }
        }
        # Should not raise — bad entry is logged and skipped
        result = loader_module.discover_and_load(cfg, corvin_home=Path("/tmp"))
        self.assertEqual(result, [])


# ──────────────────────────────────────────────────────────────────────────────
# 13. Module-level register/discover convenience functions
# ──────────────────────────────────────────────────────────────────────────────

class TestModuleLevelFunctions(unittest.TestCase):

    def setUp(self) -> None:
        # Each test gets a fresh registry to avoid cross-test pollution.
        self._orig_registry = _reg_module._registry
        _reg_module._registry = PluginRegistry()

    def tearDown(self) -> None:
        _reg_module._registry = self._orig_registry

    def test_register_and_discover(self) -> None:
        p = _StubPlugin()
        ctx = _make_ctx()
        _reg_module.register(p, ctx)
        self.assertIn("stub-plugin", _reg_module.discover())

    def test_get_returns_plugin(self) -> None:
        p = _StubPlugin()
        _reg_module.register(p, _make_ctx())
        self.assertIs(_reg_module.get("stub-plugin"), p)

    def test_unregister_removes_plugin(self) -> None:
        p = _StubPlugin()
        _reg_module.register(p, _make_ctx())
        _reg_module.unregister("stub-plugin")
        with self.assertRaises(PluginNotFound):
            _reg_module.get("stub-plugin")

    def test_health_check_all_returns_dict(self) -> None:
        p = _StubPlugin()
        _reg_module.register(p, _make_ctx())
        result = _reg_module.health_check_all()
        self.assertIn("stub-plugin", result)

    def test_get_registry_returns_registry_instance(self) -> None:
        self.assertIsInstance(_reg_module.get_registry(), PluginRegistry)

    def test_known_plugin_types(self) -> None:
        # ADR-0030 originals
        self.assertIn("compute_engine", KNOWN_PLUGIN_TYPES)
        self.assertIn("worker_engine", KNOWN_PLUGIN_TYPES)
        self.assertIn("bridge_channel", KNOWN_PLUGIN_TYPES)
        self.assertIn("stt_provider", KNOWN_PLUGIN_TYPES)
        self.assertIn("data_connector", KNOWN_PLUGIN_TYPES)
        self.assertIn("audit_backend", KNOWN_PLUGIN_TYPES)
        # ADR-0033 additions
        self.assertIn("notification_backend", KNOWN_PLUGIN_TYPES)
        self.assertIn("recall_backend", KNOWN_PLUGIN_TYPES)
        self.assertIn("summary_provider", KNOWN_PLUGIN_TYPES)
        self.assertIn("router_backend", KNOWN_PLUGIN_TYPES)


# ──────────────────────────────────────────────────────────────────────────────
# 14. ADR-0033 provider protocols — isinstance checks
# ──────────────────────────────────────────────────────────────────────────────

from corvin_plugins.protocol import (
    NotificationBackend,
    RecallBackend,
    SummaryProvider,
    RouterBackend,
)
from corvin_plugins.providers.notification_backend import (
    LogNotificationBackend,
    NotificationBackendRegistry,
)
from corvin_plugins.providers.recall_backend import (
    SqliteRecallBackend,
    RecallBackendRegistry,
)
from corvin_plugins.providers.summary_provider import (
    ClaudeCliSummaryProvider,
    SummaryProviderRegistry,
)
from corvin_plugins.providers.router_backend import (
    ChainRouterBackend,
    RouterBackendRegistry,
)


class TestProviderProtocols(unittest.TestCase):

    def test_log_notification_backend_isinstance(self) -> None:
        self.assertIsInstance(LogNotificationBackend(), NotificationBackend)

    def test_sqlite_recall_backend_isinstance(self) -> None:
        self.assertIsInstance(SqliteRecallBackend(), RecallBackend)

    def test_claude_cli_summary_provider_isinstance(self) -> None:
        self.assertIsInstance(ClaudeCliSummaryProvider(), SummaryProvider)

    def test_chain_router_backend_isinstance(self) -> None:
        self.assertIsInstance(ChainRouterBackend(), RouterBackend)


# ──────────────────────────────────────────────────────────────────────────────
# 15. ADR-0033 provider registries — set/get and default
# ──────────────────────────────────────────────────────────────────────────────

class TestProviderRegistries(unittest.TestCase):

    def test_notification_default_is_log(self) -> None:
        reg = NotificationBackendRegistry()
        self.assertIsInstance(reg.get_active(), LogNotificationBackend)

    def test_notification_set_active(self) -> None:
        reg = NotificationBackendRegistry()
        custom = LogNotificationBackend()
        reg.set_active(custom)
        self.assertIs(reg.get_active(), custom)

    def test_recall_default_is_sqlite(self) -> None:
        reg = RecallBackendRegistry()
        self.assertIsInstance(reg.get_active(), SqliteRecallBackend)

    def test_summary_default_is_claude_cli(self) -> None:
        reg = SummaryProviderRegistry()
        self.assertIsInstance(reg.get_active(), ClaudeCliSummaryProvider)

    def test_router_default_is_chain(self) -> None:
        reg = RouterBackendRegistry()
        self.assertIsInstance(reg.get_active(), ChainRouterBackend)


# ──────────────────────────────────────────────────────────────────────────────
# 16. ADR-0033 provider registries wired into PluginContext
# ──────────────────────────────────────────────────────────────────────────────

class TestPluginContextProviderFields(unittest.TestCase):

    def test_notification_registry_field_exists(self) -> None:
        ctx = _make_ctx()
        self.assertIsNone(ctx.notification_registry)

    def test_recall_registry_field_exists(self) -> None:
        ctx = _make_ctx()
        self.assertIsNone(ctx.recall_registry)

    def test_summary_registry_field_exists(self) -> None:
        ctx = _make_ctx()
        self.assertIsNone(ctx.summary_registry)

    def test_router_registry_field_exists(self) -> None:
        ctx = _make_ctx()
        self.assertIsNone(ctx.router_registry)

    def test_plugin_on_load_can_call_set_active(self) -> None:
        """A plugin can register itself via ctx.summary_registry in on_load()."""
        reg = SummaryProviderRegistry()

        class FakeSummaryPlugin(_StubPlugin):
            plugin_type = "summary_provider"

            def summarize(self, text, *, lang="de", max_chars=400, tenant_id="_default"):
                return "fake"

            def on_load(self, ctx: PluginContext) -> None:
                super().on_load(ctx)
                if ctx.summary_registry is not None:
                    ctx.summary_registry.set_active(self)

        plugin = FakeSummaryPlugin(plugin_id="fake-summary")
        ctx = PluginContext(
            plugin_id="fake-summary",
            tenant_id="test",
            corvin_home=Path("/tmp"),
            config={},
            audit_emit=lambda *_: None,
            summary_registry=reg,
        )
        registry = PluginRegistry()
        registry.register(plugin, ctx)
        self.assertIs(reg.get_active(), plugin)
        self.assertEqual(reg.get_active().summarize("hello"), "fake")


# ──────────────────────────────────────────────────────────────────────────────
# 17. Default implementations: no-raise / fallback contract
# ──────────────────────────────────────────────────────────────────────────────

class TestProviderDefaultContracts(unittest.TestCase):

    def test_log_notification_does_not_raise(self) -> None:
        b = LogNotificationBackend()
        b.notify("test.event", {"x": 1}, tenant_id="t", severity="warn")

    def test_sqlite_recall_recall_returns_list_when_module_missing(self) -> None:
        b = SqliteRecallBackend()
        result = b.recall("anything", tenant_id="t")
        self.assertIsInstance(result, list)

    def test_sqlite_recall_forget_returns_int_when_module_missing(self) -> None:
        b = SqliteRecallBackend()
        result = b.forget(channel="ch", chat_key="chat", tenant_id="t")
        self.assertIsInstance(result, int)

    def test_claude_cli_summary_falls_back_when_script_missing(self) -> None:
        p = ClaudeCliSummaryProvider()
        p._script_path = lambda: None  # type: ignore[method-assign]
        result = p.summarize("hello world", lang="de", max_chars=5)
        self.assertEqual(result, "hello")

    def test_chain_router_returns_none_when_module_missing(self) -> None:
        b = ChainRouterBackend()
        b._router_mod = lambda: None  # type: ignore[method-assign]
        result = b.route("test", [], tenant_id="t")
        self.assertIsNone(result)


if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(unittest.TestLoader().loadTestsFromModule(
        sys.modules[__name__]
    ))
    sys.exit(0 if result.wasSuccessful() else 1)

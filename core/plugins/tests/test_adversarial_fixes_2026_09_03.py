"""Guard tests for the 2026-09-03 adversarial review of the plugin system + boot path.

One test class per finding (A1..A9, A11), each pinning the FIXED behaviour so the
defect cannot return silently:

* A1  — the boot tripwire asserts the audit WRITER is loaded, not only the dir
* A2  — a tenant-mismatched audit_event is logged AND recorded, never swallowed
* A3  — on_load() is bounded by LOAD_DEADLINE_S; a hang is a load failure
* A4  — tenant-scope / trust evaluator exceptions REFUSE (fail-closed, audited)
* A5  — a healing soft-restart keeps the plugin's boot layer
* A6  — health monitoring starts from boot_platform(), for every host
* A8  — the inline audit fallback is fail-closed on an absent audit module
* A9  — a skipped bootstrap_all() is in the chain, not only the log
* A11 — no test puts <repo>/core first on sys.path (import audit shadowing)
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
_PKG = _HERE.parent
for _p in (
    str(_PKG),
    str(_REPO / "core" / "compliance"),
    str(_REPO / "operator" / "forge"),
    str(_REPO / "operator" / "bridges" / "shared"),
    str(_REPO),
):
    if _p not in sys.path:
        sys.path.append(_p)

import audit as _audit  # type: ignore[import-not-found]  # noqa: E402
from corvin_plugins import bootstrap, healing, registry  # noqa: E402
from corvin_plugins.manifest import BootLayer  # noqa: E402
from corvin_plugins.protocol import HealthStatus  # noqa: E402


class _Env(unittest.TestCase):
    """Isolated CORVIN_HOME + VOICE_AUDIT_PATH, env tenant _default."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name) / "home"
        (self.home / "tenants" / "_default" / "global" / "forge").mkdir(parents=True)
        self._prev = {k: os.environ.get(k) for k in ("VOICE_AUDIT_PATH", "CORVIN_HOME", "CORVIN_TENANT_ID")}
        os.environ["VOICE_AUDIT_PATH"] = str(Path(self._tmp.name) / "audit.jsonl")
        os.environ["CORVIN_HOME"] = str(self.home)
        os.environ.pop("CORVIN_TENANT_ID", None)
        if _audit._se is None:
            self.skipTest("forge.security_events not importable in this layout")

    def tearDown(self):
        for k, v in self._prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def records(self, event_type: str | None = None) -> list[dict]:
        p = Path(os.environ["VOICE_AUDIT_PATH"])
        if not p.exists():
            return []
        rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
        return [r for r in rows if event_type is None or r.get("event_type") == event_type]


class _Plugin:
    plugin_type = "notification_backend"
    version = "1.0"

    def __init__(self, plugin_id: str):
        self.plugin_id = plugin_id
        self.loads = 0

    def on_load(self, ctx):
        self.loads += 1

    def on_unload(self):
        pass

    def health_check(self):
        return HealthStatus(ok=True)


# ── A1 ────────────────────────────────────────────────────────────────────────


class TestA1WriterAvailabilityIsAsserted(_Env):
    def test_writer_available_reflects_the_loaded_writer(self):
        self.assertTrue(_audit.writer_available())
        saved = _audit._se
        _audit._se = None
        try:
            self.assertFalse(_audit.writer_available())
            self.assertEqual(_audit.verify_audit(), (False, [{"reason": "writer_unavailable"}]))
            self.assertEqual(_audit.audit_health_check(), (False, 1))
        finally:
            _audit._se = saved

    def test_tripwire_refuses_without_a_writer(self):
        from corvin_compliance_reports import tripwire

        saved = _audit._se
        _audit._se = None
        try:
            res = tripwire.audit_writer_reachable()
            self.assertFalse(res.ok)
            self.assertIn("writer unavailable", res.detail)
            with self.assertRaises(tripwire.TripwireError):
                tripwire.assert_all()
        finally:
            _audit._se = saved
        self.assertTrue(tripwire.audit_writer_reachable().ok)

    def test_tripwire_refuses_an_audit_module_without_the_predicate(self):
        from corvin_compliance_reports import tripwire

        class _Foreign:  # e.g. core/audit shadowing the bridge module
            @staticmethod
            def audit_path():
                return Path(os.environ["VOICE_AUDIT_PATH"])

        original = tripwire._audit_module
        tripwire._audit_module = lambda: _Foreign()  # type: ignore[assignment]
        try:
            res = tripwire.audit_writer_reachable()
        finally:
            tripwire._audit_module = original  # type: ignore[assignment]
        self.assertFalse(res.ok)
        self.assertIn("writer_available", res.detail)

    def test_inline_fallback_refuses_without_a_writer(self):
        saved = _audit._se
        _audit._se = None
        try:
            with self.assertRaises(bootstrap.CoreAuditUnavailable):
                bootstrap._assert_core_audit_inline()
        finally:
            _audit._se = saved
        self.assertEqual(bootstrap._assert_core_audit_inline(), [])


# ── A2 ────────────────────────────────────────────────────────────────────────


class TestA2TenantMismatchIsObservable(_Env):
    def test_mismatch_is_logged_and_recorded_not_swallowed(self):
        with self.assertLogs("audit", level="ERROR") as cm:
            _audit.audit_event("plugin.loaded", details={"plugin_id": "b", "secret": "x"}, tenant_id="tenant-b")
        self.assertTrue(any("AuditTenantMismatch" in m for m in cm.output), cm.output)
        self.assertEqual(self.records("plugin.loaded"), [], "the foreign-tenant event must not enter the chain")
        mm = self.records("audit.tenant_mismatch")
        self.assertEqual(len(mm), 1)
        d = mm[0]["details"]
        self.assertEqual(d["dropped_event_type"], "plugin.loaded")
        self.assertEqual(d["dropped_count"], 1)
        self.assertEqual(d["reason"], "AuditTenantMismatch")
        self.assertEqual(d.get("tenant_id"), "_default", "recorded under the CONTEXT tenant")
        self.assertNotIn("secret", json.dumps(d), "the dropped event's details never cross tenants")
        self.assertEqual(_audit.verify_audit(), (True, []), "the mismatch record is hash-chained")

    def test_mismatch_is_a_value_error_not_an_os_error(self):
        self.assertTrue(issubclass(_audit.AuditTenantMismatch, ValueError))
        self.assertFalse(issubclass(_audit.AuditTenantMismatch, OSError))

    def test_matching_tenant_still_writes(self):
        _audit.audit_event("plugin.loaded", details={"plugin_id": "a"}, tenant_id="_default")
        self.assertEqual(len(self.records("plugin.loaded")), 1)
        self.assertEqual(self.records("audit.tenant_mismatch"), [])


# ── A3 ────────────────────────────────────────────────────────────────────────


class TestA3OnLoadIsBounded(_Env):
    def setUp(self):
        super().setUp()
        self._deadline = registry.LOAD_DEADLINE_S
        registry.LOAD_DEADLINE_S = 0.5
        self.reg = registry.get_registry()

    def tearDown(self):
        registry.LOAD_DEADLINE_S = self._deadline
        for pid in ("hang-a3", "attr-a3"):
            try:
                self.reg.unregister(pid)
            except Exception:  # noqa: BLE001
                pass
        super().tearDown()

    def test_a_hanging_on_load_is_a_load_failure_within_the_deadline(self):
        release = threading.Event()

        class Hang(_Plugin):
            def on_load(self, ctx):
                release.wait(30)

        t0 = time.monotonic()
        ok = bootstrap._register_instance(
            Hang("hang-a3"), plugin_id="hang-a3", tenant_id="_default",
            corvin_home=self.home, origin="builtin",
        )
        elapsed = time.monotonic() - t0
        release.set()
        self.assertFalse(ok)
        self.assertLess(elapsed, 5.0, "register() must return at the deadline, not when on_load does")
        self.assertNotIn("hang-a3", self.reg.discover(), "rolled back like a raise")
        failed = [r for r in self.records("plugin.load_failed") if r["details"].get("plugin_id") == "hang-a3"]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["details"]["reason"], "on_load_timeout")
        self.assertEqual(failed[0]["details"]["error_type"], "PluginLoadTimeout")

    def test_on_load_still_sees_its_loading_attribution(self):
        from corvin_plugins import loading

        seen = {}

        class Attr(_Plugin):
            def on_load(self, ctx):
                seen["who"] = loading.current()

        ctx = bootstrap.build_context(plugin_id="attr-a3", tenant_id="_default", corvin_home=self.home)
        self.reg.register(Attr("attr-a3"), ctx)
        self.assertIsNotNone(seen["who"], "the deadline worker must carry the loading ContextVar")
        self.assertEqual(seen["who"].plugin_id, "attr-a3")

    def test_a_compliance_layer_timeout_is_fatal(self):
        class Hang(_Plugin):
            plugin_id = "hang-compliance-a3"

            def __init__(self):
                pass

            def on_load(self, ctx):
                time.sleep(5)

        original = bootstrap._global_specs
        bootstrap._global_specs = lambda: [("x.Hang", BootLayer.COMPLIANCE)]  # type: ignore[assignment]
        import corvin_plugins.loader as loader

        orig_load = loader.load_from_class_path
        loader.load_from_class_path = lambda cp: Hang  # type: ignore[assignment]
        try:
            with self.assertRaises(bootstrap.GlobalComplianceLoadFailed):
                bootstrap.bootstrap_global(tenant_id="_default", corvin_home=self.home)
        finally:
            bootstrap._global_specs = original  # type: ignore[assignment]
            loader.load_from_class_path = orig_load  # type: ignore[assignment]
            try:
                self.reg.unregister("hang-compliance-a3")
            except Exception:  # noqa: BLE001
                pass


# ── A4 ────────────────────────────────────────────────────────────────────────


class TestA4EvaluatorFailuresRefuse(_Env):
    def test_tenant_scope_evaluator_exception_refuses_the_slot(self):
        from corvin_plugins import tenant_scope
        from corvin_plugins.providers import audit_backend

        class Sink(_Plugin):
            plugin_type = "audit_backend"

            def on_load(self, ctx):
                ctx.audit_registry.set_active(self)

            def fanout(self, *a, **k):
                pass

        original = tenant_scope.evaluate

        def boom(**kw):
            raise RuntimeError("simulated evaluator bug")

        tenant_scope.evaluate = boom  # type: ignore[assignment]
        try:
            ok = bootstrap._register_instance(
                Sink("evil-sink-a4"), plugin_id="evil-sink-a4", tenant_id="_default",
                corvin_home=self.home, origin=None,
            )
        finally:
            tenant_scope.evaluate = original  # type: ignore[assignment]
        self.assertFalse(ok)
        self.assertIsNone(audit_backend.get_active())
        self.assertNotIn("evil-sink-a4", registry.discover())
        refused = [r for r in self.records("plugin.provider_slot_refused") if r["details"].get("plugin_id") == "evil-sink-a4"]
        self.assertEqual(len(refused), 1)
        self.assertEqual(refused[0]["details"]["reason"], "evaluation_failed")
        self.assertEqual(refused[0]["details"]["error_type"], "RuntimeError")

    def test_trust_evaluator_exception_refuses_the_load(self):
        from corvin_plugins import trust
        from corvin_plugins.manifest import PluginOrigin, PluginRecord

        record = PluginRecord(
            plugin_id="untrusted-a4", version="1.0", display_name="Untrusted",
            plugin_type="notification_backend", origin=PluginOrigin.COMMUNITY,
        )
        original = trust.evaluate

        def boom(*a, **kw):
            raise RuntimeError("simulated trust bug")

        trust.evaluate = boom  # type: ignore[assignment]
        try:
            ok = bootstrap._trust_permits(record, tenant_id="_default", corvin_home=self.home)
        finally:
            trust.evaluate = original  # type: ignore[assignment]
        self.assertFalse(ok)
        refused = [r for r in self.records("plugin.load_refused") if r["details"].get("plugin_id") == "untrusted-a4"]
        self.assertEqual(len(refused), 1)
        self.assertEqual(refused[0]["details"]["reason"], "trust_evaluation_failed")


# ── A5 ────────────────────────────────────────────────────────────────────────


class TestA5SoftRestartKeepsTheBootLayer(_Env):
    # One plugin id per test: the ADR-0233 D5 CROSS-epoch guard remembers a
    # privileged grant for the life of the process, so reusing an id after
    # advance_registration_epoch() is (correctly) downgraded.
    def tearDown(self):
        for pid in ("core-a5", "core-a5-failing", "compliance-a5"):
            try:
                registry.get_registry().unregister(pid)
            except Exception:  # noqa: BLE001
                pass
        super().tearDown()

    def test_restart_keeps_core_and_reloads_the_same_instance(self):
        registry.advance_registration_epoch()
        plugin = _Plugin("core-a5")
        ctx = bootstrap.build_context(plugin_id="core-a5", tenant_id="_default", corvin_home=self.home)
        registry.register(plugin, ctx, boot_layer=BootLayer.CORE)
        self.assertIs(registry.boot_layer_of("core-a5"), BootLayer.CORE)

        healer = healing.HealingOrchestrator(
            enabled=True, policies={"core-a5": healing.HealingPolicy.SOFT_RESTART}
        )
        rec = healer._soft_restart("core-a5", "unhealthy")
        self.assertTrue(rec.succeeded, rec)
        self.assertIs(registry.boot_layer_of("core-a5"), BootLayer.CORE, "demoted by the same-epoch guard")
        self.assertIs(registry.get("core-a5"), plugin)
        self.assertEqual(plugin.loads, 2)

    def test_restart_refuses_the_compliance_layer(self):
        registry.advance_registration_epoch()
        ctx = bootstrap.build_context(plugin_id="compliance-a5", tenant_id="_default", corvin_home=self.home)
        registry.register(_Plugin("compliance-a5"), ctx, boot_layer=BootLayer.COMPLIANCE)
        with self.assertRaises(registry.PluginDisableRefused):
            registry.restart("compliance-a5")
        self.assertIn("compliance-a5", registry.discover())

    def test_a_failed_restart_leaves_the_plugin_unregistered_and_guarded(self):
        registry.advance_registration_epoch()

        class FailsSecondLoad(_Plugin):
            def on_load(self, ctx):
                self.loads += 1
                if self.loads == 2:
                    raise RuntimeError("second load fails")

        plugin = FailsSecondLoad("core-a5-failing")
        ctx = bootstrap.build_context(plugin_id="core-a5-failing", tenant_id="_default", corvin_home=self.home)
        registry.register(plugin, ctx, boot_layer=BootLayer.CORE)
        with self.assertRaises(RuntimeError):
            registry.restart("core-a5-failing")
        self.assertNotIn("core-a5-failing", registry.discover())
        reg = registry.get_registry()
        self.assertEqual(reg._unregistered_this_epoch.get("core-a5-failing"), reg._registration_epoch)


# ── A6 ────────────────────────────────────────────────────────────────────────


class TestA6HealthMonitoringIsSharedBootWiring(_Env):
    def tearDown(self):
        asyncio.run(bootstrap.stop_health_monitoring())
        super().tearDown()

    def test_no_running_loop_means_no_collector_and_no_crash(self):
        self.assertIsNone(bootstrap.start_health_monitoring([]))
        self.assertIsNone(bootstrap.health_collector())

    def test_starts_under_a_loop_when_the_skill_says_enabled(self):
        try:
            from core.skills.skill_registry_phase1 import get_registry as _skills
        except ImportError:
            self.skipTest("core.skills absent")
        (self.home / "tenants" / "_default" / "global" / "features.json").write_text(
            json.dumps({"flags": {"plugin_health_monitoring": True, "plugin_self_healing": True}})
        )
        bootstrap._boot_skills_registry()
        if _skills().execute("os.plugin_health_monitoring", {"tenant_id": "_default"}).status != "success":
            self.skipTest("os.plugin_health_monitoring Skill not registered")

        async def run():
            collector = bootstrap.start_health_monitoring(["p1"])
            self.assertIsNotNone(collector, "the Skill said enabled — a collector must poll")
            self.assertTrue(collector.running)
            self.assertIsNotNone(collector._healer)
            self.assertTrue(collector._healer.is_enabled(), "gated on plugin_self_healing, read lazily")
            self.assertIs(bootstrap.health_collector(), collector)
            self.assertIs(bootstrap.start_health_monitoring(["p1"]), collector, "idempotent")
            await bootstrap.stop_health_monitoring()
            self.assertIsNone(bootstrap.health_collector())
            self.assertFalse(collector.running)

        asyncio.run(run())

    def test_shutdown_stops_a_collector_the_host_forgot(self):
        try:
            from core.skills.skill_registry_phase1 import get_registry as _skills
        except ImportError:
            self.skipTest("core.skills absent")
        (self.home / "tenants" / "_default" / "global" / "features.json").write_text(
            json.dumps({"flags": {"plugin_health_monitoring": True}})
        )
        bootstrap._boot_skills_registry()
        if _skills().execute("os.plugin_health_monitoring", {"tenant_id": "_default"}).status != "success":
            self.skipTest("os.plugin_health_monitoring Skill not registered")

        async def run():
            collector = bootstrap.start_health_monitoring([])
            self.assertIsNotNone(collector)
            bootstrap.shutdown([])
            self.assertIsNone(bootstrap.health_collector())
            await asyncio.sleep(0)
            self.assertFalse(collector.running)

        asyncio.run(run())


# ── A8 ────────────────────────────────────────────────────────────────────────


class TestA8InlineFallbackIsFailClosed(_Env):
    def test_absent_audit_module_refuses(self):
        saved = sys.modules.get("audit")
        sys.modules["audit"] = None  # type: ignore[assignment]
        try:
            with self.assertRaises(bootstrap.CoreAuditUnavailable):
                bootstrap._assert_core_audit_inline()
        finally:
            if saved is None:
                sys.modules.pop("audit", None)
            else:
                sys.modules["audit"] = saved


# ── A9 ────────────────────────────────────────────────────────────────────────


class TestA9SkippedBootstrapIsRecorded(_Env):
    def test_bootstrap_all_failure_is_in_the_chain(self):
        original = bootstrap.bootstrap_all

        def boom(**kw):
            raise RuntimeError("simulated bootstrap failure")

        bootstrap.bootstrap_all = boom  # type: ignore[assignment]
        try:
            with self.assertLogs("corvin.plugins.bootstrap", level="WARNING"):
                loaded = bootstrap.boot_platform()
        finally:
            bootstrap.bootstrap_all = original  # type: ignore[assignment]
            asyncio.run(bootstrap.stop_health_monitoring())
        self.assertEqual(loaded, [])
        skipped = self.records("plugin.bootstrap_skipped")
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["details"]["error_type"], "RuntimeError")
        self.assertEqual(skipped[0]["details"]["tenant_id"], "_default")


# ── A11 ───────────────────────────────────────────────────────────────────────


def test_no_test_puts_core_first_on_sys_path():
    """`<repo>/core` on sys.path makes `import audit` resolve to core/audit.

    Two perf tests did `sys.path.insert(0, <repo>/core)`; every test collected
    after them then imported the wrong `audit` module and 26 tests failed
    order-dependently. Package imports need no such entry. Runs LAST-ish by
    name; if this fails, find the test that added the entry, not this one.
    """
    core = str(_REPO / "core")
    offenders = [p for p in sys.path if p.rstrip("/") == core]
    assert not offenders, f"sys.path carries {core!r}: {offenders}"
    # Source-level guard too, so the entry is caught before it is executed:
    # a `sys.path.insert(0, ...)` whose argument ends in / "core").
    import re

    needle = re.compile(r'sys\.path\.insert\(\s*0\s*,\s*str\([^\n]*/\s*"core"\s*\)\s*\)')
    for f in sorted(_HERE.glob("test_*.py")):
        if f.name == Path(__file__).name:
            continue
        text = f.read_text(encoding="utf-8").replace("'", '"')
        assert not needle.search(text), f"{f.name} inserts <repo>/core on sys.path"

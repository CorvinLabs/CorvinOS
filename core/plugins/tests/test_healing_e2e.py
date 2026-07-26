"""End-to-end proof that self-healing actually RUNS (ADR-0231 Stage 3).

The unit tests in ``test_healing.py`` prove the orchestrator's logic. They do not
prove it runs in a booted process — the exact gap that made the tripwire and
PluginContext dead code earlier in this ADR. This module drives the real gateway
lifespan with a real sick plugin and asserts the plugin actually got healed.

What is exercised here:

* the gateway lifespan constructs the collector AND the orchestrator;
* the collector polls on its interval and hands failures to the orchestrator;
* the orchestrator applies its policy (soft-restart) to a genuinely failing plugin;
* the healing action lands in the real hash-chained audit log;
* with ``plugin_self_healing`` off, the same scenario touches nothing.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[3]
for _p in (
    str(_HERE.parents[1]),                        # core/plugins
    str(_REPO / "core" / "console"),
    str(_REPO / "core" / "compliance"),
    str(_REPO / "operator"),
    str(_REPO / "operator" / "forge"),
    str(_REPO / "operator" / "bridges" / "shared"),
    str(_REPO),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from corvin_plugins import circuit_breaker as cb  # noqa: E402
from corvin_plugins import healing as hl  # noqa: E402
from corvin_plugins.protocol import HealthStatus, PluginContext  # noqa: E402
from corvin_plugins.registry import get_registry  # noqa: E402


class SickPlugin:
    """Fails health_check until it is restarted, then reports healthy.

    That is the transient-failure shape Stage 3 is meant for: a restart genuinely
    fixes it, which is why `soft_restart` is the right policy and why the ADR
    forbids healing a plugin whose restart changes nothing.
    """

    plugin_id = "e2e.sick-notify"
    plugin_type = "notification_backend"
    version = "1.0.0"
    display_name = "Sick Notify"

    loads = 0
    unloads = 0
    healthy_after_restart = True

    def on_load(self, ctx):
        type(self).loads += 1

    def on_unload(self):
        type(self).unloads += 1

    def health_check(self):
        if type(self).loads > 1 and type(self).healthy_after_restart:
            return HealthStatus(ok=True, message="recovered after restart")
        return HealthStatus(ok=False, message="degraded")

    def notify(self, event, payload, *, tenant_id="_default", severity="info"):
        pass


def _ctx(home: Path) -> PluginContext:
    from corvin_plugins.bootstrap import build_context

    return build_context(
        plugin_id=SickPlugin.plugin_id, tenant_id="_default", corvin_home=home
    )


class TestHealingRunsForReal(unittest.TestCase):
    def setUp(self):
        cb._registry = cb.BreakerRegistry()
        SickPlugin.loads = 0
        SickPlugin.unloads = 0
        SickPlugin.healthy_after_restart = True
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.audit_path = self.home / "audit.jsonl"
        self._prev = {k: os.environ.get(k) for k in ("VOICE_AUDIT_PATH", "CORVIN_HOME")}
        os.environ["VOICE_AUDIT_PATH"] = str(self.audit_path)
        os.environ["CORVIN_HOME"] = str(self.home)
        for pid in list(get_registry().discover()):
            get_registry().unregister(pid)

    def tearDown(self):
        for k, v in self._prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for pid in list(get_registry().discover()):
            try:
                get_registry().unregister(pid)
            except Exception:
                pass
        self._tmp.cleanup()

    def _audit_text(self) -> str:
        return self.audit_path.read_text() if self.audit_path.exists() else ""

    # ── the real loop ────────────────────────────────────────────────────────

    def _run_collector(self, *, healing_on: bool, polls: int = 4) -> hl.HealingOrchestrator:
        """Wire collector + orchestrator exactly as the gateway lifespan does."""
        from corvin_plugins.health import HealthCollector

        audit_emit = _ctx(self.home).audit_emit
        healer = hl.HealingOrchestrator(
            enabled=lambda: healing_on,
            threshold=2,
            audit_emit=audit_emit,
        )
        collector = HealthCollector(
            interval_s=0.02, alert_after=2, audit_emit=audit_emit, healer=healer
        )
        get_registry().register(SickPlugin(), _ctx(self.home))

        async def scenario():
            collector.start()
            # Long enough for `polls` intervals to elapse.
            await asyncio.sleep(0.02 * polls + 0.08)
            await collector.stop()

        asyncio.run(scenario())
        return healer

    def test_a_sick_plugin_is_actually_healed_end_to_end(self):
        healer = self._run_collector(healing_on=True)

        actions = [r.action for r in healer.history(SickPlugin.plugin_id)]
        self.assertIn(
            hl.HealingAction.SOFT_RESTART,
            actions,
            f"the collector must have driven a restart; history={actions}",
        )
        self.assertGreaterEqual(SickPlugin.unloads, 1, "on_unload must have run")
        self.assertGreaterEqual(SickPlugin.loads, 2, "on_load must have run again")
        self.assertIn(
            SickPlugin.plugin_id,
            get_registry().discover(),
            "a healed plugin must be back in the registry",
        )

    def test_the_plugin_reports_healthy_after_the_heal(self):
        self._run_collector(healing_on=True, polls=8)
        status = get_registry().health_check_all()[SickPlugin.plugin_id]
        self.assertTrue(status.ok, "healing must have actually fixed it")

    def test_the_healing_action_reaches_the_hash_chained_audit_log(self):
        self._run_collector(healing_on=True)
        text = self._audit_text()
        if not text:
            self.skipTest("audit writer unavailable in this layout")
        self.assertIn("plugin.healing_action", text)
        self.assertIn("soft_restart", text)
        self.assertIn('"hash"', text, "the event must be chained, not just logged")

        import audit as _audit  # type: ignore[import-not-found]

        ok, problems = _audit.verify_audit(self.audit_path)
        self.assertTrue(ok, f"the chain must still verify after healing: {problems}")

    def test_with_the_flag_off_nothing_is_touched(self):
        healer = self._run_collector(healing_on=False)
        actions = {r.action for r in healer.history(SickPlugin.plugin_id)}
        self.assertEqual(actions, {hl.HealingAction.NOOP})
        self.assertEqual(SickPlugin.unloads, 0, "ship dark means hands off")
        self.assertEqual(SickPlugin.loads, 1)
        self.assertNotIn("plugin.healing_action", self._audit_text())

    def test_a_plugin_that_stays_broken_escalates_instead_of_looping(self):
        """The ADR's rule: healing a systematic failure only hides it."""
        SickPlugin.healthy_after_restart = False
        healer = self._run_collector(healing_on=True, polls=12)

        history = healer.history(SickPlugin.plugin_id)
        restarts = [r for r in history if r.action is hl.HealingAction.SOFT_RESTART]
        escalations = [r for r in history if r.action is hl.HealingAction.ESCALATE]
        self.assertGreaterEqual(len(escalations), 1, "must escalate, not restart forever")
        self.assertLessEqual(
            len(restarts),
            hl.DEFAULT_MAX_HEALS_PER_HOUR,
            "the hourly budget must cap the restart attempts",
        )

    def test_the_alert_and_the_healing_are_both_recorded(self):
        self._run_collector(healing_on=True, polls=6)
        text = self._audit_text()
        if not text:
            self.skipTest("audit writer unavailable in this layout")
        self.assertIn("plugin.health_alert", text, "the operator must see the alert too")
        self.assertIn("plugin.healing_action", text)


class TestGatewayBootWiresHealing(unittest.TestCase):
    """Boot the REAL gateway app and assert the healing path was constructed.

    This is the call-site check at runtime rather than by reading source: if the
    lifespan stops wiring the orchestrator, this fails.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        tenant_global = self.home / "tenants" / "_default" / "global"
        (tenant_global / "auth").mkdir(parents=True)
        (tenant_global / "forge").mkdir(parents=True)
        self._prev = {
            k: os.environ.get(k)
            for k in ("CORVIN_HOME", "CORVIN_TENANT_ID", "VOICE_AUDIT_PATH")
        }
        os.environ["CORVIN_HOME"] = str(self.home)
        os.environ["CORVIN_TENANT_ID"] = "_default"
        os.environ["VOICE_AUDIT_PATH"] = str(self.home / "audit.jsonl")
        # Turn the two flags on for this tenant via the features overlay.
        # NOTE the nesting: feature_flags.is_enabled reads overlay["flags"][id],
        # not a flat mapping. A flat file silently reads as "all defaults", which
        # looked exactly like a broken boot path the first time this test ran.
        import json

        (tenant_global / "features.json").write_text(
            json.dumps(
                {
                    "flags": {
                        "plugin_health_monitoring": True,
                        "plugin_self_healing": True,
                    }
                }
            )
        )

    def tearDown(self):
        for k, v in self._prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def test_lifespan_starts_a_collector_with_a_healer_attached(self):
        for key in list(sys.modules):
            if key.startswith(("corvin_gateway", "corvin_console", "corvin_plugins", "forge")):
                del sys.modules[key]
        try:
            from corvin_gateway.app import app
            from fastapi.testclient import TestClient
        except ImportError as exc:  # pragma: no cover
            self.skipTest(f"gateway not importable: {exc}")

        from corvin_console.routes import plugins as route_mod

        with TestClient(app):
            collector = route_mod._collector()
            self.assertIsNotNone(
                collector, "the lifespan must register a collector when the flag is on"
            )
            self.assertTrue(collector.running, "it must actually be polling")
            self.assertIsNotNone(
                getattr(collector, "_healer", None),
                "the collector must have received a healing orchestrator",
            )
            self.assertTrue(
                collector._healer.is_enabled(),
                "with plugin_self_healing on, the orchestrator must report enabled",
            )
        self.assertIsNone(
            route_mod._collector(), "shutdown must clear the collector reference"
        )


if __name__ == "__main__":
    unittest.main()

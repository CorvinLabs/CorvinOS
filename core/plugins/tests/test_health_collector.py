"""Tests for the plugin health collector + metrics (ADR-0231 Stage 2)."""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[3]
_PKG = _HERE.parents[1]
for _p in (str(_PKG), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from corvin_plugins import circuit_breaker as cb  # noqa: E402
from corvin_plugins import health as hm  # noqa: E402
from corvin_plugins.protocol import HealthStatus, PluginContext  # noqa: E402
from corvin_plugins.registry import get_registry  # noqa: E402


class _Plugin:
    plugin_type = "notification_backend"
    version = "1.0.0"

    def __init__(self, pid: str, ok: bool = True, raises: bool = False):
        self.plugin_id = pid
        self.display_name = pid
        self._ok = ok
        self._raises = raises
        self.calls = 0

    def set_ok(self, ok: bool) -> None:
        self._ok = ok

    def on_load(self, ctx):
        pass

    def on_unload(self):
        pass

    def health_check(self):
        self.calls += 1
        if self._raises:
            raise RuntimeError("health explodes for jdoe at /home/secret")
        return HealthStatus(ok=self._ok, message="ok" if self._ok else "degraded")

    def notify(self, event, payload, *, tenant_id="_default", severity="info"):
        pass


def _ctx(pid: str) -> PluginContext:
    return PluginContext(
        plugin_id=pid,
        tenant_id="_default",
        corvin_home=Path("/tmp"),
        config={},
        audit_emit=lambda *a, **k: None,
    )


class _Base(unittest.TestCase):
    def setUp(self):
        cb._registry = cb.BreakerRegistry()
        self.registry = get_registry()
        for pid in list(self.registry.discover()):
            self.registry.unregister(pid)
        self.events: list[tuple[str, dict]] = []
        self.collector = hm.HealthCollector(
            interval_s=0.05,
            alert_after=2,
            audit_emit=lambda et, d: self.events.append((et, d)),
        )

    def tearDown(self):
        for pid in list(self.registry.discover()):
            try:
                self.registry.unregister(pid)
            except Exception:
                pass


class TestPollOnce(_Base):
    def test_empty_registry_yields_an_empty_snapshot(self):
        snap = self.collector.poll_once()
        self.assertEqual(snap.samples, {})
        self.assertEqual(snap.unhealthy(), [])

    def test_healthy_plugin_is_sampled(self):
        self.registry.register(_Plugin("p.ok"), _ctx("p.ok"))
        snap = self.collector.poll_once()
        self.assertTrue(snap.samples["p.ok"].ok)
        self.assertEqual(snap.samples["p.ok"].consecutive_failures, 0)
        self.assertEqual(snap.unhealthy(), [])

    def test_unhealthy_plugin_accumulates_a_streak(self):
        self.registry.register(_Plugin("p.bad", ok=False), _ctx("p.bad"))
        self.collector.poll_once()
        snap = self.collector.poll_once()
        self.assertEqual(snap.samples["p.bad"].consecutive_failures, 2)
        self.assertEqual(snap.unhealthy(), ["p.bad"])

    def test_recovery_resets_the_streak(self):
        plugin = _Plugin("p.flappy", ok=False)
        self.registry.register(plugin, _ctx("p.flappy"))
        self.collector.poll_once()
        plugin.set_ok(True)
        cb.get_breaker("p.flappy").reset()
        snap = self.collector.poll_once()
        self.assertEqual(snap.samples["p.flappy"].consecutive_failures, 0)

    def test_a_raising_health_check_never_leaks_its_message(self):
        self.registry.register(_Plugin("p.raise", raises=True), _ctx("p.raise"))
        with self.assertLogs("corvin_plugins.registry", level="WARNING"):
            snap = self.collector.poll_once()
        blob = repr(snap.to_dict())
        self.assertIn("RuntimeError", blob)
        self.assertNotIn("jdoe", blob)
        self.assertNotIn("/home/secret", blob)

    def test_poll_survives_a_broken_registry(self):
        original = hm.get_registry
        try:
            hm.get_registry = lambda: (_ for _ in ()).throw(RuntimeError("registry gone"))
            with self.assertLogs("corvin.plugins.health", level="ERROR"):
                snap = self.collector.poll_once()
            self.assertIsInstance(snap, hm.HealthSnapshot)
        finally:
            hm.get_registry = original

    def test_a_vanished_plugin_stops_counting(self):
        self.registry.register(_Plugin("p.gone", ok=False), _ctx("p.gone"))
        self.collector.poll_once()
        self.registry.unregister("p.gone")
        snap = self.collector.poll_once()
        self.assertNotIn("p.gone", snap.samples)


class TestAlerting(_Base):
    def test_alert_fires_once_per_streak(self):
        self.registry.register(_Plugin("p.bad", ok=False), _ctx("p.bad"))
        for _ in range(4):
            self.collector.poll_once()
        alerts = [e for e in self.events if e[0] == "plugin.health_alert"]
        self.assertEqual(len(alerts), 1, "one alert per streak, not per poll")
        self.assertEqual(alerts[0][1]["plugin_id"], "p.bad")
        self.assertGreaterEqual(alerts[0][1]["consecutive_failures"], 2)

    def test_no_alert_below_the_threshold(self):
        self.registry.register(_Plugin("p.bad", ok=False), _ctx("p.bad"))
        self.collector.poll_once()  # streak = 1, threshold = 2
        self.assertEqual([e for e in self.events if e[0] == "plugin.health_alert"], [])

    def test_recovery_emits_its_own_event(self):
        plugin = _Plugin("p.flappy", ok=False)
        self.registry.register(plugin, _ctx("p.flappy"))
        for _ in range(3):
            self.collector.poll_once()
        plugin.set_ok(True)
        cb.get_breaker("p.flappy").reset()
        self.collector.poll_once()
        kinds = [e[0] for e in self.events]
        self.assertIn("plugin.health_alert", kinds)
        self.assertIn("plugin.health_recovered", kinds)

    def test_alert_details_carry_no_message_text(self):
        self.registry.register(_Plugin("p.raise", raises=True), _ctx("p.raise"))
        with self.assertLogs("corvin_plugins.registry", level="WARNING"):
            for _ in range(3):
                self.collector.poll_once()
        alerts = [e for e in self.events if e[0] == "plugin.health_alert"]
        self.assertTrue(alerts)
        self.assertNotIn("jdoe", repr(alerts))

    def test_a_raising_audit_sink_does_not_break_the_poll(self):
        def boom(event_type, details):
            raise RuntimeError("audit down")

        collector = hm.HealthCollector(interval_s=0.05, alert_after=1, audit_emit=boom)
        self.registry.register(_Plugin("p.bad", ok=False), _ctx("p.bad"))
        with self.assertLogs("corvin.plugins.health", level="ERROR"):
            snap = collector.poll_once()
        self.assertIn("p.bad", snap.samples)


class TestCollectorLifecycle(_Base):
    def test_start_stop_and_idempotence(self):
        async def scenario():
            first = self.collector.start()
            second = self.collector.start()
            self.assertIs(first, second, "start() must be idempotent")
            self.assertTrue(self.collector.running)
            await asyncio.sleep(0.12)
            await self.collector.stop()
            self.assertFalse(self.collector.running)

        self.registry.register(_Plugin("p.ok"), _ctx("p.ok"))
        asyncio.run(scenario())
        self.assertGreaterEqual(self.collector.snapshot().samples["p.ok"].ok, True)

    def test_polling_actually_calls_the_plugin_repeatedly(self):
        plugin = _Plugin("p.ok")
        self.registry.register(plugin, _ctx("p.ok"))

        async def scenario():
            self.collector.start()
            await asyncio.sleep(0.16)
            await self.collector.stop()

        asyncio.run(scenario())
        self.assertGreaterEqual(plugin.calls, 2, "the interval must actually fire")

    def test_nothing_polls_before_start(self):
        plugin = _Plugin("p.ok")
        self.registry.register(plugin, _ctx("p.ok"))

        async def scenario():
            await asyncio.sleep(0.12)

        asyncio.run(scenario())
        self.assertEqual(plugin.calls, 0, "flag-off means no timer at all")

    def test_interval_must_be_positive(self):
        with self.assertRaises(ValueError):
            hm.HealthCollector(interval_s=0)


class TestPrometheusRender(_Base):
    def test_empty_render_has_zero_samples_not_no_data(self):
        body = hm.render_prometheus()
        self.assertIn("corvin_plugin_health_ok", body)
        self.assertIn('plugin_id="none"', body)
        self.assertTrue(body.endswith("\n"))

    def test_every_family_is_declared_with_help_and_type(self):
        body = hm.render_prometheus(self.collector.poll_once())
        for name, mtype, _help in hm._FAMILIES:
            self.assertIn(f"# HELP {name} ", body)
            self.assertIn(f"# TYPE {name} {mtype}", body)

    def test_healthy_and_unhealthy_render_as_one_and_zero(self):
        self.registry.register(_Plugin("p.ok"), _ctx("p.ok"))
        self.registry.register(_Plugin("p.bad", ok=False), _ctx("p.bad"))
        body = hm.render_prometheus(self.collector.poll_once())
        self.assertIn('corvin_plugin_health_ok{plugin_id="p.ok"} 1', body)
        self.assertIn('corvin_plugin_health_ok{plugin_id="p.bad"} 0', body)

    def test_breaker_numbers_are_present_without_any_poll(self):
        """Breakers run regardless of the monitoring flag, so they always render."""
        cb.get_breaker("p.solo").record_failure(RuntimeError())
        body = hm.render_prometheus()
        self.assertIn('corvin_plugin_breaker_failures_total{plugin_id="p.solo"} 1', body)

    def test_open_breaker_renders_as_one(self):
        breaker = cb.get_breaker("p.open", failure_threshold=1)
        breaker.record_failure(RuntimeError())
        body = hm.render_prometheus()
        self.assertIn('corvin_plugin_breaker_open{plugin_id="p.open"} 1', body)

    def test_label_cardinality_is_capped(self):
        for i in range(hm.MAX_LABELLED_PLUGINS + 5):
            cb.get_breaker(f"p{i:03d}")
        body = hm.render_prometheus()
        self.assertIn('plugin_id="other"', body, "ids past the cap must collapse")

    def test_label_values_are_escaped(self):
        cb.get_breaker('weird"id')
        body = hm.render_prometheus()
        self.assertNotIn('plugin_id="weird"id"', body)

    def test_no_message_text_reaches_the_metrics(self):
        self.registry.register(_Plugin("p.raise", raises=True), _ctx("p.raise"))
        with self.assertLogs("corvin_plugins.registry", level="WARNING"):
            snap = self.collector.poll_once()
        body = hm.render_prometheus(snap)
        self.assertNotIn("jdoe", body)
        self.assertNotIn("secret", body)


if __name__ == "__main__":
    unittest.main()

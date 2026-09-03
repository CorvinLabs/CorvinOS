"""Tests for the self-healing orchestrator (ADR-0231 Stage 3, ships dark).

The ADR's constraints are what these tests pin: reversible actions only, a
per-plugin policy, a bounded action budget, no healing of a systematic failure,
and an audit record for every action. Plus the gate itself — with the flag off,
nothing happens at all.
"""
from __future__ import annotations

import sys
import time
import threading
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[3]
_PKG = _HERE.parents[1]
for _p in (str(_PKG), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from corvin_plugins import circuit_breaker as cb  # noqa: E402
from corvin_plugins import healing as hl  # noqa: E402
from corvin_plugins.protocol import HealthStatus, PluginContext  # noqa: E402
from corvin_plugins.registry import get_registry  # noqa: E402


class _Plugin:
    def __init__(self, pid: str, ptype: str = "notification_backend", fail_load=False):
        self.plugin_id = pid
        self.plugin_type = ptype
        self.version = "1.0.0"
        self.display_name = pid
        self.loads = 0
        self.unloads = 0
        self._fail_load = fail_load

    def on_load(self, ctx):
        self.loads += 1
        if self._fail_load and self.loads > 1:
            raise RuntimeError("refuses to come back")

    def on_unload(self):
        self.unloads += 1

    def health_check(self):
        return HealthStatus(ok=True)

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
        self.audit: list[tuple[str, dict]] = []
        self.healer = hl.HealingOrchestrator(
            enabled=True,
            threshold=3,
            max_heals_per_hour=3,
            audit_emit=lambda et, d: self.audit.append((et, d)),
        )

    def tearDown(self):
        for pid in list(self.registry.discover()):
            try:
                self.registry.unregister(pid)
            except Exception:
                pass

    def _register(self, plugin: _Plugin) -> _Plugin:
        self.registry.register(plugin, _ctx(plugin.plugin_id))
        return plugin


# ── The gate ──────────────────────────────────────────────────────────────────


class TestShipsDark(_Base):
    def test_disabled_healer_does_nothing(self):
        healer = hl.HealingOrchestrator(enabled=False)
        plugin = self._register(_Plugin("p.sick"))
        rec = healer.consider("p.sick", plugin_type=plugin.plugin_type, consecutive_failures=99)
        self.assertIs(rec.action, hl.HealingAction.NOOP)
        self.assertEqual(rec.reason, "healing_disabled")
        self.assertEqual(plugin.unloads, 0, "nothing may be touched while off")
        self.assertIn("p.sick", self.registry.discover())

    def test_flag_is_read_per_call(self):
        state = {"on": False}
        healer = hl.HealingOrchestrator(enabled=lambda: state["on"], threshold=1)
        self._register(_Plugin("p.sick"))
        self.assertEqual(
            healer.consider("p.sick", plugin_type="notification_backend",
                            consecutive_failures=5).reason,
            "healing_disabled",
        )
        state["on"] = True
        rec = healer.consider(
            "p.sick", plugin_type="notification_backend", consecutive_failures=5
        )
        self.assertIsNot(rec.action, hl.HealingAction.NOOP)

    def test_below_threshold_is_a_noop_with_a_reason(self):
        self._register(_Plugin("p.sick"))
        rec = self.healer.consider(
            "p.sick", plugin_type="notification_backend", consecutive_failures=2
        )
        self.assertIs(rec.action, hl.HealingAction.NOOP)
        self.assertEqual(rec.reason, "below_threshold")


# ── Policy ────────────────────────────────────────────────────────────────────


class TestPolicy(_Base):
    def test_precious_types_default_to_containment_only(self):
        for ptype in ("audit_backend", "user_backend", "compute_engine", "recall_backend"):
            self.assertIs(
                self.healer.policy_for("x", ptype),
                hl.HealingPolicy.CIRCUIT_BREAK_ONLY,
                f"{ptype} must never be restarted autonomously",
            )

    def test_degradable_types_default_to_disable(self):
        for ptype in ("router_backend", "worker_engine"):
            self.assertIs(
                self.healer.policy_for("x", ptype), hl.HealingPolicy.DISABLE_AND_DEGRADE
            )

    def test_restartable_types_default_to_soft_restart(self):
        for ptype in ("stt_provider", "summary_provider", "notification_backend"):
            self.assertIs(self.healer.policy_for("x", ptype), hl.HealingPolicy.SOFT_RESTART)

    def test_unknown_type_falls_back_to_containment(self):
        self.assertIs(
            self.healer.policy_for("x", "something_new"),
            hl.HealingPolicy.CIRCUIT_BREAK_ONLY,
            "an unclassified plugin gets the safest policy, not the most permissive",
        )

    def test_per_plugin_override_wins(self):
        self.healer.set_policy("p.special", hl.HealingPolicy.NONE)
        self.assertIs(self.healer.policy_for("p.special", "stt_provider"), hl.HealingPolicy.NONE)

    def test_policy_none_opts_out_entirely(self):
        plugin = self._register(_Plugin("p.optout", "stt_provider"))
        self.healer.set_policy("p.optout", hl.HealingPolicy.NONE)
        rec = self.healer.consider(
            "p.optout", plugin_type="stt_provider", consecutive_failures=9
        )
        self.assertEqual(rec.reason, "policy_none")
        self.assertEqual(plugin.unloads, 0)


# ── The three actions ─────────────────────────────────────────────────────────


class TestActions(_Base):
    def test_circuit_break_only_never_unloads(self):
        plugin = self._register(_Plugin("p.audit", "audit_backend"))
        rec = self.healer.consider(
            "p.audit", plugin_type="audit_backend", consecutive_failures=5
        )
        self.assertIs(rec.action, hl.HealingAction.CIRCUIT_BREAK)
        self.assertEqual(plugin.unloads, 0, "audit must not be restarted")
        self.assertIn("p.audit", self.registry.discover())

    def test_soft_restart_unloads_and_reloads_the_same_instance(self):
        plugin = self._register(_Plugin("p.stt", "stt_provider"))
        rec = self.healer.consider(
            "p.stt", plugin_type="stt_provider", consecutive_failures=3
        )
        self.assertIs(rec.action, hl.HealingAction.SOFT_RESTART)
        self.assertTrue(rec.succeeded)
        self.assertEqual(plugin.unloads, 1)
        self.assertEqual(plugin.loads, 2, "same instance, loaded again")
        self.assertIn("p.stt", self.registry.discover())

    def test_soft_restart_resets_the_breaker(self):
        self._register(_Plugin("p.stt", "stt_provider"))
        cb.get_breaker("p.stt").record_failure(RuntimeError())
        self.healer.consider("p.stt", plugin_type="stt_provider", consecutive_failures=3)
        self.assertEqual(cb.snapshot()["p.stt"]["state"], "closed")

    def test_a_failed_restart_leaves_the_plugin_unregistered_not_half_alive(self):
        plugin = self._register(_Plugin("p.stubborn", "stt_provider", fail_load=True))
        rec = self.healer.consider(
            "p.stubborn", plugin_type="stt_provider", consecutive_failures=3
        )
        self.assertIs(rec.action, hl.HealingAction.SOFT_RESTART)
        self.assertFalse(rec.succeeded)
        self.assertNotIn("p.stubborn", self.registry.discover())
        self.assertEqual(plugin.unloads, 1)

    def test_disable_and_degrade_unregisters(self):
        plugin = self._register(_Plugin("p.router", "router_backend"))
        rec = self.healer.consider(
            "p.router", plugin_type="router_backend", consecutive_failures=3
        )
        self.assertIs(rec.action, hl.HealingAction.DISABLE)
        self.assertNotIn("p.router", self.registry.discover())
        self.assertEqual(plugin.unloads, 1)

    def test_healing_an_unregistered_plugin_is_a_noop(self):
        rec = self.healer.consider(
            "p.ghost", plugin_type="stt_provider", consecutive_failures=5
        )
        self.assertIs(rec.action, hl.HealingAction.NOOP)
        self.assertEqual(rec.reason, "not_registered")

    def test_no_action_mutates_the_registry_file(self):
        """An autonomous action must not rewrite the operator's configuration."""
        import inspect

        source = inspect.getsource(hl)
        for forbidden in ("reg.save()", "TenantRegistry", "registry.yaml", "rmtree", "unlink"):
            self.assertNotIn(
                forbidden, source, f"healing must not touch {forbidden}"
            )

    def test_no_hard_kill_primitives_anywhere(self):
        import inspect

        source = inspect.getsource(hl)
        for forbidden in ("os.kill", "SIGKILL", "terminate()", "_thread.interrupt"):
            self.assertNotIn(forbidden, source, "ADR-0231: never hard-kill")


# ── Bounds and escalation ─────────────────────────────────────────────────────


class TestBounds(_Base):
    def test_budget_caps_actions_per_hour(self):
        self._register(_Plugin("p.stt", "stt_provider"))
        actions = []
        for _ in range(5):
            # Re-register between attempts so a restart is possible each time.
            if "p.stt" not in self.registry.discover():
                self._register(_Plugin("p.stt", "stt_provider"))
            self.healer._last_restart.pop("p.stt", None)  # ignore the grace window here
            actions.append(
                self.healer.consider(
                    "p.stt", plugin_type="stt_provider", consecutive_failures=3
                ).action
            )
        self.assertEqual(
            sum(1 for a in actions if a is hl.HealingAction.SOFT_RESTART),
            3,
            "max_heals_per_hour must cap the restarts",
        )
        self.assertIn(hl.HealingAction.ESCALATE, actions)

    def test_budget_exhaustion_escalates_with_a_reason(self):
        self._register(_Plugin("p.audit", "audit_backend"))
        for _ in range(3):
            self.healer.consider(
                "p.audit", plugin_type="audit_backend", consecutive_failures=5
            )
        rec = self.healer.consider(
            "p.audit", plugin_type="audit_backend", consecutive_failures=5
        )
        self.assertIs(rec.action, hl.HealingAction.ESCALATE)
        self.assertEqual(rec.reason, "heal_budget_exhausted")

    def test_a_failure_right_after_a_restart_escalates(self):
        """A restart that did not help means the fault is systematic, not transient."""
        self._register(_Plugin("p.stt", "stt_provider"))
        first = self.healer.consider(
            "p.stt", plugin_type="stt_provider", consecutive_failures=3
        )
        self.assertIs(first.action, hl.HealingAction.SOFT_RESTART)
        second = self.healer.consider(
            "p.stt", plugin_type="stt_provider", consecutive_failures=4
        )
        self.assertIs(second.action, hl.HealingAction.ESCALATE)
        self.assertEqual(second.reason, "restart_did_not_help")

    def test_budget_window_is_per_plugin(self):
        self._register(_Plugin("p.a", "audit_backend"))
        self._register(_Plugin("p.b", "audit_backend"))
        for _ in range(3):
            self.healer.consider("p.a", plugin_type="audit_backend", consecutive_failures=5)
        rec = self.healer.consider("p.b", plugin_type="audit_backend", consecutive_failures=5)
        self.assertIs(rec.action, hl.HealingAction.CIRCUIT_BREAK, "p.b has its own budget")

    def test_old_actions_fall_out_of_the_window(self):
        self._register(_Plugin("p.audit", "audit_backend"))
        for _ in range(3):
            self.healer.consider("p.audit", plugin_type="audit_backend", consecutive_failures=5)
        # Backdate the ACTION LEDGER beyond the hour (the budget reads that, not
        # the pruned diagnostic history).
        self.healer._actions["p.audit"] = [
            time.time() - 4000 for _ in self.healer._actions["p.audit"]
        ]
        self.assertEqual(self.healer.budget_left("p.audit"), 3)


# ── Audit ─────────────────────────────────────────────────────────────────────


class TestAudit(_Base):
    def test_every_action_is_audited(self):
        self._register(_Plugin("p.stt", "stt_provider"))
        self.healer.consider("p.stt", plugin_type="stt_provider", consecutive_failures=3)
        events = [e for e in self.audit if e[0] == "plugin.healing_action"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][1]["plugin_id"], "p.stt")
        self.assertEqual(events[0][1]["healing_action"], "soft_restart")

    def test_noops_are_not_audited(self):
        self._register(_Plugin("p.stt", "stt_provider"))
        self.healer.consider("p.stt", plugin_type="stt_provider", consecutive_failures=1)
        self.assertEqual(self.audit, [], "a non-action is not an audit event")

    def test_history_records_noops_for_diagnosis(self):
        self._register(_Plugin("p.stt", "stt_provider"))
        self.healer.consider("p.stt", plugin_type="stt_provider", consecutive_failures=1)
        reasons = [r.reason for r in self.healer.history("p.stt")]
        self.assertIn("below_threshold", reasons)

    def test_a_raising_audit_sink_does_not_break_healing(self):
        def boom(event_type, details):
            raise RuntimeError("audit down")

        healer = hl.HealingOrchestrator(enabled=True, threshold=1, audit_emit=boom)
        plugin = self._register(_Plugin("p.stt", "stt_provider"))
        with self.assertLogs("corvin.plugins.healing", level="ERROR"):
            rec = healer.consider(
                "p.stt", plugin_type="stt_provider", consecutive_failures=3
            )
        self.assertIs(rec.action, hl.HealingAction.SOFT_RESTART)
        self.assertEqual(plugin.loads, 2)


# ── Collector integration ─────────────────────────────────────────────────────


class TestCollectorDrivesHealing(_Base):
    def test_collector_hands_unhealthy_plugins_to_the_healer(self):
        from corvin_plugins import health as hm

        class _Unhealthy(_Plugin):
            def health_check(self):
                return HealthStatus(ok=False, message="degraded")

        collector = hm.HealthCollector(interval_s=0.05, alert_after=99, healer=self.healer)
        self._register(_Unhealthy("p.stt", "stt_provider"))
        for _ in range(3):
            if "p.stt" not in self.registry.discover():
                self._register(_Unhealthy("p.stt", "stt_provider"))
            collector.poll_once()
        actions = [r.action for r in self.healer.history("p.stt")]
        self.assertIn(hl.HealingAction.SOFT_RESTART, actions)

    def test_collector_without_a_healer_only_reports(self):
        from corvin_plugins import health as hm

        class _Unhealthy(_Plugin):
            def health_check(self):
                return HealthStatus(ok=False, message="degraded")

        collector = hm.HealthCollector(interval_s=0.05, alert_after=99)
        plugin = self._register(_Unhealthy("p.stt", "stt_provider"))
        for _ in range(4):
            collector.poll_once()
        self.assertEqual(plugin.unloads, 0, "no healer means no action")

    def test_healing_failure_never_breaks_the_poll(self):
        from corvin_plugins import health as hm

        class _BrokenHealer:
            def consider(self, *a, **k):
                raise RuntimeError("healer exploded")

        collector = hm.HealthCollector(
            interval_s=0.05, alert_after=99, healer=_BrokenHealer()
        )

        class _Unhealthy(_Plugin):
            def health_check(self):
                return HealthStatus(ok=False, message="degraded")

        self._register(_Unhealthy("p.stt", "stt_provider"))
        with self.assertLogs("corvin.plugins.health", level="ERROR"):
            snap = collector.poll_once()
        self.assertIn("p.stt", snap.samples)


if __name__ == "__main__":
    unittest.main()


class TestBootWiring(unittest.TestCase):
    """The lesson from ADR-0233's own review: a mechanism needs a CALL SITE test.

    Both the tripwire and PluginContext were once complete, tested and unreachable.
    These assertions read the boot path's source, so a future refactor that drops
    the wiring fails here instead of silently disabling the feature.
    """

    # Since 2026-09-03 (finding A6) the collector + healer are wired in the ONE
    # shared boot sequence, corvin_plugins.bootstrap.start_health_monitoring(),
    # called by boot_platform() — so the standalone console gets them too. The
    # gateway lifespan only holds the handle. These read THAT source.

    def _boot_source(self) -> str:
        boot = _REPO / "core" / "plugins" / "corvin_plugins" / "bootstrap.py"
        return boot.read_text(encoding="utf-8")

    def _lifespan_source(self) -> str:
        app = _REPO / "core" / "gateway" / "corvin_gateway" / "app.py"
        return app.read_text(encoding="utf-8")

    def test_the_orchestrator_is_constructed_at_boot(self):
        source = self._boot_source()
        self.assertIn("HealingOrchestrator", source)
        self.assertIn("healer=healer", source, "the collector must receive it")

    def test_boot_platform_starts_monitoring_and_the_gateway_has_no_inline_copy(self):
        boot = self._boot_source()
        # boot_platform() itself calls the helper — not only defines it.
        body = boot.split("def boot_platform(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("start_health_monitoring(loaded)", body)
        app = self._lifespan_source()
        self.assertNotIn("HealthCollector(", app, "the gateway must not keep an inline copy")
        self.assertNotIn("HealingOrchestrator", app, "the gateway must not keep an inline copy")
        self.assertIn("stop_health_monitoring", app, "the gateway must stop it before unloading")

    def test_healing_is_gated_on_its_own_flag(self):
        source = self._boot_source()
        self.assertIn('plugin_self_healing', source)
        # The flag must be read lazily (a lambda), so toggling it in the Console
        # takes effect without a restart.
        self.assertIn("enabled=lambda:", source)

    def test_the_flag_ships_dark(self):
        sys.path.insert(0, str(_REPO / "core" / "console"))
        from corvin_console import feature_flags as ff

        flag = ff.flag("plugin_self_healing")
        self.assertFalse(flag.default, "Stage 3 must be off on a fresh install")
        self.assertTrue(flag.owner)
        self.assertTrue(flag.target_release)

    def test_collector_and_healer_share_one_audit_sink(self):
        source = self._boot_source()
        helper = source.split("def start_health_monitoring(", 1)[1].split("\ndef ", 1)[0]
        self.assertGreaterEqual(
            helper.count("audit_emit=audit_emit"), 2,
            "collector and healer must both audit through the real chain",
        )


class TestHistoryIsBounded(_Base):
    """Review finding: the history grew without bound on the NOOP path.

    Pruning only happened in budget_left(), which the NOOP returns never reach —
    and `healing_disabled` is the DEFAULT path, appended on every poll of every
    unhealthy plugin. In a process that runs for months that is a slow leak.
    """

    def test_noop_floods_are_capped(self):
        healer = hl.HealingOrchestrator(enabled=False)
        for _ in range(5000):
            healer.consider("p", plugin_type="stt_provider", consecutive_failures=9)
        self.assertLessEqual(
            len(healer._history["p"]),
            hl.MAX_HISTORY_PER_PLUGIN,
            "the diagnostic buffer must not grow without bound",
        )

    def test_the_recent_tail_is_what_survives(self):
        healer = hl.HealingOrchestrator(enabled=True, threshold=99)
        for _ in range(200):
            # Always below the threshold, so every record is the same NOOP kind and
            # the tail is predictable.
            healer.consider("p", plugin_type="stt_provider", consecutive_failures=1)
        history = healer.history("p")
        self.assertLessEqual(len(history), hl.MAX_HISTORY_PER_PLUGIN)
        self.assertEqual(history[-1].reason, "below_threshold")

    def test_pruning_cannot_hand_back_a_fresh_budget(self):
        """The interaction bug: enough NOOPs pushed counted actions out of the
        pruned history, so the hourly cap silently reset. The budget is now kept
        in its own unpruned ledger."""
        healer = hl.HealingOrchestrator(enabled=True, threshold=1, max_heals_per_hour=2)
        self._register(_Plugin("p.audit", "audit_backend"))
        for _ in range(300):
            healer.consider("p.audit", plugin_type="audit_backend", consecutive_failures=1)
        self.assertEqual(
            healer.budget_left("p.audit"), 0, "the cap must still be exhausted"
        )
        self.assertLessEqual(
            len(healer._actions["p.audit"]),
            2,
            "at most max_heals_per_hour actions may ever have run",
        )

    def test_escalation_is_not_repeated_on_every_poll(self):
        """An escalation per poll floods the audit chain with the same record."""
        healer = hl.HealingOrchestrator(
            enabled=True, threshold=1, max_heals_per_hour=1,
            audit_emit=lambda et, d: self.audit.append((et, d)),
        )
        self._register(_Plugin("p.audit", "audit_backend"))
        for _ in range(50):
            healer.consider("p.audit", plugin_type="audit_backend", consecutive_failures=1)
        escalations = [
            r for r in healer.history("p.audit") if r.action is hl.HealingAction.ESCALATE
        ]
        self.assertEqual(len(escalations), 1, "escalate once, then stay quiet")
        audited = [e for e in self.audit if e[1].get("healing_action") == "escalate"]
        self.assertEqual(len(audited), 1, "and audit it once")

    def test_recovery_re_arms_escalation(self):
        healer = hl.HealingOrchestrator(enabled=True, threshold=1, max_heals_per_hour=1)
        self._register(_Plugin("p.audit", "audit_backend"))
        for _ in range(5):
            healer.consider("p.audit", plugin_type="audit_backend", consecutive_failures=1)
        self.assertIn("p.audit", healer._escalated)
        healer.note_recovered("p.audit")
        self.assertNotIn("p.audit", healer._escalated)

    def test_escalation_does_not_consume_the_budget(self):
        healer = hl.HealingOrchestrator(enabled=True, threshold=1, max_heals_per_hour=2)
        self._register(_Plugin("p.audit", "audit_backend"))
        healer.consider("p.audit", plugin_type="audit_backend", consecutive_failures=1)
        before = healer.budget_left("p.audit")
        healer._escalate("p.audit", "test")
        self.assertEqual(
            healer.budget_left("p.audit"), before,
            "giving up must not count as a healing attempt",
        )

    def test_the_action_ledger_expires_by_time(self):
        healer = hl.HealingOrchestrator(enabled=True, threshold=1, max_heals_per_hour=2)
        self._register(_Plugin("p.audit", "audit_backend"))
        healer.consider("p.audit", plugin_type="audit_backend", consecutive_failures=1)
        self.assertEqual(healer.budget_left("p.audit"), 1)
        healer._actions["p.audit"] = [time.time() - (hl.HISTORY_WINDOW_S + 10)]
        self.assertEqual(
            healer.budget_left("p.audit"), 2, "an hour later the budget is back"
        )

    def test_old_entries_still_fall_out_by_time(self):
        healer = hl.HealingOrchestrator(enabled=True, threshold=1)
        self._register(_Plugin("p.audit", "audit_backend"))
        healer.consider("p.audit", plugin_type="audit_backend", consecutive_failures=1)
        for rec in healer._history["p.audit"]:
            rec.at = time.time() - (hl.HISTORY_WINDOW_S + 10)
        self.assertEqual(healer.history("p.audit"), [])


class TestConcurrentHealing(unittest.TestCase):
    """consider() had no lock at all; these tests pin the properties either way.

    Measured against the pre-lock code, neither the budget nor the restart path
    actually broke: PluginRegistry.unregister() deletes the entry under its own lock
    before calling on_unload(), so concurrent healers hit PluginNotFound instead of
    stacking. These tests therefore pin a property that used to hold INCIDENTALLY,
    from another module's internal ordering — which is exactly the kind of guarantee
    that vanishes in a refactor nobody connects to healing.
    """

    def test_concurrent_consider_respects_the_budget(self):
        orch = hl.HealingOrchestrator(enabled=True, max_heals_per_hour=3)
        barrier = threading.Barrier(8)

        def hit():
            barrier.wait()
            orch.consider(
                "p.race", plugin_type="audit_backend",
                consecutive_failures=5, error_code="down",
            )

        threads = [threading.Thread(target=hit) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        acted = [r for r in orch.history("p.race") if r.action is not hl.HealingAction.NOOP]
        real = [r for r in acted if r.action is not hl.HealingAction.ESCALATE]
        self.assertLessEqual(
            len(real), 3, f"{len(real)} budgeted actions past a budget of 3"
        )

    def test_the_same_plugin_is_never_restarted_twice_at_once(self):
        inside = []
        peak = [0]
        guard = threading.Lock()

        class _Slow:
            plugin_id = "p.slow-restart"
            plugin_type = "worker_engine"
            version = "1.0.0"
            display_name = "Slow"

            def on_load(self, ctx=None):
                with guard:
                    inside.append(1)
                    peak[0] = max(peak[0], len(inside))
                time.sleep(0.15)
                with guard:
                    inside.pop()

            def on_unload(self):
                with guard:
                    inside.append(1)
                    peak[0] = max(peak[0], len(inside))
                time.sleep(0.15)
                with guard:
                    inside.pop()

            def health_check(self):
                return HealthStatus(ok=False, message="down")

        plugin = _Slow()
        reg = get_registry()
        reg._plugins[plugin.plugin_id] = plugin
        reg._contexts[plugin.plugin_id] = _ctx(plugin.plugin_id)
        try:
            orch = hl.HealingOrchestrator(
                enabled=True, max_heals_per_hour=8,
                policies={plugin.plugin_id: hl.HealingPolicy.SOFT_RESTART},
            )
            barrier = threading.Barrier(6)

            def hit():
                barrier.wait()
                orch.consider(
                    plugin.plugin_id, plugin_type="worker_engine",
                    consecutive_failures=5, error_code="down",
                )

            threads = [threading.Thread(target=hit) for _ in range(6)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(
                peak[0], 1,
                f"{peak[0]} lifecycle calls ran on the same plugin simultaneously",
            )
        finally:
            reg._plugins.pop(plugin.plugin_id, None)
            reg._contexts.pop(plugin.plugin_id, None)

    def test_different_plugins_still_heal_in_parallel(self):
        """The lock is per plugin: one slow plugin must not stall healing globally."""
        orch = hl.HealingOrchestrator(enabled=True, max_heals_per_hour=8)
        done = []
        barrier = threading.Barrier(4)

        def hit(pid):
            barrier.wait()
            orch.consider(
                pid, plugin_type="audit_backend",
                consecutive_failures=5, error_code="down",
            )
            done.append(pid)

        started = time.monotonic()
        threads = [threading.Thread(target=hit, args=(f"p.par{i}",)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(done), 4)
        self.assertLess(time.monotonic() - started, 2.0)

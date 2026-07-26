"""Tests for the per-plugin circuit breaker and its wiring (ADR-0233 Phase 2)."""
from __future__ import annotations

import asyncio
import sys
import threading
import time
import unittest
from pathlib import Path

# ── Adjust path so tests can be run standalone ───────────────────────────────
_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[3]
_PKG = _HERE.parents[1]
for _p in (str(_PKG), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from corvin_plugins import circuit_breaker as cb  # noqa: E402
from corvin_plugins.protocol import HealthStatus, PluginContext  # noqa: E402
from corvin_plugins.providers import audit_backend as audit_provider  # noqa: E402
from corvin_plugins.providers import user_backend as user_provider  # noqa: E402
from corvin_plugins.registry import PluginRegistry  # noqa: E402


class TestBreakerStateMachine(unittest.TestCase):
    def setUp(self):
        self.b = cb.CircuitBreaker("test.plugin", failure_threshold=3, cooldown_s=0.05)

    def test_starts_closed(self):
        self.assertIs(self.b.state, cb.BreakerState.CLOSED)

    def test_opens_after_threshold_consecutive_failures(self):
        for _ in range(2):
            self.b.record_failure(RuntimeError())
        self.assertIs(self.b.state, cb.BreakerState.CLOSED, "not yet at threshold")
        self.b.record_failure(RuntimeError())
        self.assertIs(self.b.state, cb.BreakerState.OPEN)

    def test_success_resets_the_failure_run(self):
        self.b.record_failure(RuntimeError())
        self.b.record_failure(RuntimeError())
        self.b.record_success()
        self.b.record_failure(RuntimeError())
        self.assertIs(self.b.state, cb.BreakerState.CLOSED,
                      "failures must be CONSECUTIVE to open the breaker")

    def test_open_refuses_calls(self):
        for _ in range(3):
            self.b.record_failure(RuntimeError())
        with self.assertRaises(cb.CircuitOpen) as ctx:
            self.b.guard()
        self.assertEqual(ctx.exception.plugin_id, "test.plugin")
        self.assertGreaterEqual(ctx.exception.retry_in_s, 0.0)

    def test_cooldown_moves_open_to_half_open(self):
        for _ in range(3):
            self.b.record_failure(RuntimeError())
        self.assertIs(self.b.state, cb.BreakerState.OPEN)
        time.sleep(0.06)
        self.assertIs(self.b.state, cb.BreakerState.HALF_OPEN)
        self.b.guard()  # the probe is admitted

    def test_probe_success_closes(self):
        for _ in range(3):
            self.b.record_failure(RuntimeError())
        time.sleep(0.06)
        self.assertIs(self.b.state, cb.BreakerState.HALF_OPEN)
        self.b.record_success()
        self.assertIs(self.b.state, cb.BreakerState.CLOSED)

    def test_probe_failure_reopens_immediately(self):
        for _ in range(3):
            self.b.record_failure(RuntimeError())
        time.sleep(0.06)
        self.assertIs(self.b.state, cb.BreakerState.HALF_OPEN)
        self.b.record_failure(RuntimeError())
        self.assertIs(self.b.state, cb.BreakerState.OPEN,
                      "a single failed probe re-opens, without waiting for threshold")

    def test_reset_forces_closed(self):
        for _ in range(3):
            self.b.record_failure(RuntimeError())
        self.b.reset()
        self.assertIs(self.b.state, cb.BreakerState.CLOSED)
        self.b.guard()

    def test_threshold_must_be_positive(self):
        with self.assertRaises(ValueError):
            cb.CircuitBreaker("x", failure_threshold=0)


class TestBreakerCall(unittest.TestCase):
    def test_successful_call_passes_the_value_through(self):
        b = cb.CircuitBreaker("x")
        self.assertEqual(b.call(lambda a, b_=1: a + b_, 2, b_=3), 5)

    def test_failing_call_returns_the_fallback(self):
        b = cb.CircuitBreaker("x", failure_threshold=1)

        def boom():
            raise RuntimeError("nope")

        self.assertEqual(b.call(boom, fallback="fb"), "fb")
        self.assertIs(b.state, cb.BreakerState.OPEN)

    def test_open_breaker_does_not_invoke_the_function(self):
        b = cb.CircuitBreaker("x", failure_threshold=1, cooldown_s=60)
        calls = []

        def boom():
            calls.append(1)
            raise RuntimeError()

        b.call(boom, fallback=None)
        b.call(boom, fallback=None)
        self.assertEqual(len(calls), 1, "the second call must be refused, not run")
        self.assertEqual(b.stats().total_refused, 1)

    def test_slow_success_counts_as_failure(self):
        b = cb.CircuitBreaker("x", failure_threshold=1, slow_call_s=0.01)
        with self.assertLogs("corvin.plugins.breaker", level="WARNING"):
            result = b.call(lambda: (time.sleep(0.05), "late")[1], fallback="fb")
        self.assertEqual(result, "fb", "a glacial answer is still an outage")
        self.assertIs(b.state, cb.BreakerState.OPEN)
        self.assertEqual(b.stats().last_failure_type, "TimeoutError")

    def test_stats_carry_no_exception_message(self):
        b = cb.CircuitBreaker("x", failure_threshold=1)

        def boom():
            raise RuntimeError("ldaps://dc1.corp.example bind failed for jdoe")

        b.call(boom, fallback=None)
        blob = repr(b.stats().to_dict())
        self.assertIn("RuntimeError", blob)
        self.assertNotIn("jdoe", blob)
        self.assertNotIn("ldaps://", blob)

    def test_stats_are_a_copy(self):
        b = cb.CircuitBreaker("x")
        snap = b.stats()
        snap.consecutive_failures = 99
        self.assertEqual(b.stats().consecutive_failures, 0)

    def test_concurrent_calls_are_counted_consistently(self):
        b = cb.CircuitBreaker("x", failure_threshold=10_000)

        def hammer():
            for _ in range(50):
                b.call(lambda: 1)

        threads = [threading.Thread(target=hammer) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(b.stats().total_calls, 200)


class TestBreakerRegistry(unittest.TestCase):
    def setUp(self):
        cb._registry = cb.BreakerRegistry()

    def test_same_id_returns_the_same_breaker(self):
        self.assertIs(cb.get_breaker("a"), cb.get_breaker("a"))
        self.assertIsNot(cb.get_breaker("a"), cb.get_breaker("b"))

    def test_snapshot_reports_every_breaker(self):
        cb.get_breaker("a").record_failure(RuntimeError())
        cb.get_breaker("b")
        snap = cb.snapshot()
        self.assertEqual(set(snap), {"a", "b"})
        self.assertEqual(snap["a"]["consecutive_failures"], 1)
        self.assertEqual(snap["a"]["last_failure_type"], "RuntimeError")

    def test_forget_drops_the_breaker(self):
        cb.get_breaker("a").record_failure(RuntimeError())
        cb.forget("a")
        self.assertNotIn("a", cb.snapshot())

    def test_reset_all_closes_everything(self):
        for pid in ("a", "b"):
            for _ in range(5):
                cb.get_breaker(pid).record_failure(RuntimeError())
        cb.reset_all()
        for pid in ("a", "b"):
            self.assertEqual(cb.snapshot()[pid]["state"], "closed")


# ── Registry integration ──────────────────────────────────────────────────────


class _Plugin:
    plugin_type = "notification_backend"
    version = "1.0.0"

    def __init__(self, pid: str, behaviour="ok"):
        self.plugin_id = pid
        self.display_name = pid
        self.behaviour = behaviour
        self.health_calls = 0

    def on_load(self, ctx):
        pass

    def on_unload(self):
        pass

    def health_check(self):
        self.health_calls += 1
        if self.behaviour == "raise":
            raise RuntimeError("/home/secret/path failed for user jdoe")
        if self.behaviour == "unhealthy":
            return HealthStatus(ok=False, message="degraded")
        return HealthStatus(ok=True, message="ok", details={"custom": 1})


def _ctx(pid: str) -> PluginContext:
    return PluginContext(
        plugin_id=pid,
        tenant_id="_default",
        corvin_home=Path("/tmp"),
        config={},
        audit_emit=lambda *a, **k: None,
    )


class TestHealthCheckIntegration(unittest.TestCase):
    def setUp(self):
        cb._registry = cb.BreakerRegistry()
        self.reg = PluginRegistry()

    def test_healthy_plugin_reports_breaker_state(self):
        p = _Plugin("p.ok")
        self.reg.register(p, _ctx("p.ok"))
        results = self.reg.health_check_all()
        self.assertTrue(results["p.ok"].ok)
        self.assertEqual(results["p.ok"].details["breaker"]["state"], "closed")
        self.assertEqual(results["p.ok"].details["custom"], 1,
                         "the plugin's own details must survive the merge")

    def test_raising_health_check_does_not_leak_the_message(self):
        p = _Plugin("p.bad", behaviour="raise")
        self.reg.register(p, _ctx("p.bad"))
        with self.assertLogs("corvin_plugins.registry", level="WARNING"):
            results = self.reg.health_check_all()
        status = results["p.bad"]
        self.assertFalse(status.ok)
        self.assertIn("RuntimeError", status.message)
        self.assertNotIn("jdoe", status.message)
        self.assertNotIn("/home/secret", status.message)

    def test_repeated_failures_open_the_breaker_and_stop_calling(self):
        p = _Plugin("p.bad", behaviour="raise")
        self.reg.register(p, _ctx("p.bad"))
        for _ in range(3):
            with self.assertLogs("corvin_plugins.registry", level="WARNING"):
                self.reg.health_check_all()
        self.assertEqual(p.health_calls, 3)

        results = self.reg.health_check_all()
        self.assertEqual(p.health_calls, 3, "an open breaker must not call the plugin")
        self.assertFalse(results["p.bad"].ok)
        self.assertEqual(results["p.bad"].details["breaker"]["state"], "open")
        self.assertIn("retry_in_s", results["p.bad"].details)

    def test_one_sick_plugin_does_not_hide_the_others(self):
        good, bad = _Plugin("p.ok"), _Plugin("p.bad", behaviour="raise")
        self.reg.register(good, _ctx("p.ok"))
        self.reg.register(bad, _ctx("p.bad"))
        with self.assertLogs("corvin_plugins.registry", level="WARNING"):
            results = self.reg.health_check_all()
        self.assertTrue(results["p.ok"].ok)
        self.assertFalse(results["p.bad"].ok)

    def test_unhealthy_status_counts_toward_the_breaker(self):
        p = _Plugin("p.degraded", behaviour="unhealthy")
        self.reg.register(p, _ctx("p.degraded"))
        for _ in range(3):
            self.reg.health_check_all()
        self.assertEqual(cb.snapshot()["p.degraded"]["state"], "open")

    def test_unregister_forgets_the_breaker(self):
        p = _Plugin("p.bad", behaviour="raise")
        self.reg.register(p, _ctx("p.bad"))
        with self.assertLogs("corvin_plugins.registry", level="WARNING"):
            self.reg.health_check_all()
        self.assertIn("p.bad", cb.snapshot())
        self.reg.unregister("p.bad")
        self.assertNotIn("p.bad", cb.snapshot(),
                         "a re-registered plugin must not inherit the old failure count")


# ── Provider wiring ───────────────────────────────────────────────────────────


class _Sink:
    plugin_id = "test.sink"
    plugin_type = "audit_backend"
    version = "1.0.0"
    display_name = "Sink"

    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0

    def fanout(self, event_type, details, *, severity="INFO", tenant_id="_default"):
        self.calls += 1
        if self.fail:
            raise RuntimeError("down")

    def verify_chain(self):
        return HealthStatus(ok=True)

    def enforce_retention(self, max_age_days, *, tenant_id="_default"):
        return {"deleted": 0}


class TestAuditProviderBreaker(unittest.TestCase):
    def setUp(self):
        cb._registry = cb.BreakerRegistry()

    def tearDown(self):
        audit_provider.clear()

    def test_dead_sink_stops_being_called(self):
        sink = _Sink(fail=True)
        audit_provider.set_active(sink)
        with self.assertLogs("corvin.audit.fanout", level="ERROR"):
            for _ in range(3):
                audit_provider.fanout("x.y", {})
        self.assertEqual(sink.calls, 3)
        audit_provider.fanout("x.y", {})
        self.assertEqual(sink.calls, 3, "an open breaker must skip the sink entirely")

    def test_healthy_sink_keeps_the_breaker_closed(self):
        sink = _Sink()
        audit_provider.set_active(sink)
        for _ in range(10):
            self.assertTrue(audit_provider.fanout("x.y", {}))
        self.assertEqual(sink.calls, 10)
        self.assertEqual(cb.snapshot()["test.sink"]["state"], "closed")


class _Users:
    plugin_id = "test.users"
    plugin_type = "user_backend"
    version = "1.0.0"
    display_name = "Users"

    def __init__(self, mode="ok"):
        self.mode = mode
        self.calls = 0

    async def authenticate(self, credentials):
        self.calls += 1
        if self.mode == "raise":
            raise ConnectionError("directory down")
        if credentials.get("password") == "correct":
            return {"user_id": "u-1", "roles": []}
        return None

    async def get_user(self, user_id):
        return None

    async def list_users(self):
        return []

    async def enforce_quota(self, user_id, resource):
        return None


class TestUserProviderBreaker(unittest.TestCase):
    def setUp(self):
        cb._registry = cb.BreakerRegistry()

    def tearDown(self):
        user_provider.clear()

    def test_wrong_passwords_never_open_the_breaker(self):
        """Three wrong passwords must not lock out every user (self-DoS)."""
        backend = _Users()
        user_provider.set_active(backend)
        for _ in range(10):
            self.assertIsNone(
                asyncio.run(user_provider.authenticate({"username": "a",
                                                        "password": "wrong"}))
            )
        self.assertEqual(cb.snapshot()["test.users"]["state"], "closed")
        # A correct credential still works right after the failed attempts.
        result = asyncio.run(
            user_provider.authenticate({"username": "a", "password": "correct"})
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["user_id"], "u-1")

    def test_unreachable_directory_opens_the_breaker_and_denies(self):
        backend = _Users(mode="raise")
        user_provider.set_active(backend)
        with self.assertLogs("corvin.auth.backend", level="ERROR"):
            for _ in range(3):
                self.assertIsNone(
                    asyncio.run(user_provider.authenticate({"username": "a",
                                                            "password": "correct"}))
                )
        self.assertEqual(backend.calls, 3)
        with self.assertLogs("corvin.auth.backend", level="ERROR"):
            self.assertIsNone(
                asyncio.run(user_provider.authenticate({"username": "a",
                                                        "password": "correct"}))
            )
        self.assertEqual(backend.calls, 3, "open breaker must not reach the directory")

    def test_open_breaker_denies_rather_than_admits(self):
        backend = _Users(mode="raise")
        user_provider.set_active(backend)
        with self.assertLogs("corvin.auth.backend", level="ERROR"):
            for _ in range(4):
                result = asyncio.run(
                    user_provider.authenticate({"username": "a", "password": "correct"})
                )
                self.assertIsNone(result, "every path here must deny")


if __name__ == "__main__":
    unittest.main()


# ── Adversarial-review regression (ADR-0233 review round) ─────────────────────


class TestHalfOpenAdmitsExactlyOneProbe(unittest.TestCase):
    """F2: guard() only checked for OPEN, so HALF_OPEN admitted everyone.

    Consequence: the moment the cooldown elapsed, every caller queued behind the
    breaker hit the still-sick plugin at once — the thundering herd a breaker
    exists to prevent.
    """

    def setUp(self):
        self.b = cb.CircuitBreaker("x", failure_threshold=1, cooldown_s=0.05)
        self.b.record_failure(RuntimeError())
        time.sleep(0.06)

    def test_only_one_of_many_callers_is_admitted(self):
        self.assertIs(self.b.state, cb.BreakerState.HALF_OPEN)
        admitted, refused = 0, 0
        for _ in range(5):
            try:
                self.b.guard()
                admitted += 1
            except cb.CircuitOpen:
                refused += 1
        self.assertEqual(admitted, 1, "exactly one probe may run")
        self.assertEqual(refused, 4)

    def test_successful_probe_reopens_the_gate_for_everyone(self):
        self.b.guard()
        self.b.record_success()
        self.assertIs(self.b.state, cb.BreakerState.CLOSED)
        for _ in range(3):
            self.b.guard()  # closed again: no refusal

    def test_failed_probe_re_arms_the_cooldown(self):
        self.b.guard()
        self.b.record_failure(RuntimeError())
        self.assertIs(self.b.state, cb.BreakerState.OPEN)
        with self.assertRaises(cb.CircuitOpen):
            self.b.guard()

    def test_concurrent_callers_admit_exactly_one(self):
        admitted = []
        lock = threading.Lock()

        def probe():
            try:
                self.b.guard()
                with lock:
                    admitted.append(1)
            except cb.CircuitOpen:
                pass

        threads = [threading.Thread(target=probe) for _ in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sum(admitted), 1, "the probe slot must be race-free")

    def test_call_uses_the_single_probe_and_reports_fallback(self):
        calls = []

        def flaky():
            calls.append(1)
            raise RuntimeError()

        results = [self.b.call(flaky, fallback="fb") for _ in range(4)]
        self.assertEqual(results, ["fb"] * 4)
        self.assertEqual(len(calls), 1, "only the probe reaches the plugin")


class TestAbandonedProbeExpires(unittest.TestCase):
    """Second-round finding: the F2 fix could wedge the breaker shut forever.

    A caller that claims the half-open slot and never reports back — killed by a
    BaseException, or a future call site that forgets record_success/failure —
    left probe_in_flight set, so the breaker refused every caller from then on.
    That is strictly worse than the thundering herd the slot prevents, so the
    claim carries a TTL.
    """

    def test_abandoned_probe_releases_after_ttl(self):
        b = cb.CircuitBreaker("x", failure_threshold=1, cooldown_s=0.05, probe_ttl_s=0.1)
        b.record_failure(RuntimeError())
        time.sleep(0.06)
        b.guard()  # claimed, then the caller vanishes

        with self.assertRaises(cb.CircuitOpen):
            b.guard()  # concurrent caller is still refused

        time.sleep(0.12)
        with self.assertLogs("corvin.plugins.breaker", level="WARNING"):
            b.guard()  # expired: a fresh probe may run

    def test_a_reporting_probe_releases_immediately(self):
        b = cb.CircuitBreaker("x", failure_threshold=1, cooldown_s=0.05, probe_ttl_s=30)
        b.record_failure(RuntimeError())
        time.sleep(0.06)
        b.guard()
        b.record_success()
        b.guard()  # closed again — no need to wait for the TTL

    def test_ttl_defaults_to_the_wider_of_cooldown_and_slow_call(self):
        self.assertEqual(
            cb.CircuitBreaker("x", cooldown_s=30, slow_call_s=5).probe_ttl_s, 30
        )
        self.assertEqual(
            cb.CircuitBreaker("x", cooldown_s=2, slow_call_s=9).probe_ttl_s, 9
        )

    def test_slow_but_legitimate_probe_is_not_stolen(self):
        """The TTL must not be shorter than a call the breaker still considers OK."""
        b = cb.CircuitBreaker("x", failure_threshold=1, cooldown_s=0.05, slow_call_s=0.5)
        b.record_failure(RuntimeError())
        time.sleep(0.06)
        b.guard()
        time.sleep(0.2)  # still inside slow_call_s, so still a valid probe
        with self.assertRaises(cb.CircuitOpen):
            b.guard()

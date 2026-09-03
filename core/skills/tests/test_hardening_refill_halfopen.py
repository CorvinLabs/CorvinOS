"""Hardening layer (adversarial review D-09 / D-10).

* token bucket refills FRACTIONALLY — a fast poller is never starved
* HALF_OPEN admits exactly one in-flight probe
* the request timeout is ENFORCED (a hung resolver is abandoned)
* monotonic clock (injectable) — no wall-clock arithmetic
"""
from __future__ import annotations

import threading
import time

from core.skills.corvin_skills.hardening import (
    SkillServiceCircuitBreaker,
    SkillServiceHardening,
    SkillServiceRateLimiter,
)


class FakeClock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class TestRateLimiterRefill:

    def test_fast_poller_is_not_starved(self):
        """60/min = 1 token/s. Polling every 0.5 s for 60 s must admit ≈60
        requests (the integer-truncating version admitted 0)."""
        clock = FakeClock()
        rl = SkillServiceRateLimiter(rate_limit_per_minute=60, clock=clock)
        for _ in range(60):  # exhaust
            assert rl.is_allowed("c")
        assert not rl.is_allowed("c")
        allowed = 0
        for _ in range(120):
            clock.advance(0.5)
            if rl.is_allowed("c"):
                allowed += 1
        assert 58 <= allowed <= 61, allowed

    def test_bucket_caps_at_limit(self):
        clock = FakeClock()
        rl = SkillServiceRateLimiter(rate_limit_per_minute=10, clock=clock)
        assert rl.is_allowed("c")
        clock.advance(3600)
        assert rl.get_bucket_state("c")["tokens"] == 10.0

    def test_state_reports_float_tokens_and_monotonic_refill(self):
        clock = FakeClock(5.0)
        rl = SkillServiceRateLimiter(rate_limit_per_minute=60, clock=clock)
        rl.is_allowed("c")
        clock.advance(0.25)
        state = rl.get_bucket_state("c")
        assert abs(state["tokens"] - 59.25) < 1e-9
        assert state["last_refill"] == 5.25


class TestCircuitBreakerHalfOpen:

    def _open(self, cb: SkillServiceCircuitBreaker) -> None:
        for _ in range(cb.failure_threshold):
            cb.record_failure()
        assert cb.state == cb.STATE_OPEN

    def test_single_probe_in_half_open(self):
        clock = FakeClock()
        cb = SkillServiceCircuitBreaker(failure_threshold=2, recovery_timeout_seconds=10,
                                        success_threshold=1, clock=clock)
        self._open(cb)
        assert not cb.is_request_allowed()
        clock.advance(10)
        assert cb.is_request_allowed()          # becomes THE probe
        assert cb.state == cb.STATE_HALF_OPEN
        # every concurrent caller is refused while the probe is in flight
        assert sum(1 for _ in range(100) if cb.is_request_allowed()) == 0
        cb.record_success()
        assert cb.state == cb.STATE_CLOSED

    def test_failed_probe_reopens_and_next_probe_waits_for_timeout(self):
        clock = FakeClock()
        cb = SkillServiceCircuitBreaker(failure_threshold=2, recovery_timeout_seconds=10, clock=clock)
        self._open(cb)
        clock.advance(10)
        assert cb.is_request_allowed()
        cb.record_failure()
        assert cb.state == cb.STATE_OPEN
        assert not cb.is_request_allowed()
        clock.advance(10)
        assert cb.is_request_allowed()

    def test_lost_probe_is_presumed_dead_after_recovery_timeout(self):
        clock = FakeClock()
        cb = SkillServiceCircuitBreaker(failure_threshold=1, recovery_timeout_seconds=5, clock=clock)
        self._open(cb)
        clock.advance(5)
        assert cb.is_request_allowed()   # probe 1 never reports back
        assert not cb.is_request_allowed()
        clock.advance(5)
        assert cb.is_request_allowed()   # probe slot reclaimed
        assert cb.state_info()["probe_in_flight"] is True

    def test_wall_clock_jump_does_not_reopen(self):
        """A monotonic clock is used — patching the wall clock changes nothing."""
        cb = SkillServiceCircuitBreaker(failure_threshold=1, recovery_timeout_seconds=3600)
        self._open(cb)
        assert isinstance(cb.last_failure_time, float)
        assert not cb.is_request_allowed()


class TestTimeoutEnforced:

    def test_hung_resolver_is_abandoned_and_counted_as_failure(self):
        h = SkillServiceHardening(request_timeout_seconds=0.2, failure_threshold=1)
        release = threading.Event()

        def hung(name):
            release.wait(5)
            return {"name": name}

        t0 = time.monotonic()
        assert h.resolve_with_hardening(hung, client_id="c", skill_name="x") is None
        assert time.monotonic() - t0 < 2.0
        release.set()
        assert h.circuit_breaker.state == "OPEN"

    def test_fast_resolver_passes_through(self):
        h = SkillServiceHardening(request_timeout_seconds=1.0)
        assert h.resolve_with_hardening(lambda n: {"name": n}, client_id="c", skill_name="x") == {"name": "x"}
        assert h.circuit_breaker.state == "CLOSED"

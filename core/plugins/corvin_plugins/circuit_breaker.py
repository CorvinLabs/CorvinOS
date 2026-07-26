"""Per-plugin circuit breaker — contain a sick plugin, don't cascade (ADR-0233).

A plugin that fails slowly is worse than one that fails fast: every call site pays
its timeout, and the platform degrades everywhere at once.  The breaker gives each
``plugin_id`` three states:

* **closed** — calls pass through.  Consecutive failures are counted.
* **open** — calls are refused immediately with the caller's fallback.  No plugin
  code runs.  After ``cooldown_s`` the breaker allows one probe.
* **half_open** — exactly one probe call is admitted.  Success closes the breaker,
  failure re-opens it (and the cooldown applies again).

Deliberate properties:

* **Time is monotonic.** Cooldowns use ``time.monotonic()``, so a wall-clock jump
  (NTP step, suspend/resume) cannot leave a breaker stuck open or pop it early.
* **A timeout is a failure.** ``call()`` measures wall time and trips on a slow
  success too — a plugin that takes 30 s to answer correctly is still an outage.
* **Fail-closed is the caller's choice, not the breaker's.** ``call()`` returns the
  fallback the caller passes. For an audit sink the fallback is "drop the copy"; for
  an auth backend the caller passes ``None``, which its own contract reads as deny.
  The breaker never invents a permissive default.
* **No global kill switch.** Per-plugin state only; there is no "disable all
  breakers" flag, because that would be an availability foot-gun.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict

log = logging.getLogger("corvin.plugins.breaker")


class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


#: Consecutive failures before the breaker opens.
DEFAULT_FAILURE_THRESHOLD = 3
#: How long an open breaker refuses calls before admitting one probe.
DEFAULT_COOLDOWN_S = 30.0
#: A call slower than this counts as a failure even if it returns.
DEFAULT_SLOW_CALL_S = 5.0


class CircuitOpen(RuntimeError):
    """Raised by :meth:`CircuitBreaker.guard` when the breaker is open."""

    def __init__(self, plugin_id: str, retry_in_s: float):
        super().__init__(f"circuit open for {plugin_id!r}, retry in {retry_in_s:.1f}s")
        self.plugin_id = plugin_id
        self.retry_in_s = retry_in_s


@dataclass
class BreakerStats:
    """Observable state of one breaker.  Contains no exception messages."""

    plugin_id: str
    state: BreakerState = BreakerState.CLOSED
    consecutive_failures: int = 0
    total_failures: int = 0
    total_calls: int = 0
    total_refused: int = 0
    #: Exception CLASS name of the most recent failure, never its message.
    last_failure_type: str | None = None
    opened_at: float | None = field(default=None, repr=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plugin_id": self.plugin_id,
            "state": self.state.value,
            "consecutive_failures": self.consecutive_failures,
            "total_failures": self.total_failures,
            "total_calls": self.total_calls,
            "total_refused": self.total_refused,
            "last_failure_type": self.last_failure_type,
        }


class CircuitBreaker:
    """One breaker for one plugin.  Thread-safe."""

    def __init__(
        self,
        plugin_id: str,
        *,
        failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
        cooldown_s: float = DEFAULT_COOLDOWN_S,
        slow_call_s: float = DEFAULT_SLOW_CALL_S,
    ):
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        self.plugin_id = plugin_id
        self.failure_threshold = failure_threshold
        self.cooldown_s = cooldown_s
        self.slow_call_s = slow_call_s
        self._lock = threading.Lock()
        self._stats = BreakerStats(plugin_id=plugin_id)

    # ── State ────────────────────────────────────────────────────────────────

    @property
    def state(self) -> BreakerState:
        """Current state, accounting for an elapsed cooldown."""
        with self._lock:
            return self._state_locked()

    def _state_locked(self) -> BreakerState:
        st = self._stats
        if st.state is BreakerState.OPEN and st.opened_at is not None:
            if time.monotonic() - st.opened_at >= self.cooldown_s:
                st.state = BreakerState.HALF_OPEN
        return st.state

    def stats(self) -> BreakerStats:
        with self._lock:
            self._state_locked()
            # Copy so a caller cannot mutate breaker state through the dataclass.
            return BreakerStats(**vars(self._stats))

    def reset(self) -> None:
        """Force the breaker closed (operator re-enable, plugin reload)."""
        with self._lock:
            self._stats.state = BreakerState.CLOSED
            self._stats.consecutive_failures = 0
            self._stats.opened_at = None

    # ── Recording ────────────────────────────────────────────────────────────

    def record_success(self) -> None:
        with self._lock:
            st = self._stats
            st.consecutive_failures = 0
            if st.state is not BreakerState.CLOSED:
                log.info("circuit closed for %r after a successful probe", self.plugin_id)
            st.state = BreakerState.CLOSED
            st.opened_at = None

    def record_failure(self, exc: BaseException | None = None) -> None:
        with self._lock:
            st = self._stats
            st.consecutive_failures += 1
            st.total_failures += 1
            if exc is not None:
                # Class name only. str(exc) routinely carries hostnames, paths,
                # bind DNs or record fragments.
                st.last_failure_type = type(exc).__name__
            was = st.state
            if (
                st.state is BreakerState.HALF_OPEN
                or st.consecutive_failures >= self.failure_threshold
            ):
                st.state = BreakerState.OPEN
                st.opened_at = time.monotonic()
                if was is not BreakerState.OPEN:
                    log.error(
                        "circuit OPEN for %r after %d consecutive failures (last: %s)",
                        self.plugin_id,
                        st.consecutive_failures,
                        st.last_failure_type,
                    )

    # ── Invocation ───────────────────────────────────────────────────────────

    def guard(self) -> None:
        """Raise :class:`CircuitOpen` when the breaker is refusing calls."""
        with self._lock:
            state = self._state_locked()
            if state is BreakerState.OPEN:
                self._stats.total_refused += 1
                retry_in = self.cooldown_s
                if self._stats.opened_at is not None:
                    retry_in = max(
                        0.0, self.cooldown_s - (time.monotonic() - self._stats.opened_at)
                    )
                raise CircuitOpen(self.plugin_id, retry_in)
            self._stats.total_calls += 1

    def call(self, fn: Callable[..., Any], *args: Any, fallback: Any = None, **kwargs: Any) -> Any:
        """Run ``fn`` under the breaker, returning ``fallback`` when it cannot.

        Returns ``fallback`` both when the breaker is open and when the call
        fails — the caller's own contract decides what that fallback means.
        """
        try:
            self.guard()
        except CircuitOpen:
            return fallback

        started = time.monotonic()
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 — containment is the whole point
            self.record_failure(exc)
            return fallback

        elapsed = time.monotonic() - started
        if elapsed > self.slow_call_s:
            # A correct-but-glacial answer is still an outage for every caller.
            log.warning(
                "plugin %r answered in %.1fs (slow-call threshold %.1fs)",
                self.plugin_id,
                elapsed,
                self.slow_call_s,
            )
            self.record_failure(TimeoutError())
            return fallback

        self.record_success()
        return result


class BreakerRegistry:
    """Holds one breaker per plugin_id.  Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._breakers: Dict[str, CircuitBreaker] = {}

    def get(self, plugin_id: str, **kwargs: Any) -> CircuitBreaker:
        """Return (creating on first use) the breaker for a plugin."""
        with self._lock:
            breaker = self._breakers.get(plugin_id)
            if breaker is None:
                breaker = CircuitBreaker(plugin_id, **kwargs)
                self._breakers[plugin_id] = breaker
            return breaker

    def forget(self, plugin_id: str) -> None:
        """Drop a breaker entirely (plugin uninstalled)."""
        with self._lock:
            self._breakers.pop(plugin_id, None)

    def snapshot(self) -> Dict[str, Dict[str, Any]]:
        """Breaker state for every known plugin — for health_check_all()."""
        with self._lock:
            breakers = list(self._breakers.values())
        return {b.plugin_id: b.stats().to_dict() for b in breakers}

    def reset_all(self) -> None:
        with self._lock:
            breakers = list(self._breakers.values())
        for b in breakers:
            b.reset()


_registry = BreakerRegistry()


def get_breaker(plugin_id: str, **kwargs: Any) -> CircuitBreaker:
    return _registry.get(plugin_id, **kwargs)


def forget(plugin_id: str) -> None:
    _registry.forget(plugin_id)


def snapshot() -> Dict[str, Dict[str, Any]]:
    return _registry.snapshot()


def reset_all() -> None:
    _registry.reset_all()

"""Per-tool rate limiter + circuit breaker.

Both are in-memory state owned by the running MCP server: they reset on
restart, which is intentional — a fresh process gives every tool a clean
slate. Persisting them would conflate "tool was buggy yesterday" with
"tool is buggy now."

Rate limiter: token bucket. ``capacity`` tokens are refilled linearly
over a 60-second window; each call consumes one token.

Circuit breaker: classic 3-state machine.

  CLOSED   ──failure_threshold consecutive fails──▶  OPEN
  OPEN     ──reset_timeout elapsed────────────────▶  HALF_OPEN
  HALF_OPEN ─success──▶ CLOSED   ─failure──▶ OPEN

Half-open allows up to ``half_open_max`` probe calls; the *first* success
flips back to CLOSED, the *first* failure flips back to OPEN.

Both classes are thread-safe via the lock owned by ``BreakerRegistry``.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid runtime import cycle
    from .policy import Policy


@dataclass
class CircuitBreaker:
    failure_threshold: int = 5
    reset_timeout: float = 60.0
    half_open_max: int = 2

    state: str = "CLOSED"
    consecutive_failures: int = 0
    opened_at: float = 0.0
    half_open_attempts: int = 0

    def _now(self) -> float:
        return time.monotonic()

    def can_execute(self) -> tuple[bool, str]:
        """Returns (ok_to_execute, reason). Side-effect: may transition
        OPEN → HALF_OPEN if reset_timeout has elapsed."""
        if self.state == "CLOSED":
            return True, "closed"
        if self.state == "OPEN":
            if self._now() - self.opened_at >= self.reset_timeout:
                self.state = "HALF_OPEN"
                self.half_open_attempts = 0
                # fall through into HALF_OPEN
            else:
                return False, "open"
        # HALF_OPEN
        if self.half_open_attempts < self.half_open_max:
            self.half_open_attempts += 1
            return True, "half_open_probe"
        return False, "half_open_exhausted"

    def record_success(self) -> str | None:
        """Returns transition name if state changed, else None."""
        prev = self.state
        if self.state == "HALF_OPEN":
            self.state = "CLOSED"
            self.consecutive_failures = 0
            self.half_open_attempts = 0
            return "closed_from_half_open"
        # CLOSED stays CLOSED, just zero the counter
        self.consecutive_failures = 0
        return None

    def record_failure(self) -> str | None:
        """Returns transition name if state changed, else None."""
        if self.state == "HALF_OPEN":
            self.state = "OPEN"
            self.opened_at = self._now()
            self.consecutive_failures = self.failure_threshold
            return "reopened_from_half_open"
        if self.state == "OPEN":
            return None  # already open, no further transition
        # CLOSED
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.failure_threshold:
            self.state = "OPEN"
            self.opened_at = self._now()
            return "opened"
        return None

    # used by tests
    def force_open(self) -> None:
        self.state = "OPEN"
        self.opened_at = self._now()
        self.consecutive_failures = self.failure_threshold


@dataclass
class RateLimiter:
    """Token-bucket — capacity tokens refilled at capacity/60 per second."""
    capacity: int
    tokens: float = field(init=False)
    last: float = field(init=False)

    def __post_init__(self):
        self.tokens = float(self.capacity)
        self.last = time.monotonic()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self.last
        if elapsed > 0:
            self.tokens = min(
                self.capacity, self.tokens + elapsed * (self.capacity / 60.0)
            )
            self.last = now

    def try_consume(self, n: float = 1.0) -> bool:
        self._refill()
        if self.tokens >= n:
            self.tokens -= n
            return True
        return False

    def available(self) -> float:
        self._refill()
        return self.tokens


class BreakerRegistry:
    """Thread-safe lazy-init registry of (CircuitBreaker, RateLimiter) per tool."""

    def __init__(self, policy: "Policy"):
        self._lock = threading.Lock()
        self._cbs: dict[str, CircuitBreaker] = {}
        self._rls: dict[str, RateLimiter] = {}
        self._policy = policy

    def get_breaker(self, tool: str) -> CircuitBreaker:
        with self._lock:
            cb = self._cbs.get(tool)
            if cb is None:
                cb = CircuitBreaker(
                    failure_threshold=self._policy.circuit_breaker_failure_threshold,
                    reset_timeout=self._policy.circuit_breaker_reset_timeout,
                    half_open_max=self._policy.circuit_breaker_half_open_max,
                )
                self._cbs[tool] = cb
            return cb

    def get_limiter(self, tool: str) -> RateLimiter:
        with self._lock:
            rl = self._rls.get(tool)
            if rl is None:
                rl = RateLimiter(capacity=self._policy.rate_limit_for(tool))
                self._rls[tool] = rl
            return rl

    def stats(self) -> dict[str, dict]:
        """Snapshot for introspection / debugging."""
        with self._lock:
            return {
                name: {
                    "state": cb.state,
                    "consecutive_failures": cb.consecutive_failures,
                    "tokens_remaining": round(self._rls.get(name).available(), 2)
                        if name in self._rls else None,
                }
                for name, cb in self._cbs.items()
            }

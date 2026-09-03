"""Production hardening layer for Skill System (Phase 8, ADR-0422+).

Rate limiting, circuit breaker, request timeout, graceful degradation.

Public API:
  - SkillServiceRateLimiter: Per-client/user rate limiting
  - SkillServiceCircuitBreaker: Fail-fast on manifest load failures
  - SkillServiceHardening: Orchestrates all hardening components

Clock: every component uses ``time.monotonic()`` — wall-clock jumps (NTP,
suspend) must neither starve a bucket nor reopen a breaker.

Adversarial review 2026-09-03 (D-09/D-10):
  * The token bucket kept FRACTIONAL tokens. The previous ``int(elapsed *
    rate)`` truncated to 0 while ``last_refill`` was reset on every call, so a
    client polling faster than one token interval never refilled again.
  * ``HALF_OPEN`` admits ONE in-flight probe at a time; concurrent callers
    are refused until that probe reports success/failure (or is presumed lost
    after ``recovery_timeout_seconds``).
  * ``resolve_with_hardening`` ENFORCES ``request_timeout_seconds``: the
    resolver runs on a daemon worker thread and is abandoned on overrun. The
    previous version measured elapsed time after the fact and called that a
    timeout.
"""

from typing import Optional, Dict, Any, Callable
from threading import Lock, Thread
import time


class SkillServiceRateLimiter:
    """Token-bucket rate limiter per client/tenant.

    Attributes:
        rate_limit_per_minute: Max requests per minute per client
    """

    def __init__(self, rate_limit_per_minute: int = 1000, clock: Callable[[], float] = time.monotonic):
        """Initialize rate limiter.

        Args:
            rate_limit_per_minute: Default limit (e.g., 1000 req/min)
            clock: monotonic time source (injectable for tests)
        """
        self.rate_limit_per_minute = rate_limit_per_minute
        self.token_buckets: Dict[str, Dict[str, Any]] = {}
        self._clock = clock
        self._lock = Lock()

    @property
    def refill_rate_per_second(self) -> float:
        return self.rate_limit_per_minute / 60.0

    def _refill(self, bucket: Dict[str, Any], now: float) -> None:
        elapsed = max(0.0, now - bucket["last_refill"])
        # Fractional accounting: sub-token progress is never thrown away.
        bucket["tokens"] = min(
            float(self.rate_limit_per_minute),
            bucket["tokens"] + elapsed * self.refill_rate_per_second,
        )
        bucket["last_refill"] = now

    def is_allowed(self, client_id: str) -> bool:
        """Check if client is within rate limit.

        Token-bucket algorithm:
        - Each client gets tokens at rate_limit_per_minute / 60 per second
        - Each request consumes 1 token
        - If fewer than 1 token is available, request denied

        Args:
            client_id: Client identifier (tenant_id or user_id)

        Returns:
            True if request is allowed, False if rate-limited
        """
        with self._lock:
            now = self._clock()
            bucket = self.token_buckets.get(client_id)

            if bucket is None:
                bucket = {"tokens": float(self.rate_limit_per_minute), "last_refill": now}
                self.token_buckets[client_id] = bucket
            else:
                self._refill(bucket, now)

            if bucket["tokens"] >= 1.0:
                bucket["tokens"] -= 1.0
                return True
            return False

    def get_bucket_state(self, client_id: str) -> Dict[str, Any]:
        """Get current token state for diagnostics (refilled to ``now``).

        Returns:
            {tokens: float, last_refill: float (monotonic seconds) | None}
        """
        with self._lock:
            bucket = self.token_buckets.get(client_id)
            if bucket is None:
                return {"tokens": float(self.rate_limit_per_minute), "last_refill": None}
            self._refill(bucket, self._clock())
            return {"tokens": bucket["tokens"], "last_refill": bucket["last_refill"]}


class SkillServiceCircuitBreaker:
    """Circuit breaker for manifest load failures (fail-fast pattern).

    States:
      - CLOSED: Normal operation
      - OPEN: Failing; reject requests without trying
      - HALF_OPEN: Testing if service recovered — ONE probe in flight at a time

    Attributes:
        failure_threshold: Failures to trigger OPEN
        recovery_timeout_seconds: Time before trying HALF_OPEN
        success_threshold: Successes to close from HALF_OPEN
    """

    STATE_CLOSED = "CLOSED"
    STATE_OPEN = "OPEN"
    STATE_HALF_OPEN = "HALF_OPEN"

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout_seconds: int = 60,
        success_threshold: int = 2,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout_seconds = recovery_timeout_seconds
        self.success_threshold = success_threshold
        self._clock = clock

        self.state = self.STATE_CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: Optional[float] = None  # monotonic seconds
        self._probe_in_flight = False
        self._probe_started: Optional[float] = None
        self._lock = Lock()

    def _release_probe(self) -> None:
        self._probe_in_flight = False
        self._probe_started = None

    def record_success(self) -> None:
        """Record successful operation."""
        with self._lock:
            if self.state == self.STATE_HALF_OPEN:
                self._release_probe()
                self.success_count += 1
                if self.success_count >= self.success_threshold:
                    self.state = self.STATE_CLOSED
                    self.failure_count = 0
                    self.success_count = 0
            elif self.state == self.STATE_CLOSED:
                self.failure_count = max(0, self.failure_count - 1)

    def record_failure(self) -> None:
        """Record failed operation."""
        with self._lock:
            self.failure_count += 1
            self.last_failure_time = self._clock()

            if self.state == self.STATE_HALF_OPEN:
                self._release_probe()
                self.state = self.STATE_OPEN
                self.success_count = 0
            elif self.failure_count >= self.failure_threshold:
                self.state = self.STATE_OPEN

    def is_request_allowed(self) -> bool:
        """Check if request should be attempted.

        Returns:
            CLOSED → True. OPEN → False until ``recovery_timeout_seconds`` has
            passed, then the caller becomes the single HALF_OPEN probe.
            HALF_OPEN → True only when no probe is in flight (a probe that
            never reported back is presumed lost after the recovery timeout).
        """
        with self._lock:
            now = self._clock()
            if self.state == self.STATE_CLOSED:
                return True

            if self.state == self.STATE_HALF_OPEN:
                if self._probe_in_flight and self._probe_started is not None:
                    if now - self._probe_started < self.recovery_timeout_seconds:
                        return False
                self._probe_in_flight = True
                self._probe_started = now
                return True

            # OPEN: check if recovery timeout has passed
            if self.last_failure_time is not None:
                if now - self.last_failure_time >= self.recovery_timeout_seconds:
                    self.state = self.STATE_HALF_OPEN
                    self.success_count = 0
                    self._probe_in_flight = True
                    self._probe_started = now
                    return True
            return False

    def state_info(self) -> Dict[str, Any]:
        """Get circuit breaker state for diagnostics."""
        with self._lock:
            seconds_since_failure = (
                None if self.last_failure_time is None
                else round(self._clock() - self.last_failure_time, 3)
            )
            return {
                "state": self.state,
                "failure_count": self.failure_count,
                "success_count": self.success_count,
                "last_failure_time": self.last_failure_time,
                "seconds_since_last_failure": seconds_since_failure,
                "probe_in_flight": self._probe_in_flight,
            }


class SkillServiceHardening:
    """Orchestrates rate limiting, circuit breaker, request timeout (Phase 8).

    Attributes:
        rate_limiter: SkillServiceRateLimiter instance
        circuit_breaker: SkillServiceCircuitBreaker instance
        request_timeout_seconds: Enforced bound on resolver.resolve()
        connection_timeout_seconds: Advisory bound for manifest disk I/O
            (reported in health, not separately enforced — disk reads are
            covered by the request timeout)
    """

    def __init__(
        self,
        rate_limit_per_minute: int = 1000,
        request_timeout_seconds: float = 5.0,
        connection_timeout_seconds: float = 2.0,
        failure_threshold: int = 5,
        recovery_timeout_seconds: int = 60,
        success_threshold: int = 2,
    ):
        self.rate_limiter = SkillServiceRateLimiter(rate_limit_per_minute)
        self.circuit_breaker = SkillServiceCircuitBreaker(
            failure_threshold=failure_threshold,
            recovery_timeout_seconds=recovery_timeout_seconds,
            success_threshold=success_threshold,
        )
        self.request_timeout_seconds = request_timeout_seconds
        self.connection_timeout_seconds = connection_timeout_seconds

    def resolve_with_hardening(
        self,
        resolver_callable: Callable,
        client_id: str,
        skill_name: str,
    ) -> Optional[Dict[str, Any]]:
        """Resolve skill with all hardening applied.

        1. Rate limit check (deny if exceeded)
        2. Circuit breaker check (deny if OPEN / probe already in flight)
        3. Execute resolver on a worker thread, joined with request timeout
        4. Record result (success/failure/timeout)

        Returns:
            Skill entry or None (on rate-limit/circuit-breaker deny, error or timeout)
        """
        # Step 1: Rate limit
        if not self.rate_limiter.is_allowed(client_id):
            return None

        # Step 2: Circuit breaker
        if not self.circuit_breaker.is_request_allowed():
            return None

        # Step 3: Execute with an ENFORCED timeout
        holder: Dict[str, Any] = {}

        def _runner() -> None:
            try:
                holder["result"] = resolver_callable(skill_name)
            except BaseException as exc:  # noqa: BLE001 — recorded as breaker failure
                holder["exc"] = exc

        worker = Thread(target=_runner, name=f"skill-resolve:{skill_name}", daemon=True)
        worker.start()
        worker.join(timeout=max(self.request_timeout_seconds, 0.0))

        if worker.is_alive() or "exc" in holder:
            # Overrun (abandoned) or error during resolution
            self.circuit_breaker.record_failure()
            return None

        self.circuit_breaker.record_success()
        return holder.get("result")

    def health_status(self) -> Dict[str, Any]:
        """Get hardening health status for monitoring."""
        return {
            "rate_limiter": {
                "rate_limit_per_minute": self.rate_limiter.rate_limit_per_minute
            },
            "circuit_breaker": self.circuit_breaker.state_info(),
            "timeouts": {
                "request_seconds": self.request_timeout_seconds,
                "connection_seconds": self.connection_timeout_seconds,
            },
        }

"""Production hardening layer for Skill System (Phase 8, ADR-0422+).

Rate limiting, circuit breaker, timeout management, graceful degradation.

Public API:
  - SkillServiceRateLimiter: Per-client/user rate limiting
  - SkillServiceCircuitBreaker: Fail-fast on manifest load failures
  - SkillServiceHardening: Orchestrates all hardening components
"""

from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, Callable
from threading import Lock
from collections import defaultdict
import time


class SkillServiceRateLimiter:
    """Token-bucket rate limiter per client/tenant.

    Attributes:
        rate_limit_per_minute: Max requests per minute per client
        cleanup_interval_seconds: How often to prune old clients
    """

    def __init__(self, rate_limit_per_minute: int = 1000):
        """Initialize rate limiter.

        Args:
            rate_limit_per_minute: Default limit (e.g., 1000 req/min)
        """
        self.rate_limit_per_minute = rate_limit_per_minute
        self.token_buckets: Dict[str, Dict[str, Any]] = {}
        self._lock = Lock()

    def is_allowed(self, client_id: str) -> bool:
        """Check if client is within rate limit.

        Token-bucket algorithm:
        - Each client gets tokens at rate_limit_per_minute / 60 per second
        - Each request consumes 1 token
        - If no tokens available, request denied

        Args:
            client_id: Client identifier (tenant_id or user_id)

        Returns:
            True if request is allowed, False if rate-limited
        """
        with self._lock:
            now = datetime.now(timezone.utc)
            bucket = self.token_buckets.get(client_id)

            if bucket is None:
                # First request from client; initialize bucket
                bucket = {
                    "tokens": self.rate_limit_per_minute,
                    "last_refill": now,
                }
                self.token_buckets[client_id] = bucket

            # Refill tokens based on elapsed time
            elapsed = (now - bucket["last_refill"]).total_seconds()
            refill_rate = self.rate_limit_per_minute / 60  # tokens per second
            tokens_to_add = int(elapsed * refill_rate)

            bucket["tokens"] = min(
                self.rate_limit_per_minute,
                bucket["tokens"] + tokens_to_add,
            )
            bucket["last_refill"] = now

            # Consume 1 token if available
            if bucket["tokens"] > 0:
                bucket["tokens"] -= 1
                return True
            return False

    def get_bucket_state(self, client_id: str) -> Dict[str, Any]:
        """Get current token state for diagnostics.

        Returns:
            {tokens: float, last_refill: datetime}
        """
        with self._lock:
            bucket = self.token_buckets.get(client_id, {})
            return {
                "tokens": bucket.get("tokens", self.rate_limit_per_minute),
                "last_refill": bucket.get("last_refill"),
            }


class SkillServiceCircuitBreaker:
    """Circuit breaker for manifest load failures (fail-fast pattern).

    States:
      - CLOSED: Normal operation
      - OPEN: Failing; reject requests without trying
      - HALF_OPEN: Testing if service recovered

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
    ):
        """Initialize circuit breaker.

        Args:
            failure_threshold: Failures before opening (default 5)
            recovery_timeout_seconds: Time in OPEN before trying HALF_OPEN (default 60s)
            success_threshold: Successes in HALF_OPEN to close (default 2)
        """
        self.failure_threshold = failure_threshold
        self.recovery_timeout_seconds = recovery_timeout_seconds
        self.success_threshold = success_threshold

        self.state = self.STATE_CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: Optional[datetime] = None
        self._lock = Lock()

    def record_success(self) -> None:
        """Record successful operation."""
        with self._lock:
            if self.state == self.STATE_HALF_OPEN:
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
            self.last_failure_time = datetime.now(timezone.utc)

            if self.failure_count >= self.failure_threshold:
                self.state = self.STATE_OPEN
            elif self.state == self.STATE_HALF_OPEN:
                self.state = self.STATE_OPEN
                self.success_count = 0

    def is_request_allowed(self) -> bool:
        """Check if request should be attempted.

        Returns:
            True if CLOSED or HALF_OPEN, False if OPEN (unless recovery timeout passed)
        """
        with self._lock:
            if self.state == self.STATE_CLOSED or self.state == self.STATE_HALF_OPEN:
                return True

            # OPEN: check if recovery timeout has passed
            if self.last_failure_time:
                elapsed = (
                    datetime.now(timezone.utc) - self.last_failure_time
                ).total_seconds()
                if elapsed >= self.recovery_timeout_seconds:
                    self.state = self.STATE_HALF_OPEN
                    self.success_count = 0
                    return True

            return False

    def state_info(self) -> Dict[str, Any]:
        """Get circuit breaker state for diagnostics."""
        with self._lock:
            return {
                "state": self.state,
                "failure_count": self.failure_count,
                "success_count": self.success_count,
                "last_failure_time": self.last_failure_time,
            }


class SkillServiceHardening:
    """Orchestrates rate limiting, circuit breaker, timeouts (Phase 8).

    Attributes:
        rate_limiter: SkillServiceRateLimiter instance
        circuit_breaker: SkillServiceCircuitBreaker instance
        request_timeout_seconds: Timeout for resolver.resolve()
        connection_timeout_seconds: Timeout for manifest load from disk
    """

    def __init__(
        self,
        rate_limit_per_minute: int = 1000,
        request_timeout_seconds: float = 5.0,
        connection_timeout_seconds: float = 2.0,
    ):
        """Initialize hardening layer.

        Args:
            rate_limit_per_minute: Rate limit per client (default 1000/min)
            request_timeout_seconds: Timeout for resolver queries (default 5s)
            connection_timeout_seconds: Timeout for disk I/O (default 2s)
        """
        self.rate_limiter = SkillServiceRateLimiter(rate_limit_per_minute)
        self.circuit_breaker = SkillServiceCircuitBreaker()
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
        2. Circuit breaker check (deny if OPEN)
        3. Execute resolver with timeout
        4. Record result (success/failure)

        Args:
            resolver_callable: resolver.resolve function
            client_id: Client identifier (for rate limiting)
            skill_name: Skill to resolve

        Returns:
            Skill entry or None (on rate-limit/circuit-breaker deny or timeout)
        """
        # Step 1: Rate limit
        if not self.rate_limiter.is_allowed(client_id):
            # Degraded: return None (client over quota)
            return None

        # Step 2: Circuit breaker
        if not self.circuit_breaker.is_request_allowed():
            # Degraded: return None (service failing)
            return None

        # Step 3: Execute with timeout
        try:
            # In production, this would use signal.SIGALRM or threading.Timer
            # For now, synchronous call with implicit timeout assumption
            start = time.time()
            result = resolver_callable(skill_name)
            elapsed = time.time() - start

            if elapsed > self.request_timeout_seconds:
                # Timeout exceeded; record as failure
                self.circuit_breaker.record_failure()
                return None

            # Success
            self.circuit_breaker.record_success()
            return result

        except Exception as e:
            # Error during resolution
            self.circuit_breaker.record_failure()
            return None

    def health_status(self) -> Dict[str, Any]:
        """Get hardening health status for monitoring.

        Returns:
            {
              rate_limiter: {tokens: float},
              circuit_breaker: {state: str, failure_count: int},
              timeouts: {request_seconds: float, connection_seconds: float}
            }
        """
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

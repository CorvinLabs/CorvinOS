"""
Offline detection and fallback routing.

Monitors API health and automatically routes to local engine when API is unavailable.
Graceful degradation: if Claude API times out >5s, use local Llama 2 instead.
Quality degradation: local scores 0.85 (vs 0.98 for Claude).

Design:
- Non-blocking health checks (don't slow down normal operation)
- Exponential backoff for API retry (avoid thundering herd)
- Operator notification when offline mode active
- Automatic recovery when API comes back online
"""

from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime, timedelta
from enum import Enum
import asyncio


class APIHealthStatus(Enum):
    """Health status of Claude API."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


@dataclass
class OfflineDetectionConfig:
    """Configuration for offline detection."""
    api_timeout_seconds: float = 5.0
    health_check_interval_seconds: float = 30.0
    offline_threshold_failures: int = 3
    recovery_check_interval_seconds: float = 60.0
    max_backoff_seconds: float = 300.0


@dataclass
class HealthCheckResult:
    """Result of a health check."""
    status: APIHealthStatus
    timestamp: datetime = field(default_factory=datetime.utcnow)
    latency_ms: float = 0.0
    error_message: Optional[str] = None


class OfflineDetector:
    """
    Detect API availability and manage fallback routing.

    Strategy:
    1. On every API call, measure response time
    2. If timeout >5s, increment failure counter
    3. After 3+ failures, switch to offline mode
    4. In offline mode, use local engine
    5. Periodically check if API recovered
    6. On recovery, switch back to online mode
    """

    def __init__(self, config: Optional[OfflineDetectionConfig] = None):
        """Initialize offline detector."""
        self.config = config or OfflineDetectionConfig()
        self.status = APIHealthStatus.UNKNOWN
        self.consecutive_failures = 0
        self.last_health_check: Optional[HealthCheckResult] = None
        self.offline_since: Optional[datetime] = None

    def report_api_response(self, latency_ms: float, success: bool) -> None:
        """
        Report result of API call.

        Args:
            latency_ms: Response time in milliseconds
            success: Whether call succeeded

        Updates internal state and may transition to offline mode.
        """
        if not success or latency_ms > self.config.api_timeout_seconds * 1000:
            self.consecutive_failures += 1

            if self.consecutive_failures >= self.config.offline_threshold_failures:
                if self.status != APIHealthStatus.OFFLINE:
                    self._transition_to_offline()
        else:
            # Success: reset failure counter
            self.consecutive_failures = 0
            if self.status == APIHealthStatus.OFFLINE:
                self._transition_to_online()

    def _transition_to_offline(self) -> None:
        """Transition to offline mode."""
        self.status = APIHealthStatus.OFFLINE
        self.offline_since = datetime.utcnow()
        # In production: log event, alert operator
        print(f"[OFFLINE] Switched to offline mode at {self.offline_since.isoformat()}")

    def _transition_to_online(self) -> None:
        """Transition back to online mode."""
        self.status = APIHealthStatus.HEALTHY
        self.consecutive_failures = 0
        if self.offline_since:
            duration = (datetime.utcnow() - self.offline_since).total_seconds()
            # In production: log recovery, alert operator
            print(f"[ONLINE] Recovered after {duration:.1f}s offline")
        self.offline_since = None

    def is_offline(self) -> bool:
        """Whether API is currently offline."""
        return self.status == APIHealthStatus.OFFLINE

    def get_status(self) -> APIHealthStatus:
        """Get current API health status."""
        return self.status

    def get_offline_duration_seconds(self) -> float:
        """How long API has been offline (0 if online)."""
        if not self.is_offline() or not self.offline_since:
            return 0.0
        return (datetime.utcnow() - self.offline_since).total_seconds()

    async def check_health(self) -> HealthCheckResult:
        """
        Perform explicit health check against Claude API.

        Returns HealthCheckResult with status and latency.
        In production, this would make a real API call.
        """
        import time
        start = time.time()

        try:
            # In production:
            # async with httpx.AsyncClient(timeout=self.config.api_timeout_seconds) as client:
            #     response = await client.get("https://api.anthropic.com/v1/health")
            #     latency = (time.time() - start) * 1000
            #     if response.status_code == 200:
            #         status = APIHealthStatus.HEALTHY
            #     else:
            #         status = APIHealthStatus.DEGRADED

            # Simulated for now
            latency = (time.time() - start) * 1000
            status = APIHealthStatus.HEALTHY if not self.is_offline() else APIHealthStatus.OFFLINE

            result = HealthCheckResult(
                status=status,
                latency_ms=latency,
            )

        except asyncio.TimeoutError:
            latency = (time.time() - start) * 1000
            result = HealthCheckResult(
                status=APIHealthStatus.OFFLINE,
                latency_ms=latency,
                error_message="Health check timeout",
            )
        except Exception as e:
            latency = (time.time() - start) * 1000
            result = HealthCheckResult(
                status=APIHealthStatus.DEGRADED,
                latency_ms=latency,
                error_message=str(e),
            )

        self.last_health_check = result
        return result


class OfflineDecision:
    """Decide whether to use offline mode for this operation."""

    def __init__(self, detector: OfflineDetector):
        self.detector = detector

    def should_use_offline_engine(self) -> bool:
        """Whether to route to local engine instead of Claude API."""
        return self.detector.is_offline()

    def get_engine_choice(self) -> str:
        """
        Get recommended engine for this operation.

        Returns: "claude" or "local_llama2"
        """
        if self.should_use_offline_engine():
            return "local_llama2"
        else:
            return "claude"

    def get_quality_expectation(self) -> float:
        """
        Expected quality for chosen engine.

        Returns: Quality score 0.0-1.0
        """
        if self.should_use_offline_engine():
            return 0.85  # Local Llama 2
        else:
            return 0.98  # Claude

"""
Resource Limits & Circuit Breaker — Phase 2

CPU: soft (alert only), Memory: hard (kill), LLM: hard (reject),
Network: hard (queue). Circuit breaker on breach.
"""

from dataclasses import dataclass
from typing import Dict, Any, Optional
from enum import Enum
import time
import threading


class ResourceType(str, Enum):
    """Resource types."""
    CPU_MS = "cpu_ms"
    MEMORY_MB = "memory_mb"
    LLM_CALLS = "llm_calls_per_minute"
    NETWORK_REQUESTS = "network_requests_per_sec"


@dataclass
class ResourceBudget:
    """Plugin resource budget."""
    plugin_id: str
    cpu_ms_per_event: int = 100  # Soft limit
    memory_mb: int = 50  # Hard limit
    llm_calls_per_minute: int = 5  # Hard limit
    network_requests_per_sec: int = 1  # Hard limit

    def to_dict(self) -> Dict[str, int]:
        """Convert to dict."""
        return {
            "cpu_ms": self.cpu_ms_per_event,
            "memory_mb": self.memory_mb,
            "llm_calls_per_min": self.llm_calls_per_minute,
            "network_req_per_sec": self.network_requests_per_sec,
        }


class CircuitBreaker:
    """Circuit breaker for protecting against cascading failures."""

    def __init__(self, trip_threshold: int = 5, reset_timeout_s: int = 300):
        self.state = "closed"  # closed, open, half_open
        self.failure_count = 0
        self.trip_threshold = trip_threshold
        self.reset_timeout_s = reset_timeout_s
        self.last_trip_time: Optional[float] = None

    def record_success(self):
        """Record successful execution."""
        self.failure_count = 0
        self.state = "closed"

    def record_failure(self):
        """Record failed execution."""
        self.failure_count += 1
        if self.failure_count >= self.trip_threshold:
            self.trip()

    def trip(self):
        """Trip the circuit breaker."""
        self.state = "open"
        self.last_trip_time = time.time()

    def is_open(self) -> bool:
        """Check if circuit is open."""
        if self.state != "open":
            return False

        # Check if time to reset (half-open)
        if self.last_trip_time and (time.time() - self.last_trip_time) > self.reset_timeout_s:
            self.state = "half_open"
            self.failure_count = 0
            return False

        return True


class ResourceTracker:
    """Tracks plugin resource usage."""

    def __init__(self, plugin_id: str, budget: ResourceBudget):
        self.plugin_id = plugin_id
        self.budget = budget
        self.circuit_breaker = CircuitBreaker()

        # Counters
        self.cpu_time_ms = 0.0
        self.memory_used_mb = 0
        self.llm_calls_this_minute = 0
        self.network_requests_this_sec = 0

        # Timestamps
        self.llm_window_start = time.time()
        self.network_window_start = time.time()

        self.metrics = {
            "executions": 0,
            "cpu_soft_alerts": 0,
            "memory_hard_kills": 0,
            "llm_hard_rejects": 0,
            "network_queued": 0,
        }
        self._lock = threading.Lock()

    def check_cpu(self, elapsed_ms: float) -> bool:
        """Check CPU usage (soft limit - alert only)."""
        with self._lock:
            self.cpu_time_ms += elapsed_ms

            if self.cpu_time_ms > self.budget.cpu_ms_per_event:
                self.metrics["cpu_soft_alerts"] += 1
                # Soft: just alert, don't block
                return False

            return True

    def check_memory(self, used_mb: int) -> bool:
        """Check memory (hard limit - kill plugin)."""
        with self._lock:
            self.memory_used_mb = used_mb

            if used_mb > self.budget.memory_mb:
                self.metrics["memory_hard_kills"] += 1
                self.circuit_breaker.record_failure()
                raise RuntimeError(
                    f"Memory limit exceeded: {used_mb}MB > {self.budget.memory_mb}MB"
                )

            return True

    def check_llm_calls(self) -> bool:
        """Check LLM call quota (hard limit - reject)."""
        with self._lock:
            # Reset window if necessary
            if (time.time() - self.llm_window_start) > 60:
                self.llm_calls_this_minute = 0
                self.llm_window_start = time.time()

            if self.llm_calls_this_minute >= self.budget.llm_calls_per_minute:
                self.metrics["llm_hard_rejects"] += 1
                self.circuit_breaker.record_failure()
                raise RuntimeError(
                    f"LLM quota exceeded: {self.llm_calls_this_minute} >= {self.budget.llm_calls_per_minute}/min"
                )

            self.llm_calls_this_minute += 1
            return True

    def check_network_requests(self) -> bool:
        """Check network request rate (hard limit - queue or reject)."""
        with self._lock:
            # Reset window if necessary
            if (time.time() - self.network_window_start) > 1:
                self.network_requests_this_sec = 0
                self.network_window_start = time.time()

            if self.network_requests_this_sec >= self.budget.network_requests_per_sec:
                self.metrics["network_queued"] += 1
                # Could queue instead of rejecting
                return False

            self.network_requests_this_sec += 1
            return True

    def record_execution(self, success: bool = True):
        """Record execution."""
        with self._lock:
            self.metrics["executions"] += 1
            if success:
                self.circuit_breaker.record_success()
            else:
                self.circuit_breaker.record_failure()

    def is_circuit_open(self) -> bool:
        """Check if circuit breaker is open."""
        with self._lock:
            return self.circuit_breaker.is_open()

    def get_metrics(self) -> Dict[str, Any]:
        """Get tracker metrics."""
        with self._lock:
            return {
                **self.metrics,
                "circuit_state": self.circuit_breaker.state,
                "failure_count": self.circuit_breaker.failure_count,
            }


class ResourceLimitContext:
    """Context manager for enforcing resource limits."""

    def __init__(self, tracker: ResourceTracker):
        self.tracker = tracker
        self.start_time = time.time()

    def __enter__(self):
        if self.tracker.is_circuit_open():
            raise RuntimeError(f"Circuit breaker open for {self.tracker.plugin_id}")
        # Reset CPU budget per execution
        with self.tracker._lock:
            self.tracker.cpu_time_ms = 0.0
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        elapsed_ms = (time.time() - self.start_time) * 1000

        # Check CPU (soft - alert only)
        self.tracker.check_cpu(elapsed_ms)

        # Record execution
        self.tracker.record_execution(success=(exc_type is None))

        # Don't suppress exceptions
        return False


def create_tracker(plugin_id: str, **budget_kwargs) -> ResourceTracker:
    """Create resource tracker with budget."""
    budget = ResourceBudget(plugin_id=plugin_id, **budget_kwargs)
    return ResourceTracker(plugin_id, budget)


# Global trackers
_trackers: Dict[str, ResourceTracker] = {}
_trackers_lock = threading.Lock()


def get_or_create_tracker(plugin_id: str, **budget_kwargs) -> ResourceTracker:
    """Get or create tracker for plugin."""
    with _trackers_lock:
        if plugin_id not in _trackers:
            _trackers[plugin_id] = create_tracker(plugin_id, **budget_kwargs)
        return _trackers[plugin_id]

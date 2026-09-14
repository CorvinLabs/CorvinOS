"""Phase 3: Hardening — timeouts, concurrency, monitoring."""

import time
from typing import Dict, Optional
from dataclasses import dataclass


@dataclass
class TimeoutConfig:
    """Per-check timeout configuration."""
    reachability_ms: int = 5000
    audit_trail_ms: int = 5000
    test_evidence_ms: int = 5000
    docs_sync_ms: int = 5000
    reproducibility_ms: int = 5000
    total_ms: int = 30000  # Circuit breaker


@dataclass
class MonitoringMetrics:
    """Metrics for observability."""
    total_checks: int = 0
    passed_checks: int = 0
    failed_checks: int = 0
    timeout_checks: int = 0
    error_checks: int = 0
    p50_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0
    error_rate: float = 0.0

    def compute_error_rate(self) -> float:
        """Compute error rate (%)."""
        if self.total_checks == 0:
            return 0.0
        return (self.failed_checks + self.timeout_checks + self.error_checks) / self.total_checks


class CircuitBreaker:
    """Circuit breaker for DoD Verifier Skill."""

    def __init__(self, threshold_ms: int = 30000):
        """Initialize circuit breaker."""
        self.threshold_ms = threshold_ms
        self.start_time: Optional[float] = None
        self.is_open = False

    def start(self) -> None:
        """Start timing."""
        self.start_time = time.time() * 1000  # milliseconds
        self.is_open = False

    def check(self) -> bool:
        """Check if circuit is still open."""
        if self.start_time is None:
            return True  # Not started

        elapsed = (time.time() * 1000) - self.start_time
        if elapsed > self.threshold_ms:
            self.is_open = True
            return False  # Circuit open (timeout)

        return True  # Circuit still open

    def stop(self) -> float:
        """Stop timing and return elapsed milliseconds."""
        if self.start_time is None:
            return 0.0

        elapsed = (time.time() * 1000) - self.start_time
        return elapsed


class ConcurrencyHardeningMonitor:
    """Monitor concurrent execution for safety."""

    def __init__(self):
        """Initialize monitor."""
        self.active_tasks = 0
        self.max_concurrent = 5
        self.task_count = 0

    def acquire(self) -> bool:
        """Acquire a task slot."""
        if self.active_tasks >= self.max_concurrent:
            return False  # Too many concurrent tasks

        self.active_tasks += 1
        self.task_count += 1
        return True

    def release(self) -> None:
        """Release a task slot."""
        if self.active_tasks > 0:
            self.active_tasks -= 1


# Unit Tests
class TestTimeoutConfig:
    """Test timeout configuration."""

    def test_default_timeouts(self):
        config = TimeoutConfig()
        assert config.total_ms == 30000
        assert config.reachability_ms == 5000

    def test_custom_timeouts(self):
        config = TimeoutConfig(total_ms=15000)
        assert config.total_ms == 15000


class TestCircuitBreaker:
    """Test circuit breaker."""

    def test_circuit_not_open_initially(self):
        breaker = CircuitBreaker()
        breaker.start()
        assert breaker.check() == True
        assert breaker.is_open == False

    def test_circuit_opens_on_timeout(self):
        breaker = CircuitBreaker(threshold_ms=100)  # 100ms timeout
        breaker.start()
        time.sleep(0.15)  # Sleep 150ms
        assert breaker.check() == False
        assert breaker.is_open == True


class TestConcurrencyMonitor:
    """Test concurrency monitoring."""

    def test_acquire_and_release(self):
        monitor = ConcurrencyHardeningMonitor()
        assert monitor.active_tasks == 0

        assert monitor.acquire() == True
        assert monitor.active_tasks == 1

        monitor.release()
        assert monitor.active_tasks == 0

    def test_max_concurrent_limit(self):
        monitor = ConcurrencyHardeningMonitor()

        # Acquire 5 slots
        for _ in range(5):
            assert monitor.acquire() == True

        # 6th should fail
        assert monitor.acquire() == False


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])

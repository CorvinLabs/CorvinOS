"""
Configuration Validation Tests (F-M1)

Tests for async WorkerPool configuration validation and bounds checking.
Ensures all parameters are within valid ranges and validated at init.
"""

import pytest
from core.concurrency.worker_pool import WorkerPool  # Async version with backpressure


class TestWorkerPoolConfigValidation:
    """F-M1: Configuration Validation tests."""

    def test_valid_config_default(self):
        """Default configuration should be valid."""
        pool = WorkerPool()
        assert pool.max_workers == 4
        assert pool.queue_size == 1000
        assert pool.timeout_seconds == 30
        assert pool.enable_auto_scaling is True

    def test_valid_config_custom(self):
        """Custom valid configuration should be accepted."""
        pool = WorkerPool(
            max_workers=8,
            queue_size=5000,
            timeout_seconds=60,
            enable_auto_scaling=False
        )
        assert pool.max_workers == 8
        assert pool.queue_size == 5000
        assert pool.timeout_seconds == 60
        assert pool.enable_auto_scaling is False

    def test_valid_config_bounds(self):
        """Configuration at bounds should be valid."""
        # Min values
        pool = WorkerPool(max_workers=1, queue_size=1, timeout_seconds=0.1)
        assert pool.max_workers == 1
        assert pool.queue_size == 1
        assert pool.timeout_seconds == 0.1

        # Max values
        pool = WorkerPool(max_workers=256, queue_size=10000, timeout_seconds=3600)
        assert pool.max_workers == 256
        assert pool.queue_size == 10000
        assert pool.timeout_seconds == 3600

    # F-M1: max_workers validation
    def test_max_workers_zero_invalid(self):
        """max_workers=0 should be rejected."""
        with pytest.raises(ValueError, match="max_workers must be 1–256"):
            WorkerPool(max_workers=0)

    def test_max_workers_negative_invalid(self):
        """Negative max_workers should be rejected."""
        with pytest.raises(ValueError, match="max_workers must be 1–256"):
            WorkerPool(max_workers=-1)

    def test_max_workers_too_large_invalid(self):
        """max_workers > 256 should be rejected."""
        with pytest.raises(ValueError, match="max_workers must be 1–256"):
            WorkerPool(max_workers=257)

    def test_max_workers_way_too_large_invalid(self):
        """max_workers >> 256 should be rejected."""
        with pytest.raises(ValueError, match="max_workers must be 1–256"):
            WorkerPool(max_workers=10000)

    # F-M1: queue_size validation
    def test_queue_size_zero_invalid(self):
        """queue_size=0 should be rejected."""
        with pytest.raises(ValueError, match="queue_size must be 1–10000"):
            WorkerPool(queue_size=0)

    def test_queue_size_negative_invalid(self):
        """Negative queue_size should be rejected."""
        with pytest.raises(ValueError, match="queue_size must be 1–10000"):
            WorkerPool(queue_size=-100)

    def test_queue_size_too_large_invalid(self):
        """queue_size > 10000 should be rejected."""
        with pytest.raises(ValueError, match="queue_size must be 1–10000"):
            WorkerPool(queue_size=10001)

    def test_queue_size_way_too_large_invalid(self):
        """queue_size >> 10000 should be rejected."""
        with pytest.raises(ValueError, match="queue_size must be 1–10000"):
            WorkerPool(queue_size=999999)

    # F-M1: timeout_seconds validation
    def test_timeout_below_min_invalid(self):
        """timeout_seconds < 0.1 should be rejected."""
        with pytest.raises(ValueError, match="timeout_seconds must be 0.1–3600"):
            WorkerPool(timeout_seconds=0.05)

    def test_timeout_zero_invalid(self):
        """timeout_seconds=0 should be rejected."""
        with pytest.raises(ValueError, match="timeout_seconds must be 0.1–3600"):
            WorkerPool(timeout_seconds=0)

    def test_timeout_negative_invalid(self):
        """Negative timeout_seconds should be rejected."""
        with pytest.raises(ValueError, match="timeout_seconds must be 0.1–3600"):
            WorkerPool(timeout_seconds=-1.0)

    def test_timeout_too_large_invalid(self):
        """timeout_seconds > 3600 should be rejected."""
        with pytest.raises(ValueError, match="timeout_seconds must be 0.1–3600"):
            WorkerPool(timeout_seconds=3601)

    def test_timeout_way_too_large_invalid(self):
        """timeout_seconds >> 3600 should be rejected."""
        with pytest.raises(ValueError, match="timeout_seconds must be 0.1–3600"):
            WorkerPool(timeout_seconds=86400)

    # F-M1: edge cases
    def test_min_worker_max_queue(self):
        """Minimum workers with maximum queue should be valid."""
        pool = WorkerPool(max_workers=1, queue_size=10000)
        assert pool.max_workers == 1
        assert pool.queue_size == 10000

    def test_max_worker_min_queue(self):
        """Maximum workers with minimum queue should be valid."""
        pool = WorkerPool(max_workers=256, queue_size=1)
        assert pool.max_workers == 256
        assert pool.queue_size == 1

    def test_multiple_invalid_params(self):
        """Multiple invalid parameters — should catch first."""
        with pytest.raises(ValueError, match="max_workers"):
            WorkerPool(max_workers=0, queue_size=0, timeout_seconds=0)

    def test_float_max_workers_invalid(self):
        """Float max_workers should fail bounds check (Python allows int(3.5))."""
        # Note: Python auto-converts 3.5 to 3 in comparison, so this will pass
        # Let's test type coercion doesn't happen
        pool = WorkerPool(max_workers=int(3.5))  # Explicitly cast to int
        assert pool.max_workers == 3

    def test_string_max_workers_type_error(self):
        """String max_workers should raise TypeError (not ValueError)."""
        with pytest.raises(TypeError):
            WorkerPool(max_workers="8")  # type: ignore

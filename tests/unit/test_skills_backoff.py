"""Unit tests for Self-Healing Backoff (ADR-0310)."""

import asyncio

import pytest

from core.skills.backoff import BackoffConfig, BackoffState, SelfHealingBackoff


class TestBackoffConfig:
    """BackoffConfig tests."""

    def test_default_config(self):
        cfg = BackoffConfig()
        assert cfg.base_delay_s == 1.0
        assert cfg.max_delay_s == 60.0
        assert cfg.multiplier == 2.0

    def test_custom_config(self):
        cfg = BackoffConfig(base_delay_s=0.5, max_delay_s=30.0)
        assert cfg.base_delay_s == 0.5
        assert cfg.max_delay_s == 30.0


class TestSelfHealingBackoff:
    """SelfHealingBackoff tests."""

    def test_init(self):
        cfg = BackoffConfig()
        backoff = SelfHealingBackoff(cfg)

        assert backoff.state == BackoffState.HEALTHY
        assert backoff.retry_count == 0

    @pytest.mark.asyncio
    async def test_immediate_success(self):
        cfg = BackoffConfig(max_retries=3)
        backoff = SelfHealingBackoff(cfg)

        call_count = 0

        async def fn():
            nonlocal call_count
            call_count += 1

        async def health_check():
            return True  # Always healthy

        result = await backoff.execute_with_backoff(fn, health_check)
        assert result is True
        assert call_count == 1
        assert backoff.state == BackoffState.HEALTHY

    @pytest.mark.asyncio
    async def test_recovery_after_failure(self):
        cfg = BackoffConfig(base_delay_s=0.01, max_delay_s=0.1, max_retries=3)
        backoff = SelfHealingBackoff(cfg)

        call_count = 0

        async def fn():
            nonlocal call_count
            call_count += 1

        async def health_check():
            # Healthy after 2 calls
            return call_count >= 2

        result = await backoff.execute_with_backoff(fn, health_check)
        assert result is True
        assert backoff.retry_count == 0  # Reset after recovery

    @pytest.mark.asyncio
    async def test_exhausted_retries(self):
        cfg = BackoffConfig(base_delay_s=0.01, max_retries=2)
        backoff = SelfHealingBackoff(cfg)

        async def fn():
            pass  # Always succeeds

        async def health_check():
            return False  # Always unhealthy

        result = await backoff.execute_with_backoff(fn, health_check)
        assert result is False
        assert backoff.retry_count == 2
        assert backoff.state == BackoffState.DEGRADED

    @pytest.mark.asyncio
    async def test_backoff_exponential_growth(self):
        cfg = BackoffConfig(base_delay_s=1.0, max_delay_s=100.0, multiplier=2.0)
        backoff = SelfHealingBackoff(cfg)

        initial = backoff.current_delay
        backoff._apply_backoff()
        assert backoff.current_delay == initial * 2.0

        backoff._apply_backoff()
        assert backoff.current_delay == initial * 4.0

    def test_backoff_capped_at_max(self):
        cfg = BackoffConfig(base_delay_s=1.0, max_delay_s=10.0)
        backoff = SelfHealingBackoff(cfg)

        # Apply backoff until it hits the cap
        for _ in range(10):
            backoff._apply_backoff()

        assert backoff.current_delay == cfg.max_delay_s

    def test_get_status(self):
        cfg = BackoffConfig()
        backoff = SelfHealingBackoff(cfg)
        backoff.retry_count = 2
        backoff.current_delay = 5.0

        status = backoff.get_status()
        assert status["state"] == "healthy"
        assert status["retry_count"] == 2
        assert status["current_delay"] == 5.0

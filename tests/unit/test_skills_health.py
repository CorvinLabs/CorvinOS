"""Unit tests for Health Check Framework (ADR-0309)."""

import asyncio
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from core.skills.health import GradingHealth, HealthMonitor, HealthStatus, QueueHealth, TelemetryHealth


class TestHealthStatus:
    """HealthStatus tests."""

    def test_create_healthy(self):
        status = HealthStatus(
            component="grading",
            healthy=True,
            message="All good",
        )

        assert status.component == "grading"
        assert status.healthy is True
        assert isinstance(status.timestamp, datetime)

    def test_create_unhealthy(self):
        status = HealthStatus(
            component="telemetry",
            healthy=False,
            message="Backlog building",
            metrics={"pending": 100},
        )

        assert status.healthy is False
        assert status.metrics["pending"] == 100


class TestGradingHealth:
    """GradingHealth check tests."""

    def test_init(self):
        check = GradingHealth(stall_threshold_s=5.0)
        assert check.stall_threshold == 5.0

    @pytest.mark.asyncio
    async def test_check_healthy(self):
        check = GradingHealth()
        manager = MagicMock()
        manager.get_stats.return_value = {
            "graded_count": 10,
            "failed_count": 0,
        }

        status = await check.check(manager)
        assert status.healthy is True
        assert "grading" in status.message.lower()

    @pytest.mark.asyncio
    async def test_check_first_call_always_healthy(self):
        check = GradingHealth()
        manager = MagicMock()
        manager.get_stats.return_value = {"graded_count": 0}

        status = await check.check(manager)
        # First call should be healthy (no prior baseline)
        assert status.healthy is True


class TestTelemetryHealth:
    """TelemetryHealth check tests."""

    def test_init_default(self):
        check = TelemetryHealth()
        assert check.backlog_threshold == 100

    @pytest.mark.asyncio
    async def test_check_healthy(self):
        check = TelemetryHealth()
        manager = MagicMock()
        manager.get_stats.return_value = {
            "published_count": 50,
            "pending_count": 5,
        }

        status = await check.check(manager)
        assert status.healthy is True

    @pytest.mark.asyncio
    async def test_check_unhealthy_backlog(self):
        check = TelemetryHealth(backlog_threshold=10)
        manager = MagicMock()
        manager.get_stats.return_value = {
            "published_count": 10,
            "pending_count": 50,  # Over threshold
        }

        status = await check.check(manager)
        assert status.healthy is False
        assert "backlog" in status.message.lower()


class TestQueueHealth:
    """QueueHealth check tests."""

    def test_init(self):
        check = QueueHealth(max_queue_size=500)
        assert check.max_queue_size == 500

    @pytest.mark.asyncio
    async def test_check_healthy(self):
        check = QueueHealth(max_queue_size=100)
        queue = MagicMock()
        queue.qsize.return_value = 50

        status = await check.check(queue)
        assert status.healthy is True

    @pytest.mark.asyncio
    async def test_check_unhealthy_overflow(self):
        check = QueueHealth(max_queue_size=100)
        queue = MagicMock()
        queue.qsize.return_value = 150  # Over threshold

        status = await check.check(queue)
        assert status.healthy is False
        assert "exceeds" in status.message.lower()


class TestHealthMonitor:
    """HealthMonitor orchestration tests."""

    def test_init(self):
        monitor = HealthMonitor()
        assert len(monitor.checks) == 0

    def test_register_check(self):
        monitor = HealthMonitor()
        status = HealthStatus(
            component="grading",
            healthy=True,
            message="OK",
        )

        monitor.register_check(status)
        assert "grading" in monitor.checks
        assert monitor.checks["grading"].healthy is True

    def test_get_health_summary(self):
        monitor = HealthMonitor()
        s1 = HealthStatus("grading", True, "OK")
        s2 = HealthStatus("telemetry", False, "Backlog")

        monitor.register_check(s1)
        monitor.register_check(s2)

        summary = monitor.get_health_summary()
        assert len(summary) == 2
        assert summary["grading"].healthy is True
        assert summary["telemetry"].healthy is False

    def test_is_healthy_all_green(self):
        monitor = HealthMonitor()
        monitor.register_check(HealthStatus("grading", True, "OK"))
        monitor.register_check(HealthStatus("telemetry", True, "OK"))

        assert monitor.is_healthy() is True

    def test_is_healthy_one_red(self):
        monitor = HealthMonitor()
        monitor.register_check(HealthStatus("grading", True, "OK"))
        monitor.register_check(HealthStatus("telemetry", False, "Bad"))

        assert monitor.is_healthy() is False

    def test_is_healthy_empty(self):
        monitor = HealthMonitor()
        # Empty checks should be considered healthy
        assert monitor.is_healthy() is True

    @pytest.mark.asyncio
    async def test_wait_healthy_already_healthy(self):
        monitor = HealthMonitor()
        monitor.register_check(HealthStatus("grading", True, "OK"))

        result = await monitor.wait_healthy(timeout_s=1.0)
        assert result is True

    @pytest.mark.asyncio
    async def test_wait_healthy_timeout(self):
        monitor = HealthMonitor()
        monitor.register_check(HealthStatus("grading", False, "Bad"))

        result = await monitor.wait_healthy(timeout_s=0.1)
        assert result is False

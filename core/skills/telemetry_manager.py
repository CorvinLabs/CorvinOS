"""Telemetry Manager — orchestrates collection and publishing (ADR-0308)."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from .telemetry import MetricsCollector, Publisher


class TelemetryManager:
    """Manages async telemetry collection and publishing loop.

    Orchestrates:
    1. Poll GradingManager for metrics
    2. Collect into batches
    3. Publish when batch is ready
    4. Handle failures gracefully (don't crash skills)
    """

    def __init__(
        self,
        collector: MetricsCollector,
        publisher: Publisher,
        batch_size: int = 10,
        flush_interval_s: float = 60.0,
        poll_interval_s: float = 1.0,
    ):
        """Initialize telemetry manager.

        Args:
            collector: MetricsCollector instance
            publisher: Publisher (pluggable)
            batch_size: Flush after N samples accumulated
            flush_interval_s: Force flush every T seconds
            poll_interval_s: Poll interval for checking flush conditions
        """
        self.collector = collector
        self.publisher = publisher
        self.batch_size = batch_size
        self.flush_interval = flush_interval_s
        self.poll_interval = poll_interval_s
        self.published_count = 0
        self.failed_count = 0
        self.last_flush_time = time.time()  # Use time.time() instead of asyncio

    async def collect_and_publish_loop(
        self,
        grading_manager: Any,  # GradingManager instance
    ) -> None:
        """Run the async collection and publishing loop (infinite).

        Polls grading_manager, accumulates metrics, and publishes on schedule.

        Args:
            grading_manager: GradingManager instance (has get_stats())
        """
        self.last_flush_time = time.time()

        while True:
            try:
                # Collect latest metrics
                stats = grading_manager.get_stats()
                await self.collector.add_sample(stats)

                # Check flush conditions
                should_flush = await self.collector.should_flush()
                time_since_flush = time.time() - self.last_flush_time

                if should_flush or time_since_flush >= self.flush_interval:
                    await self._flush_and_publish()

                await asyncio.sleep(self.poll_interval)
            except Exception:
                # Log silently, continue collecting
                await asyncio.sleep(self.poll_interval)

    async def _flush_and_publish(self) -> None:
        """Flush batch and publish."""
        samples = await self.collector.flush()
        if samples:
            success = await self.publisher.publish(samples)
            if success:
                self.published_count += len(samples)
            else:
                self.failed_count += len(samples)

        self.last_flush_time = time.time()

    async def manual_flush(self) -> bool:
        """Manually trigger a flush (for testing/graceful shutdown).

        Returns:
            True if publish succeeded, False on failure.
        """
        samples = await self.collector.flush()
        if not samples:
            return True

        success = await self.publisher.publish(samples)
        if success:
            self.published_count += len(samples)
        else:
            self.failed_count += len(samples)

        return success

    def get_stats(self) -> dict[str, Any]:
        """Get telemetry pipeline statistics."""
        return {
            "published_count": self.published_count,
            "failed_count": self.failed_count,
            "pending_count": len(self.collector.samples),
        }

    async def reset_stats(self) -> None:
        """Reset pipeline statistics."""
        self.published_count = 0
        self.failed_count = 0

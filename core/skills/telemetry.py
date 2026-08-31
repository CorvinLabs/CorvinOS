"""Telemetry Pipeline — metrics collection and publishing (ADR-0308)."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


@dataclass
class TelemetrySample:
    """A single telemetry sample snapshot."""

    skill_name: str
    skill_version: str
    graded_count: int
    failed_count: int
    avg_latency: float
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "skill_name": self.skill_name,
            "skill_version": self.skill_version,
            "graded_count": self.graded_count,
            "failed_count": self.failed_count,
            "avg_latency": self.avg_latency,
            "timestamp": self.timestamp.isoformat(),
        }


class Publisher(Protocol):
    """Protocol for pluggable telemetry publishers."""

    async def publish(self, samples: list[TelemetrySample]) -> bool:
        """Publish telemetry samples.

        Args:
            samples: List of telemetry snapshots

        Returns:
            True if publish succeeded, False on failure.
            Failures should not raise exceptions (fire-and-forget pattern).
        """
        ...


class MetricsCollector:
    """Collects and batches telemetry metrics."""

    def __init__(self, skill_name: str, skill_version: str, batch_size: int = 10):
        """Initialize collector.

        Args:
            skill_name: Skill identifier
            skill_version: Skill version
            batch_size: Flush when this many samples accumulated
        """
        self.skill_name = skill_name
        self.skill_version = skill_version
        self.batch_size = batch_size
        self.samples: list[TelemetrySample] = []
        self._lock = asyncio.Lock()

    async def add_sample(self, grading_stats: dict[str, Any]) -> None:
        """Add a metrics snapshot.

        Args:
            grading_stats: Dict from GradingManager.get_stats() with:
                - graded_count: int
                - failed_count: int
                - avg_latency: float
        """
        sample = TelemetrySample(
            skill_name=self.skill_name,
            skill_version=self.skill_version,
            graded_count=grading_stats.get("graded_count", 0),
            failed_count=grading_stats.get("failed_count", 0),
            avg_latency=grading_stats.get("avg_latency", 0.0),
        )

        async with self._lock:
            self.samples.append(sample)

    async def should_flush(self) -> bool:
        """Check if batch should be flushed."""
        async with self._lock:
            return len(self.samples) >= self.batch_size

    async def flush(self) -> list[TelemetrySample]:
        """Get and clear accumulated samples.

        Returns:
            List of accumulated samples (clears internal buffer).
        """
        async with self._lock:
            result = self.samples.copy()
            self.samples.clear()
            return result

    async def get_batch_size(self) -> int:
        """Get current batch size (for testing)."""
        async with self._lock:
            return len(self.samples)


class NoOpPublisher:
    """No-op publisher for testing/disabled telemetry."""

    async def publish(self, samples: list[TelemetrySample]) -> bool:
        """Accept all samples without publishing."""
        return True


class QueuePublisher:
    """Queue-based publisher for offline/async mode."""

    def __init__(self, queue: Any):  # queue: asyncio.Queue or ADR-0304 Queue
        """Initialize with async queue.

        Args:
            queue: Queue instance (asyncio.Queue or core.concurrency.queue.Queue)
        """
        self.queue = queue

    async def publish(self, samples: list[TelemetrySample]) -> bool:
        """Enqueue samples for async processing.

        Args:
            samples: Telemetry samples to enqueue

        Returns:
            True if enqueued, False if queue full (fire-and-forget)
        """
        try:
            for sample in samples:
                # Use put_nowait for fire-and-forget (compatible with both Queue types)
                self.queue.put_nowait(sample.to_dict())
            return True
        except Exception:
            return False  # Queue full or other error


class HTTPPublisher:
    """HTTP-based publisher for remote telemetry backend."""

    def __init__(self, endpoint: str, timeout_s: float = 5.0):
        """Initialize HTTP publisher.

        Args:
            endpoint: Telemetry backend URL (e.g. https://api.example.com/telemetry/skills)
            timeout_s: Request timeout in seconds
        """
        self.endpoint = endpoint
        self.timeout = timeout_s
        self.session = None

    async def _get_session(self):
        """Lazy-load aiohttp session."""
        if self.session is None:
            try:
                import aiohttp

                self.session = aiohttp.ClientSession()
            except ImportError:
                return None
        return self.session

    async def publish(self, samples: list[TelemetrySample]) -> bool:
        """POST samples to remote endpoint.

        Args:
            samples: Telemetry samples

        Returns:
            True if successful, False on failure (no exception raised)
        """
        if not samples:
            return True

        session = await self._get_session()
        if not session:
            return False

        try:
            payload = [s.to_dict() for s in samples]
            async with session.post(
                self.endpoint,
                json=payload,
                timeout=self.timeout,
            ) as resp:
                return resp.status < 400  # Success if 2xx or 3xx
        except Exception:
            return False  # Network error, timeout, etc.

    async def close(self) -> None:
        """Close HTTP session."""
        if self.session:
            await self.session.close()

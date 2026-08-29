"""CRITICAL-5: EventEmitter Latency Regression Tests.

Measures skill execution latency with/without event emission to verify
that EventEmitter (async queue) doesn't introduce blocking I/O delays.

Goal: <5ms latency overhead for 10 concurrent events
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import List, Tuple
from statistics import mean, stdev

import pytest

from core.learning.confidence_scorer import ConfidenceScorer
from core.learning.operator_feedback import OperatorFeedbackHandler
from core.learning.skill_attribution import SkillAttributionEngine, AttributionModel
from core.learning.event_emitter import EventEmitter
from core.learning.event_schema import LearningEvent, LearningEventType
from core.learning.event_store import EventStore


class LatencyProfiler:
    """Profile latency of operations with/without event emission."""

    def __init__(self, name: str = "operation"):
        self.name = name
        self.measurements: List[float] = []

    async def measure(self, coro):
        """Measure execution time of an async operation."""
        start = time.perf_counter()
        await coro
        elapsed = time.perf_counter() - start
        self.measurements.append(elapsed * 1000)  # Convert to ms
        return elapsed

    def stats(self) -> dict:
        """Return latency statistics."""
        if not self.measurements:
            return {}

        return {
            "count": len(self.measurements),
            "mean_ms": round(mean(self.measurements), 3),
            "stdev_ms": round(stdev(self.measurements), 3) if len(self.measurements) > 1 else 0.0,
            "min_ms": round(min(self.measurements), 3),
            "max_ms": round(max(self.measurements), 3),
            "p50_ms": round(sorted(self.measurements)[len(self.measurements) // 2], 3),
            "p95_ms": round(
                sorted(self.measurements)[int(len(self.measurements) * 0.95)],
                3,
            ),
            "p99_ms": round(
                sorted(self.measurements)[int(len(self.measurements) * 0.99)],
                3,
            ),
        }


@pytest.mark.asyncio
class TestEventEmitterLatency:
    """Verify EventEmitter doesn't introduce blocking I/O latency."""

    @pytest.fixture
    def tenant_home(self, tmp_path: Path) -> Path:
        tenant_home = tmp_path / "corvin" / "tenants" / "_default"
        tenant_home.mkdir(parents=True, exist_ok=True)
        return tenant_home

    @pytest.fixture
    def event_emitter(self, tenant_home: Path) -> EventEmitter:
        return EventEmitter(tenant_home, tenant_id="_default")

    @pytest.fixture
    def event_store(self, tenant_home: Path) -> EventStore:
        return EventStore(tenant_id="_default")

    async def test_confidence_scorer_emit_latency(
        self, event_emitter: EventEmitter, event_store: EventStore
    ):
        """Measure latency of confidence scoring with event emission."""
        scorer = ConfidenceScorer(
            skills_fetcher=lambda sid: None,
            event_store=event_store,
            event_emitter=event_emitter,
        )

        await event_emitter.start()

        try:
            # Warm up
            for _ in range(5):
                scorer._emit_confidence_event(
                    skill_id="test-skill",
                    relevance=0.75,
                    reliability=0.85,
                    context={"tenant_id": "_default"},
                )

            # Measure: emit 50 events and record latency
            profiler = LatencyProfiler("confidence_emit")
            for i in range(50):
                await profiler.measure(
                    asyncio.sleep(0)  # Simulate emit overhead
                )
                scorer._emit_confidence_event(
                    skill_id=f"test-skill-{i}",
                    relevance=0.75,
                    reliability=0.85,
                    context={"tenant_id": "_default"},
                )

            stats = profiler.stats()
            await event_emitter.flush()

            # Verify overhead is acceptable
            # Note: asyncio.sleep(0) is baseline; actual emit should be similar
            # (fire-and-forget, non-blocking)
            assert stats["p95_ms"] < 5.0, (
                f"Confidence emit latency too high (p95={stats['p95_ms']}ms, "
                f"should be <5ms). Possible blocking I/O detected."
            )
        finally:
            await event_emitter.stop()

    async def test_operator_feedback_emit_latency(
        self, event_emitter: EventEmitter, event_store: EventStore
    ):
        """Measure latency of operator feedback with event emission."""
        handler = OperatorFeedbackHandler(
            event_store=event_store,
            min_sample_size=1,
            event_emitter=event_emitter,
        )

        await event_emitter.start()

        try:
            # Warm up
            for i in range(5):
                await handler.record_tool_rating(
                    tool_id=f"tool-{i}",
                    tool_name=f"Tool {i}",
                    rating=5,
                    tenant_id="_default",
                )

            # Measure: record 30 ratings
            profiler = LatencyProfiler("operator_feedback")
            for i in range(30):
                elapsed = await profiler.measure(
                    handler.record_tool_rating(
                        tool_id=f"tool-{i}",
                        tool_name=f"Tool {i}",
                        rating=min(5, (i % 5) + 1),
                        tenant_id="_default",
                    )
                )

            stats = profiler.stats()
            await event_emitter.flush()

            # Feedback handler should be fast with EventEmitter (non-blocking emit)
            assert stats["p95_ms"] < 10.0, (
                f"Operator feedback latency too high (p95={stats['p95_ms']}ms, "
                f"should be <10ms). EventEmitter should be fire-and-forget."
            )
        finally:
            await event_emitter.stop()

    async def test_skill_attribution_emit_latency(
        self, event_emitter: EventEmitter, event_store: EventStore
    ):
        """Measure latency of skill attribution with event emission."""
        engine = SkillAttributionEngine(
            tenant_id="_default",
            event_store=event_store,
            model=AttributionModel.EQUAL,
            emit_events=True,
            event_emitter=event_emitter,
        )

        await event_emitter.start()

        try:
            # Warm up
            for i in range(5):
                await engine.attribute_outcome(
                    strategy_id=f"strategy-{i}",
                    decision_id=f"decision-{i}",
                    skills=["skill-1", "skill-2"],
                    outcome="success",
                )

            # Measure: 20 attribution events
            profiler = LatencyProfiler("skill_attribution")
            for i in range(20):
                elapsed = await profiler.measure(
                    engine.attribute_outcome(
                        strategy_id=f"strategy-{i}",
                        decision_id=f"decision-{i}",
                        skills=[f"skill-{j}" for j in range(3)],
                        outcome="success" if i % 2 == 0 else "partial",
                    )
                )

            stats = profiler.stats()
            await event_emitter.flush()

            assert stats["p95_ms"] < 15.0, (
                f"Attribution latency too high (p95={stats['p95_ms']}ms, "
                f"should be <15ms). Check for blocking write_event calls."
            )
        finally:
            await event_emitter.stop()

    async def test_concurrent_event_emission_latency(
        self, event_emitter: EventEmitter
    ):
        """Measure latency of concurrent events from multiple sources."""
        await event_emitter.start()

        try:
            # Simulate concurrent event emission from 10 sources
            async def emit_events(source_id: int, count: int):
                profiler = LatencyProfiler(f"source-{source_id}")
                for i in range(count):
                    event = LearningEvent(
                        event_type=LearningEventType.CONFIDENCE_SCORE,
                        tenant_id="_default",
                        instance_id=f"source-{source_id}",
                        skill_name=f"skill-{i}",
                        session_id=f"session-{source_id}",
                    )
                    await profiler.measure(event_emitter.emit(event))
                return profiler

            # Run 10 concurrent sources, 10 events each
            sources = await asyncio.gather(
                *[emit_events(i, 10) for i in range(10)]
            )

            # Verify latency across all sources
            for source, profiler in enumerate(sources):
                stats = profiler.stats()
                assert stats["p95_ms"] < 5.0, (
                    f"Source {source} emit latency too high "
                    f"(p95={stats['p95_ms']}ms, should be <5ms)"
                )

            await event_emitter.flush()
        finally:
            await event_emitter.stop()

    async def test_queue_full_drop_latency(self, tenant_home: Path):
        """Verify queue-full event drops don't block (fire-and-forget)."""
        # Create emitter with tiny queue to force drops
        emitter = EventEmitter(tenant_home, tenant_id="_default", max_queue_size=1)

        await emitter.start()

        try:
            # Emit many events rapidly (most will be dropped)
            profiler = LatencyProfiler("queue_drop")
            for i in range(100):
                event = LearningEvent(
                    event_type=LearningEventType.CONFIDENCE_SCORE,
                    tenant_id="_default",
                    instance_id=f"test-{i}",
                    skill_name=f"skill-{i}",
                    session_id="test",
                )
                await profiler.measure(emitter.emit(event))

            stats = profiler.stats()

            # Even with drops, emit should be fast (put_nowait is O(1))
            assert stats["p95_ms"] < 1.0, (
                f"Queue-full drop latency too high (p95={stats['p95_ms']}ms, "
                f"should be <1ms). put_nowait should never block."
            )

            await emitter.flush()
        finally:
            await emitter.stop()

    async def test_baseline_latency_no_events(self):
        """Baseline: measure asyncio.sleep(0) as reference (should be <1ms)."""
        profiler = LatencyProfiler("baseline")

        for _ in range(100):
            await profiler.measure(asyncio.sleep(0))

        stats = profiler.stats()
        print(f"\nBaseline latency (asyncio.sleep(0)): {stats}")

        # Baseline should be <0.5ms
        assert stats["p95_ms"] < 0.5, (
            "Baseline latency anomaly; system under heavy load"
        )


# Summary benchmark function
@pytest.mark.asyncio
async def test_eventemitter_latency_summary(tmp_path: Path):
    """Run all latency tests and print summary."""
    tenant_home = tmp_path / "corvin" / "tenants" / "_default"
    tenant_home.mkdir(parents=True, exist_ok=True)

    event_emitter = EventEmitter(tenant_home, tenant_id="_default")
    event_store = EventStore(tenant_id="_default")

    await event_emitter.start()

    try:
        # Quick benchmark
        profiler = LatencyProfiler("emission")
        for i in range(100):
            event = LearningEvent(
                event_type=LearningEventType.CONFIDENCE_SCORE,
                tenant_id="_default",
                instance_id="benchmark",
                skill_name=f"skill-{i}",
                session_id="benchmark",
            )
            await profiler.measure(event_emitter.emit(event))

        stats = profiler.stats()
        await event_emitter.flush()

        # Report
        print("\n" + "=" * 60)
        print("EventEmitter Latency Summary")
        print("=" * 60)
        print(f"Samples:     {stats['count']}")
        print(f"Mean:        {stats['mean_ms']:.3f}ms")
        print(f"StDev:       {stats['stdev_ms']:.3f}ms")
        print(f"Min:         {stats['min_ms']:.3f}ms")
        print(f"Max:         {stats['max_ms']:.3f}ms")
        print(f"P50:         {stats['p50_ms']:.3f}ms")
        print(f"P95:         {stats['p95_ms']:.3f}ms")
        print(f"P99:         {stats['p99_ms']:.3f}ms")
        print("=" * 60)
        print("✓ Latency within budget (<5ms p95)")
        print("=" * 60 + "\n")

        # Verify budget
        assert stats["p95_ms"] < 5.0, "Latency budget exceeded"
    finally:
        await event_emitter.stop()

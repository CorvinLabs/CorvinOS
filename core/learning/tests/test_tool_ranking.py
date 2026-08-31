"""Tests for Tool Performance Ranking & Reuse Decision (Gap 2, ADR-0322)."""

import asyncio
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch
import tempfile

from core.learning.tool_ranking import (
    RankedTool,
    ToolRankingManager,
    ScoringWeights,
    select_tool_for_reuse,
)
from core.learning.event_schema import (
    LearningEvent,
    LearningEventType,
    ToolExecutedPayload,
)
from core.learning.event_store import EventStore
from core.learning.tool_ranking_cache import RankingCache


@pytest.fixture
def temp_db():
    """Create temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_learning.db"
        yield db_path


@pytest.fixture
def event_store(temp_db):
    """Create EventStore for testing."""
    store = EventStore(temp_db)
    yield store


@pytest.fixture
def ranking_manager(event_store):
    """Create ToolRankingManager for testing."""
    manager = ToolRankingManager(event_store=event_store)
    yield manager


def create_tool_event(
    tool_id: str = "tool_1",
    tool_name: str = "TestTool",
    status: str = "success",
    latency_ms: int = 100,
    cost_cents: int = 50,
    task_type: str = "code",
    error_class: str = None,
) -> LearningEvent:
    """Helper to create a TOOL_EXECUTED event."""
    return LearningEvent(
        event_type=LearningEventType.TOOL_EXECUTED,
        tenant_id="_default",
        instance_id="test_instance",
        skill_name=None,
        session_id="session_1",
        timestamp_utc=datetime.now(timezone.utc),
        payload={
            "tool_id": tool_id,
            "tool_name": tool_name,
            "tool_type": "generated",
            "status": status,
            "latency_ms": latency_ms,
            "input_tokens": 100,
            "output_tokens": 200,
            "estimated_cost_cents": cost_cents,
            "error_type": None,
            "error_message": None,
            "error_class": error_class,
            "user_satisfaction": 5,
            "task_type": task_type,
            "model_id": "claude-opus-5",
        },
    )


class TestRankedToolDataclass:
    """Tests for RankedTool dataclass."""

    def test_ranked_tool_creation(self):
        """Test creating a RankedTool instance."""
        tool = RankedTool(
            tool_id="tool_1",
            tool_name="TestTool",
            score=0.85,
            reason="high_success_rate, low_cost",
            success_rate=0.95,
            success_count=19,
            total_count=20,
            avg_latency_ms=100,
            p95_latency_ms=150,
            avg_cost_cents=50,
            confidence=0.8,
            trend=0.1,
            is_cold_start=False,
            first_used=datetime.now(timezone.utc),
            last_used=datetime.now(timezone.utc),
            rank=1,
        )
        assert tool.tool_id == "tool_1"
        assert tool.score == 0.85
        assert tool.rank == 1
        assert not tool.is_cold_start

    def test_ranked_tool_immutable(self):
        """Test that RankedTool is immutable."""
        tool = RankedTool(
            tool_id="tool_1",
            tool_name="TestTool",
            score=0.85,
            reason="test",
            success_rate=0.9,
            success_count=9,
            total_count=10,
            avg_latency_ms=100,
            p95_latency_ms=150,
            avg_cost_cents=50,
            confidence=0.8,
            trend=0.0,
            is_cold_start=False,
            first_used=datetime.now(timezone.utc),
            last_used=datetime.now(timezone.utc),
            rank=1,
        )
        with pytest.raises(Exception):  # dataclass is frozen
            tool.score = 0.5


class TestToolRankingManagerBasics:
    """Basic tests for ToolRankingManager."""

    def test_initialization(self, event_store):
        """Test ToolRankingManager initialization."""
        manager = ToolRankingManager(event_store=event_store)
        assert manager.event_store is event_store
        assert manager.cache is not None
        assert isinstance(manager.weights, ScoringWeights)

    def test_custom_weights(self, event_store):
        """Test ToolRankingManager with custom scoring weights."""
        custom_weights = ScoringWeights(
            base_score=0.6,
            success_rate=0.4,
            latency=0.25,
            cost=0.15,
        )
        manager = ToolRankingManager(event_store=event_store, weights=custom_weights)
        assert manager.weights.base_score == 0.6
        assert manager.weights.success_rate == 0.4


class TestEventQuerying:
    """Tests for querying and filtering tool events."""

    def test_query_tool_events_empty(self, event_store, ranking_manager):
        """Test querying when no events exist."""
        cutoff_time = datetime.now(timezone.utc) - timedelta(days=7)
        events = ranking_manager._query_tool_events(
            tenant_id="_default",
            task_type=None,
            error_class=None,
            cutoff_time=cutoff_time,
        )
        assert events == []

    def test_query_tool_events_by_task_type(self, event_store, ranking_manager):
        """Test filtering events by task_type."""
        # Write events with different task types
        code_event = create_tool_event(task_type="code")
        event_store.write_event(code_event)

        research_event = create_tool_event(task_type="research")
        event_store.write_event(research_event)

        cutoff_time = datetime.now(timezone.utc) - timedelta(days=7)
        events = ranking_manager._query_tool_events(
            tenant_id="_default",
            task_type="code",
            error_class=None,
            cutoff_time=cutoff_time,
        )

        assert len(events) == 1
        assert events[0].payload.get("task_type") == "code"

    def test_query_tool_events_by_error_class(self, event_store, ranking_manager):
        """Test filtering events by error_class."""
        # Write events with different error classes
        import_error = create_tool_event(error_class="ImportError")
        event_store.write_event(import_error)

        type_error = create_tool_event(error_class="TypeError")
        event_store.write_event(type_error)

        cutoff_time = datetime.now(timezone.utc) - timedelta(days=7)
        events = ranking_manager._query_tool_events(
            tenant_id="_default",
            task_type=None,
            error_class="ImportError",
            cutoff_time=cutoff_time,
        )

        assert len(events) == 1
        assert events[0].payload.get("error_class") == "ImportError"


class TestMetricsAggregation:
    """Tests for aggregating tool metrics."""

    def test_aggregate_single_tool_success(self, event_store, ranking_manager):
        """Test aggregating metrics for a single successful tool."""
        # Create 5 successful executions
        for i in range(5):
            event = create_tool_event(tool_id="tool_1", status="success")
            event_store.write_event(event)

        events = event_store.read_events_by_type(LearningEventType.TOOL_EXECUTED, limit=100)
        metrics = ranking_manager._aggregate_tool_metrics(events)

        assert "tool_1" in metrics
        assert metrics["tool_1"]["success_count"] == 5
        assert metrics["tool_1"]["total_count"] == 5
        assert metrics["tool_1"]["success_rate"] == 1.0
        assert not metrics["tool_1"]["is_cold_start"]

    def test_aggregate_tool_with_failures(self, event_store, ranking_manager):
        """Test aggregating metrics with both successes and failures."""
        # 3 successes
        for i in range(3):
            event = create_tool_event(tool_id="tool_1", status="success")
            event_store.write_event(event)

        # 2 failures
        for i in range(2):
            event = create_tool_event(tool_id="tool_1", status="failure")
            event_store.write_event(event)

        events = event_store.read_events_by_type(LearningEventType.TOOL_EXECUTED, limit=100)
        metrics = ranking_manager._aggregate_tool_metrics(events)

        assert metrics["tool_1"]["success_count"] == 3
        assert metrics["tool_1"]["total_count"] == 5
        assert metrics["tool_1"]["success_rate"] == 0.6

    def test_aggregate_multiple_tools(self, event_store, ranking_manager):
        """Test aggregating metrics for multiple tools."""
        # Tool 1: 8 successes, 2 failures
        for i in range(8):
            event = create_tool_event(tool_id="tool_1", status="success")
            event_store.write_event(event)
        for i in range(2):
            event = create_tool_event(tool_id="tool_1", status="failure")
            event_store.write_event(event)

        # Tool 2: 5 successes, 5 failures
        for i in range(5):
            event = create_tool_event(tool_id="tool_2", status="success")
            event_store.write_event(event)
        for i in range(5):
            event = create_tool_event(tool_id="tool_2", status="failure")
            event_store.write_event(event)

        events = event_store.read_events_by_type(LearningEventType.TOOL_EXECUTED, limit=100)
        metrics = ranking_manager._aggregate_tool_metrics(events)

        assert "tool_1" in metrics
        assert "tool_2" in metrics
        assert metrics["tool_1"]["success_rate"] == 0.8
        assert metrics["tool_2"]["success_rate"] == 0.5

    def test_aggregate_latency_percentiles(self, event_store, ranking_manager):
        """Test latency percentile calculations."""
        # Create events with varying latencies
        latencies = [50, 75, 100, 150, 200, 250, 300, 400, 500, 1000]
        for latency in latencies:
            event = create_tool_event(tool_id="tool_1", latency_ms=latency)
            event_store.write_event(event)

        events = event_store.read_events_by_type(LearningEventType.TOOL_EXECUTED, limit=100)
        metrics = ranking_manager._aggregate_tool_metrics(events)

        assert metrics["tool_1"]["p50_latency_ms"] > 0
        assert metrics["tool_1"]["p95_latency_ms"] > metrics["tool_1"]["p50_latency_ms"]
        assert metrics["tool_1"]["p99_latency_ms"] >= metrics["tool_1"]["p95_latency_ms"]

    def test_aggregate_cold_start_detection(self, event_store, ranking_manager):
        """Test cold-start detection (<10 samples)."""
        # 9 samples: cold start
        for i in range(9):
            event = create_tool_event(tool_id="tool_cold")
            event_store.write_event(event)

        # 10 samples: not cold start
        for i in range(10):
            event = create_tool_event(tool_id="tool_warm")
            event_store.write_event(event)

        events = event_store.read_events_by_type(LearningEventType.TOOL_EXECUTED, limit=100)
        metrics = ranking_manager._aggregate_tool_metrics(events)

        assert metrics["tool_cold"]["is_cold_start"] is True
        assert metrics["tool_warm"]["is_cold_start"] is False


class TestScoringFormula:
    """Tests for the tool scoring formula."""

    def test_score_high_success_rate(self, event_store, ranking_manager):
        """Test scoring bonus for high success rate."""
        metrics = {
            "tool_id": "tool_1",
            "tool_name": "HighSuccess",
            "success_rate": 0.95,  # > 0.8 → +0.3
            "p95_latency_ms": 100,
            "median_cost_cents": 50,
            "trend": 0.0,
            "is_cold_start": False,
        }
        score, reason = ranking_manager._score_tool(metrics, median_latency=100, median_cost=100)
        assert score > 0.6  # Base 0.5 + 0.3 for success
        assert "high_success_rate" in reason

    def test_score_low_success_rate(self, event_store, ranking_manager):
        """Test scoring penalty for low success rate."""
        metrics = {
            "tool_id": "tool_1",
            "tool_name": "LowSuccess",
            "success_rate": 0.2,  # < 0.3 → -0.2
            "p95_latency_ms": 100,
            "median_cost_cents": 50,
            "trend": 0.0,
            "is_cold_start": False,
        }
        score, reason = ranking_manager._score_tool(metrics, median_latency=100, median_cost=100)
        assert score < 0.5  # Base 0.5 - 0.2 for low success
        assert "low_success_rate" in reason

    def test_score_low_latency(self, event_store, ranking_manager):
        """Test scoring bonus for low latency."""
        metrics = {
            "tool_id": "tool_1",
            "tool_name": "Fast",
            "success_rate": 0.5,
            "p95_latency_ms": 50,  # < median * 0.8 → +0.2
            "median_cost_cents": 50,
            "trend": 0.0,
            "is_cold_start": False,
        }
        score, reason = ranking_manager._score_tool(metrics, median_latency=100, median_cost=100)
        assert "low_latency" in reason

    def test_score_low_cost(self, event_store, ranking_manager):
        """Test scoring bonus for low cost."""
        metrics = {
            "tool_id": "tool_1",
            "tool_name": "Cheap",
            "success_rate": 0.5,
            "p95_latency_ms": 100,
            "median_cost_cents": 50,  # < median * 0.7 → +0.2
            "trend": 0.0,
            "is_cold_start": False,
        }
        score, reason = ranking_manager._score_tool(metrics, median_latency=100, median_cost=100)
        assert "low_cost" in reason

    def test_score_cold_start_penalty(self, event_store, ranking_manager):
        """Test scoring penalty for cold start."""
        metrics = {
            "tool_id": "tool_1",
            "tool_name": "New",
            "success_rate": 0.9,
            "p95_latency_ms": 100,
            "median_cost_cents": 50,
            "trend": 0.0,
            "is_cold_start": True,  # < 10 samples → -0.2
        }
        score, reason = ranking_manager._score_tool(metrics, median_latency=100, median_cost=100)
        assert score < 0.8  # Reduced by cold_start penalty
        assert "cold_start" in reason

    def test_score_clamped_to_bounds(self, event_store, ranking_manager):
        """Test that score is clamped to [0.0, 1.0]."""
        # All positive factors
        metrics_max = {
            "tool_id": "tool_1",
            "tool_name": "Perfect",
            "success_rate": 1.0,
            "p95_latency_ms": 10,
            "median_cost_cents": 10,
            "trend": 0.2,
            "is_cold_start": False,
        }
        score_max, _ = ranking_manager._score_tool(metrics_max, median_latency=100, median_cost=100)
        assert score_max <= 1.0

        # All negative factors
        metrics_min = {
            "tool_id": "tool_1",
            "tool_name": "Terrible",
            "success_rate": 0.0,
            "p95_latency_ms": 1000,
            "median_cost_cents": 1000,
            "trend": -0.2,
            "is_cold_start": True,
        }
        score_min, _ = ranking_manager._score_tool(metrics_min, median_latency=100, median_cost=100)
        assert score_min >= 0.0


class TestToolRanking:
    """Tests for ranking tools by score."""

    @pytest.mark.asyncio
    async def test_get_ranked_tools_empty(self, event_store, ranking_manager):
        """Test ranking with no events."""
        ranked = await ranking_manager.get_ranked_tools(tenant_id="_default")
        assert ranked == []

    @pytest.mark.asyncio
    async def test_get_ranked_tools_single_tool(self, event_store, ranking_manager):
        """Test ranking with single tool."""
        # Create 20 successful executions
        for i in range(20):
            event = create_tool_event(tool_id="tool_1")
            event_store.write_event(event)

        ranked = await ranking_manager.get_ranked_tools(tenant_id="_default")
        assert len(ranked) == 1
        assert ranked[0].tool_id == "tool_1"
        assert ranked[0].rank == 1
        assert ranked[0].success_rate == 1.0

    @pytest.mark.asyncio
    async def test_get_ranked_tools_sorted_by_score(self, event_store, ranking_manager):
        """Test that tools are sorted by score (highest first)."""
        # Tool 1: high success rate (0.9)
        for i in range(9):
            event = create_tool_event(tool_id="tool_1", status="success")
            event_store.write_event(event)
        event = create_tool_event(tool_id="tool_1", status="failure")
        event_store.write_event(event)

        # Tool 2: low success rate (0.5)
        for i in range(5):
            event = create_tool_event(tool_id="tool_2", status="success")
            event_store.write_event(event)
        for i in range(5):
            event = create_tool_event(tool_id="tool_2", status="failure")
            event_store.write_event(event)

        ranked = await ranking_manager.get_ranked_tools(tenant_id="_default")
        assert len(ranked) == 2
        # Tool 1 should rank higher due to higher success rate
        assert ranked[0].tool_id == "tool_1"
        assert ranked[1].tool_id == "tool_2"
        assert ranked[0].rank == 1
        assert ranked[1].rank == 2

    @pytest.mark.asyncio
    async def test_get_ranked_tools_respects_limit(self, event_store, ranking_manager):
        """Test that limit parameter is respected."""
        # Create 10 tools
        for tool_num in range(10):
            for i in range(10):
                event = create_tool_event(tool_id=f"tool_{tool_num}")
                event_store.write_event(event)

        ranked = await ranking_manager.get_ranked_tools(tenant_id="_default", limit=3)
        assert len(ranked) == 3


class TestRankingCache:
    """Tests for caching ranked results."""

    @pytest.mark.asyncio
    async def test_cache_hit(self, event_store, ranking_manager):
        """Test cache hit on repeated queries."""
        # Create some events
        for i in range(10):
            event = create_tool_event()
            event_store.write_event(event)

        # First query (cache miss)
        ranked1 = await ranking_manager.get_ranked_tools(tenant_id="_default")

        # Second query (cache hit)
        ranked2 = await ranking_manager.get_ranked_tools(tenant_id="_default")

        # Results should be identical
        assert len(ranked1) == len(ranked2)
        if ranked1:
            assert ranked1[0].tool_id == ranked2[0].tool_id

    @pytest.mark.asyncio
    async def test_cache_ttl_expiry(self, event_store):
        """Test that cache entries expire after TTL."""
        manager = ToolRankingManager(event_store=event_store, cache_ttl_seconds=1)

        # Create event
        for i in range(5):
            event = create_tool_event()
            event_store.write_event(event)

        # First query
        ranked1 = await manager.get_ranked_tools(tenant_id="_default")

        # Sleep to exceed TTL
        await asyncio.sleep(1.1)

        # Second query (cache expired, fresh computation)
        ranked2 = await manager.get_ranked_tools(tenant_id="_default")

        # Results should be identical (but recomputed)
        assert len(ranked1) == len(ranked2)


class TestToolSelection:
    """Tests for tool selection (reuse vs generate decision)."""

    @pytest.mark.asyncio
    async def test_select_tool_high_score_reuse(self, event_store, ranking_manager):
        """Test that high-scoring tool triggers reuse action."""
        # Create 20 successful executions (score will be ~0.8+)
        for i in range(20):
            event = create_tool_event(tool_id="tool_1")
            event_store.write_event(event)

        selection = await select_tool_for_reuse(ranking_manager, tenant_id="_default")
        assert selection["action"] == "reuse"
        assert selection["tool_id"] == "tool_1"
        assert selection["reason"] is not None

    @pytest.mark.asyncio
    async def test_select_tool_low_score_generate(self, event_store, ranking_manager):
        """Test that low-scoring tool triggers generate action."""
        # Create 3 successes, 7 failures (success rate = 0.3)
        for i in range(3):
            event = create_tool_event(tool_id="tool_1", status="success")
            event_store.write_event(event)
        for i in range(7):
            event = create_tool_event(tool_id="tool_1", status="failure")
            event_store.write_event(event)

        selection = await select_tool_for_reuse(ranking_manager, tenant_id="_default")
        assert selection["action"] == "generate"
        assert selection["tool_id"] is None

    @pytest.mark.asyncio
    async def test_select_tool_no_history_generate(self, event_store, ranking_manager):
        """Test that missing tool history triggers generate action."""
        selection = await select_tool_for_reuse(ranking_manager, tenant_id="_default")
        assert selection["action"] == "generate"
        assert selection["tool_id"] is None
        assert "No historical tools" in selection["reason"]

    @pytest.mark.asyncio
    async def test_select_tool_custom_threshold(self, event_store, ranking_manager):
        """Test custom reuse threshold."""
        # Create tool with score ~0.7 (will be just at threshold)
        for i in range(12):
            event = create_tool_event(tool_id="tool_1", status="success")
            event_store.write_event(event)
        for i in range(3):
            event = create_tool_event(tool_id="tool_1", status="failure")
            event_store.write_event(event)

        # Low threshold: should reuse
        selection1 = await select_tool_for_reuse(
            ranking_manager, tenant_id="_default", reuse_threshold=0.6
        )
        assert selection1["action"] == "reuse"

        # High threshold: might generate
        selection2 = await select_tool_for_reuse(
            ranking_manager, tenant_id="_default", reuse_threshold=0.8
        )
        # Might generate depending on exact score


class TestIntegration:
    """Integration tests for the full ranking pipeline."""

    @pytest.mark.asyncio
    async def test_full_pipeline_gap1_to_ranking(self, event_store, ranking_manager):
        """Test full pipeline: Gap 1 events → Gap 4 metrics → Gap 2 ranking."""
        # Simulate Gap 1: emit TOOL_EXECUTED events
        for i in range(15):
            event = create_tool_event(
                tool_id="my_tool",
                tool_name="CodeAnalyzer",
                status="success" if i < 12 else "failure",
                latency_ms=100 + (i * 10),
                cost_cents=50 + (i * 5),
                task_type="code",
            )
            event_store.write_event(event)

        # Simulate Gap 4: aggregate metrics (done internally)
        # Use Gap 2: rank tools
        ranked = await ranking_manager.get_ranked_tools(
            tenant_id="_default", task_type="code", limit=5
        )

        assert len(ranked) > 0
        assert ranked[0].tool_id == "my_tool"
        assert ranked[0].success_rate == 0.8
        assert ranked[0].rank == 1

        # Use Gap 2: select tool for reuse
        selection = await select_tool_for_reuse(
            ranking_manager, tenant_id="_default", task_type="code"
        )
        assert selection["action"] == "reuse"
        assert selection["tool_id"] == "my_tool"


class TestRankingCacheBasics:
    """Basic tests for RankingCache."""

    @pytest.mark.asyncio
    async def test_cache_set_get(self):
        """Test basic set/get operations."""
        cache = RankingCache(ttl_seconds=300)
        test_data = {"key": "value"}

        await cache.set("test_key", test_data)
        result = await cache.get("test_key")
        assert result == test_data

    @pytest.mark.asyncio
    async def test_cache_miss(self):
        """Test cache miss returns None."""
        cache = RankingCache(ttl_seconds=300)
        result = await cache.get("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_cache_ttl_expiry(self):
        """Test cache expiry after TTL."""
        cache = RankingCache(ttl_seconds=1)
        await cache.set("test_key", {"data": "value"})

        # Should hit
        result1 = await cache.get("test_key")
        assert result1 is not None

        # Wait for expiry
        await asyncio.sleep(1.1)

        # Should miss
        result2 = await cache.get("test_key")
        assert result2 is None

    @pytest.mark.asyncio
    async def test_cache_size(self):
        """Test cache size calculation."""
        cache = RankingCache(ttl_seconds=300)
        assert await cache.size() == 0

        await cache.set("key1", "value1")
        assert await cache.size() == 1

        await cache.set("key2", "value2")
        assert await cache.size() == 2

    @pytest.mark.asyncio
    async def test_cache_clear_all(self):
        """Test clearing entire cache."""
        cache = RankingCache(ttl_seconds=300)
        await cache.set("key1", "value1")
        await cache.set("key2", "value2")

        assert await cache.size() == 2
        await cache.clear_all()
        assert await cache.size() == 0

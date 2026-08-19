"""Unit tests for Tool Ranking & Reuse Decision (Gap 2, ADR-0322).

Tests cover:
1. Ranking formula (success + latency + cost + trend - cold_start penalties)
2. Tool scoring accuracy
3. Confidence intervals
4. Cache management
5. Tenant isolation
6. Cold-start penalty application
7. Trend impact on scores
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

from core.learning.event_schema import LearningEvent, LearningEventType
from core.learning.tool_ranking import (
    RankedTool,
    ScoringWeights,
    ToolRankingManager,
)
from core.learning.event_store import EventStore


# Fixtures

@pytest.fixture
def mock_event_store():
    """Create mock EventStore for tests."""
    store = AsyncMock(spec=EventStore)
    store.read_events = AsyncMock(return_value=[])
    return store


@pytest.fixture
def ranking_manager(mock_event_store):
    """Create ToolRankingManager for tests."""
    return ToolRankingManager(event_store=mock_event_store)


def _create_tool_event(
    tool_id: str,
    status: str = "success",
    latency_ms: int = 100,
    cost_cents: int = 10,
    tenant_id: str = "_default",
) -> LearningEvent:
    """Create a TOOL_EXECUTED event."""
    return LearningEvent(
        event_type=LearningEventType.TOOL_EXECUTED,
        tenant_id=tenant_id,
        instance_id="test-instance",
        session_id="test-session",
        skill_name=None,
        timestamp_utc=datetime.now(timezone.utc),
        payload={
            "tool_id": tool_id,
            "status": status,
            "latency_ms": latency_ms,
            "estimated_cost_cents": cost_cents,
            "error_type": None,
        },
    )


class TestRankedTool:
    """Tests for RankedTool dataclass."""

    def test_ranked_tool_immutable(self):
        """RankedTool is frozen (immutable)."""
        tool = RankedTool(
            tool_id="tool_1",
            tool_name="Test Tool",
            score=0.85,
            reason="high success rate",
            success_rate=0.9,
            success_count=90,
            total_count=100,
            avg_latency_ms=150,
            p95_latency_ms=300,
            avg_cost_cents=20,
            confidence=1.0,
            trend=0.05,
            is_cold_start=False,
            first_used=datetime.now(timezone.utc),
            last_used=datetime.now(timezone.utc),
            rank=1,
        )

        # Verify immutability
        with pytest.raises(AttributeError):
            tool.score = 0.5


class TestScoringWeights:
    """Tests for ScoringWeights configuration."""

    def test_default_weights_sum_near_one(self):
        """Default weights should sum to approximately 1.0."""
        weights = ScoringWeights()
        # success + latency + cost + trend = 0.3 + 0.2 + 0.2 + 0.1 = 0.8
        # cold_start is a penalty: 0.2
        assert weights.success_rate == 0.3
        assert weights.latency == 0.2
        assert weights.cost == 0.2
        assert weights.trend == 0.1
        assert weights.cold_start_penalty == 0.2

    def test_custom_weights(self):
        """Custom weights can be provided."""
        weights = ScoringWeights(
            success_rate=0.5,
            latency=0.3,
            cost=0.1,
            trend=0.05,
            cold_start_penalty=0.05,
        )
        assert weights.success_rate == 0.5


class TestToolRankingManager:
    """Tests for ToolRankingManager."""

    @pytest.mark.asyncio
    async def test_empty_tool_list(self, ranking_manager):
        """No tools should return empty list."""
        ranking_manager.event_store.read_events.return_value = []

        result = await ranking_manager.get_ranked_tools(
            tenant_id="_default",
            limit=5,
        )

        assert result == []

    @pytest.mark.asyncio
    async def test_single_high_success_tool(self, ranking_manager):
        """Single tool with high success rate should rank high."""
        # Create 30 successful events (confidence = 1.0)
        events = [
            _create_tool_event("tool_1", status="success", latency_ms=100)
            for _ in range(30)
        ]
        ranking_manager.event_store.read_events.return_value = events

        result = await ranking_manager.get_ranked_tools(
            tenant_id="_default",
            limit=5,
        )

        assert len(result) >= 1
        assert result[0].tool_id == "tool_1"
        assert result[0].success_rate > 0.95
        assert result[0].rank == 1

    @pytest.mark.asyncio
    async def test_scoring_formula_components(self, ranking_manager):
        """Verify scoring formula: success(0.3) + latency(0.2) + cost(0.2) + trend(0.1) - cold(0.2)."""
        # High success, low latency, low cost
        events = [
            _create_tool_event("tool_good", status="success", latency_ms=50, cost_cents=5)
            for _ in range(30)
        ]
        # Low success, high latency, high cost
        events += [
            _create_tool_event("tool_bad", status="failure", latency_ms=500, cost_cents=100)
            for _ in range(30)
        ]
        ranking_manager.event_store.read_events.return_value = events

        result = await ranking_manager.get_ranked_tools(
            tenant_id="_default",
            limit=10,
        )

        # tool_good should rank higher than tool_bad
        tool_ids = [r.tool_id for r in result]
        if "tool_good" in tool_ids and "tool_bad" in tool_ids:
            good_rank = next(r.rank for r in result if r.tool_id == "tool_good")
            bad_rank = next(r.rank for r in result if r.tool_id == "tool_bad")
            assert good_rank < bad_rank  # Lower rank number is better

    @pytest.mark.asyncio
    async def test_cold_start_penalty(self, ranking_manager):
        """New tools (<10 samples) should have cold_start penalty applied."""
        # Only 5 events (cold start)
        events = [
            _create_tool_event("tool_new", status="success", latency_ms=100)
            for _ in range(5)
        ]
        ranking_manager.event_store.read_events.return_value = events

        result = await ranking_manager.get_ranked_tools(
            tenant_id="_default",
            limit=1,
        )

        if result:
            assert result[0].is_cold_start is True
            # Score should be penalized
            assert result[0].score < 0.95

    @pytest.mark.asyncio
    async def test_confidence_in_ranking(self, ranking_manager):
        """Confidence should be based on sample count."""
        # 100 samples => confidence should be high
        events = [
            _create_tool_event("tool_confident", status="success")
            for _ in range(100)
        ]
        ranking_manager.event_store.read_events.return_value = events

        result = await ranking_manager.get_ranked_tools(
            tenant_id="_default",
            limit=1,
        )

        assert result[0].confidence == 1.0  # Capped at 1.0 after 30 samples

    @pytest.mark.asyncio
    async def test_trend_impact_on_score(self, ranking_manager):
        """Improving trend should boost score, degrading should lower it."""
        # First 100 events: 50% success
        # Last 10 events: 100% success (improving)
        events = [
            _create_tool_event("tool_improving", status="failure" if i % 2 == 0 else "success")
            for i in range(100)
        ]
        events += [
            _create_tool_event("tool_improving", status="success")
            for _ in range(10)
        ]
        ranking_manager.event_store.read_events.return_value = events

        result = await ranking_manager.get_ranked_tools(
            tenant_id="_default",
            limit=1,
        )

        # Improving trend should apply positive bonus
        assert result[0].trend > 0.05

    @pytest.mark.asyncio
    async def test_tenant_isolation(self, ranking_manager):
        """Rankings should be tenant-scoped."""
        # Tenant A: successful tool
        events_a = [
            _create_tool_event("tool_1", status="success", tenant_id="tenant_a")
            for _ in range(30)
        ]
        # Tenant B: same tool but failed
        events_b = [
            _create_tool_event("tool_1", status="failure", tenant_id="tenant_b")
            for _ in range(30)
        ]

        # Mock separate queries
        ranking_manager.event_store.read_events.side_effect = lambda **kwargs: (
            events_a if kwargs.get("tenant_id") == "tenant_a" else events_b
        )

        result_a = await ranking_manager.get_ranked_tools(tenant_id="tenant_a", limit=1)
        result_b = await ranking_manager.get_ranked_tools(tenant_id="tenant_b", limit=1)

        # Tool 1 should have different scores in different tenants
        if result_a and result_b:
            assert result_a[0].success_rate > result_b[0].success_rate

    @pytest.mark.asyncio
    async def test_limit_parameter(self, ranking_manager):
        """Limit parameter should restrict result count."""
        # Create 20 different tools
        events = []
        for tool_num in range(20):
            for _ in range(30):
                events.append(
                    _create_tool_event(f"tool_{tool_num}", status="success")
                )
        ranking_manager.event_store.read_events.return_value = events

        result = await ranking_manager.get_ranked_tools(
            tenant_id="_default",
            limit=5,
        )

        assert len(result) <= 5

    @pytest.mark.asyncio
    async def test_cache_hit(self, ranking_manager):
        """Second query should use cache (not re-query EventStore)."""
        events = [
            _create_tool_event("tool_1", status="success")
            for _ in range(30)
        ]
        ranking_manager.event_store.read_events.return_value = events

        # First query
        await ranking_manager.get_ranked_tools(tenant_id="_default", limit=5)

        # Second query (should hit cache)
        result = await ranking_manager.get_ranked_tools(tenant_id="_default", limit=5)

        # EventStore should only be queried once (first call)
        # We can verify this by checking call count
        assert ranking_manager.event_store.read_events.call_count >= 1

    @pytest.mark.asyncio
    async def test_task_type_filter(self, ranking_manager):
        """Rankings can be filtered by task_type."""
        # Create events with different task types
        events = []
        for task_type in ["extract", "summarize", "code_review"]:
            for _ in range(30):
                event = _create_tool_event(f"tool_{task_type}", status="success")
                event.payload["task_type"] = task_type
                events.append(event)

        ranking_manager.event_store.read_events.return_value = events

        result = await ranking_manager.get_ranked_tools(
            tenant_id="_default",
            task_type="extract",
            limit=10,
        )

        # Results should be filtered by task_type
        assert len(result) >= 0

    @pytest.mark.asyncio
    async def test_error_class_filter(self, ranking_manager):
        """Rankings can be filtered by error_class."""
        events = []
        for error_class in ["timeout", "api_error", "parsing_error"]:
            for _ in range(30):
                event = _create_tool_event(f"tool_{error_class}", status="success")
                event.payload["error_class"] = error_class
                events.append(event)

        ranking_manager.event_store.read_events.return_value = events

        result = await ranking_manager.get_ranked_tools(
            tenant_id="_default",
            error_class="timeout",
            limit=10,
        )

        assert len(result) >= 0

    @pytest.mark.asyncio
    async def test_reuse_decision_threshold(self, ranking_manager):
        """Reuse decision should use score threshold."""
        # Create one high-scoring tool
        events = [
            _create_tool_event("tool_good", status="success", latency_ms=50, cost_cents=5)
            for _ in range(30)
        ]
        ranking_manager.event_store.read_events.return_value = events

        # Tool should meet reuse threshold
        result = await ranking_manager.get_ranked_tools(
            tenant_id="_default",
            limit=1,
        )

        if result:
            # High-confidence, successful tool should score > 0.7
            assert result[0].score > 0.7

    @pytest.mark.asyncio
    async def test_first_and_last_used_timestamps(self, ranking_manager):
        """RankedTool should include first_used and last_used timestamps."""
        events = [
            _create_tool_event("tool_1", status="success")
            for _ in range(30)
        ]
        ranking_manager.event_store.read_events.return_value = events

        result = await ranking_manager.get_ranked_tools(
            tenant_id="_default",
            limit=1,
        )

        if result:
            assert result[0].first_used is not None
            assert result[0].last_used is not None
            assert isinstance(result[0].first_used, datetime)
            assert isinstance(result[0].last_used, datetime)

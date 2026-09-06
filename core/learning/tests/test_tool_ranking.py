"""Tests for Tool Performance Ranking & Reuse Decision (Gap 2, ADR-0322).

Real contract (adversarial review N-02 / N-08): the producer
(``ToolForgeSubsystem._emit_tool_executed_event``) and the reader
(``ToolRankingManager``) share ONE wire format —
``learning_events.LearningEvent(EventType.METRIC, skill_id="tool:<id>",
signal={"kind": "tool_executed", ...})`` persisted by
``core.learning.event_store.EventStore(tenant_home)``. The previous version of
this file pinned the bug: ``EventStore(<file path>)``, ``event_schema`` events
and ``read_events_by_type`` (a method no store defines).
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.learning.event_emitter import EventEmitter
from core.learning.event_schema import ToolExecutedPayload
from core.learning.event_store import EventStore
from core.learning.learning_events import EventType, LearningEvent
from core.learning.tool_ranking import (
    RankedTool,
    ScoringWeights,
    ToolRankingManager,
    is_tool_executed_event,
    select_tool_for_reuse,
    tool_executed_event,
)
from core.learning.tool_ranking_cache import RankingCache

TENANT = "_default"


@pytest.fixture
def tenant_home(tmp_path: Path) -> Path:
    """``<corvin_home>/tenants/<tenant_id>/`` — a DIRECTORY, never a file path."""
    home = tmp_path / "tenants" / TENANT
    home.mkdir(parents=True)
    return home


@pytest.fixture
def event_store(tenant_home: Path) -> EventStore:
    return EventStore(tenant_home)


@pytest.fixture
def ranking_manager(event_store: EventStore) -> ToolRankingManager:
    return ToolRankingManager(event_store=event_store)


def create_tool_event(
    tool_id: str = "tool_1",
    tool_name: str = "TestTool",
    status: str = "success",
    latency_ms: int = 100,
    cost_cents: int = 50,
    task_type: str = "code",
    error_class: str | None = None,
    error_message: str | None = None,
    tenant_id: str = TENANT,
) -> LearningEvent:
    """Build a tool_executed record exactly as the producer does."""
    payload = ToolExecutedPayload(
        tool_id=tool_id,
        tool_name=tool_name,
        tool_type="generated",
        status=status,
        latency_ms=latency_ms,
        input_tokens=100,
        output_tokens=200,
        estimated_cost_cents=cost_cents,
        error_type=None,
        error_message=error_message,
        error_class=error_class,
        user_satisfaction=5,
        task_type=task_type,
        model_id="claude-opus-5",
    )
    return tool_executed_event(tenant_id, payload, session_id="session_1", instance_id="test_instance")


def _tool_events(store: EventStore, tenant_id: str = TENANT) -> list[LearningEvent]:
    return [e for e in store.query_events(tenant_id, event_type=EventType.METRIC) if is_tool_executed_event(e)]


class TestWireFormat:
    """The ONE record shape shared by producer and reader."""

    def test_tool_executed_event_shape(self):
        ev = create_tool_event(tool_id="t1", tool_name="T", task_type="code", error_class="ImportError")
        assert ev.event_type == EventType.METRIC
        assert ev.skill_id == "tool:t1"
        assert ev.tenant_id == TENANT
        assert ev.signal["kind"] == "tool_executed"
        assert ev.signal["tool_id"] == "t1"
        assert ev.signal["tool_name"] == "T"
        assert ev.signal["status"] == "success"
        assert ev.signal["task_type"] == "code"
        assert ev.signal["error_class"] == "ImportError"
        assert ev.signal["session_id"] == "session_1"
        assert ev.signal["instance_id"] == "test_instance"
        assert ev.lom
        assert is_tool_executed_event(ev)

    def test_accepts_dict_payload(self):
        ev = tool_executed_event(TENANT, {"tool_id": "x", "tool_name": "X", "status": "failure", "latency_ms": 5})
        assert ev.skill_id == "tool:x"
        assert ev.signal["status"] == "failure"

    def test_error_message_is_pii_scrubbed_and_bounded(self):
        ev = create_tool_event(
            error_message="failed for john.doe@example.com at /home/shumway/secret key sk_live_ABCDEFGHIJKLMNOP " + "x" * 500
        )
        msg = ev.signal["error_message"]
        assert "john.doe@example.com" not in msg
        assert "/home/shumway" not in msg
        assert "sk_live_ABCDEFGHIJKLMNOP" not in msg
        assert len(msg) <= 200

    def test_non_tool_metric_is_not_a_tool_event(self):
        other = LearningEvent.create(EventType.METRIC, skill_id="os.x", tenant_id=TENANT, signal={"kind": "token_metrics"})
        assert not is_tool_executed_event(other)
        feedback = LearningEvent.create(EventType.FEEDBACK, skill_id="tool:t1", tenant_id=TENANT, signal={"kind": "tool_executed"})
        assert not is_tool_executed_event(feedback)


class TestRankedToolDataclass:
    def _tool(self, **kw) -> RankedTool:
        base = dict(
            tool_id="tool_1", tool_name="TestTool", score=0.85, reason="high_success_rate, low_cost",
            success_rate=0.95, success_count=19, total_count=20, avg_latency_ms=100, p95_latency_ms=150,
            avg_cost_cents=50, confidence=0.8, trend=0.1, is_cold_start=False,
            first_used=datetime.now(timezone.utc), last_used=datetime.now(timezone.utc), rank=1,
        )
        base.update(kw)
        return RankedTool(**base)

    def test_ranked_tool_creation(self):
        tool = self._tool()
        assert tool.tool_id == "tool_1"
        assert tool.score == 0.85
        assert tool.rank == 1
        assert not tool.is_cold_start

    def test_ranked_tool_immutable(self):
        tool = self._tool()
        with pytest.raises(Exception):
            tool.score = 0.5  # type: ignore[misc]


class TestToolRankingManagerBasics:
    def test_initialization(self, event_store):
        manager = ToolRankingManager(event_store=event_store)
        assert manager.event_store is event_store
        assert manager.cache is not None
        assert isinstance(manager.weights, ScoringWeights)

    def test_custom_weights(self, event_store):
        custom_weights = ScoringWeights(base_score=0.6, success_rate=0.4, latency=0.25, cost=0.15)
        manager = ToolRankingManager(event_store=event_store, weights=custom_weights)
        assert manager.weights.base_score == 0.6
        assert manager.weights.success_rate == 0.4


class TestEventQuerying:
    def _cutoff(self) -> datetime:
        return datetime.now(timezone.utc) - timedelta(days=7)

    def test_query_tool_events_empty(self, ranking_manager):
        assert ranking_manager._query_tool_events(TENANT, None, None, self._cutoff()) == []

    def test_query_tool_events_by_task_type(self, event_store, ranking_manager):
        event_store.write_event(create_tool_event(task_type="code"))
        event_store.write_event(create_tool_event(task_type="research"))
        events = ranking_manager._query_tool_events(TENANT, "code", None, self._cutoff())
        assert len(events) == 1
        assert events[0].signal["task_type"] == "code"

    def test_query_tool_events_by_error_class(self, event_store, ranking_manager):
        event_store.write_event(create_tool_event(error_class="ImportError"))
        event_store.write_event(create_tool_event(error_class="TypeError"))
        events = ranking_manager._query_tool_events(TENANT, None, "ImportError", self._cutoff())
        assert len(events) == 1
        assert events[0].signal["error_class"] == "ImportError"

    def test_query_ignores_other_metric_kinds(self, event_store, ranking_manager):
        event_store.write_event(create_tool_event())
        event_store.write_event(
            LearningEvent.create(EventType.METRIC, skill_id="os.tokens", tenant_id=TENANT, signal={"kind": "token_metrics"})
        )
        events = ranking_manager._query_tool_events(TENANT, None, None, self._cutoff())
        assert len(events) == 1

    def test_query_is_tenant_scoped(self, event_store, ranking_manager, monkeypatch):
        event_store.write_event(create_tool_event(tenant_id=TENANT))
        monkeypatch.setenv("CORVIN_TENANT_ID", "tenant_b")  # audit-first: chain admits the process tenant only
        event_store.write_event(create_tool_event(tenant_id="tenant_b"))
        monkeypatch.setenv("CORVIN_TENANT_ID", TENANT)
        assert len(ranking_manager._query_tool_events(TENANT, None, None, self._cutoff())) == 1
        assert len(ranking_manager._query_tool_events("tenant_b", None, None, self._cutoff())) == 1

    def test_query_respects_cutoff(self, event_store, ranking_manager):
        event_store.write_event(create_tool_event())
        future_cutoff = datetime.now(timezone.utc) + timedelta(days=1)
        assert ranking_manager._query_tool_events(TENANT, None, None, future_cutoff) == []


class TestMetricsAggregation:
    def test_aggregate_single_tool_success(self, event_store, ranking_manager):
        for _ in range(5):
            event_store.write_event(create_tool_event(tool_id="tool_1", status="success"))
        metrics = ranking_manager._aggregate_tool_metrics(_tool_events(event_store))
        assert metrics["tool_1"]["success_count"] == 5
        assert metrics["tool_1"]["total_count"] == 5
        assert metrics["tool_1"]["success_rate"] == 1.0
        assert metrics["tool_1"]["is_cold_start"]  # < 10 samples
        assert metrics["tool_1"]["first_used"] <= metrics["tool_1"]["last_used"]

    def test_aggregate_tool_with_failures(self, event_store, ranking_manager):
        for _ in range(3):
            event_store.write_event(create_tool_event(tool_id="tool_1", status="success"))
        for _ in range(2):
            event_store.write_event(create_tool_event(tool_id="tool_1", status="failure"))
        metrics = ranking_manager._aggregate_tool_metrics(_tool_events(event_store))
        assert metrics["tool_1"]["success_count"] == 3
        assert metrics["tool_1"]["total_count"] == 5
        assert metrics["tool_1"]["success_rate"] == 0.6

    def test_aggregate_multiple_tools(self, event_store, ranking_manager):
        for _ in range(8):
            event_store.write_event(create_tool_event(tool_id="tool_1", status="success"))
        for _ in range(2):
            event_store.write_event(create_tool_event(tool_id="tool_1", status="failure"))
        for _ in range(5):
            event_store.write_event(create_tool_event(tool_id="tool_2", status="success"))
        for _ in range(5):
            event_store.write_event(create_tool_event(tool_id="tool_2", status="failure"))
        metrics = ranking_manager._aggregate_tool_metrics(_tool_events(event_store))
        assert metrics["tool_1"]["success_rate"] == 0.8
        assert metrics["tool_2"]["success_rate"] == 0.5

    def test_aggregate_latency_percentiles(self, event_store, ranking_manager):
        for latency in [50, 75, 100, 150, 200, 250, 300, 400, 500, 1000]:
            event_store.write_event(create_tool_event(tool_id="tool_1", latency_ms=latency))
        metrics = ranking_manager._aggregate_tool_metrics(_tool_events(event_store))
        assert metrics["tool_1"]["p50_latency_ms"] > 0
        assert metrics["tool_1"]["p95_latency_ms"] > metrics["tool_1"]["p50_latency_ms"]
        assert metrics["tool_1"]["p99_latency_ms"] >= metrics["tool_1"]["p95_latency_ms"]

    def test_aggregate_cold_start_detection(self, event_store, ranking_manager):
        for _ in range(9):
            event_store.write_event(create_tool_event(tool_id="tool_cold"))
        for _ in range(10):
            event_store.write_event(create_tool_event(tool_id="tool_warm"))
        metrics = ranking_manager._aggregate_tool_metrics(_tool_events(event_store))
        assert metrics["tool_cold"]["is_cold_start"] is True
        assert metrics["tool_warm"]["is_cold_start"] is False


class TestScoringFormula:
    def _m(self, **kw):
        # Neutral baseline: latency == median, cost == median → no latency/cost
        # component fires (ADR-0322 formula), so each test isolates ONE factor.
        base = {"tool_id": "tool_1", "tool_name": "T", "success_rate": 0.5, "p95_latency_ms": 100,
                "median_cost_cents": 100, "trend": 0.0, "is_cold_start": False}
        base.update(kw)
        return base

    def test_score_high_success_rate(self, ranking_manager):
        score, reason = ranking_manager._score_tool(self._m(success_rate=0.95), 100, 100)
        assert score > 0.6
        assert "high_success_rate" in reason

    def test_score_low_success_rate(self, ranking_manager):
        score, reason = ranking_manager._score_tool(self._m(success_rate=0.2), 100, 100)
        assert score < 0.5
        assert "low_success_rate" in reason

    def test_score_low_latency(self, ranking_manager):
        _, reason = ranking_manager._score_tool(self._m(p95_latency_ms=50), 100, 100)
        assert "low_latency" in reason

    def test_score_low_cost(self, ranking_manager):
        _, reason = ranking_manager._score_tool(self._m(median_cost_cents=50), 100, 100)
        assert "low_cost" in reason

    def test_score_cold_start_penalty(self, ranking_manager):
        score, reason = ranking_manager._score_tool(self._m(success_rate=0.9, is_cold_start=True), 100, 100)
        assert score < 0.8
        assert "cold_start" in reason

    def test_score_clamped_to_bounds(self, ranking_manager):
        score_max, _ = ranking_manager._score_tool(
            self._m(success_rate=1.0, p95_latency_ms=10, median_cost_cents=10, trend=0.2), 100, 100)
        assert score_max <= 1.0
        score_min, _ = ranking_manager._score_tool(
            self._m(success_rate=0.0, p95_latency_ms=1000, median_cost_cents=1000, trend=-0.2, is_cold_start=True), 100, 100)
        assert score_min >= 0.0


class TestToolRanking:
    @pytest.mark.asyncio
    async def test_get_ranked_tools_empty(self, ranking_manager):
        assert await ranking_manager.get_ranked_tools(tenant_id=TENANT) == []

    @pytest.mark.asyncio
    async def test_get_ranked_tools_single_tool(self, event_store, ranking_manager):
        for _ in range(20):
            event_store.write_event(create_tool_event(tool_id="tool_1"))
        ranked = await ranking_manager.get_ranked_tools(tenant_id=TENANT)
        assert len(ranked) == 1
        assert ranked[0].tool_id == "tool_1"
        assert ranked[0].rank == 1
        assert ranked[0].success_rate == 1.0

    @pytest.mark.asyncio
    async def test_get_ranked_tools_sorted_by_score(self, event_store, ranking_manager):
        for _ in range(9):
            event_store.write_event(create_tool_event(tool_id="tool_1", status="success"))
        event_store.write_event(create_tool_event(tool_id="tool_1", status="failure"))
        for _ in range(5):
            event_store.write_event(create_tool_event(tool_id="tool_2", status="success"))
        for _ in range(5):
            event_store.write_event(create_tool_event(tool_id="tool_2", status="failure"))
        ranked = await ranking_manager.get_ranked_tools(tenant_id=TENANT)
        assert [t.tool_id for t in ranked] == ["tool_1", "tool_2"]
        assert [t.rank for t in ranked] == [1, 2]

    @pytest.mark.asyncio
    async def test_get_ranked_tools_respects_limit(self, event_store, ranking_manager):
        for tool_num in range(10):
            for _ in range(10):
                event_store.write_event(create_tool_event(tool_id=f"tool_{tool_num}"))
        ranked = await ranking_manager.get_ranked_tools(tenant_id=TENANT, limit=3)
        assert len(ranked) == 3

    @pytest.mark.asyncio
    async def test_ranking_never_crosses_tenants(self, event_store, ranking_manager, monkeypatch):
        monkeypatch.setenv("CORVIN_TENANT_ID", "tenant_b")  # audit-first: chain admits the process tenant only
        for _ in range(10):
            event_store.write_event(create_tool_event(tool_id="tool_b", tenant_id="tenant_b"))
        monkeypatch.setenv("CORVIN_TENANT_ID", TENANT)
        assert await ranking_manager.get_ranked_tools(tenant_id=TENANT) == []


class TestRankingCache:
    @pytest.mark.asyncio
    async def test_cache_hit(self, event_store, ranking_manager):
        for _ in range(10):
            event_store.write_event(create_tool_event())
        ranked1 = await ranking_manager.get_ranked_tools(tenant_id=TENANT)
        ranked2 = await ranking_manager.get_ranked_tools(tenant_id=TENANT)
        assert len(ranked1) == len(ranked2) == 1
        assert ranked1[0].tool_id == ranked2[0].tool_id

    @pytest.mark.asyncio
    async def test_cache_ttl_expiry(self, event_store):
        manager = ToolRankingManager(event_store=event_store, cache_ttl_seconds=1)
        for _ in range(5):
            event_store.write_event(create_tool_event())
        ranked1 = await manager.get_ranked_tools(tenant_id=TENANT)
        await asyncio.sleep(1.1)
        ranked2 = await manager.get_ranked_tools(tenant_id=TENANT)
        assert len(ranked1) == len(ranked2) == 1


class TestToolSelection:
    @pytest.mark.asyncio
    async def test_select_tool_high_score_reuse(self, event_store, ranking_manager):
        for _ in range(20):
            event_store.write_event(create_tool_event(tool_id="tool_1"))
        selection = await select_tool_for_reuse(ranking_manager, tenant_id=TENANT)
        assert selection["action"] == "reuse"
        assert selection["tool_id"] == "tool_1"
        assert selection["reason"]

    @pytest.mark.asyncio
    async def test_select_tool_low_score_generate(self, event_store, ranking_manager):
        for _ in range(3):
            event_store.write_event(create_tool_event(tool_id="tool_1", status="success"))
        for _ in range(7):
            event_store.write_event(create_tool_event(tool_id="tool_1", status="failure"))
        selection = await select_tool_for_reuse(ranking_manager, tenant_id=TENANT)
        assert selection["action"] == "generate"
        assert selection["tool_id"] is None

    @pytest.mark.asyncio
    async def test_select_tool_no_history_generate(self, ranking_manager):
        selection = await select_tool_for_reuse(ranking_manager, tenant_id=TENANT)
        assert selection["action"] == "generate"
        assert selection["tool_id"] is None
        assert "No historical tools" in selection["reason"]

    @pytest.mark.asyncio
    async def test_select_tool_custom_threshold(self, event_store, ranking_manager):
        # 13/15 = 0.867 > 0.8 → +0.3 (ADR-0322: the bonus needs STRICTLY > 0.8;
        # the old 12/15 = 0.8 exactly never scored above the 0.5 base). A lone
        # tool is its own latency/cost median, so the score is exactly 0.8.
        for _ in range(13):
            event_store.write_event(create_tool_event(tool_id="tool_1", status="success"))
        for _ in range(2):
            event_store.write_event(create_tool_event(tool_id="tool_1", status="failure"))
        selection1 = await select_tool_for_reuse(ranking_manager, tenant_id=TENANT, reuse_threshold=0.6)
        assert selection1["action"] == "reuse"
        selection2 = await select_tool_for_reuse(ranking_manager, tenant_id=TENANT, reuse_threshold=0.99)
        assert selection2["action"] == "generate"


class TestIntegration:
    @pytest.mark.asyncio
    async def test_full_pipeline_gap1_to_ranking(self, event_store, ranking_manager):
        # 13/15 successes: the ADR-0322 success bonus needs success_rate > 0.8
        # (strict); 12/15 == 0.8 scored the bare 0.5 base and could never reuse.
        for i in range(15):
            event_store.write_event(create_tool_event(
                tool_id="my_tool", tool_name="CodeAnalyzer",
                status="success" if i < 13 else "failure",
                latency_ms=100 + (i * 10), cost_cents=50 + (i * 5), task_type="code",
            ))
        ranked = await ranking_manager.get_ranked_tools(tenant_id=TENANT, task_type="code", limit=5)
        assert ranked and ranked[0].tool_id == "my_tool"
        assert abs(ranked[0].success_rate - 13 / 15) < 1e-9
        assert ranked[0].rank == 1
        selection = await select_tool_for_reuse(ranking_manager, tenant_id=TENANT, task_type="code")
        assert selection["action"] == "reuse"
        assert selection["tool_id"] == "my_tool"


def _wait_for(pred, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


class TestTelemetryLandsOnDisk:
    """ADR-0321/0322: a tool_executed record emitted through the SYNC emitter is
    persisted under ``<tenant_home>/learning/events/`` and read back by the ranker."""

    @pytest.mark.asyncio
    async def test_emit_then_rank_reads_back(self, tenant_home, event_store):
        emitter = EventEmitter(event_store)
        try:
            for i in range(12):
                assert emitter.emit(create_tool_event(tool_id="emitted", status="success" if i < 11 else "failure")) is True
        finally:
            emitter.stop()
        files = list((tenant_home / "learning" / "events").glob("*.jsonl"))
        assert files, "events must land in <tenant_home>/learning/events/"
        lines = [json.loads(l) for f in files for l in f.read_text().splitlines() if l.strip()]
        assert len(lines) == 12
        assert {l["event_type"] for l in lines} == {"metric"}
        assert {l["skill_id"] for l in lines} == {"tool:emitted"}
        assert all(l["signal"]["kind"] == "tool_executed" for l in lines)

        manager = ToolRankingManager(event_store=EventStore(tenant_home))
        ranked = await manager.get_ranked_tools(tenant_id=TENANT)
        assert len(ranked) == 1
        assert ranked[0].tool_id == "emitted"
        assert ranked[0].total_count == 12
        assert ranked[0].success_count == 11

    @pytest.mark.asyncio
    async def test_producer_subsystem_emits_the_shared_record(self, tenant_home, event_store):
        """Drive the REAL producer: ToolForgeSubsystem._emit_tool_executed_event."""
        from core.orchestration.subsystems.tool_forge_subsystem import ToolForgeSubsystem

        subsystem = ToolForgeSubsystem(tenant_id=TENANT)
        emitter = EventEmitter(event_store)
        subsystem.event_emitter = emitter
        try:
            await subsystem._emit_tool_executed_event(
                tool_name="real_tool", task_id="task_1", turn_id="turn_1", session_id="sess_1",
                status="failure", latency_ms=42, error="boom at /home/someone/x for a@b.io",
                error_type="ValueError", error_class="ValueError",
            )
            assert _wait_for(lambda: bool(_tool_events(event_store)))
        finally:
            emitter.stop()

        events = _tool_events(event_store)
        assert len(events) == 1
        ev = events[0]
        assert ev.skill_id == "tool:real_tool"
        assert ev.tenant_id == TENANT
        assert ev.signal["status"] == "failure"
        assert ev.signal["latency_ms"] == 42
        assert ev.signal["task_id"] == "task_1"
        assert ev.signal["turn_id"] == "turn_1"
        assert ev.signal["session_id"] == "sess_1"
        assert ev.signal["error_class"] == "ValueError"
        assert "a@b.io" not in ev.signal["error_message"]
        assert "/home/someone" not in ev.signal["error_message"]
        assert ev.lom.endswith("_emit_tool_executed_event")

        manager = ToolRankingManager(event_store=event_store)
        ranked = await manager.get_ranked_tools(tenant_id=TENANT)
        assert ranked[0].tool_id == "real_tool"

    def test_subsystem_store_is_the_emitter_store_in_a_tenant_dir(self, tmp_path, monkeypatch):
        """startup() must NOT hand a FILE path to EventStore (N-02)."""
        from unittest.mock import MagicMock
        from core.orchestration.subsystems.tool_forge_subsystem import ToolForgeSubsystem

        monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "home"))
        subsystem = ToolForgeSubsystem(tenant_id=TENANT)
        hub = MagicMock()
        hub.context_bus = MagicMock()
        hub.get_service = MagicMock(return_value=None)
        subsystem.startup(hub)
        try:
            assert isinstance(subsystem.event_store, EventStore)
            assert subsystem.event_store is subsystem.event_emitter.store
            assert subsystem.event_store.tenant_home.is_dir()
            assert subsystem.event_store.tenant_home.name == TENANT
            assert not subsystem.event_store.tenant_home.suffix  # never ".db"
            assert subsystem.ranking_manager.event_store is subsystem.event_store
        finally:
            subsystem.event_emitter.stop()


class TestRankingCacheBasics:
    @pytest.mark.asyncio
    async def test_cache_set_get(self):
        cache = RankingCache(ttl_seconds=300)
        await cache.set("test_key", {"key": "value"})
        assert await cache.get("test_key") == {"key": "value"}

    @pytest.mark.asyncio
    async def test_cache_miss(self):
        cache = RankingCache(ttl_seconds=300)
        assert await cache.get("nonexistent") is None

    @pytest.mark.asyncio
    async def test_cache_ttl_expiry(self):
        cache = RankingCache(ttl_seconds=1)
        await cache.set("test_key", {"data": "value"})
        assert await cache.get("test_key") is not None
        await asyncio.sleep(1.1)
        assert await cache.get("test_key") is None

    @pytest.mark.asyncio
    async def test_cache_size(self):
        cache = RankingCache(ttl_seconds=300)
        assert await cache.size() == 0
        await cache.set("key1", "value1")
        await cache.set("key2", "value2")
        assert await cache.size() == 2

    @pytest.mark.asyncio
    async def test_cache_clear_all(self):
        cache = RankingCache(ttl_seconds=300)
        await cache.set("key1", "value1")
        await cache.set("key2", "value2")
        await cache.clear_all()
        assert await cache.size() == 0

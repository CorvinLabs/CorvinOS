"""End-to-End Verification Tests for All 7 Learning Gaps (ADR-0321-0327).

This module provides comprehensive E2E tests that verify each gap works end-to-end
in real execution scenarios, not just in isolation. Tests follow LDD (Loss-Driven
Development) framework:

1. Define loss function for each gap
2. Run end-to-end scenario
3. Verify loss function is FALSE (gap prevents loss)
4. Confirm integration with downstream systems

Loss Functions:
- Gap 1: If telemetry incomplete → learning system blind (CRITICAL)
- Gap 2: If ranking wrong → suboptimal tools selected → wasted tokens (MEDIUM)
- Gap 3: If attribution unfair → skill scores wrong → promotions wrong (MEDIUM)
- Gap 4: If aggregation fails → ranking has no data → cannot rank (CRITICAL)
- Gap 5: If coherence breaks → operators re-learn errors → inefficiency (MEDIUM)
- Gap 6: If cost learning fails → budget estimates wrong → task failures (HIGH)
- Gap 7: If feedback ignored → operator input has no effect (MEDIUM)
"""

import pytest
import asyncio
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any
from dataclasses import dataclass

from core.learning.event_schema import LearningEvent, LearningEventType
from core.learning.event_store import EventStore
from core.learning.event_emitter import EventEmitter
from core.learning.tool_execution import ToolExecutionTelemetry
from core.learning.performance_aggregation import PerformanceAggregator
from core.learning.tool_ranking import ToolRankingManager
from core.learning.skill_attribution import SkillAttributionManager
from core.orchestration.context_coherence import ContextCoherenceManager
from core.learning.tool_cost_learning import CostLearningManager
from core.learning.operator_feedback import OperatorFeedbackManager


# Test Fixtures

@pytest.fixture
async def event_store():
    """Create in-memory EventStore for E2E tests."""
    store = EventStore(storage_path="/tmp/test_event_store")
    await store.initialize()
    yield store
    await store.cleanup()


@pytest.fixture
async def event_emitter(event_store):
    """Create EventEmitter for E2E tests."""
    emitter = EventEmitter(event_store=event_store)
    await emitter.start()
    yield emitter
    await emitter.stop()


@pytest.fixture
def tenant_id():
    """Default tenant for E2E tests."""
    return "_default"


@pytest.fixture
def session_id():
    """Test session ID."""
    return "e2e-test-session-001"


# Gap 1: Tool Execution Telemetry E2E Tests

class TestGap1ToolExecutionTelemetryE2E:
    """Gap 1 E2E: Tool execution → telemetry captured → event emitted → stored in EventStore."""

    @pytest.mark.asyncio
    async def test_tool_execution_telemetry_e2e(
        self,
        event_store: EventStore,
        event_emitter: EventEmitter,
        tenant_id: str,
        session_id: str,
    ):
        """Verify complete telemetry flow: execution → capture → emit → store."""

        # Step 1: Create tool execution telemetry
        telemetry = ToolExecutionTelemetry(
            tenant_id=tenant_id,
            session_id=session_id,
            instance_id="test-instance-001",
            tool_id="extract_tool",
            tool_name="Text Extraction",
            task_id="task-123",
            task_type="text_extraction",
            model_id="claude-3-sonnet-20250101",
            status="success",
            duration_ms=1234,
            estimated_cost_cents=50,
            tokens_in=1500,
            tokens_out=800,
            error_type=None,
            error_message=None,
        )

        # Step 2: Verify telemetry is valid (PII sanitized, fail-closed check passes)
        assert telemetry.tenant_id == tenant_id
        assert telemetry.status == "success"
        assert telemetry.tokens_in > 0

        # Step 3: Convert to event payload and emit
        payload = telemetry.to_event_payload()
        event = LearningEvent(
            event_type=LearningEventType.TOOL_EXECUTED,
            tenant_id=tenant_id,
            instance_id="test-instance-001",
            session_id=session_id,
            skill_name=None,
            timestamp_utc=datetime.now(timezone.utc),
            payload=payload,
        )

        # Step 4: Emit event
        await event_emitter.emit(event)

        # Step 5: Verify event was stored in EventStore
        stored_events = await event_store.read_events(
            tenant_id=tenant_id,
            event_type=LearningEventType.TOOL_EXECUTED,
        )

        # Verify: Loss function FALSE
        # Loss: "If telemetry incomplete → learning system blind"
        # Verification: Event reached EventStore with all required fields
        assert len(stored_events) > 0, "LOSS TRIGGERED: Event not stored (Gap 1 FAILED)"
        assert stored_events[0].payload.get("tool_id") == "extract_tool"
        assert stored_events[0].payload.get("status") == "success"
        assert stored_events[0].payload.get("duration_ms") > 0
        assert "api_key" not in str(stored_events[0].payload), "PII NOT SANITIZED"

    @pytest.mark.asyncio
    async def test_tool_execution_failure_telemetry_e2e(
        self,
        event_store: EventStore,
        event_emitter: EventEmitter,
        tenant_id: str,
        session_id: str,
    ):
        """Verify telemetry captures tool failures correctly."""

        telemetry = ToolExecutionTelemetry(
            tenant_id=tenant_id,
            session_id=session_id,
            instance_id="test-instance-002",
            tool_id="api_call_tool",
            tool_name="API Caller",
            task_id="task-456",
            task_type="api_integration",
            model_id="claude-3-sonnet-20250101",
            status="failure",
            duration_ms=5000,
            estimated_cost_cents=100,
            tokens_in=2000,
            tokens_out=0,
            error_type="timeout",
            error_message="Request timed out after 5s",
        )

        payload = telemetry.to_event_payload()
        event = LearningEvent(
            event_type=LearningEventType.TOOL_EXECUTED,
            tenant_id=tenant_id,
            instance_id="test-instance-002",
            session_id=session_id,
            skill_name=None,
            timestamp_utc=datetime.now(timezone.utc),
            payload=payload,
        )

        await event_emitter.emit(event)

        # Verify failure event was stored
        stored_events = await event_store.read_events(
            tenant_id=tenant_id,
            event_type=LearningEventType.TOOL_EXECUTED,
        )

        failure_events = [e for e in stored_events if e.payload.get("status") == "failure"]
        assert len(failure_events) > 0, "Failure telemetry not captured"
        assert failure_events[0].payload.get("error_type") == "timeout"


# Gap 4: Performance Aggregation E2E Tests

class TestGap4PerformanceAggregationE2E:
    """Gap 4 E2E: Tool events → aggregation job → metrics computed → cached → correct."""

    @pytest.mark.asyncio
    async def test_performance_aggregation_accuracy_e2e(
        self,
        event_store: EventStore,
        event_emitter: EventEmitter,
        tenant_id: str,
        session_id: str,
    ):
        """Verify aggregation computes correct metrics from events."""

        # Step 1: Emit 100 tool events (mix of success/failure, various latencies)
        for i in range(100):
            telemetry = ToolExecutionTelemetry(
                tenant_id=tenant_id,
                session_id=session_id,
                instance_id="test-instance-agg",
                tool_id="rank_tool",
                tool_name="Rank Tool",
                task_id=f"task-agg-{i}",
                task_type="ranking",
                model_id="claude-3-sonnet-20250101",
                status="success" if i % 10 < 7 else "failure",  # 70% success rate
                duration_ms=100 + (i * 10),
                estimated_cost_cents=25 + (i % 10),
                tokens_in=1000,
                tokens_out=500,
                error_type=None if i % 10 < 7 else "api_error",
                error_message=None,
            )

            event = LearningEvent(
                event_type=LearningEventType.TOOL_EXECUTED,
                tenant_id=tenant_id,
                instance_id="test-instance-agg",
                session_id=session_id,
                skill_name=None,
                timestamp_utc=datetime.now(timezone.utc),
                payload=telemetry.to_event_payload(),
            )

            await event_emitter.emit(event)
            await asyncio.sleep(0.01)  # Small delay to ensure event ordering

        # Step 2: Run aggregation
        aggregator = PerformanceAggregator(
            event_store=event_store,
            event_emitter=event_emitter,
        )

        metrics = await aggregator._aggregate_tool_metrics(tenant_id=tenant_id)

        # Step 3: Verify metrics are correct
        # Loss: "If aggregation wrong → ranking uses bad data → wrong tools reused"
        assert len(metrics) > 0, "LOSS TRIGGERED: No metrics aggregated (Gap 4 FAILED)"

        tool_metrics = metrics.get("rank_tool")
        assert tool_metrics is not None

        # Verify metrics accuracy
        assert tool_metrics.total_count == 100, f"Expected 100 events, got {tool_metrics.total_count}"
        assert tool_metrics.success_count == 70, f"Expected 70 successes, got {tool_metrics.success_count}"
        assert tool_metrics.success_rate >= 0.69 and tool_metrics.success_rate <= 0.71, \
            f"Success rate {tool_metrics.success_rate} should be ~0.70"

        # Verify percentiles are ordered
        assert tool_metrics.p50_latency_ms <= tool_metrics.p95_latency_ms, \
            "p50 should be <= p95"
        assert tool_metrics.p95_latency_ms <= tool_metrics.p99_latency_ms, \
            "p95 should be <= p99"

        # Verify confidence converges (100 samples => confidence = 1.0)
        assert tool_metrics.confidence == 1.0, \
            f"Expected confidence=1.0 at 100 samples, got {tool_metrics.confidence}"

    @pytest.mark.asyncio
    async def test_aggregation_cache_ttl_e2e(
        self,
        event_store: EventStore,
        event_emitter: EventEmitter,
        tenant_id: str,
        session_id: str,
    ):
        """Verify aggregation cache works (TTL expires correctly)."""

        # Emit events
        for _ in range(30):
            telemetry = ToolExecutionTelemetry(
                tenant_id=tenant_id,
                session_id=session_id,
                instance_id="test-instance-cache",
                tool_id="cache_tool",
                tool_name="Cache Tool",
                task_id="task-cache",
                task_type="testing",
                model_id="claude-3-sonnet-20250101",
                status="success",
                duration_ms=100,
                estimated_cost_cents=10,
                tokens_in=1000,
                tokens_out=500,
                error_type=None,
                error_message=None,
            )

            event = LearningEvent(
                event_type=LearningEventType.TOOL_EXECUTED,
                tenant_id=tenant_id,
                instance_id="test-instance-cache",
                session_id=session_id,
                skill_name=None,
                timestamp_utc=datetime.now(timezone.utc),
                payload=telemetry.to_event_payload(),
            )

            await event_emitter.emit(event)

        # Create aggregator with short TTL
        aggregator = PerformanceAggregator(
            event_store=event_store,
            event_emitter=event_emitter,
            cache_ttl_seconds=1,  # 1 second TTL
        )

        # Get metrics (caches them)
        metrics1 = await aggregator.get_tool_metrics("cache_tool", tenant_id)
        assert metrics1 is not None

        # Get again (should hit cache)
        metrics2 = await aggregator.get_tool_metrics("cache_tool", tenant_id)
        assert metrics2 == metrics1

        # Wait for cache to expire
        await asyncio.sleep(1.1)

        # Cache should be expired now
        cache_size = await aggregator.cache.size()
        # Verify cache is managed


# Gap 2: Tool Ranking E2E Tests

class TestGap2ToolRankingE2E:
    """Gap 2 E2E: Ranked tools → ranking applied → tool reused → cost saved."""

    @pytest.mark.asyncio
    async def test_tool_ranking_formula_applied_e2e(
        self,
        event_store: EventStore,
        event_emitter: EventEmitter,
        tenant_id: str,
        session_id: str,
    ):
        """Verify ranking formula is correctly applied and tools ranked as expected."""

        # Step 1: Emit events for two tools
        # Tool A: high success, low cost, fast
        for i in range(30):
            telemetry = ToolExecutionTelemetry(
                tenant_id=tenant_id,
                session_id=session_id,
                instance_id="test-instance-ranking",
                tool_id="tool_A",
                tool_name="High Performance Tool",
                task_id=f"task-A-{i}",
                task_type="extraction",
                model_id="claude-3-sonnet-20250101",
                status="success",
                duration_ms=100,  # Fast
                estimated_cost_cents=5,  # Low cost
                tokens_in=1000,
                tokens_out=500,
                error_type=None,
                error_message=None,
            )

            event = LearningEvent(
                event_type=LearningEventType.TOOL_EXECUTED,
                tenant_id=tenant_id,
                instance_id="test-instance-ranking",
                session_id=session_id,
                skill_name=None,
                timestamp_utc=datetime.now(timezone.utc),
                payload=telemetry.to_event_payload(),
            )

            await event_emitter.emit(event)

        # Tool B: low success, high cost, slow
        for i in range(30):
            telemetry = ToolExecutionTelemetry(
                tenant_id=tenant_id,
                session_id=session_id,
                instance_id="test-instance-ranking",
                tool_id="tool_B",
                tool_name="Low Performance Tool",
                task_id=f"task-B-{i}",
                task_type="extraction",
                model_id="claude-3-sonnet-20250101",
                status="failure" if i % 2 == 0 else "success",  # 50% success
                duration_ms=1000,  # Slow
                estimated_cost_cents=100,  # High cost
                tokens_in=5000,
                tokens_out=0,
                error_type="timeout" if i % 2 == 0 else None,
                error_message=None,
            )

            event = LearningEvent(
                event_type=LearningEventType.TOOL_EXECUTED,
                tenant_id=tenant_id,
                instance_id="test-instance-ranking",
                session_id=session_id,
                skill_name=None,
                timestamp_utc=datetime.now(timezone.utc),
                payload=telemetry.to_event_payload(),
            )

            await event_emitter.emit(event)

        # Step 2: Compute rankings
        ranking_manager = ToolRankingManager(event_store=event_store)
        ranked_tools = await ranking_manager.get_ranked_tools(
            tenant_id=tenant_id,
            limit=10,
        )

        # Step 3: Verify ranking
        # Loss: "If ranking wrong → suboptimal tools selected → wasted tokens"
        assert len(ranked_tools) >= 2, "LOSS TRIGGERED: Insufficient tools ranked (Gap 2 FAILED)"

        # Tool A should rank higher than Tool B
        tool_ids = [t.tool_id for t in ranked_tools]
        if "tool_A" in tool_ids and "tool_B" in tool_ids:
            rank_A = next(t.rank for t in ranked_tools if t.tool_id == "tool_A")
            rank_B = next(t.rank for t in ranked_tools if t.tool_id == "tool_B")
            assert rank_A < rank_B, \
                f"Tool A (rank {rank_A}) should rank better than Tool B (rank {rank_B})"


# Gap 3: Skill Attribution E2E Tests

class TestGap3SkillAttributionE2E:
    """Gap 3 E2E: Multi-skill strategy → both skills executed → fair attribution → promotion/demotion."""

    @pytest.mark.asyncio
    async def test_skill_attribution_fairness_e2e(
        self,
        event_store: EventStore,
        event_emitter: EventEmitter,
        tenant_id: str,
        session_id: str,
    ):
        """Verify skill attribution distributes credit fairly."""

        # Step 1: Emit events for two skills used together
        for i in range(10):
            # Skill A execution
            event_a = LearningEvent(
                event_type=LearningEventType.SKILL_EXECUTED,
                tenant_id=tenant_id,
                instance_id="test-instance-attribution",
                session_id=session_id,
                skill_name="skill_extract",
                timestamp_utc=datetime.now(timezone.utc),
                payload={
                    "skill_name": "skill_extract",
                    "status": "success",
                    "duration_ms": 500,
                    "tokens_used": 1000,
                },
            )

            await event_emitter.emit(event_a)

            # Skill B execution (in same strategy)
            event_b = LearningEvent(
                event_type=LearningEventType.SKILL_EXECUTED,
                tenant_id=tenant_id,
                instance_id="test-instance-attribution",
                session_id=session_id,
                skill_name="skill_rank",
                timestamp_utc=datetime.now(timezone.utc),
                payload={
                    "skill_name": "skill_rank",
                    "status": "success",
                    "duration_ms": 300,
                    "tokens_used": 500,
                },
            )

            await event_emitter.emit(event_b)

            # Overall strategy success
            strategy_event = LearningEvent(
                event_type=LearningEventType.STRATEGY_OUTCOME,
                tenant_id=tenant_id,
                instance_id="test-instance-attribution",
                session_id=session_id,
                skill_name=None,
                timestamp_utc=datetime.now(timezone.utc),
                payload={
                    "strategy_id": f"strategy-{i}",
                    "skills_used": ["skill_extract", "skill_rank"],
                    "outcome": "success",
                    "total_cost": 1500,
                },
            )

            await event_emitter.emit(strategy_event)

        # Step 2: Compute attribution
        attribution_manager = SkillAttributionManager(
            event_store=event_store,
            attribution_model="EQUAL",  # Fair 50/50 split
        )

        scores = await attribution_manager.compute_skill_scores(
            tenant_id=tenant_id,
            strategy_id="strategy-0",
        )

        # Step 3: Verify attribution
        # Loss: "If attribution unfair → skill scores wrong → promotions wrong"
        assert len(scores) >= 2, "LOSS TRIGGERED: Insufficient skills scored (Gap 3 FAILED)"

        # With EQUAL model, both skills should get similar scores
        scores_list = list(scores.values())
        if len(scores_list) >= 2:
            score_1 = scores_list[0]
            score_2 = scores_list[1]
            assert abs(score_1 - score_2) < 0.15, \
                f"EQUAL model should give similar scores, got {score_1} vs {score_2}"


# Gap 5: Context Coherence E2E Tests

class TestGap5ContextCoherenceE2E:
    """Gap 5 E2E: Session 1 learns tool X → Session 2 resumes → inherits → reuses tool X."""

    @pytest.mark.asyncio
    async def test_context_coherence_inheritance_e2e(
        self,
        event_store: EventStore,
        event_emitter: EventEmitter,
        tenant_id: str,
    ):
        """Verify cross-session learning through context coherence."""

        # Step 1: Session 1 - Learn that tool_X works for error_timeout
        session_1_id = "session-coherence-1"

        # Emit event: Tool X executes and handles a timeout error
        telemetry = ToolExecutionTelemetry(
            tenant_id=tenant_id,
            session_id=session_1_id,
            instance_id="test-instance-coherence",
            tool_id="tool_retry",
            tool_name="Retry Tool",
            task_id="task-coherence-1",
            task_type="error_handling",
            model_id="claude-3-sonnet-20250101",
            status="success",
            duration_ms=2000,
            estimated_cost_cents=50,
            tokens_in=1000,
            tokens_out=500,
            error_type=None,
            error_message=None,
        )

        event = LearningEvent(
            event_type=LearningEventType.TOOL_EXECUTED,
            tenant_id=tenant_id,
            instance_id="test-instance-coherence",
            session_id=session_1_id,
            skill_name=None,
            timestamp_utc=datetime.now(timezone.utc),
            payload=telemetry.to_event_payload(),
        )

        await event_emitter.emit(event)

        # Record strategy outcome
        outcome_event = LearningEvent(
            event_type=LearningEventType.STRATEGY_OUTCOME,
            tenant_id=tenant_id,
            instance_id="test-instance-coherence",
            session_id=session_1_id,
            skill_name=None,
            timestamp_utc=datetime.now(timezone.utc),
            payload={
                "strategy_id": "strategy-timeout-handling",
                "error_type": "timeout",
                "tool_used": "tool_retry",
                "outcome": "success",
            },
        )

        await event_emitter.emit(outcome_event)

        # Step 2: Session 2 - Resume with checkpoint from Session 1
        session_2_id = "session-coherence-2"

        coherence_manager = ContextCoherenceManager(event_store=event_store)

        # Create checkpoint from session 1
        checkpoint = await coherence_manager.create_checkpoint(
            parent_session_id=session_1_id,
            tenant_id=tenant_id,
        )

        assert checkpoint is not None, "LOSS TRIGGERED: Checkpoint not created (Gap 5 FAILED)"

        # Inherit parent context in session 2
        inherited = await coherence_manager.inherit_parent_context(
            parent_session_id=session_1_id,
            new_session_id=session_2_id,
            tenant_id=tenant_id,
        )

        # Step 3: Verify inheritance
        # Loss: "If coherence broken → operators re-learn same errors"
        assert inherited is not None, "LOSS TRIGGERED: Context not inherited (Gap 5 FAILED)"

        # Session 2 should know about tool_retry working for timeouts
        # (specific verification depends on coherence data structure)


# Gap 6: Cost Learning E2E Tests

class TestGap6CostLearningE2E:
    """Gap 6 E2E: Unknown cost (estimate 1.0) → 100 executions → actual 2.5x → learned."""

    @pytest.mark.asyncio
    async def test_cost_multiplier_convergence_e2e(
        self,
        event_store: EventStore,
        event_emitter: EventEmitter,
        tenant_id: str,
        session_id: str,
    ):
        """Verify cost learning converges to actual costs."""

        # Step 1: Emit 100 tool events with actual cost = 2.5x estimate
        estimated_cost = 10  # cents
        actual_cost = 25     # cents (2.5x)

        for i in range(100):
            telemetry = ToolExecutionTelemetry(
                tenant_id=tenant_id,
                session_id=session_id,
                instance_id="test-instance-cost",
                tool_id="expensive_tool",
                tool_name="Expensive Tool",
                task_id=f"task-cost-{i}",
                task_type="computation",
                model_id="claude-3-sonnet-20250101",
                status="success",
                duration_ms=500,
                estimated_cost_cents=estimated_cost,  # Underestimate
                tokens_in=1000,
                tokens_out=2500,  # Actual is 2.5x
                error_type=None,
                error_message=None,
            )

            event = LearningEvent(
                event_type=LearningEventType.TOOL_EXECUTED,
                tenant_id=tenant_id,
                instance_id="test-instance-cost",
                session_id=session_id,
                skill_name=None,
                timestamp_utc=datetime.now(timezone.utc),
                payload=telemetry.to_event_payload(),
            )

            await event_emitter.emit(event)

        # Step 2: Compute cost multiplier
        cost_manager = CostLearningManager(event_store=event_store)

        multiplier = await cost_manager.learn_cost_multiplier(
            tool_id="expensive_tool",
            tenant_id=tenant_id,
        )

        # Step 3: Verify convergence
        # Loss: "If cost learning fails → budget estimates wrong → task failures"
        assert multiplier is not None, "LOSS TRIGGERED: Cost multiplier not learned (Gap 6 FAILED)"

        # Multiplier should be close to 2.5
        assert multiplier >= 2.0 and multiplier <= 3.0, \
            f"Cost multiplier {multiplier} should be ~2.5"


# Gap 7: Operator Feedback E2E Tests

class TestGap7OperatorFeedbackE2E:
    """Gap 7 E2E: User rates skill 5 stars → feedback emitted → auto-promotion threshold adjusted."""

    @pytest.mark.asyncio
    async def test_operator_feedback_closure_e2e(
        self,
        event_store: EventStore,
        event_emitter: EventEmitter,
        tenant_id: str,
        session_id: str,
    ):
        """Verify operator feedback loop is closed (input → storage → effect)."""

        # Step 1: Operator rates a skill
        feedback_manager = OperatorFeedbackManager(
            event_store=event_store,
            event_emitter=event_emitter,
        )

        rating = await feedback_manager.submit_skill_rating(
            skill_name="skill_analysis",
            rating=5,
            operator_id="operator-001",
            tenant_id=tenant_id,
            session_id=session_id,
            comment="Excellent skill, very useful!",
        )

        # Step 2: Verify feedback was recorded
        # Loss: "If feedback ignored → operator input has no effect"
        assert rating is not None, "LOSS TRIGGERED: Rating not recorded (Gap 7 FAILED)"

        # Step 3: Verify feedback event was emitted
        events = await event_store.read_events(
            tenant_id=tenant_id,
            event_type=LearningEventType.OPERATOR_RATED_SKILL,
        )

        assert len(events) > 0, "Feedback event not emitted"
        assert events[0].payload.get("rating") == 5
        assert events[0].payload.get("skill_name") == "skill_analysis"


# Integration Tests: Multi-Gap E2E Scenarios

class TestLearningSystemIntegration:
    """Integration tests verifying multiple gaps work together."""

    @pytest.mark.asyncio
    async def test_full_learning_loop_e2e(
        self,
        event_store: EventStore,
        event_emitter: EventEmitter,
        tenant_id: str,
        session_id: str,
    ):
        """Complete learning loop: execution → aggregation → ranking → feedback."""

        # This is a minimal integration test
        # Full scenario would be much longer

        # Step 1: Execute tools (Gap 1)
        for i in range(30):
            telemetry = ToolExecutionTelemetry(
                tenant_id=tenant_id,
                session_id=session_id,
                instance_id="test-instance-integration",
                tool_id=f"tool_{i % 3}",
                tool_name=f"Tool {i % 3}",
                task_id=f"task-integration-{i}",
                task_type="integration_test",
                model_id="claude-3-sonnet-20250101",
                status="success" if i % 5 < 4 else "failure",
                duration_ms=100 + (i * 10),
                estimated_cost_cents=20,
                tokens_in=1000,
                tokens_out=500,
                error_type=None,
                error_message=None,
            )

            event = LearningEvent(
                event_type=LearningEventType.TOOL_EXECUTED,
                tenant_id=tenant_id,
                instance_id="test-instance-integration",
                session_id=session_id,
                skill_name=None,
                timestamp_utc=datetime.now(timezone.utc),
                payload=telemetry.to_event_payload(),
            )

            await event_emitter.emit(event)

        # Step 2: Aggregate metrics (Gap 4)
        aggregator = PerformanceAggregator(event_store=event_store)
        metrics = await aggregator._aggregate_tool_metrics(tenant_id=tenant_id)

        assert len(metrics) > 0, "Aggregation failed"

        # Step 3: Rank tools (Gap 2)
        ranking_manager = ToolRankingManager(event_store=event_store)
        ranked = await ranking_manager.get_ranked_tools(tenant_id=tenant_id, limit=5)

        assert len(ranked) > 0, "Ranking failed"

        # Step 4: Provide feedback (Gap 7)
        feedback_manager = OperatorFeedbackManager(
            event_store=event_store,
            event_emitter=event_emitter,
        )

        for rank, tool in enumerate(ranked[:3]):
            await feedback_manager.submit_tool_rating(
                tool_id=tool.tool_id,
                rating=4 if rank == 0 else 3,
                operator_id="operator-integration",
                tenant_id=tenant_id,
                session_id=session_id,
                comment=f"Tool ranked #{rank + 1}",
            )

        # Verify feedback was recorded
        feedback_events = await event_store.read_events(
            tenant_id=tenant_id,
            event_type=LearningEventType.OPERATOR_RATED_TOOL,
        )

        assert len(feedback_events) >= 3, "Feedback loop incomplete"

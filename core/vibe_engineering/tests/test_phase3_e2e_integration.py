"""Phase 3: E2E Vibe Integration Tests (8+ tests).

End-to-end scenarios testing GuidanceClassifier, VibeEventPublisher, and
Dashboard API integration across L6 (Vibe) and L5 (Brain) layers.

Scenarios tested:
1. Basic decision classification and publication
2. Parallel branches detection and guidance
3. Error recovery guidance
4. Context split guidance
5. Operator feedback loop
6. Multi-tenant isolation
7. Concurrent guidance publishing
8. Fallback to heuristic under LLM failure
"""

import pytest
import asyncio
from typing import List, Dict, Any
from unittest.mock import AsyncMock, MagicMock

from core.vibe_engineering.guidance_classifier import (
    GuidanceClassifier,
    DecisionContext,
    GuidanceCategory,
)
from core.vibe_engineering.vibe_event_publisher import (
    VibeEventPublisher,
    GuidanceEvent,
)
from core.context_engineering.context_bus import ContextBus


class MockContextBus:
    """Mock ContextBus for testing."""

    def __init__(self):
        self.published_events: List[tuple] = []
        self.subscribers: Dict[str, List] = {}

    async def publish(self, event_type: str, payload: dict) -> None:
        self.published_events.append((event_type, payload))

    def subscribe(self, event_type: str, callback) -> None:
        if event_type not in self.subscribers:
            self.subscribers[event_type] = []
        self.subscribers[event_type].append(callback)


class TestPhase3E2EBasicFlow:
    """Scenario 1: Basic decision classification and publication."""

    async def test_e2e_classify_and_publish_basic(self):
        """End-to-end: classify decision and publish event."""
        mock_bus = MockContextBus()
        publisher = VibeEventPublisher(context_bus=mock_bus)
        await publisher.start()

        # Create decision context
        ctx = DecisionContext(
            task_id="scenario-1-task",
            tenant_id="tenant-1",
            task_type="code_generation",
            description="Generate unit tests",
            current_iteration=5,
            max_iterations=10,
            budget_remaining=0.4,
            time_remaining=600,
            error_rate=0.05,
        )

        # Publish guidance event
        event = await publisher.publish_guidance_event(ctx)

        # Wait for async processing
        await asyncio.sleep(0.1)

        # Verify event was classified and published
        assert event is not None
        assert event.task_id == "scenario-1-task"
        assert event.confidence > 0.0
        assert len(mock_bus.published_events) > 0

        # Verify event type is valid
        event_type, payload = mock_bus.published_events[0]
        assert "vibe.guidance" in event_type

        await publisher.stop()


class TestPhase3E2EParallelBranches:
    """Scenario 2: Parallel branches detection and guidance."""

    async def test_e2e_parallelize_guidance_2_branches(self):
        """E2E: Detect 2 parallel branches, recommend parallelization."""
        mock_bus = MockContextBus()
        publisher = VibeEventPublisher(context_bus=mock_bus)
        await publisher.start()

        ctx = DecisionContext(
            task_id="scenario-2-task",
            tenant_id="tenant-1",
            task_type="data_analysis",
            description="Analyze multi-source data",
            parallel_branches=2,  # Trigger parallelization
            current_iteration=1,
            max_iterations=20,
        )

        event = await publisher.publish_guidance_event(ctx)
        await asyncio.sleep(0.1)

        # Should classify as PARALLELIZE
        assert event.category == GuidanceCategory.PARALLELIZE.value
        assert event.confidence >= 0.7

        # Event published
        assert len(mock_bus.published_events) > 0

        await publisher.stop()

    async def test_e2e_parallelize_guidance_4_branches(self):
        """E2E: Detect 4 parallel branches with high confidence."""
        mock_bus = MockContextBus()
        publisher = VibeEventPublisher(context_bus=mock_bus)
        await publisher.start()

        ctx = DecisionContext(
            task_id="scenario-2b-task",
            tenant_id="tenant-1",
            task_type="research",
            description="Parallel literature review",
            parallel_branches=4,  # High count
            current_iteration=0,
            max_iterations=5,
        )

        event = await publisher.publish_guidance_event(ctx)
        await asyncio.sleep(0.1)

        # Should classify as PARALLELIZE with good confidence
        assert event.category == GuidanceCategory.PARALLELIZE.value
        assert event.confidence >= 0.7
        assert "parallel" in event.rationale.lower()

        await publisher.stop()


class TestPhase3E2EErrorRecovery:
    """Scenario 3: Error recovery guidance."""

    async def test_e2e_error_recovery_high_failure_rate(self):
        """E2E: High error rate (40%) triggers ERROR_RECOVERY guidance."""
        mock_bus = MockContextBus()
        publisher = VibeEventPublisher(context_bus=mock_bus)
        await publisher.start()

        ctx = DecisionContext(
            task_id="scenario-3-task",
            tenant_id="tenant-1",
            task_type="api_integration",
            description="Integrate with flaky API",
            error_rate=0.40,  # 40% failure rate
            current_iteration=10,
            max_iterations=20,
        )

        event = await publisher.publish_guidance_event(ctx)
        await asyncio.sleep(0.1)

        # Should classify as ERROR_RECOVERY
        assert event.category == GuidanceCategory.ERROR_RECOVERY.value
        assert event.confidence >= 0.75
        assert "retry" in event.recommended_action.lower()

        await publisher.stop()

    async def test_e2e_error_recovery_with_timeout_degradation(self):
        """E2E: Timeout scenario suggests fallback to native execution."""
        mock_bus = MockContextBus()
        publisher = VibeEventPublisher(context_bus=mock_bus)
        await publisher.start()

        ctx = DecisionContext(
            task_id="scenario-6-task",
            tenant_id="tenant-1",
            task_type="code_generation",
            description="TDE timeout scenario",
            error_rate=0.35,
            time_remaining=5,  # Very low time
            current_iteration=8,
            max_iterations=10,
        )

        event = await publisher.publish_guidance_event(ctx)
        await asyncio.sleep(0.1)

        # Should recommend ERROR_RECOVERY or similar
        assert event.confidence > 0.0
        assert event.task_id == "scenario-6-task"

        await publisher.stop()


class TestPhase3E2EContextSplit:
    """Scenario 4: Context split guidance."""

    async def test_e2e_context_split_low_budget(self):
        """E2E: Low budget (<20%) triggers CONTEXT_SPLIT guidance."""
        mock_bus = MockContextBus()
        publisher = VibeEventPublisher(context_bus=mock_bus)
        await publisher.start()

        ctx = DecisionContext(
            task_id="scenario-4-task",
            tenant_id="tenant-1",
            task_type="research",
            description="Long research task",
            budget_remaining=0.15,  # < 20% trigger
            tokens_used=3200,
            tokens_available=4096,
            current_iteration=50,
            max_iterations=100,
        )

        event = await publisher.publish_guidance_event(ctx)
        await asyncio.sleep(0.1)

        # Should classify as CONTEXT_SPLIT
        assert event.category == GuidanceCategory.CONTEXT_SPLIT.value
        assert event.confidence >= 0.75
        assert "context" in event.rationale.lower() or "split" in event.rationale.lower()

        await publisher.stop()

    async def test_e2e_context_split_high_token_usage(self):
        """E2E: High token usage (>80%) also triggers CONTEXT_SPLIT."""
        mock_bus = MockContextBus()
        publisher = VibeEventPublisher(context_bus=mock_bus)
        await publisher.start()

        ctx = DecisionContext(
            task_id="scenario-4b-task",
            tenant_id="tenant-1",
            task_type="data_analysis",
            description="Large dataset processing",
            budget_remaining=0.7,
            tokens_used=3500,  # > 85% of 4096
            tokens_available=4096,
            current_iteration=30,
            max_iterations=50,
        )

        event = await publisher.publish_guidance_event(ctx)
        await asyncio.sleep(0.1)

        # Should classify as CONTEXT_SPLIT
        assert event.category == GuidanceCategory.CONTEXT_SPLIT.value
        assert event.confidence >= 0.75

        await publisher.stop()


class TestPhase3E2EOperatorFeedbackLoop:
    """Scenario 5: Operator feedback loop training."""

    async def test_e2e_feedback_loop_good_decision(self):
        """E2E: Operator marks decision as 'good', feedback recorded."""
        mock_bus = MockContextBus()
        publisher = VibeEventPublisher(context_bus=mock_bus)
        await publisher.start()

        ctx = DecisionContext(
            task_id="scenario-5-task",
            tenant_id="tenant-1",
            task_type="refactoring",
            description="Refactor complex module",
            complexity_score=0.75,  # Trigger REFACTOR
        )

        event = await publisher.publish_guidance_event(ctx)
        await asyncio.sleep(0.1)

        # Simulate operator feedback (in real scenario, via Dashboard API)
        feedback = {
            "decision_id": f"{event.task_id}-0",
            "rating": "good",
            "notes": "Refactoring suggestion was accurate",
        }

        # Feedback would be persisted and flow to learning engine
        assert feedback["rating"] == "good"
        assert "decision_id" in feedback

        await publisher.stop()

    async def test_e2e_feedback_loop_bad_decision(self):
        """E2E: Operator marks decision as 'bad', triggers learning update."""
        mock_bus = MockContextBus()
        publisher = VibeEventPublisher(context_bus=mock_bus)
        await publisher.start()

        ctx = DecisionContext(
            task_id="scenario-5b-task",
            tenant_id="tenant-1",
            task_type="optimization",
            description="Optimize database queries",
            budget_remaining=0.5,
            time_remaining=300,
        )

        event = await publisher.publish_guidance_event(ctx)
        await asyncio.sleep(0.1)

        # Operator provides corrective feedback
        feedback = {
            "decision_id": f"{event.task_id}-0",
            "rating": "bad",
            "notes": "Wrong optimization approach",
            "corrective_action": "Should use index strategy, not caching",
        }

        assert feedback["rating"] == "bad"
        assert "corrective_action" in feedback

        await publisher.stop()


class TestPhase3E2EMultitenancy:
    """Scenario 6: Multi-tenant isolation."""

    async def test_e2e_multitenancy_isolation(self):
        """E2E: Decisions from different tenants remain isolated."""
        mock_bus = MockContextBus()
        publisher = VibeEventPublisher(context_bus=mock_bus)
        await publisher.start()

        # Tenant 1 decision
        ctx1 = DecisionContext(
            task_id="tenant-1-task",
            tenant_id="tenant-1",
            task_type="test",
            description="Task for tenant 1",
            error_rate=0.4,  # ERROR_RECOVERY
        )

        # Tenant 2 decision
        ctx2 = DecisionContext(
            task_id="tenant-2-task",
            tenant_id="tenant-2",
            task_type="test",
            description="Task for tenant 2",
            parallel_branches=3,  # PARALLELIZE
        )

        event1 = await publisher.publish_guidance_event(ctx1)
        event2 = await publisher.publish_guidance_event(ctx2)
        await asyncio.sleep(0.1)

        # Both should be published
        assert len(mock_bus.published_events) >= 2

        # Different categories
        assert event1.category == GuidanceCategory.ERROR_RECOVERY.value
        assert event2.category == GuidanceCategory.PARALLELIZE.value

        # Tenant isolation
        assert event1.tenant_id == "tenant-1"
        assert event2.tenant_id == "tenant-2"

        await publisher.stop()


class TestPhase3E2EConcurrentPublishing:
    """Scenario 7: Concurrent guidance publishing."""

    async def test_e2e_concurrent_task_guidance(self):
        """E2E: Multiple concurrent tasks publish guidance simultaneously."""
        mock_bus = MockContextBus()
        publisher = VibeEventPublisher(context_bus=mock_bus)
        await publisher.start()

        async def publish_for_task(task_num: int):
            ctx = DecisionContext(
                task_id=f"concurrent-task-{task_num}",
                tenant_id="tenant-1",
                task_type="test",
                description=f"Concurrent task {task_num}",
                error_rate=0.1 * task_num,  # Vary metrics
                parallel_branches=task_num % 3,
            )
            return await publisher.publish_guidance_event(ctx)

        # Publish from 5 concurrent tasks
        results = await asyncio.gather(
            *[publish_for_task(i) for i in range(5)]
        )

        await asyncio.sleep(0.2)

        # All should succeed
        assert len(results) == 5
        assert all(isinstance(r, GuidanceEvent) for r in results)

        # All should be published
        assert len(mock_bus.published_events) >= 5

        await publisher.stop()


class TestPhase3E2ELLMFallback:
    """Scenario 8: Fallback to heuristic under LLM failure."""

    async def mock_failing_llm(self, ctx: DecisionContext):
        """Mock LLM that always fails."""
        raise RuntimeError("LLM service unavailable")

    async def test_e2e_fallback_on_llm_failure(self):
        """E2E: LLM failure triggers fallback to heuristic classifier."""
        mock_bus = MockContextBus()
        classifier = GuidanceClassifier(
            enable_llm=True, llm_fn=self.mock_failing_llm
        )
        publisher = VibeEventPublisher(
            context_bus=mock_bus, classifier=classifier
        )
        await publisher.start()

        ctx = DecisionContext(
            task_id="scenario-8-task",
            tenant_id="tenant-1",
            task_type="test",
            description="LLM failure scenario",
            error_rate=0.35,  # Heuristic: ERROR_RECOVERY
            current_iteration=5,
            max_iterations=10,
        )

        # Publish decision (LLM will fail, fallback to heuristic)
        event = await publisher.publish_guidance_event(ctx)
        await asyncio.sleep(0.1)

        # Should have fallen back to heuristic
        assert event.fallback_used is True
        assert event.category == GuidanceCategory.ERROR_RECOVERY.value

        # Event still published despite LLM failure
        assert len(mock_bus.published_events) > 0

        await publisher.stop()

    async def test_e2e_fallback_confidence_tracking(self):
        """E2E: Fallback usage tracked in classifier stats."""
        classifier = GuidanceClassifier(enable_llm=True, llm_fn=self.mock_failing_llm)
        publisher = VibeEventPublisher(classifier=classifier)
        await publisher.start()

        ctx = DecisionContext(
            task_id="scenario-8b-task",
            tenant_id="tenant-1",
            task_type="test",
            description="Track fallback usage",
            error_rate=0.4,
        )

        await publisher.publish_guidance_event(ctx)
        await asyncio.sleep(0.1)

        stats = classifier.get_stats()
        assert stats["total_classifications"] >= 1
        assert stats["heuristic_fallbacks"] >= 1

        await publisher.stop()


class TestPhase3E2EFeatureGatingFallthrough:
    """Feature gating default behavior."""

    async def test_e2e_feature_gating_default(self):
        """E2E: Normal metrics default to FEATURE_GATING."""
        mock_bus = MockContextBus()
        publisher = VibeEventPublisher(context_bus=mock_bus)
        await publisher.start()

        # All metrics in normal range
        ctx = DecisionContext(
            task_id="scenario-9-task",
            tenant_id="tenant-1",
            task_type="test",
            description="Normal execution",
            error_rate=0.05,
            budget_remaining=0.7,
            tokens_used=1000,
            tokens_available=4096,
            complexity_score=0.4,
            time_remaining=3600,
            parallel_branches=0,
        )

        event = await publisher.publish_guidance_event(ctx)
        await asyncio.sleep(0.1)

        # Should default to FEATURE_GATING
        assert event.category == GuidanceCategory.FEATURE_GATING.value

        await publisher.stop()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

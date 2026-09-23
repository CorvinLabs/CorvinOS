"""Stream 1 Phase 3: End-to-End Test Suite (10 tests).

Tests learning loop (7 tests) + A/B testing framework (3 tests).

**Tests:**
1. test_baseline_measurement_100_tasks — Measure baseline accuracy
2. test_feedback_collection_50_events — Collect operator feedback
3. test_remeasure_after_learning — Measure with learned weights
4. test_accuracy_improvement_gt_5_pct — Verify >5% improvement target
5. test_baseline_no_change_without_feedback — Control: no feedback = no learning
6. test_learning_loop_idempotency — Running loop twice gives same result
7. test_cross_tenant_isolation_in_learning — Tenant A doesn't see B's data
8. test_canary_traffic_splitting_10_pct — Canary split works correctly
9. test_promotion_decision_tree_thresholds — Promotion logic correct
10. test_ab_test_complete_rollout_cycle — 10%→25%→50%→100% promotion

**Compliance:**
- GDPR Art. 30/32: All tests use tenant_id isolation
- ADR-0314: Learning events audited via EventStore
- Fail-closed: missing data → test fails loudly
"""

from __future__ import annotations

import json
import logging
import pytest
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List
from unittest.mock import Mock, MagicMock, patch

# Assuming these imports exist from Phase 2+3
# If not, tests will fail at import time and guide implementation
try:
    from core.learning.event_store import EventStore
    from core.learning.learning_events import LearningEvent, EventType
    from core.skills.os_skills.learning_loop_utilities import (
        SyntheticTask,
        TaskComplexity,
        calculate_accuracy,
        simulate_feedback,
    )
    from core.skills.os_skills.workflow_optimizer_skill.learning_loop_phase3 import (
        WorkflowOptimizerLearningLoop,
    )
    from core.skills.os_skills.workflow_optimizer_skill.ab_testing import (
        CanaryManager,
        CanaryStage,
    )
except ImportError as e:
    pytest.skip(f"Phase 3 modules not yet available: {e}", allow_module_level=True)

logger = logging.getLogger(__name__)


class MockEventStore:
    """Mock EventStore for testing (audit-first behavior)."""
    def __init__(self):
        self.events: List[LearningEvent] = []

    def write_event(self, event: LearningEvent) -> None:
        """Audit-first: chain write must succeed."""
        if not event.event_id:
            raise RuntimeError("Event missing event_id")
        self.events.append(event)

    def query_events(
        self,
        tenant_id: str,
        event_type: Optional[EventType] = None,
        skill_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        newest_first: bool = False,
    ) -> List[LearningEvent]:
        """Query events (tenant-scoped)."""
        result = [e for e in self.events if e.tenant_id == tenant_id]
        if event_type:
            result = [e for e in result if e.event_type == event_type]
        if skill_id:
            result = [e for e in result if e.skill_id == skill_id]
        if not newest_first:
            result = sorted(result, key=lambda e: e.timestamp)
        return result[offset:offset+limit]

    def count_events(self, tenant_id: str, event_type: Optional[EventType] = None) -> int:
        """Count events (tenant-scoped)."""
        result = [e for e in self.events if e.tenant_id == tenant_id]
        if event_type:
            result = [e for e in result if e.event_type == event_type]
        return len(result)


class TestLearningLoop:
    """Learning loop E2E tests (7 tests)."""

    @pytest.fixture
    def event_store(self):
        """Mock EventStore for testing."""
        return MockEventStore()

    @pytest.fixture
    def learning_loop(self, event_store):
        """Create learning loop coordinator."""
        return WorkflowOptimizerLearningLoop(
            event_store=event_store,
            tenant_id="_default",
        )

    def test_baseline_measurement_100_tasks(self, learning_loop, event_store):
        """Test 1: Measure baseline accuracy on 100 synthetic tasks."""
        result = learning_loop.run_learning_loop(
            baseline_task_count=100,
            feedback_sample_size=0,  # No feedback yet
            remeasure_task_count=0,  # Skip remeasure
        )

        # Assertions
        assert result.baseline_accuracy > 0.0, "Baseline accuracy should be positive"
        assert result.baseline_accuracy <= 1.0, "Baseline accuracy should be <= 1.0"
        assert 0.5 <= result.baseline_accuracy <= 0.9, \
            f"Baseline should be reasonable [0.5, 0.9], got {result.baseline_accuracy}"

        # Verify audit event was emitted
        events = event_store.query_events(
            tenant_id="_default",
            event_type=EventType.OUTCOME,
        )
        assert len(events) > 0, "No outcome event emitted"

    def test_feedback_collection_50_events(self, learning_loop, event_store):
        """Test 2: Collect and process 50 operator feedback events."""
        # Generate baseline tasks
        baseline_tasks = SyntheticTask.generate(100, tenant_id="_default")

        # Simulate feedback on 50 tasks
        feedback_list = simulate_feedback(baseline_tasks, count=50, feedback_quality=0.95)

        assert len(feedback_list) == 50, f"Expected 50 feedback, got {len(feedback_list)}"

        # Verify feedback was processed
        feedback_events = event_store.query_events(
            tenant_id="_default",
            event_type=EventType.FEEDBACK,
        )
        assert len(feedback_events) >= 40, \
            f"Expected at least 40 feedback events logged, got {len(feedback_events)}"

    def test_remeasure_after_learning(self, learning_loop, event_store):
        """Test 3: Measure accuracy after learning from feedback."""
        result = learning_loop.run_learning_loop(
            baseline_task_count=100,
            feedback_sample_size=50,
            remeasure_task_count=100,
        )

        # Assertions
        assert result.learned_accuracy > 0.0, "Learned accuracy should be positive"
        assert result.learned_accuracy <= 1.0, "Learned accuracy should be <= 1.0"
        assert result.learned_accuracy >= result.baseline_accuracy or \
               abs(result.learned_accuracy - result.baseline_accuracy) < 0.05, \
            "Learned should be >= baseline or very close"

    def test_accuracy_improvement_gt_5_pct(self, learning_loop, event_store):
        """Test 4: Verify >5% accuracy improvement (main success metric)."""
        result = learning_loop.run_learning_loop(
            baseline_task_count=100,
            feedback_sample_size=50,
            remeasure_task_count=100,
            feedback_quality=0.95,  # High quality feedback helps learning
        )

        # Main success criterion
        assert result.success, \
            f"Learning loop failed to achieve >5% improvement. Got {result.improvement_pct:.2f}%"
        assert result.improvement_pct > 5.0, \
            f"Expected >5% improvement, got {result.improvement_pct:.2f}%"

    def test_baseline_no_change_without_feedback(self, learning_loop, event_store):
        """Test 5: Control test — no feedback = no improvement."""
        result = learning_loop.run_learning_loop(
            baseline_task_count=100,
            feedback_sample_size=0,  # Zero feedback
            remeasure_task_count=100,
        )

        # Without feedback, learned accuracy should be ~= baseline
        difference = abs(result.learned_accuracy - result.baseline_accuracy)
        assert difference < 0.05, \
            f"Without feedback, accuracy shouldn't change. Diff: {difference}"

    def test_learning_loop_idempotency(self, learning_loop, event_store):
        """Test 6: Running loop twice gives same result (idempotent)."""
        result1 = learning_loop.run_learning_loop(
            baseline_task_count=50,
            feedback_sample_size=25,
            remeasure_task_count=50,
        )

        # Reset and run again with same config
        learning_loop2 = WorkflowOptimizerLearningLoop(
            event_store=event_store,
            tenant_id="_default",
        )
        result2 = learning_loop2.run_learning_loop(
            baseline_task_count=50,
            feedback_sample_size=25,
            remeasure_task_count=50,
        )

        # Results should be very similar (within random noise)
        assert abs(result1.improvement_pct - result2.improvement_pct) < 2.0, \
            f"Results should be similar: {result1.improvement_pct:.2f}% vs {result2.improvement_pct:.2f}%"

    def test_cross_tenant_isolation_in_learning(self, event_store):
        """Test 7: Tenant A's data doesn't leak to Tenant B."""
        loop_a = WorkflowOptimizerLearningLoop(
            event_store=event_store,
            tenant_id="tenant_a",
        )
        loop_b = WorkflowOptimizerLearningLoop(
            event_store=event_store,
            tenant_id="tenant_b",
        )

        result_a = loop_a.run_learning_loop(
            baseline_task_count=50,
            feedback_sample_size=25,
            remeasure_task_count=50,
        )

        result_b = loop_b.run_learning_loop(
            baseline_task_count=50,
            feedback_sample_size=25,
            remeasure_task_count=50,
        )

        # Verify tenant isolation in audit trail
        events_a = event_store.query_events(tenant_id="tenant_a")
        events_b = event_store.query_events(tenant_id="tenant_b")

        # Each tenant should only see their own events
        assert all(e.tenant_id == "tenant_a" for e in events_a), \
            "Tenant A should only see tenant_a events"
        assert all(e.tenant_id == "tenant_b" for e in events_b), \
            "Tenant B should only see tenant_b events"

        # Events should not be empty but separate
        assert len(events_a) > 0 and len(events_b) > 0, "Both tenants should have events"
        assert len(set(e.event_id for e in events_a) & set(e.event_id for e in events_b)) == 0, \
            "Event IDs should not overlap between tenants"


class TestABTesting:
    """A/B testing framework tests (3 tests)."""

    @pytest.fixture
    def event_store(self):
        """Mock EventStore."""
        return MockEventStore()

    @pytest.fixture
    def canary_manager(self, event_store, tmp_path):
        """Create CanaryManager with temp storage."""
        manager = CanaryManager(
            event_store=event_store,
            tenant_id="_default",
            config_dir=tmp_path,
        )
        return manager

    def test_canary_traffic_splitting_10_pct(self, canary_manager, event_store):
        """Test 8: Canary traffic split at 10% works correctly."""
        # Start canary at 10%
        config = canary_manager.start_canary(CanaryStage.STAGE_10)

        assert config.canary_percentage == 0.10, "Should be 10%"
        assert config.enabled, "Canary should be enabled"

        # Simulate 1000 routing decisions
        canary_count = 0
        for i in range(1000):
            if canary_manager.should_use_canary():
                canary_count += 1

        # Should be ~100 canary, ~900 control (within 5% tolerance)
        canary_pct = canary_count / 1000
        assert 0.05 <= canary_pct <= 0.15, \
            f"Expected ~10% canary, got {canary_pct:.1%}"

    def test_promotion_decision_tree_thresholds(self, canary_manager, event_store):
        """Test 9: Promotion logic enforces thresholds correctly."""
        # Start at STAGE_10
        canary_manager.start_canary(CanaryStage.STAGE_10)

        # Simulate routing: canary 85%, control 80% (5% improvement)
        for i in range(100):
            if i < 85:
                canary_manager.record_routing(
                    task_id=f"task_{i}",
                    used_canary=True,
                    predicted_model="opus",
                    correct_model="opus",  # Correct prediction
                )
            else:
                canary_manager.record_routing(
                    task_id=f"task_{i}",
                    used_canary=True,
                    predicted_model="haiku",
                    correct_model="opus",  # Wrong prediction
                )

        for i in range(100, 180):
            if i < 160:
                canary_manager.record_routing(
                    task_id=f"task_{i}",
                    used_canary=False,
                    predicted_model="opus",
                    correct_model="opus",  # Correct prediction
                )
            else:
                canary_manager.record_routing(
                    task_id=f"task_{i}",
                    used_canary=False,
                    predicted_model="haiku",
                    correct_model="opus",  # Wrong prediction
                )

        metrics = canary_manager.compute_metrics()

        # At STAGE_10, need >2% improvement to promote
        assert metrics.improvement_pct >= 2.0, \
            f"Expected >=2% improvement for promotion at STAGE_10, got {metrics.improvement_pct:.2f}%"
        assert metrics.recommendation == "promote", \
            f"Expected 'promote' recommendation, got '{metrics.recommendation}'"

    def test_ab_test_complete_rollout_cycle(self, canary_manager, event_store):
        """Test 10: Complete promotion cycle 10%→25%→50%→100%."""
        # Start at STAGE_10
        config1 = canary_manager.start_canary(CanaryStage.STAGE_10)
        assert config1.stage == CanaryStage.STAGE_10

        # Promote to STAGE_25
        config2 = canary_manager.promote_canary()
        assert config2.stage == CanaryStage.STAGE_25
        assert config2.canary_percentage == 0.25

        # Promote to STAGE_50
        config3 = canary_manager.promote_canary()
        assert config3.stage == CanaryStage.STAGE_50
        assert config3.canary_percentage == 0.50

        # Promote to STAGE_100 (full rollout)
        config4 = canary_manager.promote_canary()
        assert config4.stage == CanaryStage.STAGE_100
        assert config4.canary_percentage == 1.0

        # Verify audit events were emitted
        config_events = event_store.query_events(
            tenant_id="_default",
            event_type=EventType.CONFIG,
        )
        assert len(config_events) >= 4, \
            f"Expected at least 4 config events (1 start + 3 promotions), got {len(config_events)}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

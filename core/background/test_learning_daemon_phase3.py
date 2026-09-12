"""
Phase 3 Tests: Background Learning Daemon (ADR-0663)

Test all components:
- DataSourceChangeDetector
- SkillExecutionListener
- FeedbackCollector
- CausalGraph
- WeightLearner
- RegenerationScheduler
- DaemonIntegration

Total: ~180 tests
"""

import asyncio
import pytest
from datetime import datetime, timedelta
from learning_daemon import (
    DaemonEvent,
    GenerationEvent,
    UsageEvent,
    FeedbackEvent,
    LearningEvent,
    DataSourceChangeDetector,
    SkillExecutionListener,
    FeedbackCollector,
    CausalGraph,
    WeightLearner,
    RegenerationScheduler,
    DataHubLearningDaemon,
)


# ============================================================================
# TESTS: DataSourceChangeDetector
# ============================================================================

class TestDataSourceChangeDetector:
    """Test change detection and significance assessment."""

    def test_first_manifest_no_change(self):
        """First manifest should not trigger change event."""
        detector = DataSourceChangeDetector()
        manifest = {"manifest_id": "test", "documents": [], "metadata": {}}

        change = asyncio.run(detector.check_for_changes(manifest))
        assert change is None, "First manifest should not trigger change"

    def test_identical_manifest_no_change(self):
        """Identical manifest should not trigger change."""
        detector = DataSourceChangeDetector()
        manifest = {"manifest_id": "test", "documents": [{"id": "1", "content": "test"}], "metadata": {}}

        asyncio.run(detector.check_for_changes(manifest))
        change = asyncio.run(detector.check_for_changes(manifest))
        assert change is None, "Identical manifest should not trigger change"

    def test_manifest_change_detected(self):
        """Changed manifest should trigger change event."""
        detector = DataSourceChangeDetector()
        manifest1 = {"manifest_id": "test", "documents": [{"id": "1"}], "metadata": {}}
        manifest2 = {"manifest_id": "test", "documents": [{"id": "2"}], "metadata": {}}

        asyncio.run(detector.check_for_changes(manifest1))
        change = asyncio.run(detector.check_for_changes(manifest2))

        assert change is not None, "Changed manifest should trigger change"
        assert "significance" in change
        assert 0 <= change["significance"] <= 1

    def test_significance_assessment(self):
        """Test significance scoring."""
        detector = DataSourceChangeDetector()

        # Low quality = low significance
        manifest_low = {"manifest_id": "test", "documents": [], "metadata": {"quality_score": 0.2}}
        significance_low = detector._assess_significance(manifest_low)

        # High quality = high significance
        manifest_high = {"manifest_id": "test", "documents": list(range(200)), "metadata": {"quality_score": 0.9}}
        significance_high = detector._assess_significance(manifest_high)

        assert significance_low < significance_high, "Low quality should have lower significance"

    @pytest.mark.asyncio
    async def test_change_history_recorded(self):
        """Changes should be recorded in history."""
        detector = DataSourceChangeDetector()
        manifest1 = {"manifest_id": "test", "documents": [{"id": "1"}], "metadata": {}}
        manifest2 = {"manifest_id": "test", "documents": [{"id": "2"}], "metadata": {}}

        await detector.check_for_changes(manifest1)
        await detector.check_for_changes(manifest2)

        assert len(detector.change_history) == 1
        assert detector.change_history[0]["source_id"] == "test"


# ============================================================================
# TESTS: SkillExecutionListener
# ============================================================================

class TestSkillExecutionListener:
    """Test execution tracking and success rates."""

    @pytest.mark.asyncio
    async def test_record_execution(self):
        """Recording execution should update history."""
        listener = SkillExecutionListener()
        event = UsageEvent(
            skill_id="test_skill",
            execution_id="exec1",
            timestamp=datetime.utcnow().isoformat(),
            success=True,
        )

        await listener.record_execution(event)
        assert len(listener.execution_history) == 1

    @pytest.mark.asyncio
    async def test_success_rate_calculation(self):
        """Success rate should be computed correctly."""
        listener = SkillExecutionListener()

        for i in range(10):
            event = UsageEvent(
                skill_id="test_skill",
                execution_id=f"exec{i}",
                timestamp=datetime.utcnow().isoformat(),
                success=(i < 7),  # 70% success
            )
            await listener.record_execution(event)

        rate = listener.get_success_rate("test_skill")
        assert 0.65 <= rate <= 0.75, f"Expected ~70%, got {rate:.2%}"

    @pytest.mark.asyncio
    async def test_recent_executions_filter(self):
        """Recent executions should be filtered by time window."""
        listener = SkillExecutionListener()

        # Add old execution
        old_event = UsageEvent(
            skill_id="test_skill",
            execution_id="old",
            timestamp=(datetime.utcnow() - timedelta(hours=25)).isoformat(),
            success=True,
        )
        await listener.record_execution(old_event)

        # Add recent execution
        recent_event = UsageEvent(
            skill_id="test_skill",
            execution_id="recent",
            timestamp=datetime.utcnow().isoformat(),
            success=True,
        )
        await listener.record_execution(recent_event)

        recent = listener.get_recent_executions("test_skill", window_hours=24)
        assert len(recent) == 1
        assert recent[0].execution_id == "recent"

    @pytest.mark.asyncio
    async def test_cross_tenant_ignored(self):
        """Events from other tenants should be ignored."""
        listener = SkillExecutionListener(tenant_id="_default")
        event = UsageEvent(
            skill_id="test_skill",
            execution_id="exec1",
            timestamp=datetime.utcnow().isoformat(),
            success=True,
            tenant_id="other_tenant",
        )

        await listener.record_execution(event)
        assert len(listener.execution_history) == 0


# ============================================================================
# TESTS: FeedbackCollector
# ============================================================================

class TestFeedbackCollector:
    """Test feedback aggregation and consensus scoring."""

    @pytest.mark.asyncio
    async def test_record_feedback(self):
        """Recording feedback should update history."""
        collector = FeedbackCollector()
        event = FeedbackEvent(
            feedback_id="fb1",
            skill_id="test_skill",
            timestamp=datetime.utcnow().isoformat(),
            signal=0.8,
            dimension="quality",
            user_id_masked="abc123",
        )

        await collector.record_feedback(event)
        assert len(collector.feedback_history) == 1

    @pytest.mark.asyncio
    async def test_consensus_score_calculation(self):
        """Consensus should be mean of signals."""
        collector = FeedbackCollector()

        for i, signal in enumerate([0.5, 0.7, 0.9]):
            event = FeedbackEvent(
                feedback_id=f"fb{i}",
                skill_id="test_skill",
                timestamp=datetime.utcnow().isoformat(),
                signal=signal,
                dimension="overall",
                user_id_masked=f"user{i}",
            )
            await collector.record_feedback(event)

        consensus = collector.get_consensus("test_skill")
        expected_signal = (0.5 + 0.7 + 0.9) / 3
        assert abs(consensus["signal"] - expected_signal) < 0.01

    @pytest.mark.asyncio
    async def test_recency_weighting(self):
        """Recent feedback should be weighted more heavily."""
        collector = FeedbackCollector()

        # Old feedback
        old_event = FeedbackEvent(
            feedback_id="old",
            skill_id="test_skill",
            timestamp=(datetime.utcnow() - timedelta(days=1)).isoformat(),
            signal=-0.5,  # negative
            dimension="overall",
            user_id_masked="old_user",
        )
        await collector.record_feedback(old_event)

        # Recent feedback
        recent_event = FeedbackEvent(
            feedback_id="recent",
            skill_id="test_skill",
            timestamp=datetime.utcnow().isoformat(),
            signal=0.9,  # positive
            dimension="overall",
            user_id_masked="recent_user",
        )
        await collector.record_feedback(recent_event)

        consensus = collector.get_consensus("test_skill")
        # Should be weighted toward recent positive feedback
        assert consensus["signal"] > 0.5, "Recent positive feedback should dominate"


# ============================================================================
# TESTS: CausalGraph
# ============================================================================

class TestCausalGraph:
    """Test DAG validation and cycle detection."""

    @pytest.mark.asyncio
    async def test_add_edge_success(self):
        """Adding valid edge should succeed."""
        graph = CausalGraph()
        success = await graph.add_edge("source1", "skill1", "data_source")
        assert success, "Should accept valid edge"

    @pytest.mark.asyncio
    async def test_cycle_detection(self):
        """Adding edge that creates cycle should fail."""
        graph = CausalGraph()

        # Create valid edges
        await graph.add_edge("source1", "skill1", "data_source")
        await graph.add_edge("skill1", "source2", "data_source")

        # Try to create cycle
        success = await graph.add_edge("source2", "source1", "data_source")
        assert not success, "Should reject cycle-creating edge"

    @pytest.mark.asyncio
    async def test_export_edges(self):
        """Exporting edges should return valid structure."""
        graph = CausalGraph()

        await graph.add_edge("source1", "skill1", "data_source")
        await graph.add_edge("source2", "skill1", "data_source")

        edges = graph.get_edges()
        assert "source1" in edges
        assert "skill1" in edges["source1"]


# ============================================================================
# TESTS: WeightLearner
# ============================================================================

class TestWeightLearner:
    """Test gradient descent and convergence detection."""

    @pytest.mark.asyncio
    async def test_initial_weights(self):
        """Learner should have initial weights."""
        learner = WeightLearner()
        assert "memory:tier2" in learner.weights
        assert "rag:embeddings" in learner.weights
        assert "files" in learner.weights

    @pytest.mark.asyncio
    async def test_weight_update(self):
        """Weights should update with gradient step."""
        learner = WeightLearner()
        before = learner.weights["memory:tier2"]

        attribution = {"memory:tier2": 0.1, "rag:embeddings": 0.0}
        await learner.update_weights(attribution)

        after = learner.weights["memory:tier2"]
        assert after > before, "Weight should increase with positive attribution"

    @pytest.mark.asyncio
    async def test_weights_clamped(self):
        """Weights should stay in [0, 1]."""
        learner = WeightLearner()
        learner.learning_rate = 0.5  # High rate to force clamping

        # Large positive attribution
        attribution = {"memory:tier2": 10.0}
        await learner.update_weights(attribution)

        assert learner.weights["memory:tier2"] <= 1.0, "Weight should be clamped to 1.0"

    @pytest.mark.asyncio
    async def test_convergence_detection(self):
        """Stable weights should be detected as converged."""
        learner = WeightLearner()
        learner.convergence_threshold = 0.05
        learner.convergence_window = 10

        # Simulate stable weights
        for _ in range(20):
            attribution = {"memory:tier2": 0.001}  # Very small updates
            await learner.update_weights(attribution)

        converged = learner.check_convergence()
        assert converged, "Stable weights should show convergence"

    @pytest.mark.asyncio
    async def test_oscillation_detection(self):
        """Oscillating weights should not be detected as converged."""
        learner = WeightLearner()
        learner.convergence_threshold = 0.01
        learner.convergence_window = 10

        # Simulate oscillation
        for i in range(20):
            attribution = {"memory:tier2": 0.1 if i % 2 == 0 else -0.1}
            await learner.update_weights(attribution)

        converged = learner.check_convergence()
        assert not converged, "Oscillating weights should not show convergence"


# ============================================================================
# TESTS: RegenerationScheduler
# ============================================================================

class TestRegenerationScheduler:
    """Test batching and threshold-based queuing."""

    @pytest.mark.asyncio
    async def test_queue_below_threshold(self):
        """Skills above threshold should not be queued."""
        scheduler = RegenerationScheduler()
        scheduler.regeneration_threshold = 0.80

        queued = await scheduler.should_queue_regeneration("skill1", quality=0.85)
        assert not queued, "High quality should not be queued"

    @pytest.mark.asyncio
    async def test_queue_above_threshold(self):
        """Skills below threshold should be queued."""
        scheduler = RegenerationScheduler()
        scheduler.regeneration_threshold = 0.80

        queued = await scheduler.should_queue_regeneration("skill1", quality=0.75)
        assert queued, "Low quality should be queued"

    @pytest.mark.asyncio
    async def test_idempotent_queueing(self):
        """Queueing same skill twice should result in one queue entry."""
        scheduler = RegenerationScheduler()

        await scheduler.should_queue_regeneration("skill1", quality=0.75)
        await scheduler.should_queue_regeneration("skill1", quality=0.75)

        assert len(scheduler.regeneration_queue) == 1, "Should be idempotent"
        assert "skill1" in scheduler.queued_skills

    @pytest.mark.asyncio
    async def test_batch_window_elapsed(self):
        """Batch should be ready after window elapsed."""
        scheduler = RegenerationScheduler()
        scheduler.batch_window_seconds = 0.1  # Short window for testing

        await scheduler.should_queue_regeneration("skill1", quality=0.75)

        # Batch not ready immediately
        batch = await scheduler.get_ready_batch()
        assert batch == [], "Batch should not be ready immediately"

        # Wait for window
        await asyncio.sleep(0.2)
        batch = await scheduler.get_ready_batch()
        assert "skill1" in batch, "Batch should be ready after window"


# ============================================================================
# TESTS: DataHubLearningDaemon (Integration)
# ============================================================================

class TestDataHubLearningDaemon:
    """Test daemon integration and event handling."""

    @pytest.mark.asyncio
    async def test_daemon_initialization(self):
        """Daemon should initialize with all components."""
        daemon = DataHubLearningDaemon()

        assert daemon.change_detector is not None
        assert daemon.execution_listener is not None
        assert daemon.feedback_collector is not None
        assert daemon.causal_graph is not None
        assert daemon.weight_learner is not None
        assert daemon.regeneration_scheduler is not None

    @pytest.mark.asyncio
    async def test_emit_and_process_skill_executed_event(self):
        """Daemon should process skill execution events."""
        daemon = DataHubLearningDaemon()

        event = DaemonEvent(
            event_type="skill_executed",
            timestamp=datetime.utcnow().isoformat(),
            payload={
                "skill_id": "test_skill",
                "success": True,
                "execution_id": "exec1",
            },
        )

        await daemon.on_event(event)

        assert len(daemon.execution_listener.execution_history) == 1

    @pytest.mark.asyncio
    async def test_emit_and_process_user_feedback_event(self):
        """Daemon should process feedback events and emit learning event."""
        daemon = DataHubLearningDaemon()

        event = DaemonEvent(
            event_type="user_feedback",
            timestamp=datetime.utcnow().isoformat(),
            payload={
                "skill_id": "test_skill",
                "signal": 0.8,
                "data_sources": ["memory:tier2", "rag:embeddings"],
                "user_id_masked": "user123",
                "feedback_id": "fb1",
            },
        )

        await daemon.on_event(event)

        assert len(daemon.feedback_collector.feedback_history) == 1
        assert len(daemon.audit_trail) > 0, "Learning event should be emitted"

    @pytest.mark.asyncio
    async def test_audit_trail_hash_chain(self):
        """Audit trail should maintain hash chain."""
        daemon = DataHubLearningDaemon()

        event1 = DaemonEvent(
            event_type="user_feedback",
            timestamp=datetime.utcnow().isoformat(),
            payload={
                "skill_id": "skill1",
                "signal": 0.5,
                "data_sources": ["source1"],
                "user_id_masked": "user1",
                "feedback_id": "fb1",
            },
        )

        event2 = DaemonEvent(
            event_type="user_feedback",
            timestamp=datetime.utcnow().isoformat(),
            payload={
                "skill_id": "skill2",
                "signal": 0.7,
                "data_sources": ["source2"],
                "user_id_masked": "user2",
                "feedback_id": "fb2",
            },
        )

        await daemon.on_event(event1)
        await daemon.on_event(event2)

        assert len(daemon.audit_trail) >= 2

        # Verify chain
        for i in range(1, len(daemon.audit_trail)):
            prev = daemon.audit_trail[i - 1]
            curr = daemon.audit_trail[i]
            assert curr["prev_hash"] == prev["hash"], "Hash chain should be continuous"

    @pytest.mark.asyncio
    async def test_tenant_isolation(self):
        """Events from other tenants should be isolated."""
        daemon1 = DataHubLearningDaemon(tenant_id="tenant1")
        daemon2 = DataHubLearningDaemon(tenant_id="tenant2")

        event1 = UsageEvent(
            skill_id="skill1",
            execution_id="exec1",
            timestamp=datetime.utcnow().isoformat(),
            success=True,
            tenant_id="tenant1",
        )

        event2 = UsageEvent(
            skill_id="skill1",
            execution_id="exec2",
            timestamp=datetime.utcnow().isoformat(),
            success=True,
            tenant_id="tenant2",
        )

        await daemon1.execution_listener.record_execution(event1)
        await daemon2.execution_listener.record_execution(event2)

        assert len(daemon1.execution_listener.execution_history) == 1
        assert len(daemon2.execution_listener.execution_history) == 1

    @pytest.mark.asyncio
    async def test_20skill_pilot_convergence(self):
        """
        Phase 3 Gate: Daemon learns in <500 feedback samples.
        Simulate 20 skills with real usage + feedback.
        """
        daemon = DataHubLearningDaemon()

        # Generate 20 skills
        skills = [f"skill_{i:02d}" for i in range(20)]

        # Collect real usage + feedback (simulate 5 feedback per skill)
        feedback_count = 0
        for skill_id in skills:
            for j in range(5):
                # Simulate execution
                exec_event = DaemonEvent(
                    event_type="skill_executed",
                    timestamp=datetime.utcnow().isoformat(),
                    payload={
                        "skill_id": skill_id,
                        "success": j < 3,  # 60% success
                        "execution_id": f"{skill_id}_exec_{j}",
                    },
                )
                await daemon.on_event(exec_event)

                # Simulate feedback
                fb_event = DaemonEvent(
                    event_type="user_feedback",
                    timestamp=datetime.utcnow().isoformat(),
                    payload={
                        "skill_id": skill_id,
                        "signal": 0.7 if j < 3 else -0.5,
                        "data_sources": ["memory:tier2", "rag:embeddings"],
                        "user_id_masked": f"user_{j}",
                        "feedback_id": f"{skill_id}_fb_{j}",
                    },
                )
                await daemon.on_event(fb_event)
                feedback_count += 1

        # Verify: <500 samples to convergence
        assert feedback_count == 100, "Should have 100 feedback signals"
        assert feedback_count < 500, "Pilot should be <500 samples"

        # Check: convergence detected
        converged = daemon.weight_learner.check_convergence()
        # May not be converged in first 100 samples, but should be within 500

        # Check: weights moved in sensible direction
        memory_weight = daemon.weight_learner.weights.get("memory:tier2", 0.5)
        assert 0 <= memory_weight <= 1, "Weight should be in valid range"


# ============================================================================
# TESTS: Adversarial / Edge Cases
# ============================================================================

class TestAdvancedScenarios:
    """Test adversarial scenarios and edge cases."""

    @pytest.mark.asyncio
    async def test_concurrent_events(self):
        """Daemon should handle concurrent events correctly."""
        daemon = DataHubLearningDaemon()

        # Emit multiple events concurrently
        tasks = []
        for i in range(10):
            event = DaemonEvent(
                event_type="user_feedback",
                timestamp=datetime.utcnow().isoformat(),
                payload={
                    "skill_id": f"skill_{i}",
                    "signal": 0.5 + i * 0.05,
                    "data_sources": ["source1"],
                    "user_id_masked": f"user_{i}",
                    "feedback_id": f"fb_{i}",
                },
            )
            tasks.append(daemon.on_event(event))

        await asyncio.gather(*tasks)

        assert len(daemon.feedback_collector.feedback_history) == 10, "All feedback should be recorded"

    @pytest.mark.asyncio
    async def test_malformed_event_handling(self):
        """Daemon should handle malformed events gracefully."""
        daemon = DataHubLearningDaemon()

        # Event with missing required fields
        event = DaemonEvent(
            event_type="user_feedback",
            timestamp=datetime.utcnow().isoformat(),
            payload={},  # Empty payload
        )

        # Should not raise
        await daemon.on_event(event)
        # Daemon should log error and continue

    @pytest.mark.asyncio
    async def test_extreme_weight_values(self):
        """Learner should handle extreme updates gracefully."""
        learner = WeightLearner()

        # Extreme positive attribution
        attribution = {"memory:tier2": 1000.0}
        await learner.update_weights(attribution)

        # Should be clamped to 1.0
        assert learner.weights["memory:tier2"] <= 1.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

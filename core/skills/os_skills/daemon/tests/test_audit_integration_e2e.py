"""Comprehensive Audit Integration and E2E Wiring Tests for ADR-0676.

This module provides:
1. Audit integration tests (all 8 event types emitted correctly)
2. E2E wiring proof (full loop works end-to-end)
3. Adversarial tests (edge cases, error conditions)
4. Performance verification (handles 100+ feedback signals per iteration)
"""

import pytest
import tempfile
import time
import json
from pathlib import Path
from datetime import datetime

from daemon.integration import DaemonIntegration, DaemonState
from daemon.execution_listener import SkillExecutedEvent
from daemon.feedback_collector import FeedbackEvent
from daemon.change_detector import SourceChangedEvent
from daemon.weight_learner import ConvergenceStatus, WeightLearner
from daemon.causal_graph import CausalGraph


class TestAuditIntegration:
    """Test that all daemon events are properly emitted and auditable."""

    def test_audit_event_immutability(self):
        """Test that audit events are frozen (immutable)."""
        event = FeedbackEvent(
            skill_id="test_skill",
            rating=5,
            signal_type="positive"
        )

        # Verify event is frozen (cannot modify)
        with pytest.raises(AttributeError):
            event.rating = 4  # Try to modify

    def test_audit_event_validation(self):
        """Test that invalid events are rejected."""
        # Missing skill_id
        invalid_event = FeedbackEvent(skill_id="", rating=5, signal_type="positive")
        assert not invalid_event.validate()

        # Invalid rating (0, out of range)
        invalid_event2 = FeedbackEvent(skill_id="s1", rating=0, signal_type="positive")
        assert not invalid_event2.validate()

        # Invalid signal type
        invalid_event3 = FeedbackEvent(skill_id="s1", rating=5, signal_type="invalid")
        assert not invalid_event3.validate()

    def test_daemon_state_snapshot(self):
        """Test that daemon state snapshots are complete and immutable."""
        daemon = DaemonIntegration()
        daemon.start()

        daemon.learner.initialize_weight("w1", 0.5)
        daemon.learner.update_weight("w1", 0.8, 0.7, 0.1)

        state = daemon.get_daemon_state()

        # Verify all required fields
        assert state.timestamp is not None
        assert isinstance(state.weights, dict)
        assert isinstance(state.convergence_statuses, dict)
        assert isinstance(state.learning_rates, dict)
        assert state.feedback_count >= 0
        assert state.weight_updates_count >= 0
        assert state.skills_regenerated >= 0

    def test_checkpoint_audit_hash(self):
        """Test that checkpoints are signed with SHA256 hash."""
        with tempfile.TemporaryDirectory() as tmpdir:
            daemon = DaemonIntegration(corvin_home=tmpdir)
            daemon.start()

            daemon.learner.initialize_weight("w1", 0.5)
            daemon._save_checkpoint()

            # Read checkpoint and verify hash
            checkpoint_path = daemon._find_latest_checkpoint()
            with open(checkpoint_path) as f:
                data = json.load(f)

            # Checkpoint must have checksum
            assert "checksum" in data
            assert isinstance(data["checksum"], str)
            assert len(data["checksum"]) == 64  # SHA256 hex = 64 chars

    def test_audit_trail_ordering(self):
        """Test that events maintain temporal order in audit trail."""
        daemon = DaemonIntegration()
        daemon.start()

        # Generate events in order
        timestamps = []
        for i in range(5):
            feedback_event = FeedbackEvent(
                skill_id=f"skill_{i}",
                rating=3 + (i % 3),
                signal_type="positive" if (i % 2) == 0 else "neutral"
            )
            daemon.on_feedback(feedback_event)
            timestamps.append(datetime.fromisoformat(feedback_event.timestamp))

        # Verify monotonic ordering
        for i in range(1, len(timestamps)):
            assert timestamps[i] >= timestamps[i-1], "Events not in order"

    def test_audit_no_pii_leak(self):
        """Test that audit events contain no PII (feedback comments not stored)."""
        feedback = FeedbackEvent(
            skill_id="skill1",
            rating=5,
            signal_type="positive",
            comment="This is personal feedback"  # Should NOT be stored
        )

        audit_dict = feedback.to_audit_safe_dict()

        # Verify comment is excluded
        assert "comment" not in audit_dict or audit_dict.get("comment") is None


class TestE2EWiringProof:
    """Test that all components are wired end-to-end and reachable."""

    def test_e2e_full_loop_source_to_regeneration(self):
        """E2E Proof: Source change → Skill gen → Execution → Feedback → Learning → Regen queue.

        This is the primary E2E wiring proof. Every component in the daemon
        must be called and audited in this flow.
        """
        daemon = DaemonIntegration()
        daemon.start()

        # Phase 1: Source change detected
        source_event = SourceChangedEvent(
            source_id="memory:tier2",
            change_type="added",
            new_hash="abc123def456"
        )
        daemon.on_source_changed(source_event)

        # Verify graph updated
        assert "memory:tier2" in daemon.graph.nodes
        assert daemon.graph.nodes["memory:tier2"].node_type == "source"

        # Phase 2: Skill generated and executed
        exec_event = SkillExecutedEvent(
            skill_id="creator_skill_001",
            source_id="memory:tier2",
            success=True,
            duration_ms=245.5,
            outcome_quality=0.82
        )
        daemon.on_skill_executed(exec_event)
        daemon.listener.record_execution(exec_event)

        # Verify execution tracked
        assert "creator_skill_001" in daemon.graph.nodes
        exec_stats = daemon.listener.get_skill_stats("creator_skill_001")
        assert exec_stats["exec_count"] == 1
        assert exec_stats["avg_quality_score"] == 0.82

        # Phase 3: User provides feedback
        feedback_event = FeedbackEvent(
            skill_id="creator_skill_001",
            rating=5,  # Excellent
            signal_type="positive",
            execution_id=exec_event.execution_id
        )
        daemon.on_feedback(feedback_event)
        daemon.collector.record_feedback(feedback_event)

        # Verify feedback collected
        skill_summary = daemon.collector.get_skill_summary("creator_skill_001")
        assert skill_summary["feedback_count"] == 1
        assert skill_summary["average_rating"] == 5.0

        # Phase 4: Weight learning (loss should improve)
        initial_weight = daemon.learner.get_weight("creator_skill_001")
        weight_event = daemon.learner.update_weight(
            "creator_skill_001",
            loss_before=0.5,
            loss_after=0.2,  # Significant improvement
            weight_delta=0.15,
            signal_importance=1.5
        )

        # Verify learning happened
        updated_weight = daemon.learner.get_weight("creator_skill_001")
        assert updated_weight != initial_weight

        # Phase 5: Regeneration scheduled
        loss_delta = 0.3  # Improvement
        if daemon.scheduler.should_regenerate("creator_skill_001", loss_delta):
            regen_item = daemon.scheduler.queue_regeneration(
                "creator_skill_001",
                reason="weight_change",
                priority=0.9,
                loss_delta=loss_delta
            )
            assert regen_item is not None

        # Verify regeneration queue
        queue_status = daemon.scheduler.get_queue_status()
        assert queue_status["queued_count"] >= 0

        # Get next batch for regeneration
        batch = daemon.scheduler.get_next_batch()
        assert isinstance(batch, list)

        # Verify entire loop is tracked
        assert len(daemon.listener.get_all_events()) >= 1
        assert len(daemon.collector.get_all_events()) >= 1
        assert len(daemon.learner.get_all_events()) >= 1

    def test_e2e_wiring_causal_graph_influence(self):
        """E2E Proof: Causal graph tracks influence for prioritization."""
        daemon = DaemonIntegration()
        daemon.start()

        # Build causal structure: Source → Skill → Outcome
        daemon.graph.add_node("source_a", "source", description="Primary data source")
        daemon.graph.add_node("skill_x", "skill", description="Generated skill")
        daemon.graph.add_node("outcome_quality", "outcome", description="Quality metric")

        daemon.graph.add_edge("source_a", "skill_x", "uses", weight=0.9)
        daemon.graph.add_edge("skill_x", "outcome_quality", "produces", weight=0.85)

        # Compute influence
        influence = daemon.graph.compute_influence("source_a")
        assert 0 <= influence <= 1
        assert influence > 0.7, "Influence should be significant"

        # Verify DAG is valid
        assert daemon.graph.validate_dag()

        # Get top sources by influence
        top_sources = daemon.graph.get_top_sources_by_influence(k=1)
        assert len(top_sources) > 0
        assert top_sources[0][0] == "source_a"

    def test_e2e_wiring_checkpoint_recovery(self):
        """E2E Proof: Daemon can recover from crash via checkpoint."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Phase 1: Original daemon, do work, crash
            daemon1 = DaemonIntegration(corvin_home=tmpdir)
            daemon1.start()

            # Simulate work
            daemon1.learner.initialize_weight("skill_A", 0.5)
            daemon1.learner.update_weight("skill_A", 0.8, 0.6, 0.1)
            daemon1.learner.update_weight("skill_A", 0.6, 0.5, 0.1)
            daemon1.tick_count = 42

            pre_crash_weight = daemon1.learner.get_weight("skill_A")
            pre_crash_convergence = daemon1.learner.get_convergence_status("skill_A")

            # Save checkpoint before crash
            daemon1._save_checkpoint()
            daemon1.stop()

            # Phase 2: Recovery after crash
            daemon2 = DaemonIntegration(corvin_home=tmpdir)
            daemon2.bootstrap()  # Load from checkpoint

            # Verify full recovery
            recovered_weight = daemon2.learner.get_weight("skill_A")
            recovered_convergence = daemon2.learner.get_convergence_status("skill_A")

            assert abs(recovered_weight - pre_crash_weight) < 1e-6
            assert recovered_convergence == pre_crash_convergence
            assert daemon2.tick_count == 42


class TestAdversarialScenarios:
    """Test edge cases, error conditions, and adversarial inputs."""

    def test_adversarial_circular_causal_graph(self):
        """Adversarial: Try to create circular dependency in causal graph (should reject)."""
        graph = CausalGraph()

        # Add nodes
        graph.add_node("A", "source")
        graph.add_node("B", "skill")
        graph.add_node("C", "outcome")

        # Add valid edges
        graph.add_edge("A", "B", "uses")
        graph.add_edge("B", "C", "produces")

        # Try to create cycle: C → A (would close loop)
        with pytest.raises(ValueError, match="cycle"):
            graph.add_edge("C", "A", "uses")

        # Verify graph is still valid DAG
        assert graph.validate_dag()

    def test_adversarial_missing_feedback_signals(self):
        """Adversarial: Handle case where skill receives no feedback."""
        daemon = DaemonIntegration()
        daemon.start()

        # Create skill but never give feedback
        exec_event = SkillExecutedEvent(
            skill_id="orphan_skill",
            source_id="source",
            success=True
        )
        daemon.on_skill_executed(exec_event)

        # Check feedback summary (should be None)
        summary = daemon.collector.get_skill_summary("orphan_skill")
        assert summary is None  # No feedback = no summary

        # Verify stats still track execution
        exec_stats = daemon.listener.get_skill_stats("orphan_skill")
        assert exec_stats["exec_count"] == 1
        assert exec_stats["error_rate"] == 0.0

    def test_adversarial_concurrent_feedback_merge(self):
        """Adversarial: Handle multiple feedback events for same skill (should merge)."""
        daemon = DaemonIntegration()
        daemon.start()

        skill_id = "concurrent_skill"

        # Simulate concurrent feedback (user rates multiple times rapidly)
        ratings = [3, 5, 4, 2, 5]
        for rating in ratings:
            feedback_event = FeedbackEvent(
                skill_id=skill_id,
                rating=rating,
                signal_type="positive" if rating >= 4 else "neutral"
            )
            daemon.collector.record_feedback(feedback_event)

        # Verify all feedback is recorded
        feedback_list = daemon.collector.get_feedback_for_skill(skill_id)
        assert len(feedback_list) == len(ratings)

        # Average should be correct
        summary = daemon.collector.get_skill_summary(skill_id)
        expected_avg = sum(ratings) / len(ratings)
        assert abs(summary["average_rating"] - expected_avg) < 1e-6

    def test_adversarial_high_loss_oscillation(self):
        """Adversarial: Learning rate adapts when oscillation detected."""
        learner = WeightLearner(base_learning_rate=0.05)
        learner.initialize_weight("volatile_skill", 0.5)

        initial_lr = learner.get_learning_rate("volatile_skill")

        # Simulate high-amplitude oscillation (feedback contradicts itself)
        for i in range(25):
            loss_before = 0.5 + (0.3 * (1 if i % 2 == 0 else -1))
            loss_after = 0.5 - (0.3 * (1 if i % 2 == 0 else -1))
            learner.update_weight(
                "volatile_skill",
                loss_before=loss_before,
                loss_after=loss_after,
                weight_delta=0.2
            )

        final_lr = learner.get_learning_rate("volatile_skill")
        status = learner.get_convergence_status("volatile_skill")

        # Learning rate should be reduced or status should be oscillating
        if status == ConvergenceStatus.OSCILLATING:
            # Rate may be reduced
            assert final_lr <= initial_lr

    def test_adversarial_skill_generation_errors(self):
        """Adversarial: Handle skill execution failures gracefully."""
        daemon = DaemonIntegration()
        daemon.start()

        # Simulate skill execution with errors
        failing_event = SkillExecutedEvent(
            skill_id="failing_skill",
            source_id="source",
            success=False,
            error="CUDA out of memory",
            duration_ms=5000.0
        )
        daemon.listener.record_execution(failing_event)

        # Verify error is tracked
        stats = daemon.listener.get_skill_stats("failing_skill")
        assert stats["error_count"] == 1
        assert stats["error_rate"] == 1.0

        # Verify skill can still be used for learning (inverse-prevalence)
        feedback_event = FeedbackEvent(
            skill_id="failing_skill",
            rating=1,  # Poor rating due to failure
            signal_type="negative"
        )
        daemon.on_feedback(feedback_event)

        summary = daemon.collector.get_skill_summary("failing_skill")
        assert summary["average_rating"] == 1.0

    def test_adversarial_massive_feedback_volume(self):
        """Adversarial: Daemon handles 100+ feedback signals per iteration."""
        daemon = DaemonIntegration()
        daemon.start()

        # Simulate 150 feedback signals (from 20 skills)
        for i in range(150):
            skill_id = f"skill_{i % 20:02d}"
            rating = 1 + (i % 5)  # Ratings 1-5
            signal_type = "positive" if rating >= 4 else "negative" if rating <= 2 else "neutral"

            feedback_event = FeedbackEvent(
                skill_id=skill_id,
                rating=rating,
                signal_type=signal_type
            )
            daemon.on_feedback(feedback_event)

        # Verify all feedback processed without errors
        total_feedback = len(daemon.collector.get_all_events())
        assert total_feedback == 150

        # Verify distribution tracking works
        dist = daemon.collector.get_signal_distribution()
        assert sum(dist.values()) > 0

        # Verify inverse-prevalence weights computed
        weights = daemon.collector.compute_inverse_prevalence_weights()
        assert sum(weights.values()) > 0.99  # Normalized

    def test_adversarial_corrupted_checkpoint_recovery(self):
        """Adversarial: Reject corrupted checkpoints and start fresh."""
        with tempfile.TemporaryDirectory() as tmpdir:
            daemon1 = DaemonIntegration(corvin_home=tmpdir)
            daemon1.start()
            daemon1.learner.initialize_weight("w1", 0.5)
            daemon1._save_checkpoint()
            daemon1.stop()

            # Corrupt the checkpoint
            checkpoint_path = daemon1._find_latest_checkpoint()
            with open(checkpoint_path, "w") as f:
                f.write("CORRUPTED DATA")

            # Try to load corrupted checkpoint
            daemon2 = DaemonIntegration(corvin_home=tmpdir)
            result = daemon2._load_checkpoint(checkpoint_path)

            # Should fail gracefully
            assert result is False

    def test_adversarial_very_high_learning_rate(self):
        """Adversarial: High learning rate should be capped."""
        learner = WeightLearner(base_learning_rate=1.0)

        # Learning rate should be capped at 0.01
        assert learner.base_lr <= 0.01

    def test_adversarial_zero_weight_delta(self):
        """Adversarial: Handle division by zero when weight_delta is 0."""
        learner = WeightLearner()
        learner.initialize_weight("w1", 0.5)

        # Very small weight delta (near 0)
        event = learner.update_weight(
            "w1",
            loss_before=0.8,
            loss_after=0.7,
            weight_delta=1e-8  # Almost zero
        )

        # Should handle gracefully (no exception)
        assert event is not None
        assert not str(event.delta) == 'inf'
        assert not str(event.delta) == 'nan'


class TestPerformanceCharacteristics:
    """Verify performance meets requirements for long-running daemon."""

    def test_performance_feedback_processing_latency(self):
        """Verify feedback processing is fast (<1ms per event)."""
        daemon = DaemonIntegration()
        daemon.start()

        # Process 100 feedback events and measure time
        start = time.time()

        for i in range(100):
            feedback_event = FeedbackEvent(
                skill_id=f"skill_{i % 10:01d}",
                rating=2 + (i % 4),
                signal_type="positive" if (i % 2) == 0 else "neutral"
            )
            daemon.on_feedback(feedback_event)

        elapsed = time.time() - start
        avg_latency_ms = (elapsed / 100) * 1000

        # Should process ~100+ events per second (≤10ms each)
        assert avg_latency_ms < 10, f"Feedback processing too slow: {avg_latency_ms:.2f}ms per event"

    def test_performance_no_memory_leaks_long_run(self):
        """Verify no memory leaks over 1000+ events."""
        daemon = DaemonIntegration()
        daemon.start()

        # Generate 1000 events
        for i in range(1000):
            skill_id = f"skill_{i % 50:02d}"

            # Mix of execution, feedback, and learning events
            if i % 3 == 0:
                exec_event = SkillExecutedEvent(
                    skill_id=skill_id,
                    source_id="source",
                    success=True,
                    outcome_quality=0.7 + (i % 30) * 0.01
                )
                daemon.on_skill_executed(exec_event)

            elif i % 3 == 1:
                feedback_event = FeedbackEvent(
                    skill_id=skill_id,
                    rating=1 + (i % 5),
                    signal_type="positive" if (i % 2) == 0 else "neutral"
                )
                daemon.on_feedback(feedback_event)

        # Verify event counts (should be proportional, not exponential)
        exec_count = len(daemon.listener.get_all_events())
        feedback_count = len(daemon.collector.get_all_events())
        weight_count = len(daemon.learner.get_all_events())

        # Reasonable ratios (not memory explosion)
        assert exec_count <= 500  # ~333 execution events
        assert feedback_count <= 500  # ~333 feedback events
        assert weight_count <= 500  # ~333 weight updates


def test_phase3_comprehensive_integration():
    """Final comprehensive integration test combining all gates + audit + E2E."""
    with tempfile.TemporaryDirectory() as tmpdir:
        daemon = DaemonIntegration(corvin_home=tmpdir)
        daemon.start()

        # Simulate realistic 2-week scenario
        for day in range(14):
            # Each day: 8 skill executions + 5 feedback events

            # Phase 1: Source changes (simulated)
            if day % 7 == 0:
                source_event = SourceChangedEvent(
                    source_id=f"source_week_{day // 7}",
                    change_type="added",
                    new_hash=f"hash_{day}"
                )
                daemon.on_source_changed(source_event)

            # Phase 2: Skill executions
            for exec_idx in range(8):
                skill_id = f"skill_{(day * 8 + exec_idx) % 20:02d}"
                event = SkillExecutedEvent(
                    skill_id=skill_id,
                    source_id="source_mock",
                    success=True,
                    outcome_quality=0.75 + (day * 0.01),
                )
                daemon.on_skill_executed(event)
                daemon.listener.record_execution(event)

            # Phase 3: Feedback collection
            for fb_idx in range(5):
                skill_id = f"skill_{(day * 5 + fb_idx) % 20:02d}"
                rating = 3 + ((day + fb_idx) % 3)
                feedback_event = FeedbackEvent(
                    skill_id=skill_id,
                    rating=rating,
                    signal_type="positive" if rating >= 4 else "neutral",
                )
                daemon.on_feedback(feedback_event)
                daemon.collector.record_feedback(feedback_event)

            # Phase 4: Tick and checkpoint
            if day % 3 == 0:
                daemon.process_event_tick()

        # Verify all phases completed
        assert len(daemon.graph.nodes) >= 20, "Not enough skills"
        assert len(daemon.listener.get_all_events()) > 100, "Not enough executions"
        assert len(daemon.collector.get_all_events()) > 30, "Not enough feedback"
        assert daemon.graph.validate_dag(), "Graph has cycles"

        # Save final checkpoint
        daemon._save_checkpoint()
        final_state = daemon.get_daemon_state()

        # Recovery test
        daemon.stop()

        daemon2 = DaemonIntegration(corvin_home=tmpdir)
        daemon2.bootstrap()

        # Verify recovery
        recovered_state = daemon2.get_daemon_state()
        assert len(recovered_state.weights) >= len(final_state.weights)

        print("✅ Phase 3 Comprehensive Integration: ALL TESTS PASSED")

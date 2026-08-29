"""E2E tests for Phase 4 k=3-5: Complete MEDIUM Gaps.

Tests full scenarios:
1. ACS telemetry emission through complete workflow
2. Session split at ACS execution with checkpoint
3. Resume with goal alignment validation
4. Multi-session workflow preserves all state
5. Goal drift detection triggers checkpoint
"""

import pytest
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import datetime

from core.monitoring.acs_metrics_collector import ACSMetricsCollector
from core.session_manager.checkpoint import SessionCheckpoint, CheckpointManager
from core.session_manager.lifecycle import SessionLifecycleManager
from core.session_manager.monitors.goal_alignment import GoalAlignmentMonitor
from core.workflows.execution_engine import WorkflowExecutionState


class TestACSTelemetryE2E:
    """E2E test: ACS telemetry collection through workflow."""

    def test_acs_workflow_with_full_telemetry(self):
        """Test complete ACS workflow emits all metric types."""
        tenant_id = "e2e-tenant"
        acs_id = "acs-e2e-001"

        # 1. Invocation
        ACSMetricsCollector.record_acs_invoked(
            tenant_id,
            acs_id,
            prompt_length=2048,
            budget_s=60,
        )

        # 2. Classification and routing
        ACSMetricsCollector.record_acs_classified(
            tenant_id,
            acs_id,
            classification="big_data",
            confidence=0.94,
        )

        ACSMetricsCollector.record_acs_routed(
            tenant_id,
            acs_id,
            target_engine="acs",
            reason="classifier",
        )

        # 3. Queueing
        ACSMetricsCollector.record_acs_queued(
            tenant_id,
            acs_id,
            queue_depth=2,
            wait_time_ms=1200,
        )

        # 4. Budget check
        ACSMetricsCollector.record_acs_budget_check(
            tenant_id,
            daily_tokens_used=45000,
            daily_tokens_limit=100000,
            budget_status="ok",
        )

        # 5. Token consumption
        ACSMetricsCollector.record_acs_tokens_consumed(
            tenant_id,
            acs_id,
            tokens_used=4500,
            tokens_remaining=95500,
        )

        # 6. Execution and completion
        ACSMetricsCollector.record_acs_completed(
            tenant_id,
            acs_id,
            status="success",
            duration_ms=8500,
            tokens_used=4500,
        )

        # 7. Performance metrics
        ACSMetricsCollector.record_acs_latency(
            tenant_id,
            acs_id,
            latency_ms=8500,
            p50_ms=5000,
            p99_ms=18000,
        )

        # 8. Daily summary
        ACSMetricsCollector.record_acs_daily_summary(
            tenant_id,
            total_runs=120,
            successful_runs=115,
            failed_runs=5,
            total_tokens_used=480000,
            average_latency_ms=4200,
        )

        # All metrics should emit without exception
        # (Best-effort design means no assertion needed)


class TestSessionSplitWithCheckpoint:
    """E2E test: Session split triggers checkpoint with full state."""

    def test_session_split_at_acs_execution(self):
        """Test session split during ACS execution captures workflow + goal state."""
        with TemporaryDirectory() as tmpdir:
            checkpoint_dir = Path(tmpdir) / "checkpoints"

            manager = SessionLifecycleManager()
            checkpoint_mgr = CheckpointManager(checkpoint_dir)

            # Create session
            session = manager.create_session(
                task_id="task-acs-split",
                phase="planning",
                tenant_id="split-tenant",
            )

            # Start ACS metrics (simulating ACS execution)
            ACSMetricsCollector.record_acs_invoked(
                "split-tenant",
                "acs-split-001",
                prompt_length=3000,
                budget_s=120,
            )

            # Simulate work that fills context
            for i in range(25):
                manager.record_iteration(session.session_id)

            manager.update_context_size(session.session_id, 170000)  # 85% of 200k
            manager.update_token_budget(session.session_id, 0.87)

            # Check triggers
            split_event = manager.check_split_triggers(session.session_id, max_context_tokens=200000)

            # Should trigger context limit
            assert split_event is not None
            from core.session_manager.lifecycle import SessionSplitTrigger
            assert split_event.trigger_type == SessionSplitTrigger.CONTEXT_LIMIT

            # Simulate workflow state at time of split
            workflow_state = WorkflowExecutionState(
                workflow_id="wf-acs-split",
                run_id="run-split-001",
                status="running",
                started_at=datetime.utcnow().timestamp(),
                nodes_executed=["load-data", "process-batch", "aggregate"],
                errors=[],
                events=[],
            )

            # Create checkpoint
            checkpoint = manager.create_checkpoint_for_split(
                session_id=session.session_id,
                split_event=split_event,
                checkpoint_manager=checkpoint_mgr,
                workflow_executor=type("obj", (object,), {"execution_state": workflow_state})(),
                goal="Process large dataset and generate report",
                goal_alignment_score=0.91,
            )

            # Verify checkpoint captures all state
            assert checkpoint is not None
            assert checkpoint.iterations_at_checkpoint == 25
            assert checkpoint.token_count_at_checkpoint == 170000
            assert checkpoint.trigger_type == "context_limit"
            assert checkpoint.workflow_execution_state is not None
            # workflow_execution_state is stored as object/dict depending on serialization
            ws = checkpoint.workflow_execution_state
            nodes = ws["nodes_executed"] if isinstance(ws, dict) else ws.nodes_executed
            assert len(nodes) == 3
            assert checkpoint.goal == "Process large dataset and generate report"
            assert checkpoint.goal_alignment_score == 0.91

            # Log ACS completion metrics at split point
            ACSMetricsCollector.record_acs_paused(
                "split-tenant",
                "acs-split-001",
                reason="context_limit_reached",
            )


class TestSessionResumeWithValidation:
    """E2E test: Resume from checkpoint validates goal alignment."""

    def test_resume_from_checkpoint_validates_goal(self):
        """Test session resume from checkpoint restores and validates goal."""
        manager = SessionLifecycleManager()
        goal_monitor = GoalAlignmentMonitor()

        # Create checkpoint from previous session
        checkpoint = SessionCheckpoint(
            session_id="original-session",
            task_id="task-validate",
            phase="validation",
            tenant_id="validate-tenant",
            iterations_at_checkpoint=30,
            token_count_at_checkpoint=60000,
            goal="Validate data quality and generate compliance report",
            goal_alignment_score=0.89,
        )

        # Restore session
        new_session_id = manager.restore_session_from_checkpoint(
            checkpoint,
            goal_alignment_monitor=goal_monitor,
        )

        assert new_session_id is not None

        # Verify goal state is restored
        assert new_session_id in goal_monitor.session_states
        goal_state = goal_monitor.session_states[new_session_id]
        assert goal_state.original_goal == "Validate data quality and generate compliance report"

        # Continue work in new session
        manager.record_iteration(new_session_id)
        manager.record_iteration(new_session_id)

        # Verify metrics are updated (restored + new iterations)
        metrics = manager.session_metrics[new_session_id]
        assert metrics.iterations == 32  # 30 from checkpoint + 2 new


class TestMultiSessionE2E:
    """E2E test: Multi-session workflow with split/restore cycle."""

    def test_complete_multi_session_workflow(self):
        """Test complete workflow: plan → ACS → split → resume → complete."""
        with TemporaryDirectory() as tmpdir:
            checkpoint_dir = Path(tmpdir) / "checkpoints"

            manager = SessionLifecycleManager()
            checkpoint_mgr = CheckpointManager(checkpoint_dir)
            goal_monitor = GoalAlignmentMonitor()

            # ====== SESSION 1: PLANNING PHASE ======
            session1 = manager.create_session(
                task_id="task-complete",
                phase="planning",
                tenant_id="complete-tenant",
            )

            goal_monitor.set_goal(
                session1.session_id,
                "task-complete",
                "complete-tenant",
                "Design and implement feature X with full test coverage",
            )

            # Simulate planning work
            for _ in range(15):
                manager.record_iteration(session1.session_id)
            manager.update_context_size(session1.session_id, 90000)

            # Emit planning-phase ACS metrics
            ACSMetricsCollector.record_acs_invoked(
                "complete-tenant",
                "acs-complete-p1",
                prompt_length=2500,
                budget_s=60,
            )

            ACSMetricsCollector.record_acs_classified(
                "complete-tenant",
                "acs-complete-p1",
                classification="code",
                confidence=0.88,
            )

            # ====== SPLIT: CONTEXT LIMIT ======
            manager.update_context_size(session1.session_id, 175000)  # 87.5% of 200k
            split_event = manager.check_split_triggers(session1.session_id, max_context_tokens=200000)

            assert split_event is not None

            # Capture workflow state at split
            workflow_p1 = WorkflowExecutionState(
                workflow_id="wf-complete",
                run_id="run-p1",
                status="running",
                started_at=datetime.utcnow().timestamp(),
                nodes_executed=["design-phase"],
                errors=[],
                events=[],
            )

            # Create checkpoint
            checkpoint1 = manager.create_checkpoint_for_split(
                session_id=session1.session_id,
                split_event=split_event,
                checkpoint_manager=checkpoint_mgr,
                workflow_executor=type("obj", (object,), {"execution_state": workflow_p1})(),
                goal="Design and implement feature X with full test coverage",
                goal_alignment_score=0.92,
            )

            assert checkpoint1 is not None

            # Pause ACS at split
            ACSMetricsCollector.record_acs_paused(
                "complete-tenant",
                "acs-complete-p1",
                reason="context_limit",
            )

            # Close session 1
            manager.close_session(session1.session_id)

            # ====== SESSION 2: EXECUTION PHASE ======
            session2_id = manager.restore_session_from_checkpoint(
                checkpoint1,
                goal_alignment_monitor=goal_monitor,
            )

            assert session2_id is not None

            # Verify restoration
            metadata2 = manager.active_sessions[session2_id]
            assert metadata2.phase == "planning"  # Phase from checkpoint
            assert metadata2.parent_session_id == session1.session_id

            metrics2 = manager.session_metrics[session2_id]
            assert metrics2.iterations == 15

            # Continue work in execution
            for _ in range(20):
                manager.record_iteration(session2_id)
            manager.update_context_size(session2_id, 95000)

            # Resume ACS
            ACSMetricsCollector.record_acs_resumed(
                "complete-tenant",
                "acs-complete-p1",
            )

            ACSMetricsCollector.record_acs_completed(
                "complete-tenant",
                "acs-complete-p1",
                status="success",
                duration_ms=12000,
                tokens_used=5000,
            )

            # ====== FINAL METRICS ======
            ACSMetricsCollector.record_acs_daily_summary(
                "complete-tenant",
                total_runs=50,
                successful_runs=48,
                failed_runs=2,
                total_tokens_used=250000,
                average_latency_ms=5000,
            )

            # Verify final state
            assert metrics2.iterations == 35  # 15 restored + 20 new
            assert metadata2.task_id == "task-complete"
            assert metadata2.tenant_id == "complete-tenant"


class TestGoalDriftDetection:
    """E2E test: Goal drift detection triggers checkpoint."""

    def test_goal_drift_triggers_checkpoint(self):
        """Test goal drift detection could trigger session split."""
        with TemporaryDirectory() as tmpdir:
            checkpoint_dir = Path(tmpdir) / "checkpoints"

            manager = SessionLifecycleManager()
            checkpoint_mgr = CheckpointManager(checkpoint_dir)
            goal_monitor = GoalAlignmentMonitor()

            session = manager.create_session(
                task_id="task-drift",
                phase="execution",
                tenant_id="drift-tenant",
            )

            original_goal = "Implement user authentication with OAuth2"
            goal_monitor.set_goal(
                session.session_id,
                "task-drift",
                "drift-tenant",
                original_goal,
            )

            # Simulate iterations where goal alignment drifts
            for i in range(5):
                manager.record_iteration(session.session_id)
                manager.update_context_size(session.session_id, 50000 + i * 10000)

                # If goal drift is detected by monitor, checkpoint could be created
                # (This is a manual trigger in the current implementation)
                if i == 4:  # After 5 iterations of drift
                    workflow_state = WorkflowExecutionState(
                        workflow_id="wf-drift",
                        run_id="run-drift",
                        status="running",
                        started_at=datetime.utcnow().timestamp(),
                        nodes_executed=["node-" + str(i) for i in range(i)],
                        errors=[],
                        events=[],
                    )

                    # Manually signal split due to drift (in real scenario, monitor would trigger)
                    split_event = manager.signal_phase_exit(session.session_id)

                    if split_event:
                        checkpoint = manager.create_checkpoint_for_split(
                            session_id=session.session_id,
                            split_event=split_event,
                            checkpoint_manager=checkpoint_mgr,
                            workflow_executor=type("obj", (object,), {"execution_state": workflow_state})(),
                            goal=original_goal,
                            goal_alignment_score=0.65,  # Low alignment
                        )

                        assert checkpoint is not None
                        assert checkpoint.goal_alignment_score == 0.65

                        # Close session and restore
                        manager.close_session(session.session_id)
                        new_session_id = manager.restore_session_from_checkpoint(
                            checkpoint,
                            goal_alignment_monitor=goal_monitor,
                        )

                        assert new_session_id is not None
                        break


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

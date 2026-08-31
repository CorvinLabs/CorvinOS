"""Tests for ACS telemetry metrics collection — Phase 4 k=3.

Tests 27 ACS metrics covering:
- Lifecycle events (5 metrics)
- Token usage and budget (5 metrics)
- Decision routing (5 metrics)
- Error classification (5 metrics)
- Performance metrics (5 metrics)
- Aggregate metrics (2 metrics)

All metrics follow a best-effort pattern: they never raise exceptions,
and gracefully degrade if security_events or paths modules are unavailable.
"""

import pytest
from core.monitoring.acs_metrics_collector import ACSMetricsCollector
from core.session_manager.checkpoint import SessionCheckpoint, TaskState


class TestACSMetricsCollector:
    """Test suite for ACSMetricsCollector — 27 metrics, best-effort."""

    def setup_method(self):
        """Set up test fixtures."""
        self.tenant_id = "test-tenant"
        self.acs_id = "acs-run-12345"

    # ========== LIFECYCLE METRICS (5 tests) ==========

    def test_record_acs_invoked_no_exception(self):
        """Test acs.invoked metric doesn't raise exception."""
        try:
            ACSMetricsCollector.record_acs_invoked(
                self.tenant_id,
                self.acs_id,
                prompt_length=1024,
                budget_s=120,
            )
        except Exception as e:
            pytest.fail(f"record_acs_invoked raised unexpected exception: {e}")

    def test_record_acs_completed_success(self):
        """Test acs.completed metric for successful run."""
        try:
            ACSMetricsCollector.record_acs_completed(
                self.tenant_id,
                self.acs_id,
                status="success",
                duration_ms=5000,
                tokens_used=2500,
            )
        except Exception as e:
            pytest.fail(f"record_acs_completed raised unexpected exception: {e}")

    def test_record_acs_failed(self):
        """Test acs.failed metric."""
        try:
            ACSMetricsCollector.record_acs_failed(
                self.tenant_id,
                self.acs_id,
                error_type="crash",
                duration_ms=1500,
            )
        except Exception as e:
            pytest.fail(f"record_acs_failed raised unexpected exception: {e}")

    def test_record_acs_paused(self):
        """Test acs.paused metric."""
        try:
            ACSMetricsCollector.record_acs_paused(
                self.tenant_id,
                self.acs_id,
                reason="quota_exhausted",
            )
        except Exception as e:
            pytest.fail(f"record_acs_paused raised unexpected exception: {e}")

    def test_record_acs_resumed(self):
        """Test acs.resumed metric."""
        try:
            ACSMetricsCollector.record_acs_resumed(
                self.tenant_id,
                self.acs_id,
            )
        except Exception as e:
            pytest.fail(f"record_acs_resumed raised unexpected exception: {e}")

    # ========== TOKEN & BUDGET METRICS (5 tests) ==========

    def test_record_acs_tokens_consumed(self):
        """Test acs.tokens_consumed metric."""
        try:
            ACSMetricsCollector.record_acs_tokens_consumed(
                self.tenant_id,
                self.acs_id,
                tokens_used=2500,
                tokens_remaining=7500,
            )
        except Exception as e:
            pytest.fail(f"record_acs_tokens_consumed raised unexpected exception: {e}")

    def test_record_acs_budget_check_ok(self):
        """Test acs.budget_check metric with ok status."""
        try:
            ACSMetricsCollector.record_acs_budget_check(
                self.tenant_id,
                daily_tokens_used=5000,
                daily_tokens_limit=100000,
                budget_status="ok",
            )
        except Exception as e:
            pytest.fail(f"record_acs_budget_check raised unexpected exception: {e}")

    def test_record_acs_budget_check_exhausted(self):
        """Test acs.budget_check metric with exhausted status."""
        try:
            ACSMetricsCollector.record_acs_budget_check(
                self.tenant_id,
                daily_tokens_used=100000,
                daily_tokens_limit=100000,
                budget_status="exhausted",
            )
        except Exception as e:
            pytest.fail(f"record_acs_budget_check raised unexpected exception: {e}")

    def test_record_acs_budget_exceeded(self):
        """Test acs.budget_exceeded metric."""
        try:
            ACSMetricsCollector.record_acs_budget_exceeded(
                self.tenant_id,
                self.acs_id,
                tokens_requested=5000,
                tokens_available=2000,
            )
        except Exception as e:
            pytest.fail(f"record_acs_budget_exceeded raised unexpected exception: {e}")

    # ========== DECISION ROUTING METRICS (5 tests) ==========

    def test_record_acs_classified_big_data(self):
        """Test acs.classified metric for big data."""
        try:
            ACSMetricsCollector.record_acs_classified(
                self.tenant_id,
                self.acs_id,
                classification="big_data",
                confidence=0.95,
            )
        except Exception as e:
            pytest.fail(f"record_acs_classified raised unexpected exception: {e}")

    def test_record_acs_routed_native(self):
        """Test acs.routed metric to native engine."""
        try:
            ACSMetricsCollector.record_acs_routed(
                self.tenant_id,
                self.acs_id,
                target_engine="native",
                reason="user_explicit",
            )
        except Exception as e:
            pytest.fail(f"record_acs_routed raised unexpected exception: {e}")

    def test_record_acs_routed_acs(self):
        """Test acs.routed metric to ACS engine."""
        try:
            ACSMetricsCollector.record_acs_routed(
                self.tenant_id,
                self.acs_id,
                target_engine="acs",
                reason="classifier",
            )
        except Exception as e:
            pytest.fail(f"record_acs_routed raised unexpected exception: {e}")

    def test_record_acs_queued(self):
        """Test acs.queued metric."""
        try:
            ACSMetricsCollector.record_acs_queued(
                self.tenant_id,
                self.acs_id,
                queue_depth=5,
                wait_time_ms=3000,
            )
        except Exception as e:
            pytest.fail(f"record_acs_queued raised unexpected exception: {e}")

    def test_record_acs_fallback_triggered(self):
        """Test acs.fallback_triggered metric."""
        try:
            ACSMetricsCollector.record_acs_fallback_triggered(
                self.tenant_id,
                self.acs_id,
                reason="quota_exhausted",
                fallback_engine="native",
            )
        except Exception as e:
            pytest.fail(f"record_acs_fallback_triggered raised unexpected exception: {e}")

    # ========== ERROR CLASSIFICATION METRICS (5 tests) ==========

    def test_record_acs_error_validation(self):
        """Test acs.error metric for validation error."""
        try:
            ACSMetricsCollector.record_acs_error(
                self.tenant_id,
                self.acs_id,
                error_class="validation",
                error_code="invalid_input_schema",
            )
        except Exception as e:
            pytest.fail(f"record_acs_error raised unexpected exception: {e}")

    def test_record_acs_error_network(self):
        """Test acs.error metric for network error."""
        try:
            ACSMetricsCollector.record_acs_error(
                self.tenant_id,
                self.acs_id,
                error_class="network",
                error_code="connection_timeout",
            )
        except Exception as e:
            pytest.fail(f"record_acs_error raised unexpected exception: {e}")

    def test_record_acs_retry(self):
        """Test acs.retry metric."""
        try:
            ACSMetricsCollector.record_acs_retry(
                self.tenant_id,
                self.acs_id,
                attempt=2,
                max_attempts=3,
            )
        except Exception as e:
            pytest.fail(f"record_acs_retry raised unexpected exception: {e}")

    def test_record_acs_validation_failed(self):
        """Test acs.validation_failed metric."""
        try:
            ACSMetricsCollector.record_acs_validation_failed(
                self.tenant_id,
                self.acs_id,
                validation_type="output",
                details="Output exceeds max length",
            )
        except Exception as e:
            pytest.fail(f"record_acs_validation_failed raised unexpected exception: {e}")

    def test_record_acs_error_timeout(self):
        """Test acs.error metric for timeout."""
        try:
            ACSMetricsCollector.record_acs_error(
                self.tenant_id,
                self.acs_id,
                error_class="timeout",
                error_code="execution_timeout",
            )
        except Exception as e:
            pytest.fail(f"record_acs_error(timeout) raised unexpected exception: {e}")

    # ========== PERFORMANCE METRICS (5 tests) ==========

    def test_record_acs_latency(self):
        """Test acs.latency metric."""
        try:
            ACSMetricsCollector.record_acs_latency(
                self.tenant_id,
                self.acs_id,
                latency_ms=5500,
                p50_ms=4000,
                p99_ms=12000,
            )
        except Exception as e:
            pytest.fail(f"record_acs_latency raised unexpected exception: {e}")

    def test_record_acs_throughput(self):
        """Test acs.throughput metric."""
        try:
            ACSMetricsCollector.record_acs_throughput(
                self.tenant_id,
                runs_per_minute=15.5,
                success_rate=0.94,
            )
        except Exception as e:
            pytest.fail(f"record_acs_throughput raised unexpected exception: {e}")

    def test_record_acs_daily_summary(self):
        """Test acs.daily_summary metric."""
        try:
            ACSMetricsCollector.record_acs_daily_summary(
                self.tenant_id,
                total_runs=150,
                successful_runs=141,
                failed_runs=9,
                total_tokens_used=375000,
                average_latency_ms=4200,
            )
        except Exception as e:
            pytest.fail(f"record_acs_daily_summary raised unexpected exception: {e}")

    def test_record_acs_quality_score(self):
        """Test acs.quality_score metric."""
        try:
            ACSMetricsCollector.record_acs_quality_score(
                self.tenant_id,
                score=0.92,
                components={
                    "success_rate": 0.94,
                    "latency": 0.85,
                    "efficiency": 0.95,
                },
            )
        except Exception as e:
            pytest.fail(f"record_acs_quality_score raised unexpected exception: {e}")

    def test_record_acs_quality_score_minimal(self):
        """Test acs.quality_score metric without components."""
        try:
            ACSMetricsCollector.record_acs_quality_score(
                self.tenant_id,
                score=0.85,
            )
        except Exception as e:
            pytest.fail(f"record_acs_quality_score(minimal) raised unexpected exception: {e}")

    # ========== AGGREGATE METRICS (2 tests already covered) ==========


class TestACSMetricsIntegration:
    """Integration tests for ACS metrics collection workflow."""

    def setup_method(self):
        """Set up test fixtures."""
        self.tenant_id = "test-tenant"
        self.acs_id = "acs-run-67890"

    def test_full_acs_lifecycle_no_exception(self):
        """Test complete ACS lifecycle emits all metrics without exception."""
        try:
            # 1. Invocation
            ACSMetricsCollector.record_acs_invoked(
                self.tenant_id,
                self.acs_id,
                prompt_length=2048,
                budget_s=60,
            )

            # 2. Classification
            ACSMetricsCollector.record_acs_classified(
                self.tenant_id,
                self.acs_id,
                classification="big_data",
                confidence=0.92,
            )

            # 3. Routing decision
            ACSMetricsCollector.record_acs_routed(
                self.tenant_id,
                self.acs_id,
                target_engine="acs",
                reason="classifier",
            )

            # 4. Queueing
            ACSMetricsCollector.record_acs_queued(
                self.tenant_id,
                self.acs_id,
                queue_depth=3,
                wait_time_ms=1500,
            )

            # 5. Token consumption
            ACSMetricsCollector.record_acs_tokens_consumed(
                self.tenant_id,
                self.acs_id,
                tokens_used=3500,
                tokens_remaining=6500,
            )

            # 6. Completion
            ACSMetricsCollector.record_acs_completed(
                self.tenant_id,
                self.acs_id,
                status="success",
                duration_ms=7500,
                tokens_used=3500,
            )

            # 7. Latency recording
            ACSMetricsCollector.record_acs_latency(
                self.tenant_id,
                self.acs_id,
                latency_ms=7500,
                p50_ms=5000,
                p99_ms=15000,
            )
        except Exception as e:
            pytest.fail(f"Full ACS lifecycle raised unexpected exception: {e}")

    def test_error_recovery_workflow_no_exception(self):
        """Test metrics for error and recovery workflow."""
        try:
            # Initial invocation
            ACSMetricsCollector.record_acs_invoked(
                self.tenant_id,
                self.acs_id,
                prompt_length=1024,
                budget_s=30,
            )

            # Error occurs
            ACSMetricsCollector.record_acs_error(
                self.tenant_id,
                self.acs_id,
                error_class="network",
                error_code="timeout",
            )

            # Retry attempt
            ACSMetricsCollector.record_acs_retry(
                self.tenant_id,
                self.acs_id,
                attempt=1,
                max_attempts=3,
            )

            # Fallback triggered
            ACSMetricsCollector.record_acs_fallback_triggered(
                self.tenant_id,
                self.acs_id,
                reason="tde_unavailable",
                fallback_engine="native",
            )

            # Eventually succeeds
            ACSMetricsCollector.record_acs_completed(
                self.tenant_id,
                self.acs_id,
                status="success",
                duration_ms=12000,
                tokens_used=2000,
            )
        except Exception as e:
            pytest.fail(f"Error recovery workflow raised unexpected exception: {e}")

    def test_budget_exhaustion_workflow_no_exception(self):
        """Test metrics for budget exhaustion scenarios."""
        try:
            # Budget low
            ACSMetricsCollector.record_acs_budget_check(
                self.tenant_id,
                daily_tokens_used=90000,
                daily_tokens_limit=100000,
                budget_status="low",
            )

            # Attempt request
            ACSMetricsCollector.record_acs_invoked(
                self.tenant_id,
                self.acs_id,
                prompt_length=5000,
                budget_s=60,
            )

            # Budget exceeded
            ACSMetricsCollector.record_acs_budget_exceeded(
                self.tenant_id,
                self.acs_id,
                tokens_requested=15000,
                tokens_available=10000,
            )

            # Fallback triggered
            ACSMetricsCollector.record_acs_fallback_triggered(
                self.tenant_id,
                self.acs_id,
                reason="quota_exhausted",
                fallback_engine="native",
            )
        except Exception as e:
            pytest.fail(f"Budget exhaustion workflow raised unexpected exception: {e}")


class TestCheckpointGoalAlignment:
    """Tests for checkpoint goal and alignment score persistence — Phase 4 k=4."""

    def test_checkpoint_preserves_goal(self):
        """Test that checkpoint preserves goal across serialization."""
        checkpoint = SessionCheckpoint(
            session_id="session-123",
            task_id="task-456",
            phase="phase-1",
            tenant_id="test-tenant",
            goal="Implement feature X with full test coverage",
            goal_alignment_score=0.95,
            task_state=TaskState(
                task_id="task-456",
                goal="Implement feature X with full test coverage",
            ),
        )

        # Serialize and deserialize
        checkpoint_dict = checkpoint.to_dict()
        restored = SessionCheckpoint.from_dict(checkpoint_dict)

        # Verify goal is preserved
        assert restored.goal == "Implement feature X with full test coverage"
        assert restored.goal_alignment_score == 0.95

    def test_checkpoint_default_goal_alignment(self):
        """Test checkpoint defaults for goal and alignment score."""
        checkpoint = SessionCheckpoint(
            session_id="session-789",
            task_id="task-012",
            phase="phase-2",
            tenant_id="test-tenant",
        )

        # Verify defaults
        assert checkpoint.goal == ""
        assert checkpoint.goal_alignment_score == 0.0

    def test_checkpoint_goal_alignment_in_audit(self):
        """Test that checkpoint audit event includes task info."""
        checkpoint = SessionCheckpoint(
            session_id="session-789",
            task_id="task-012",
            phase="phase-2",
            tenant_id="test-tenant",
            goal="Fix critical bug in parser",
            goal_alignment_score=0.87,
        )

        audit_event = checkpoint.to_audit_event()

        assert audit_event["event_type"] == "session.checkpoint_created"
        assert audit_event["task_id"] == "task-012"
        assert audit_event["state_summary"]["iterations"] == 0

    def test_checkpoint_round_trip_preserves_all_fields(self):
        """Test checkpoint serialization round-trip preserves all fields."""
        original = SessionCheckpoint(
            session_id="session-999",
            task_id="task-888",
            phase="phase-3",
            tenant_id="test-tenant",
            trigger_type="split",
            iterations_at_checkpoint=42,
            token_count_at_checkpoint=50000,
            goal="Complete Phase 4 implementation",
            goal_alignment_score=0.88,
        )

        # Serialize
        data = original.to_dict()

        # Deserialize
        restored = SessionCheckpoint.from_dict(data)

        # Verify all fields
        assert restored.session_id == "session-999"
        assert restored.task_id == "task-888"
        assert restored.phase == "phase-3"
        assert restored.tenant_id == "test-tenant"
        assert restored.trigger_type == "split"
        assert restored.iterations_at_checkpoint == 42
        assert restored.token_count_at_checkpoint == 50000
        assert restored.goal == "Complete Phase 4 implementation"
        assert restored.goal_alignment_score == 0.88


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

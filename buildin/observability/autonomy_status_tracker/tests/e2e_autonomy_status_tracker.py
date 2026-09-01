"""E2E tests for Autonomy Status Tracker plugin."""

import pytest
import asyncio
from datetime import datetime, timezone
from typing import Dict, Any
import time
import json

# Import the real plugin components (not mocks)
from buildin.observability.autonomy_status_tracker import AutonomyStatusTracker
from buildin.observability.autonomy_status_tracker.events import (
    AutonomyEvent,
    EventType,
)
from core.audit.audit_writer import AuditWriter
from core.compliance.tripwire import boot_tripwire


class TestAutonomyStatusTrackerE2E:
    """End-to-end tests for Autonomy Status Tracker."""

    @pytest.fixture
    async def tracker(self):
        """Fixture: initialized tracker instance."""
        # Ensure boot tripwire is satisfied
        await boot_tripwire()

        tracker = AutonomyStatusTracker(tenant_id="test_tenant")
        await tracker.initialize()
        yield tracker
        await tracker.shutdown()

    @pytest.mark.asyncio
    async def test_full_session_lifecycle(self, tracker):
        """Test complete session lifecycle: INIT → ACTIVE → HARDENED → COMPLETE."""
        session_id = "sess_lifecycle_test"

        # Register session
        session_info = {
            "session_id": session_id,
            "user_id": "user_123",
            "started_at": datetime.now(timezone.utc),
            "autonomy_level": "FULL"
        }
        await tracker.register_session(session_info)

        # Verify initial state
        status = await tracker.get_session_status(session_id)
        assert status["current_state"] == "INIT"
        assert status["autonomy_level"] == "FULL"

        # Transition to ACTIVE
        event = AutonomyEvent(
            event_type=EventType.STATE_TRANSITION,
            session_id=session_id,
            data={
                "from_state": "INIT",
                "to_state": "ACTIVE",
                "reason": "bootstrap_complete"
            }
        )
        await tracker.emit_event(event)
        status = await tracker.get_session_status(session_id)
        assert status["current_state"] == "ACTIVE"

        # Transition to HARDENED
        event = AutonomyEvent(
            event_type=EventType.STATE_TRANSITION,
            session_id=session_id,
            data={
                "from_state": "ACTIVE",
                "to_state": "HARDENED",
                "reason": "hardening_audit_passed"
            }
        )
        await tracker.emit_event(event)
        status = await tracker.get_session_status(session_id)
        assert status["current_state"] == "HARDENED"

        # Transition to COMPLETE
        event = AutonomyEvent(
            event_type=EventType.STATE_TRANSITION,
            session_id=session_id,
            data={
                "from_state": "HARDENED",
                "to_state": "COMPLETE",
                "reason": "task_completion"
            }
        )
        await tracker.emit_event(event)
        status = await tracker.get_session_status(session_id)
        assert status["current_state"] == "COMPLETE"

    @pytest.mark.asyncio
    async def test_hardening_checkpoints(self, tracker):
        """Test hardening audit checkpoints."""
        session_id = "sess_hardening_test"

        await tracker.register_session({
            "session_id": session_id,
            "user_id": "user_456",
            "started_at": datetime.now(timezone.utc),
            "autonomy_level": "FULL"
        })

        # Emit hardening checkpoint events
        checkpoints = [
            ("path_gate_locked", True),
            ("consent_model_verified", True),
            ("audit_chain_intact", True),
            ("context_isolated", True),
            ("error_handlers_active", True),
        ]

        for checkpoint_name, passed in checkpoints:
            event = AutonomyEvent(
                event_type=EventType.HARDENING_CHECKPOINT,
                session_id=session_id,
                data={
                    "checkpoint": checkpoint_name,
                    "passed": passed,
                    "details": {
                        "verified_at": datetime.now(timezone.utc).isoformat(),
                        "component": checkpoint_name.split("_")[0]
                    }
                }
            )
            await tracker.emit_event(event)

        # Retrieve diagnostics
        diag = await tracker.get_diagnostics(session_id)
        assert diag["hardening_checkpoints"] >= 5
        assert all(cp["passed"] for cp in diag.get("checkpoint_details", []))

    @pytest.mark.asyncio
    async def test_recovery_detection_and_trigger(self, tracker):
        """Test failure detection and recovery trigger."""
        session_id = "sess_recovery_test"

        await tracker.register_session({
            "session_id": session_id,
            "user_id": "user_789",
            "started_at": datetime.now(timezone.utc),
            "autonomy_level": "FULL"
        })

        # Simulate failure detection
        failure_event = AutonomyEvent(
            event_type=EventType.FAILURE_DETECTED,
            session_id=session_id,
            data={
                "failure_type": "context_loss",
                "severity": "HIGH",
                "details": {
                    "lost_context_items": ["execution_state", "session_memory"],
                    "last_known_state": "ACTIVE"
                },
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        )
        await tracker.emit_event(failure_event)

        # Simulate recovery attempt
        recovery_event = AutonomyEvent(
            event_type=EventType.RECOVERY_ATTEMPT,
            session_id=session_id,
            data={
                "recovery_strategy": "context_restore_from_checkpoint",
                "initiated_at": datetime.now(timezone.utc).isoformat(),
                "success": True,
                "recovery_time_ms": 245,
                "state_before": "RECOVERING",
                "state_after": "ACTIVE"
            }
        )
        await tracker.emit_event(recovery_event)

        # Verify recovery tracked
        diag = await tracker.get_diagnostics(session_id)
        assert diag["recovery_attempts"] >= 1
        status = await tracker.get_session_status(session_id)
        assert status["last_recovery_time_ms"] == 245

    @pytest.mark.asyncio
    async def test_concurrent_sessions(self, tracker):
        """Test tracker with concurrent session events."""
        num_sessions = 10
        sessions = [f"sess_concurrent_{i}" for i in range(num_sessions)]

        # Register all sessions
        for sid in sessions:
            await tracker.register_session({
                "session_id": sid,
                "user_id": f"user_{sid}",
                "started_at": datetime.now(timezone.utc),
                "autonomy_level": "FULL"
            })

        # Emit concurrent events
        async def emit_events_for_session(sid):
            for transition_num in range(5):
                event = AutonomyEvent(
                    event_type=EventType.STATE_TRANSITION,
                    session_id=sid,
                    data={
                        "transition_num": transition_num,
                        "reason": f"transition_{transition_num}"
                    }
                )
                await tracker.emit_event(event)
                await asyncio.sleep(0.001)  # Small delay

        # Run concurrently
        tasks = [emit_events_for_session(sid) for sid in sessions]
        await asyncio.gather(*tasks)

        # Verify all sessions tracked
        aggregate = await tracker.get_aggregate_metrics()
        assert aggregate["total_sessions"] >= num_sessions
        assert aggregate["active_sessions"] >= 0

    @pytest.mark.asyncio
    async def test_performance_event_ingestion(self, tracker):
        """Test event ingestion performance (<5ms per event)."""
        session_id = "sess_perf_test"

        await tracker.register_session({
            "session_id": session_id,
            "user_id": "user_perf",
            "started_at": datetime.now(timezone.utc),
            "autonomy_level": "FULL"
        })

        # Measure ingestion of 100 events
        num_events = 100
        times = []

        for i in range(num_events):
            event = AutonomyEvent(
                event_type=EventType.STATE_TRANSITION,
                session_id=session_id,
                data={
                    "event_num": i,
                    "reason": f"event_{i}"
                }
            )

            start = time.time()
            await tracker.emit_event(event)
            elapsed_ms = (time.time() - start) * 1000
            times.append(elapsed_ms)

        # Verify SLA
        mean_time = sum(times) / len(times)
        max_time = max(times)

        assert mean_time < 5.0, f"Mean ingestion time {mean_time:.2f}ms exceeds 5ms target"
        assert max_time < 15.0, f"Max ingestion time {max_time:.2f}ms exceeds 15ms threshold"

        # Print performance metrics
        print(f"\nEvent Ingestion Performance:")
        print(f"  Mean: {mean_time:.2f}ms")
        print(f"  Max:  {max_time:.2f}ms")
        print(f"  95th percentile: {sorted(times)[int(0.95*len(times))]:.2f}ms")

    @pytest.mark.asyncio
    async def test_diagnostics_retrieval_performance(self, tracker):
        """Test diagnostics retrieval performance (<50ms)."""
        session_id = "sess_diag_perf"

        await tracker.register_session({
            "session_id": session_id,
            "user_id": "user_diag",
            "started_at": datetime.now(timezone.utc),
            "autonomy_level": "FULL"
        })

        # Emit 50 events
        for i in range(50):
            event = AutonomyEvent(
                event_type=EventType.STATE_TRANSITION,
                session_id=session_id,
                data={"event_num": i}
            )
            await tracker.emit_event(event)

        # Measure diagnostics retrieval
        times = []
        for _ in range(10):
            start = time.time()
            diag = await tracker.get_diagnostics(session_id)
            elapsed_ms = (time.time() - start) * 1000
            times.append(elapsed_ms)

        mean_time = sum(times) / len(times)
        assert mean_time < 50.0, f"Mean retrieval time {mean_time:.2f}ms exceeds 50ms target"

        print(f"\nDiagnostics Retrieval Performance:")
        print(f"  Mean: {mean_time:.2f}ms")
        print(f"  Max:  {max(times):.2f}ms")

    @pytest.mark.asyncio
    async def test_audit_trail_integration(self, tracker):
        """Test that events are properly recorded in audit trail."""
        session_id = "sess_audit_test"

        await tracker.register_session({
            "session_id": session_id,
            "user_id": "user_audit",
            "started_at": datetime.now(timezone.utc),
            "autonomy_level": "FULL"
        })

        # Emit event that should be audit-logged
        event = AutonomyEvent(
            event_type=EventType.STATE_TRANSITION,
            session_id=session_id,
            data={
                "from_state": "INIT",
                "to_state": "ACTIVE",
                "reason": "bootstrap_complete"
            }
        )
        await tracker.emit_event(event)

        # Verify event was emitted
        diag = await tracker.get_diagnostics(session_id)
        assert diag["total_events"] >= 1

        # Verify audit records exist (via audit subsystem)
        # This ensures compliance with GDPR Art. 30
        status = await tracker.get_session_status(session_id)
        assert status["audit_verified"] is True

    @pytest.mark.asyncio
    async def test_health_score_calculation(self, tracker):
        """Test health score reflects session state."""
        session_id = "sess_health_test"

        await tracker.register_session({
            "session_id": session_id,
            "user_id": "user_health",
            "started_at": datetime.now(timezone.utc),
            "autonomy_level": "FULL"
        })

        # New session should have high health
        status = await tracker.get_session_status(session_id)
        initial_health = status["health_score"]
        assert initial_health >= 80

        # Simulate degradation (failure without recovery)
        failure_event = AutonomyEvent(
            event_type=EventType.FAILURE_DETECTED,
            session_id=session_id,
            data={
                "failure_type": "error_handler_timeout",
                "severity": "MEDIUM"
            }
        )
        await tracker.emit_event(failure_event)

        status = await tracker.get_session_status(session_id)
        degraded_health = status["health_score"]
        assert degraded_health < initial_health

        # Recovery should restore health
        recovery_event = AutonomyEvent(
            event_type=EventType.RECOVERY_ATTEMPT,
            session_id=session_id,
            data={
                "recovery_strategy": "error_handler_restart",
                "success": True,
                "recovery_time_ms": 50
            }
        )
        await tracker.emit_event(recovery_event)

        status = await tracker.get_session_status(session_id)
        recovered_health = status["health_score"]
        assert recovered_health > degraded_health

    @pytest.mark.asyncio
    async def test_aggregate_metrics(self, tracker):
        """Test aggregate metrics across all sessions."""
        # Register 5 sessions
        for i in range(5):
            sid = f"sess_agg_{i}"
            await tracker.register_session({
                "session_id": sid,
                "user_id": f"user_agg_{i}",
                "started_at": datetime.now(timezone.utc),
                "autonomy_level": "FULL"
            })

            # Emit different event types
            for j in range(3):
                event = AutonomyEvent(
                    event_type=EventType.STATE_TRANSITION,
                    session_id=sid,
                    data={"transition": j}
                )
                await tracker.emit_event(event)

        # Get aggregate metrics
        aggregate = await tracker.get_aggregate_metrics()

        assert aggregate["total_sessions"] >= 5
        assert aggregate["total_events"] >= 15
        assert "mean_health_score" in aggregate
        assert 0 <= aggregate["mean_health_score"] <= 100
        assert "sessions_in_recovery" in aggregate

    @pytest.mark.asyncio
    async def test_persistence_checkpoint(self, tracker):
        """Test that session state persists across checks."""
        session_id = "sess_persist_test"

        await tracker.register_session({
            "session_id": session_id,
            "user_id": "user_persist",
            "started_at": datetime.now(timezone.utc),
            "autonomy_level": "FULL"
        })

        # Emit and record event
        event = AutonomyEvent(
            event_type=EventType.STATE_TRANSITION,
            session_id=session_id,
            data={
                "from_state": "INIT",
                "to_state": "ACTIVE",
                "reason": "bootstrap"
            }
        )
        await tracker.emit_event(event)

        # Get initial diagnostics
        diag1 = await tracker.get_diagnostics(session_id)
        event_count_1 = diag1["total_events"]

        # Emit another event
        event2 = AutonomyEvent(
            event_type=EventType.STATE_TRANSITION,
            session_id=session_id,
            data={"from_state": "ACTIVE", "to_state": "HARDENED"}
        )
        await tracker.emit_event(event2)

        # Verify count increased
        diag2 = await tracker.get_diagnostics(session_id)
        event_count_2 = diag2["total_events"]

        assert event_count_2 > event_count_1
        assert event_count_2 >= 2


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--log-cli-level=INFO"])

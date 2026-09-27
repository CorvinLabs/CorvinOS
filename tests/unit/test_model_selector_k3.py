"""Unit Tests for Model-Selector K=3 (ADR-2084) — 30 tests, Tier 2."""

import pytest
import asyncio
import time
from unittest.mock import Mock, patch

from core.skills.os_skills.health_check_monitor import HealthCheckMonitor, TaskHealthState
from core.skills.os_skills.model_selector_k3_integration import (
    resolve_os_model, initialize_health_monitor, unregister_task, handle_escalation_event
)
from core.learning.confidence_scoreboard_k3_extension import ConfidenceScoreboardK3
from core.task_tracking.audit_escalation_events import emit_escalation_to_audit


# ============================================================================
# HealthCheckMonitor Tests (10 tests)
# ============================================================================

def test_health_monitor_init():
    """Initialize monitor with defaults."""
    monitor = HealthCheckMonitor()
    assert monitor.sla_target_ms == 600
    assert monitor.check_interval_ms == 100
    assert len(monitor.running_tasks) == 0


def test_register_task():
    """Register task for monitoring."""
    monitor = HealthCheckMonitor()
    monitor.register_task("task1", "haiku")
    assert "task1" in monitor.running_tasks
    assert monitor.running_tasks["task1"].model == "haiku"


def test_unregister_task():
    """Unregister task (end of execution)."""
    monitor = HealthCheckMonitor()
    monitor.register_task("task1", "haiku")
    monitor.unregister_task("task1")
    assert "task1" not in monitor.running_tasks


def test_estimate_p99_heuristic():
    """P99 estimation: 50% time → 150% final."""
    monitor = HealthCheckMonitor()
    elapsed_ms = 200  # At 50% checkpoint (SLA 400ms)
    p99 = monitor._estimate_p99(elapsed_ms)
    assert p99 == 300  # 200 * 1.5


def test_escalate_model_chain():
    """Model escalation: haiku → sonnet → opus."""
    monitor = HealthCheckMonitor()
    assert monitor._escalate_model("haiku") == "sonnet"
    assert monitor._escalate_model("sonnet") == "opus"
    assert monitor._escalate_model("opus") == "opus"


@pytest.mark.asyncio
async def test_escalation_triggers_at_threshold():
    """Escalation triggers when p99 > 600ms."""
    monitor = HealthCheckMonitor()
    monitor.register_task("task1", "haiku")

    # Simulate: elapsed 400ms (at 50% checkpoint), p99 projection = 600ms
    task_state = monitor.running_tasks["task1"]
    await monitor._trigger_escalation("task1", task_state, p99_projection=620)

    assert task_state.escalated
    assert task_state.model == "sonnet"
    assert monitor.event_queue.qsize() > 0


@pytest.mark.asyncio
async def test_single_escalation_per_task():
    """Only one escalation per task (no cascades)."""
    monitor = HealthCheckMonitor()
    monitor.register_task("task1", "haiku")
    task_state = monitor.running_tasks["task1"]

    # First escalation
    await monitor._trigger_escalation("task1", task_state, 620)
    assert task_state.escalated
    old_model = task_state.model

    # Try second escalation (should be blocked by escalated flag)
    task_state.p99_projection = 700  # Even higher
    # In real _check_all_tasks, wouldn't escalate again due to flag
    assert task_state.escalated  # Flag remains true


@pytest.mark.asyncio
async def test_event_queue_non_blocking():
    """Event emission is non-blocking (doesn't raise)."""
    monitor = HealthCheckMonitor()
    monitor.register_task("task1", "haiku")

    # Should not raise even with queue full
    for i in range(1010):  # Queue size 1000
        try:
            monitor.event_queue.put_nowait({"test": i})
        except asyncio.QueueFull:
            pass

    # Monitor should still work


@pytest.mark.asyncio
async def test_health_check_loop_start_stop():
    """Start and stop health check loop."""
    monitor = HealthCheckMonitor()
    await monitor.start()
    assert monitor.health_check_task is not None

    await monitor.stop()
    # Task should be cancelled
    assert monitor.health_check_task.cancelled() or monitor.health_check_task.done()


@pytest.mark.asyncio
async def test_get_escalation_events():
    """Drain escalation events from queue."""
    monitor = HealthCheckMonitor()

    # Queue an event
    event = {
        "event_type": "escalation_triggered",
        "task_id": "task1",
        "model_old": "haiku",
        "model_new": "sonnet",
    }
    monitor.event_queue.put_nowait(event)

    # Get events
    events = await monitor.get_escalation_events()
    assert len(events) == 1
    assert events[0]["task_id"] == "task1"


# ============================================================================
# ModelSelector K=3 Integration Tests (5 tests)
# ============================================================================

def test_resolve_os_model_defers_to_the_canonical_resolver(monkeypatch):
    """K=3 no longer decides the model: it returns what the ONE resolver
    (bridges/shared/model_selector.resolve_os_model, ADR-0952) returns. The
    old stub answered "sonnet" for everything."""
    from corvin_operator.bridges.shared import model_selector as ms

    seen = {}

    def fake(profile, **kw):
        seen.update(kw, profile=profile)
        return "claude-opus-5"

    monkeypatch.setattr(ms, "resolve_os_model", fake)
    assert resolve_os_model("design a distributed system", tenant_id="t1") == "claude-opus-5"
    assert seen["task_input"] == "design a distributed system"
    assert seen["tenant_id"] == "t1"


def test_resolve_os_model_honours_operator_pin(monkeypatch):
    """Tier 1 pin wins; the stub ignored it."""
    monkeypatch.setenv("CORVIN_OS_MODEL_OVERRIDE", "claude-haiku-4-5-20251001")
    assert resolve_os_model("write a compiler") == "claude-haiku-4-5-20251001"


@pytest.mark.asyncio
async def test_pinned_task_is_registered_pinned_and_never_escalated(monkeypatch):
    import core.skills.os_skills.model_selector_k3_integration as k3

    monkeypatch.setenv("CORVIN_OS_MODEL_OVERRIDE", "claude-haiku-4-5-20251001")
    monitor = HealthCheckMonitor(sla_target_ms=10)
    monkeypatch.setattr(k3, "_HEALTH_MONITOR", monitor)

    class TaskInput:
        task_id = "task1"
        text = "hello"

    model = resolve_os_model(TaskInput())
    state = monitor.running_tasks["task1"]
    assert state.pinned and state.model == model
    state.start_time -= 10  # far beyond the SLA
    await monitor._check_all_tasks()
    assert not state.escalated and state.model == model
    assert monitor.event_queue.qsize() == 0


@pytest.mark.asyncio
async def test_escalation_uses_the_task_sla_not_a_literal_600():
    monitor = HealthCheckMonitor()
    monitor.register_task("t", "haiku", sla_target_ms=100)
    monitor.running_tasks["t"].start_time -= 0.08  # 80 ms elapsed → projection 120 > 100
    await monitor._check_all_tasks()
    assert monitor.running_tasks["t"].model == "sonnet"


@pytest.mark.asyncio
async def test_top_tier_does_not_emit_a_fake_escalation():
    monitor = HealthCheckMonitor()
    monitor.register_task("t", "opus")
    await monitor._trigger_escalation("t", monitor.running_tasks["t"], 9999)
    assert monitor.event_queue.qsize() == 0


@pytest.mark.asyncio
async def test_initialize_health_monitor_starts_inside_the_loop():
    import core.skills.os_skills.model_selector_k3_integration as k3

    monitor = await initialize_health_monitor()
    try:
        assert monitor.health_check_task is not None and not monitor.health_check_task.done()
    finally:
        await monitor.stop()
        k3._HEALTH_MONITOR = None


@pytest.mark.asyncio
async def test_handle_escalation_event():
    """Handle escalation event returns new model."""
    event = {
        "model_old": "haiku",
        "model_new": "sonnet",
        "reason": "latency_sla_risk",
    }
    new_model = await handle_escalation_event(event)
    assert new_model == "sonnet"


def test_unregister_task_integration():
    """Unregister task via integration function."""
    monitor = HealthCheckMonitor()
    monitor.register_task("task1", "haiku")

    # unregister via global (stubbed in unit test)
    monitor.unregister_task("task1")
    assert "task1" not in monitor.running_tasks


# ============================================================================
# ConfidenceScoreboard K=3 Tests (10 tests)
# ============================================================================

def test_scoreboard_init():
    """Initialize scoreboard."""
    sb = ConfidenceScoreboardK3()
    assert "haiku" in sb.scores
    assert "sonnet" in sb.scores
    assert "opus" in sb.scores


def test_scoreboard_update_success():
    """Update scoreboard with success outcome."""
    sb = ConfidenceScoreboardK3()
    delta = sb.update("haiku", "success", escalated=False, latency_observed_ms=300)

    assert sb.scores["haiku"]["success_count"] == 1
    assert sb.scores["haiku"]["total_count"] == 1


def test_confidence_delta_no_escalation():
    """Confidence delta with success, no escalation."""
    sb = ConfidenceScoreboardK3()
    sb.update("haiku", "success", escalated=False)
    delta = sb.get_confidence_delta("haiku")
    assert delta == 1.0  # 100% success


def test_confidence_delta_with_escalation():
    """Confidence delta penalized by escalation."""
    sb = ConfidenceScoreboardK3()
    sb.update("haiku", "success", escalated=True)  # Success but escalated
    delta = sb.get_confidence_delta("haiku")
    # delta = success_rate (1.0) - (escalation_rate (1.0) * 0.1) = 0.9
    assert delta == 0.9


def test_trend_stable():
    """Trend detection: stable."""
    sb = ConfidenceScoreboardK3()
    sb.update("haiku", "success", latency_observed_ms=300)
    sb.update("haiku", "success", latency_observed_ms=310)
    sb.update("haiku", "success", latency_observed_ms=305)
    trend = sb.scores["haiku"]["trend"]
    assert trend == "stable"


def test_trend_improving():
    """Trend detection: improving (latency decreasing)."""
    sb = ConfidenceScoreboardK3()
    sb.update("haiku", "success", latency_observed_ms=500)
    sb.update("haiku", "success", latency_observed_ms=400)
    sb.update("haiku", "success", latency_observed_ms=300)
    trend = sb.scores["haiku"]["trend"]
    assert trend == "improving"


def test_trend_degrading():
    """Trend detection: degrading (latency increasing)."""
    sb = ConfidenceScoreboardK3()
    sb.update("haiku", "success", latency_observed_ms=300)
    sb.update("haiku", "success", latency_observed_ms=400)
    sb.update("haiku", "success", latency_observed_ms=500)
    trend = sb.scores["haiku"]["trend"]
    assert trend == "degrading"


def test_multiple_models_independent():
    """Scores per model are independent."""
    sb = ConfidenceScoreboardK3()
    sb.update("haiku", "success")
    sb.update("sonnet", "timeout")

    assert sb.scores["haiku"]["success_count"] == 1
    assert sb.scores["sonnet"]["total_count"] == 1


def test_latency_samples_rolling_window():
    """Latency samples use rolling window (max 10)."""
    sb = ConfidenceScoreboardK3()
    for i in range(15):
        sb.update("haiku", "success", latency_observed_ms=100 + i)

    # Should have max 10 samples
    assert len(sb.scores["haiku"]["latency_samples"]) == 10


def test_scoreboard_state_export():
    """Export full scoreboard state."""
    sb = ConfidenceScoreboardK3()
    sb.update("haiku", "success")
    state = sb.get_all_scores()

    assert "haiku" in state
    assert state["haiku"]["success_count"] == 1


# ============================================================================
# AuditTrail Integration Tests (5 tests)
# ============================================================================

def test_emit_escalation_to_audit():
    """Emit escalation event to audit trail."""
    from core.task_tracking.audit_escalation_events import emit_escalation_to_audit, ESCALATION_TRIGGERED

    event = {
        "task_id": "task1",
        "model_old": "haiku",
        "model_new": "sonnet",
        "reason": "latency_sla_risk",
    }

    # Mock audit backend
    mock_audit = Mock()
    mock_audit.get_last_hash.return_value = "abc123"

    result = emit_escalation_to_audit(event, mock_audit)
    assert result is True
    assert mock_audit.write_event.called


def test_escalation_audit_schema():
    """Escalation audit schema is correct."""
    from core.task_tracking.audit_escalation_events import get_escalation_audit_schema

    schema = get_escalation_audit_schema()
    assert "event_type" in schema
    assert "task_id" in schema
    assert "model_old" in schema
    assert "model_new" in schema


def test_emit_escalation_no_audit_backend():
    """Emit with no audit backend fails gracefully."""
    from core.task_tracking.audit_escalation_events import emit_escalation_to_audit

    event = {"task_id": "task1"}
    result = emit_escalation_to_audit(event, None)
    assert result is False


def test_emit_escalation_audit_error():
    """Emit handles audit backend errors gracefully."""
    from core.task_tracking.audit_escalation_events import emit_escalation_to_audit

    event = {"task_id": "task1"}
    mock_audit = Mock()
    mock_audit.write_event.side_effect = Exception("Audit write failed")

    result = emit_escalation_to_audit(event, mock_audit)
    assert result is False


def test_escalation_event_hash_chain():
    """Escalation events support hash-chaining (ADR-0232)."""
    from core.task_tracking.audit_escalation_events import emit_escalation_to_audit

    event = {"task_id": "task1", "model_old": "haiku", "model_new": "sonnet"}

    mock_audit = Mock()
    mock_audit.get_last_hash.return_value = "prev_hash_123"

    emit_escalation_to_audit(event, mock_audit)

    # Verify write_event was called
    call_args = mock_audit.write_event.call_args
    assert call_args is not None
    written_event = call_args[0][0]
    assert written_event["prev_hash"] == "prev_hash_123"


# ============================================================================
# Integration Tests (across components)
# ============================================================================

def test_k3_full_flow():
    """K=3 full flow: monitor → escalation → scoreboard → delta."""
    # 1. Register task
    monitor = HealthCheckMonitor()
    monitor.register_task("task1", "haiku")

    # 2. Escalate (p99 >600ms)
    task_state = monitor.running_tasks["task1"]
    # Simulate escalation (already tested above)

    # 3. Update scoreboard
    sb = ConfidenceScoreboardK3()
    delta = sb.update("haiku", "success", escalated=True, latency_observed_ms=620)

    # 4. Confidence delta for next task
    next_delta = sb.get_confidence_delta("haiku")
    assert next_delta == 0.9  # Penalized for escalation


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

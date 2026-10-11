"""E2E Tests: Learning Loop Closure (ADR-0314 + ADR-0722).

Complete learning loop from skill execution → feedback → confidence scoring → parameter optimization.

Tests cover:
1. Event ingestion (skill.executed, feedback, outcomes)
2. Signal aggregation (batching, windowing, deduplication)
3. Confidence scoring (success rate, latency, cost)
4. Parameter optimization (convergence, bounds, PII safety)
5. Config updates (manifest writing, audit logging)
6. Tenant isolation (GDPR Art. 32)
7. Compliance (audit-first, hash-chain, immutability)
"""

import asyncio
import json
import pytest
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List
from uuid import uuid4

from core.learning.learning_events import LearningEvent, EventType
from core.learning.event_store import EventStore
from core.learning.event_emitter import EventEmitter
from core.learning.outcome_sink import emit_task_outcome, recent_outcomes


@pytest.fixture
def tenant_id():
    """Fixture: tenant ID."""
    return "_default"


@pytest.fixture
def tmp_tenant_home(tmp_path, tenant_id):
    """Fixture: temporary tenant home directory."""
    tenant_dir = tmp_path / "tenants" / tenant_id / "global"
    tenant_dir.mkdir(parents=True, exist_ok=True)
    return tenant_dir


@pytest.fixture
def event_store(tmp_tenant_home, tenant_id):
    """Fixture: EventStore for learning events."""
    return EventStore(tmp_tenant_home, tenant_id=tenant_id)


@pytest.fixture
def event_emitter(event_store):
    """Fixture: EventEmitter (fire-and-forget queue)."""
    em = EventEmitter(event_store, queue_size=10000)
    yield em
    em.stop()  # Flush queue


@pytest.fixture
def skill_id():
    """Fixture: skill ID being tested."""
    return "os.delegation_router"






class TestSkillExecutionEvent:
    """Test skill.executed event emission and ingestion."""

    def test_create_skill_executed_event(self, tenant_id, skill_id):
        """Create a skill.executed learning event."""
        event = LearningEvent.create(
            event_type=EventType.SKILL_EXECUTED,
            skill_id=skill_id,
            tenant_id=tenant_id,
            signal={
                "input": "classify_request(...)",
                "output": "route_to=opus",
                "latency_ms": 42,
                "success": True,
            },
            skill_version="1.0.0",
            lom="core/skills/os_skills/delegation_router.py:execute:L100",
        )

        assert event.event_type == EventType.SKILL_EXECUTED
        assert event.skill_id == skill_id
        assert event.tenant_id == tenant_id
        assert event.signal["latency_ms"] == 42
        assert event.lom  # Line of moral responsibility present

















class TestAuditTrail:
    """Test audit trail integration (audit-first writes, hash-chaining)."""


    @pytest.mark.asyncio
    async def test_outcome_sink_integration(self, event_emitter, event_store, tenant_id):
        """Test outcome_sink emits OUTCOME events to learning store."""
        # Emit a task outcome
        success = emit_task_outcome(
            tenant_id=tenant_id,
            task_id="task-outcome-test",
            status="completed",
            exit_code=0,
            duration_ms=1500,
            engine="opus",
            emitter=event_emitter,
        )

        assert success

        # Flush emitter to ensure events are written
        await asyncio.sleep(0.1)
        event_emitter.stop()

        # Query outcomes from store
        successes, total = recent_outcomes(tenant_id, limit=10, store=event_store)

        assert total >= 1


                    # (success depends on manifest path availability)

        # Loop closure verified: events → signals → optimization → config


class TestComplianceAndSafety:
    """Test compliance requirements (GDPR Art. 5/30/32, EU AI Act)."""



    def test_events_are_immutable(self, tenant_id, skill_id):
        """Verify LearningEvent dataclass is frozen."""
        event = LearningEvent.create(
            event_type=EventType.OUTCOME,
            skill_id=skill_id,
            tenant_id=tenant_id,
        )

        # Attempting to modify should raise FrozenInstanceError
        with pytest.raises(Exception):  # frozen dataclass raises AttributeError
            event.tenant_id = "hacked"

    def test_content_free_events(self, tenant_id, skill_id):
        """Verify no PII/secrets/prompts in events."""
        event = LearningEvent.create(
            event_type=EventType.OUTCOME,
            skill_id=skill_id,
            tenant_id=tenant_id,
            signal={
                "task_id": "uuid-12345",
                "success": True,
                "duration_ms": 100,
                "cost": 0.05,
            },
        )

        # No prompts, transcripts, user data
        signal_json = json.dumps(event.signal or {})
        assert "prompt" not in signal_json.lower()
        assert "instruction" not in signal_json.lower()
        assert "secret" not in signal_json.lower()


        # ingester1 should accept event1, reject event2
        # Both should be None (window not closed)


__all__ = [
    "TestSkillExecutionEvent",
    "TestFeedbackEventProcessing",
    "TestOutcomeEventProcessing",
    "TestWindowAggregation",
    "TestConfidenceScoring",
    "TestConvergenceDetection",
    "TestParameterOptimization",
    "TestConfigUpdate",
    "TestAuditTrail",
    "TestEndToEndLoopClosure",
    "TestComplianceAndSafety",
]

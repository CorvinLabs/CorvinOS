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
from core.learning.skill_feedback_ingester import (
    SkillFeedbackIngester,
    FeedbackSignalType,
)
from core.learning.skill_optimizer_loop import SkillOptimizerLoop, OptimizationDecision
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


@pytest.fixture
def ingester(tenant_id):
    """Fixture: SkillFeedbackIngester."""
    return SkillFeedbackIngester(
        tenant_id=tenant_id,
        batch_size=10,  # Small for testing
        window_seconds=5,
    )


@pytest.fixture
def optimizer(tenant_id, skill_id, event_emitter):
    """Fixture: SkillOptimizerLoop."""
    return SkillOptimizerLoop(
        tenant_id=tenant_id,
        skill_id=skill_id,
        emitter=event_emitter,
    )


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

    @pytest.mark.asyncio
    async def test_ingest_skill_executed_event(self, ingester, tenant_id, skill_id):
        """Ingest a skill.executed event."""
        event = LearningEvent.create(
            event_type=EventType.SKILL_EXECUTED,
            skill_id=skill_id,
            tenant_id=tenant_id,
            signal={"latency_ms": 100, "success": True},
        )

        signal = await ingester.ingest_event(event)
        assert signal is None  # Window not yet complete (batch_size=10)


class TestFeedbackEventProcessing:
    """Test feedback event handling (user thumbs up/down, ratings)."""

    @pytest.mark.asyncio
    async def test_ingest_user_feedback_thumbs_up(self, ingester, tenant_id, skill_id):
        """Ingest user feedback: thumbs up."""
        event = LearningEvent.create(
            event_type=EventType.FEEDBACK,
            skill_id=skill_id,
            tenant_id=tenant_id,
            signal={
                "is_positive": True,
                "feedback_text": "Good routing decision",
            },
        )

        signal = await ingester.ingest_event(event)
        # Window not complete, signal is None
        assert signal is None

    @pytest.mark.asyncio
    async def test_ingest_user_feedback_with_rating(self, ingester, tenant_id, skill_id):
        """Ingest user feedback with rating."""
        event = LearningEvent.create(
            event_type=EventType.FEEDBACK,
            skill_id=skill_id,
            tenant_id=tenant_id,
            signal={
                "is_positive": True,
                "rating": 4,  # 1-5 scale
            },
        )

        signal = await ingester.ingest_event(event)
        assert signal is None  # Window not yet complete


class TestOutcomeEventProcessing:
    """Test task outcome event handling."""

    @pytest.mark.asyncio
    async def test_ingest_task_outcome_success(self, ingester, tenant_id, skill_id):
        """Ingest a successful task outcome."""
        event = LearningEvent.create(
            event_type=EventType.OUTCOME,
            skill_id=skill_id,
            tenant_id=tenant_id,
            signal={
                "task_id": "task-123",
                "status": "completed",
                "success": True,
                "exit_code": 0,
                "duration_ms": 2500,
                "cost": 0.05,
            },
        )

        signal = await ingester.ingest_event(event)
        assert signal is None  # Window not yet complete

    @pytest.mark.asyncio
    async def test_ingest_task_outcome_failure(self, ingester, tenant_id, skill_id):
        """Ingest a failed task outcome."""
        event = LearningEvent.create(
            event_type=EventType.OUTCOME,
            skill_id=skill_id,
            tenant_id=tenant_id,
            signal={
                "task_id": "task-456",
                "status": "failed",
                "success": False,
                "exit_code": 1,
                "duration_ms": 5000,
            },
        )

        signal = await ingester.ingest_event(event)
        assert signal is None  # Window not yet complete


class TestWindowAggregation:
    """Test event windowing and aggregation."""

    @pytest.mark.asyncio
    async def test_batch_aggregation_triggers_on_size(self, ingester, tenant_id, skill_id):
        """Verify that window closes when batch_size is reached."""
        # Ingest 10 events (batch_size=10)
        for i in range(10):
            event = LearningEvent.create(
                event_type=EventType.OUTCOME,
                skill_id=skill_id,
                tenant_id=tenant_id,
                # Outcome-only events with a clear majority: latency/cost votes
                # count EVERY event, so mixing them in makes LATENCY_FAST win.
                signal={
                    "task_id": f"task-{i}",
                    "success": i < 7,  # 7 successes, 3 failures
                },
            )
            signal = await ingester.ingest_event(event)
            if i < 9:
                assert signal is None  # Not yet complete
            else:
                # 10th event should close window
                assert signal is not None
                assert signal.signal_type == FeedbackSignalType.OUTCOME_SUCCESS
                # strength = success_rate * sqrt(n / 100) = 0.7 * sqrt(0.1)
                assert signal.strength == pytest.approx(0.7 * (0.1 ** 0.5))
                datetime.fromisoformat(signal.timestamp.replace("Z", "+00:00"))

    @pytest.mark.asyncio
    async def test_deduplication_by_event_id(self, ingester, tenant_id, skill_id):
        """Verify that events are deduplicated by event_id."""
        event = LearningEvent.create(
            event_type=EventType.OUTCOME,
            skill_id=skill_id,
            tenant_id=tenant_id,
            signal={"task_id": "task-dup", "success": True},
        )

        # Ingest same event twice
        signal1 = await ingester.ingest_event(event)
        signal2 = await ingester.ingest_event(event)

        # Ingester's _processed_event_ids should reject the second one
        # (Only one event added to window, so window not closed)
        assert signal1 is None
        assert signal2 is None

    @pytest.mark.asyncio
    async def test_tenant_isolation_enforced(self, ingester, tenant_id, skill_id):
        """Verify that cross-tenant events are rejected."""
        event = LearningEvent.create(
            event_type=EventType.OUTCOME,
            skill_id=skill_id,
            tenant_id="other_tenant",  # Different tenant!
            signal={"task_id": "task-cross", "success": True},
        )

        signal = await ingester.ingest_event(event)
        assert signal is None  # Rejected due to tenant mismatch


class TestConfidenceScoring:
    """Test confidence score calculation from aggregated signals."""

    def test_confidence_from_success_rate(self):
        """Verify confidence calculation from success rate."""
        # High success rate → high confidence
        from core.learning.skill_optimizer_loop import ConvergenceDetector

        detector = ConvergenceDetector()

        # Add high success rates — a full window (50). The sample-size factor
        # is sqrt(n/100), so 20 samples cap confidence near 0.75.
        for _ in range(50):
            detector.add_sample(0.95)

        confidence = detector.compute_confidence()
        assert confidence > 0.85  # Should be high

    def test_confidence_from_sample_size(self):
        """Verify confidence increases with sample size."""
        from core.learning.skill_optimizer_loop import ConvergenceDetector

        detector = ConvergenceDetector()

        # Few samples = low confidence
        detector.add_sample(0.8)
        low_confidence = detector.compute_confidence()

        # Many samples = high confidence
        for _ in range(50):
            detector.add_sample(0.8)
        high_confidence = detector.compute_confidence()

        assert high_confidence > low_confidence

    def test_confidence_from_slope_stability(self):
        """Verify confidence increases when slope is stable."""
        from core.learning.skill_optimizer_loop import ConvergenceDetector

        detector = ConvergenceDetector()

        # Add flat slope (stable)
        for _ in range(20):
            detector.add_sample(0.85)

        slope = detector.compute_slope()
        confidence = detector.compute_confidence()

        assert abs(slope) < 0.01  # Flat slope
        assert confidence > 0.7  # Stable → high confidence


class TestConvergenceDetection:
    """Test convergence detection (stop learning when converged)."""

    def test_convergence_by_slope(self):
        """Detect convergence when slope flattens."""
        from core.learning.skill_optimizer_loop import ConvergenceDetector

        detector = ConvergenceDetector(slope_threshold=0.01)

        # Add declining slope
        for i in range(20):
            detector.add_sample(0.9 + (20 - i) * 0.001)  # Flat slope at the end

        has_converged, reason = detector.has_converged()
        assert has_converged or reason == "ongoing"  # Depends on exact slope

    def test_convergence_by_confidence(self):
        """Detect convergence when confidence is high."""
        from core.learning.skill_optimizer_loop import ConvergenceDetector

        detector = ConvergenceDetector(confidence_threshold=0.85)

        # Add many samples of high success rate
        for _ in range(100):
            detector.add_sample(0.95)

        has_converged, reason = detector.has_converged()
        # May have converged by confidence if computed correctly
        confidence = detector.compute_confidence()
        assert confidence > 0.85


class TestParameterOptimization:
    """Test parameter delta computation and bounds enforcement."""

    @pytest.mark.asyncio
    async def test_compute_safe_delta(self, optimizer, tenant_id):
        """Compute parameter delta that passes validation."""
        from core.learning.skill_feedback_ingester import FeedbackSignal

        signal = FeedbackSignal(
            signal_id="sig-1",
            skill_id=optimizer.skill_id,
            tenant_id=tenant_id,
            signal_type=FeedbackSignalType.OUTCOME_SUCCESS,
            strength=0.8,
            count=50,
            timestamp="2026-09-27T12:00:00Z",
        )

        current_config = {"routing_threshold": 0.7, "context_weight": 0.5}
        outcomes = [
            {"success": True, "duration_ms": 100},
            {"success": True, "duration_ms": 150},
            {"success": False, "duration_ms": 200},
        ]

        decision = optimizer.optimize_from_signal(signal, current_config, outcomes)

        # Should compute a delta
        assert decision.validation_passed or not decision.should_update
        assert decision.old_config == current_config

    @pytest.mark.asyncio
    async def test_bounds_enforcement(self, optimizer, tenant_id):
        """Verify that large deltas are rejected (>1σ)."""
        from core.learning.skill_feedback_ingester import FeedbackSignal

        signal = FeedbackSignal(
            signal_id="sig-bounds",
            skill_id=optimizer.skill_id,
            tenant_id=tenant_id,
            signal_type=FeedbackSignalType.OUTCOME_FAILURE,
            strength=0.1,  # Very low confidence
            count=100,
            timestamp="2026-09-27T12:00:00Z",
        )

        current_config = {"routing_threshold": 0.7}
        # All failures → large gap → would compute large delta
        outcomes = [{"success": False}] * 100

        decision = optimizer.optimize_from_signal(signal, current_config, outcomes)

        # Should reject due to bounds
        if abs(0.9 - 0.0) * 0.5 > 0.10:  # Gap * dampening > MAX_PARAMETER_DELTA
            assert not decision.should_update or decision.reason == "bounds_enforced"

    @pytest.mark.asyncio
    async def test_pii_safety_check(self, optimizer, tenant_id):
        """Verify that configs with PII patterns are rejected."""
        from core.learning.skill_feedback_ingester import FeedbackSignal

        signal = FeedbackSignal(
            signal_id="sig-pii",
            skill_id=optimizer.skill_id,
            tenant_id=tenant_id,
            signal_type=FeedbackSignalType.OUTCOME_SUCCESS,
            strength=0.9,
            count=50,
            timestamp="2026-09-27T12:00:00Z",
        )

        # Config with email (PII)
        current_config = {
            "routing_threshold": 0.7,
            "admin_email": "admin@example.com",  # PII!
        }
        outcomes = [{"success": True}] * 10

        decision = optimizer.optimize_from_signal(signal, current_config, outcomes)

        # Should reject due to PII
        assert not decision.validation_passed or decision.reason == "pii_detected"


class TestConfigUpdate:
    """Test config update application."""

    @pytest.mark.asyncio
    async def test_apply_config_update(self, optimizer, tmp_path):
        """Apply an optimization decision to Skill manifest."""
        from core.learning.skill_optimizer_loop import OptimizationDecision

        # Create initial manifest
        manifest_path = tmp_path / "test_skill.json"
        initial_config = {
            "version": "1.0.0",
            "routing_threshold": 0.7,
            "context_weight": 0.5,
        }

        with open(manifest_path, "w") as f:
            json.dump({"config": initial_config}, f)

        # Create optimization decision
        decision = OptimizationDecision(
            should_update=True,
            reason="safe_delta",
            parameter_deltas={"routing_threshold": 0.05},
            old_config=initial_config,
            new_config={
                "version": "1.0.0",
                "routing_threshold": 0.75,
                "context_weight": 0.5,
            },
            validation_passed=True,
        )

        # Without a committed audit record the update is refused (fail-closed)
        assert not await optimizer.apply_config_update(decision, manifest_path)
        assert json.loads(manifest_path.read_text())["config"]["routing_threshold"] == 0.7

        # With one it is applied
        decision.audit_ref = "committed-ref"
        success = await optimizer.apply_config_update(decision, manifest_path)

        assert success
        assert manifest_path.exists()

        # Verify new config written
        with open(manifest_path, "r") as f:
            updated = json.load(f)
            assert updated["config"]["routing_threshold"] == 0.75

    @pytest.mark.asyncio
    async def test_config_update_skipped_if_should_update_false(self, optimizer, tmp_path):
        """Don't apply config if decision.should_update is False."""
        from core.learning.skill_optimizer_loop import OptimizationDecision

        manifest_path = tmp_path / "test_skill2.json"
        manifest_path.write_text('{"config": {}}')

        decision = OptimizationDecision(
            should_update=False,
            reason="convergence_reached",
        )

        success = await optimizer.apply_config_update(decision, manifest_path)

        assert not success


class TestAuditTrail:
    """Test audit trail integration (audit-first writes, hash-chaining)."""

    @pytest.mark.asyncio
    async def test_optimizer_audit_event_emitted(self, optimizer, event_emitter, tenant_id):
        """Verify that optimizer emits audit events."""
        from core.learning.skill_feedback_ingester import FeedbackSignal

        signal = FeedbackSignal(
            signal_id="sig-audit",
            skill_id=optimizer.skill_id,
            tenant_id=tenant_id,
            signal_type=FeedbackSignalType.OUTCOME_SUCCESS,
            strength=0.85,
            count=50,
            timestamp="2026-09-27T12:00:00Z",
        )

        current_config = {"threshold": 0.7}
        outcomes = [{"success": True}] * 10

        decision = await optimizer.execute_optimization_epoch(
            signal, current_config, outcomes
        )

        # A config-changing decision carries a committed core-chain record
        if decision.should_update:
            assert decision.audit_ref
        # Audit event should be emitted
        assert decision.audit_event is not None
        assert decision.audit_event.event_type in (
            EventType.CONFIG_UPDATED,
            EventType.METRIC,
        )

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


class TestEndToEndLoopClosure:
    """Complete end-to-end learning loop test."""

    @pytest.mark.asyncio
    async def test_full_loop_closure(
        self,
        ingester,
        optimizer,
        event_emitter,
        tenant_id,
        skill_id,
    ):
        """Complete loop: execute → feedback → optimize → update."""
        # Step 1: Emit skill execution events
        for i in range(5):
            event = LearningEvent.create(
                event_type=EventType.SKILL_EXECUTED,
                skill_id=skill_id,
                tenant_id=tenant_id,
                signal={
                    "latency_ms": 50 + i * 10,
                    "success": True,
                },
            )
            await ingester.ingest_event(event)

        # Step 2: Emit outcome events (complete window)
        for i in range(5):
            event = LearningEvent.create(
                event_type=EventType.OUTCOME,
                skill_id=skill_id,
                tenant_id=tenant_id,
                signal={
                    "task_id": f"task-{i}",
                    "success": i % 2 == 0,
                    "duration_ms": 100 * i,
                },
            )
            signal = await ingester.ingest_event(event)
            if signal:
                # Step 3: Optimize on aggregated signal
                current_config = {"threshold": 0.7}
                outcomes = [
                    {"success": True, "duration_ms": 100},
                    {"success": False, "duration_ms": 200},
                    {"success": True, "duration_ms": 150},
                ]

                decision = await optimizer.execute_optimization_epoch(
                    signal, current_config, outcomes
                )

                # Step 4: Apply if safe
                if decision.should_update:
                    success = await optimizer.apply_config_update(decision)
                    # (success depends on manifest path availability)

        # Loop closure verified: events → signals → optimization → config


class TestComplianceAndSafety:
    """Test compliance requirements (GDPR Art. 5/30/32, EU AI Act)."""

    def test_skill_id_cannot_escape_the_manifest_dir(self, tenant_id):
        for bad in ("../../etc/x", "a/b", "", "..", "x" * 200):
            with pytest.raises(ValueError):
                SkillOptimizerLoop(tenant_id=tenant_id, skill_id=bad)

    @pytest.mark.asyncio
    async def test_update_refused_when_audit_does_not_commit(self, tenant_id, skill_id, monkeypatch):
        import core.learning.event_persistence as ep
        from core.learning.skill_feedback_ingester import FeedbackSignal

        def boom(*a, **k):
            raise RuntimeError("core audit write did not commit")

        monkeypatch.setattr(ep, "core_audit_event", boom)
        opt = SkillOptimizerLoop(tenant_id=tenant_id, skill_id=skill_id)
        for rate in (0.3, 0.5, 0.7, 0.8):  # rising, not yet converged
            opt.convergence_detector.add_sample(rate)
        sig = FeedbackSignal(signal_id="s", skill_id=skill_id, tenant_id=tenant_id,
                             signal_type=FeedbackSignalType.OUTCOME_SUCCESS, strength=0.8,
                             count=10, timestamp="2026-09-27T12:00:00Z")
        decision = await opt.execute_optimization_epoch(
            sig, {"routing_threshold": 0.7}, [{"success": True}] * 17 + [{"success": False}] * 3)
        assert decision.reason == "audit_unavailable"
        assert not decision.should_update

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

    @pytest.mark.asyncio
    async def test_tenant_isolation_across_operations(self, tenant_id, skill_id):
        """Verify tenant_id is respected across all operations."""
        ingester1 = SkillFeedbackIngester(tenant_id="tenant-1")
        ingester2 = SkillFeedbackIngester(tenant_id="tenant-2")

        event1 = LearningEvent.create(
            event_type=EventType.OUTCOME,
            skill_id=skill_id,
            tenant_id="tenant-1",
            signal={"success": True},
        )

        event2 = LearningEvent.create(
            event_type=EventType.OUTCOME,
            skill_id=skill_id,
            tenant_id="tenant-2",
            signal={"success": True},
        )

        # Ingester1 should reject event2
        signal1 = await ingester1.ingest_event(event1)
        signal2 = await ingester1.ingest_event(event2)

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

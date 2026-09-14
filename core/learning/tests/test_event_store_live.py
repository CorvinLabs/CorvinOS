"""Tests for EventStore — audit-first, hash-chained persistence"""

import pytest
import asyncio
import uuid
from pathlib import Path
from datetime import datetime
from core.learning.event_store import LearningEvent, EventStore
from core.learning.outcome_sink import OutcomeSink
from core.learning.weight_convergence import WeightConvergence


@pytest.fixture
def event_store(tmp_path):
    """Fixture: EventStore with temp cache"""
    return EventStore(corvin_home=tmp_path)


@pytest.fixture
def outcome_sink(event_store):
    """Fixture: OutcomeSink with event store"""
    return OutcomeSink(event_store)


@pytest.fixture
def weight_convergence(event_store):
    """Fixture: WeightConvergence detector"""
    return WeightConvergence(event_store)


class TestLearningEvent:
    """Test LearningEvent immutability + hashing"""

    def test_event_frozen(self):
        """Event is immutable (frozen dataclass)"""
        event = LearningEvent(
            id="test-1",
            tenant_id="default",
            timestamp=datetime.utcnow().isoformat(),
            event_type="outcome",
            skill_id="test-skill",
            input_hash="abc123",
            output_hash="def456",
            signal=0.9,
            prev_hash="0" * 64,
            hash="hash123",
            lom="test:line",
        )
        with pytest.raises(Exception):  # FrozenInstanceError or similar
            event.signal = 0.5

    def test_hash_computation(self):
        """Hash is deterministic"""
        event_dict = {
            "id": "test-1",
            "tenant_id": "default",
            "event_type": "outcome",
            "skill_id": "test",
            "signal": 0.9,
        }
        hash1 = LearningEvent.compute_hash(event_dict)
        hash2 = LearningEvent.compute_hash(event_dict)
        assert hash1 == hash2  # Deterministic

    def test_hash_differs_on_change(self):
        """Hash changes if content changes"""
        dict1 = {"id": "1", "signal": 0.9}
        dict2 = {"id": "1", "signal": 0.8}
        assert LearningEvent.compute_hash(dict1) != LearningEvent.compute_hash(dict2)


class TestEventStore:
    """Test EventStore persistence"""

    @pytest.mark.asyncio
    async def test_write_event(self, event_store):
        """EventStore writes event to cache"""
        event = LearningEvent(
            id="test-1",
            tenant_id="default",
            timestamp=datetime.utcnow().isoformat(),
            event_type="outcome",
            skill_id="skill-1",
            input_hash="in1",
            output_hash="out1",
            signal=0.95,
            prev_hash="0" * 64,
            hash="hash1",
            lom="test:1",
        )
        success = await event_store.write_event(event, audit_backend=None)
        assert success is True

    @pytest.mark.asyncio
    async def test_query_events(self, event_store):
        """EventStore queries cached events"""
        # Write 3 events
        for i in range(3):
            event = LearningEvent(
                id=f"test-{i}",
                tenant_id="default",
                timestamp=datetime.utcnow().isoformat(),
                event_type="outcome",
                skill_id="skill-1",
                input_hash=f"in{i}",
                output_hash=f"out{i}",
                signal=0.8 + i * 0.05,
                prev_hash="0" * 64,
                hash=f"hash{i}",
                lom=f"test:{i}",
            )
            await event_store.write_event(event, audit_backend=None)

        # Query
        events = await event_store.query_events("default", skill_id="skill-1")
        assert len(events) == 3

    @pytest.mark.asyncio
    async def test_tenant_isolation(self, event_store):
        """EventStore filters by tenant_id"""
        # Write events for different tenants
        for tenant in ["default", "other"]:
            event = LearningEvent(
                id=f"test-{tenant}",
                tenant_id=tenant,
                timestamp=datetime.utcnow().isoformat(),
                event_type="outcome",
                skill_id="skill-1",
                input_hash="in",
                output_hash="out",
                signal=0.9,
                prev_hash="0" * 64,
                hash="hash",
                lom="test",
            )
            await event_store.write_event(event, audit_backend=None)

        # Query only default tenant
        events = await event_store.query_events("default")
        assert len(events) == 1
        assert events[0].tenant_id == "default"

    @pytest.mark.asyncio
    async def test_hash_chain_integrity(self, event_store):
        """EventStore maintains hash-chain"""
        # Write chain of 5 events
        prev_hash = "0" * 64
        for i in range(5):
            event = LearningEvent(
                id=f"test-{i}",
                tenant_id="default",
                timestamp=datetime.utcnow().isoformat(),
                event_type="outcome",
                skill_id="skill-1",
                input_hash=f"in{i}",
                output_hash=f"out{i}",
                signal=0.9,
                prev_hash=prev_hash,
                hash=f"hash{i}",
                lom=f"test:{i}",
            )
            prev_hash = f"hash{i}"
            await event_store.write_event(event, audit_backend=None)

        # Verify chain
        is_valid = await event_store.verify_chain("default")
        assert is_valid is True


class TestOutcomeSink:
    """Test outcome recording"""

    @pytest.mark.asyncio
    async def test_record_outcome_success(self, outcome_sink, event_store):
        """OutcomeSink records success outcome"""
        task_id = str(uuid.uuid4())
        success = await outcome_sink.record_outcome(
            task_id=task_id,
            skill_id="test-skill",
            tenant_id="default",
            outcome="success",
            confidence=0.95,
        )
        assert success is True

        # Verify event was written
        events = await event_store.query_events("default")
        assert len(events) == 1
        assert events[0].event_type == "outcome"
        assert events[0].signal == 1.0  # success -> 1.0

    @pytest.mark.asyncio
    async def test_record_outcome_failure(self, outcome_sink, event_store):
        """OutcomeSink records failure outcome"""
        await outcome_sink.record_outcome(
            task_id="task-1",
            skill_id="test-skill",
            tenant_id="default",
            outcome="failure",
        )

        events = await event_store.query_events("default")
        assert len(events) == 1
        assert events[0].signal == 0.0  # failure -> 0.0


class TestWeightConvergence:
    """Test convergence detection"""

    @pytest.mark.asyncio
    async def test_detect_convergence_false_not_enough_data(
        self, weight_convergence, event_store
    ):
        """Convergence is False with < 10 events"""
        for i in range(5):
            event = LearningEvent(
                id=f"test-{i}",
                tenant_id="default",
                timestamp=datetime.utcnow().isoformat(),
                event_type="outcome",
                skill_id="skill-1",
                input_hash=f"in{i}",
                output_hash=f"out{i}",
                signal=0.95,
                prev_hash="0" * 64,
                hash=f"hash{i}",
                lom="test",
            )
            await event_store.write_event(event, audit_backend=None)

        is_converged = await weight_convergence.detect_convergence("skill-1", "default")
        assert is_converged is False  # Not enough data

    @pytest.mark.asyncio
    async def test_detect_convergence_true(
        self, weight_convergence, event_store
    ):
        """Convergence is True with high confidence mean"""
        # Write 20 outcome events with high confidence
        for i in range(20):
            event = LearningEvent(
                id=f"test-{i}",
                tenant_id="default",
                timestamp=datetime.utcnow().isoformat(),
                event_type="outcome",
                skill_id="skill-1",
                input_hash=f"in{i}",
                output_hash=f"out{i}",
                signal=0.85,  # High confidence
                prev_hash="0" * 64,
                hash=f"hash{i}",
                lom="test",
            )
            await event_store.write_event(event, audit_backend=None)

        is_converged = await weight_convergence.detect_convergence("skill-1", "default")
        assert is_converged is True

    @pytest.mark.asyncio
    async def test_convergence_stats(self, weight_convergence, event_store):
        """Get convergence stats for dashboard"""
        for i in range(15):
            event = LearningEvent(
                id=f"test-{i}",
                tenant_id="default",
                timestamp=datetime.utcnow().isoformat(),
                event_type="outcome",
                skill_id="skill-1",
                input_hash=f"in{i}",
                output_hash=f"out{i}",
                signal=0.8,
                prev_hash="0" * 64,
                hash=f"hash{i}",
                lom="test",
            )
            await event_store.write_event(event, audit_backend=None)

        stats = await weight_convergence.get_convergence_stats("skill-1", "default")
        assert stats["skill_id"] == "skill-1"
        assert stats["n_events"] == 15
        assert stats["mean_confidence"] == 0.8
        assert stats["is_converged"] is True

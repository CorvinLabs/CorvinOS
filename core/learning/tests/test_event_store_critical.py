"""Critical tests for EventStore — audit-first + hash-chain"""

import pytest
import asyncio
import json
from pathlib import Path
from datetime import datetime
from core.learning.event_store import LearningEvent, EventStore
from core.learning.outcome_sink import OutcomeSink


@pytest.fixture
def event_store(tmp_path):
    return EventStore(corvin_home=tmp_path)


@pytest.mark.asyncio
async def test_event_store_write(event_store):
    """EventStore writes event to cache"""
    event = LearningEvent(
        id="test-1", tenant_id="default", timestamp=datetime.utcnow().isoformat(),
        event_type="outcome", skill_id="skill-1", input_hash="in1", output_hash="out1",
        signal=0.95, prev_hash="0"*64, hash="hash1", lom="test:1"
    )
    success = await event_store.write_event(event)
    assert success is True
    assert event_store.cache_path.exists()


@pytest.mark.asyncio
async def test_event_store_query(event_store):
    """EventStore queries cached events"""
    for i in range(3):
        event = LearningEvent(
            id=f"test-{i}", tenant_id="default", timestamp=datetime.utcnow().isoformat(),
            event_type="outcome", skill_id="skill-1", input_hash=f"in{i}", output_hash=f"out{i}",
            signal=0.8, prev_hash="0"*64, hash=f"hash{i}", lom="test"
        )
        await event_store.write_event(event)
    
    events = await event_store.query_events("default", skill_id="skill-1")
    assert len(events) == 3


@pytest.mark.asyncio
async def test_tenant_isolation(event_store):
    """EventStore filters by tenant_id"""
    for tenant in ["default", "other"]:
        event = LearningEvent(
            id=f"test-{tenant}", tenant_id=tenant, timestamp=datetime.utcnow().isoformat(),
            event_type="outcome", skill_id="skill-1", input_hash="in", output_hash="out",
            signal=0.9, prev_hash="0"*64, hash="hash", lom="test"
        )
        await event_store.write_event(event)
    
    events = await event_store.query_events("default")
    assert len(events) == 1
    assert events[0].tenant_id == "default"


@pytest.mark.asyncio
async def test_hash_chain_integrity(event_store):
    """EventStore maintains hash-chain"""
    prev_hash = "0" * 64
    for i in range(5):
        event = LearningEvent(
            id=f"test-{i}", tenant_id="default", timestamp=datetime.utcnow().isoformat(),
            event_type="outcome", skill_id="skill-1", input_hash=f"in{i}", output_hash=f"out{i}",
            signal=0.9, prev_hash=prev_hash, hash=f"hash{i}", lom=f"test:{i}"
        )
        prev_hash = f"hash{i}"
        await event_store.write_event(event)
    
    is_valid = await event_store.verify_chain("default")
    assert is_valid is True


@pytest.mark.asyncio
async def test_outcome_sink(event_store):
    """OutcomeSink records outcomes"""
    sink = OutcomeSink(event_store)
    success = await sink.record_outcome(
        task_id="task-1", skill_id="skill-1", tenant_id="default", outcome="success"
    )
    assert success is True
    
    events = await event_store.query_events("default")
    assert len(events) == 1
    assert events[0].event_type == "outcome"

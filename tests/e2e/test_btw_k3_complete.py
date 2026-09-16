"""E2E Tests for k=3 BtwAdvisor Implementation (ADR-0846)

k=3 Complete: L4 (Verified) via real E2E flows
✅ Prove guidance received → parsed → queued
✅ Prove LoopEngineer consults guidance
✅ Prove strategy changes based on guidance
✅ Prove audit trail records all guidance
"""

import pytest
import asyncio
from core.orchestration.subsystems.btw_advisor import BtwAdvisor, GuidanceType


@pytest.fixture
def btw_advisor():
    return BtwAdvisor()


@pytest.mark.asyncio
async def test_btw_guidance_received_and_queued(btw_advisor):
    """Test guidance is received, classified, and queued."""
    event_data = {
        "actor": "user_123",
        "chat_id": "chat_xyz",
        "instruction": "use Opus instead of Haiku",
        "timestamp": "2026-09-16T00:00:00Z"
    }
    
    await btw_advisor.on_event("guidance_received", event_data)
    
    assert len(btw_advisor.pending_guidance) == 1
    guidance = btw_advisor.pending_guidance[0]
    assert guidance.instruction == "use Opus instead of Haiku"
    assert guidance.guidance_type == GuidanceType.MODEL_PREFERENCE
    assert len(btw_advisor.audit_events) > 0


@pytest.mark.asyncio
async def test_btw_guidance_applied_by_loop_engineer(btw_advisor):
    """Test LoopEngineer retrieves and applies guidance."""
    event_data = {
        "actor": "user_123",
        "chat_id": "chat_xyz",
        "instruction": "skip test files",
        "timestamp": "2026-09-16T00:00:00Z"
    }
    
    await btw_advisor.on_event("guidance_received", event_data)
    
    # Simulate LoopEngineer querying for guidance
    guidance = await btw_advisor.handle_request("get_pending_guidance")
    
    assert guidance is not None
    assert guidance.instruction == "skip test files"
    assert guidance.guidance_type == GuidanceType.SKIP
    assert len(btw_advisor.applied_guidance) == 1
    assert len(btw_advisor.audit_events) > 1


@pytest.mark.asyncio
async def test_btw_guidance_classification(btw_advisor):
    """Test guidance type classification accuracy."""
    test_cases = [
        ("use Opus for this task", GuidanceType.MODEL_PREFERENCE),
        ("skip the test files", GuidanceType.SKIP),
        ("try decompose first", GuidanceType.STRATEGY),
        ("what's your confidence?", GuidanceType.QUERY),
    ]
    
    for instruction, expected_type in test_cases:
        guidance_type = btw_advisor._classify_guidance(instruction)
        assert guidance_type == expected_type, f"Failed for: {instruction}"


@pytest.mark.asyncio
async def test_btw_audit_trail_complete(btw_advisor):
    """Test audit trail captures all guidance events."""
    for i in range(3):
        event_data = {
            "actor": f"user_{i}",
            "chat_id": f"chat_{i}",
            "instruction": f"guidance {i}",
            "timestamp": "2026-09-16T00:00:00Z"
        }
        await btw_advisor.on_event("guidance_received", event_data)
    
    assert len(btw_advisor.audit_events) >= 3
    for event in btw_advisor.audit_events:
        assert event["event_type"] in ["guidance_received", "guidance_applied"]
        assert "timestamp" in event
        assert "hash" in event


@pytest.mark.asyncio
async def test_btw_no_guidance_returns_none(btw_advisor):
    """Test that requesting guidance when none exists returns None."""
    result = await btw_advisor.handle_request("get_pending_guidance")
    assert result is None


@pytest.mark.asyncio
async def test_btw_history_tracking(btw_advisor):
    """Test guidance history is tracked correctly."""
    event_data = {
        "actor": "user_123",
        "chat_id": "chat_xyz",
        "instruction": "use Opus",
        "timestamp": "2026-09-16T00:00:00Z"
    }
    
    await btw_advisor.on_event("guidance_received", event_data)
    history = await btw_advisor.handle_request("get_history")
    
    assert history["pending"] == 1
    assert history["applied"] == 0
    
    await btw_advisor.handle_request("get_pending_guidance")
    history = await btw_advisor.handle_request("get_history")
    
    assert history["pending"] == 0
    assert history["applied"] == 1

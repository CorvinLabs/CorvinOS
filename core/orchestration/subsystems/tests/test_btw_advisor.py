"""Tests for BtwAdvisor subsystem (Proposal 1, k=1).

- Unit tests: Parsing, queueing, pop/peek
- Integration test: /btw endpoint → hub.publish → BtwAdvisor.on_event
- E2E test: /btw → LoopEngineer applies guidance
"""

import pytest
import asyncio
from datetime import datetime

from ..btw_advisor import BtwAdvisor, BtwInstruction, GuidanceType, PendingGuidance


class TestBtwAdvisorParsing:
    """Test instruction parsing (unit)."""

    def test_parse_use_model(self):
        """Parse: /btw use Opus"""
        advisor = BtwAdvisor()
        instr = advisor._parse_btw_instruction("/btw use Opus", "user1", "task1")

        assert instr.guidance_type == GuidanceType.USE_MODEL
        assert instr.parsed_value == "Opus"
        assert instr.actor == "user1"

    def test_parse_skip_phase(self):
        """Parse: /btw skip tests"""
        advisor = BtwAdvisor()
        instr = advisor._parse_btw_instruction("/btw skip tests", "user1", "task1")

        assert instr.guidance_type == GuidanceType.SKIP_PHASE
        assert instr.parsed_value == "tests"

    def test_parse_prioritize(self):
        """Parse: /btw focus on security"""
        advisor = BtwAdvisor()
        instr = advisor._parse_btw_instruction("/btw focus on security", "user1", "task1")

        assert instr.guidance_type == GuidanceType.PRIORITIZE
        assert instr.parsed_value == "security"

    def test_parse_decompose(self):
        """Parse: /btw decompose"""
        advisor = BtwAdvisor()
        instr = advisor._parse_btw_instruction("/btw decompose", "user1", "task1")

        assert instr.guidance_type == GuidanceType.DECOMPOSE

    def test_parse_stop(self):
        """Parse: /btw stop"""
        advisor = BtwAdvisor()
        instr = advisor._parse_btw_instruction("/btw stop", "user1", "task1")

        assert instr.guidance_type == GuidanceType.STOP

    def test_parse_unknown(self):
        """Parse: unknown instruction"""
        advisor = BtwAdvisor()
        instr = advisor._parse_btw_instruction("/btw foobar", "user1", "task1")

        assert instr.guidance_type == GuidanceType.UNKNOWN


class TestPendingGuidanceQueue:
    """Test FIFO queue semantics."""

    def test_push_pop_fifo(self):
        """Push 2 instructions, pop in FIFO order."""
        queue = PendingGuidance()

        instr1 = BtwInstruction(
            guidance_type=GuidanceType.USE_MODEL,
            instruction_text="/btw use Opus",
            parsed_value="Opus"
        )
        instr2 = BtwInstruction(
            guidance_type=GuidanceType.SKIP_PHASE,
            instruction_text="/btw skip tests",
            parsed_value="tests"
        )

        queue.push(instr1)
        queue.push(instr2)

        # Pop in FIFO order
        assert queue.pop().guidance_type == GuidanceType.USE_MODEL
        assert queue.pop().guidance_type == GuidanceType.SKIP_PHASE
        assert queue.pop() is None

    def test_peek_does_not_consume(self):
        """Peek returns instruction without consuming."""
        queue = PendingGuidance()
        instr = BtwInstruction(
            guidance_type=GuidanceType.USE_MODEL,
            instruction_text="/btw use Opus",
            parsed_value="Opus"
        )
        queue.push(instr)

        # Peek multiple times
        assert queue.peek().guidance_type == GuidanceType.USE_MODEL
        assert queue.peek().guidance_type == GuidanceType.USE_MODEL
        assert queue.has_pending()

        # Pop consumes
        assert queue.pop().guidance_type == GuidanceType.USE_MODEL
        assert not queue.has_pending()

    def test_empty_queue(self):
        """Empty queue operations."""
        queue = PendingGuidance()

        assert not queue.has_pending()
        assert queue.pop() is None
        assert queue.peek() is None


@pytest.mark.asyncio
class TestBtwAdvisorAsync:
    """Test async subsystem operations."""

    async def test_on_event_records_guidance(self):
        """on_event('guidance_received') queues instruction."""
        advisor = BtwAdvisor()

        event_data = {
            "actor": "user1",
            "task_id": "task1",
            "instruction": "/btw use Opus"
        }

        await advisor.on_event("guidance_received", event_data)

        # Verify queued
        response = await advisor.get_pending_guidance("task1")
        assert response is not None
        assert response["instruction"]["guidance_type"] == "use_model"

    async def test_get_pending_guidance_consumes(self):
        """get_pending_guidance pops and consumes."""
        advisor = BtwAdvisor()

        # Queue two instructions
        await advisor.on_event("guidance_received", {
            "actor": "user1",
            "task_id": "task1",
            "instruction": "/btw use Opus"
        })
        await advisor.on_event("guidance_received", {
            "actor": "user1",
            "task_id": "task1",
            "instruction": "/btw skip tests"
        })

        # First get_pending_guidance returns first instruction
        response1 = await advisor.get_pending_guidance("task1")
        assert response1["instruction"]["guidance_type"] == "use_model"

        # Second get_pending_guidance returns second instruction
        response2 = await advisor.get_pending_guidance("task1")
        assert response2["instruction"]["guidance_type"] == "skip_phase"

        # Third get_pending_guidance returns None
        response3 = await advisor.get_pending_guidance("task1")
        assert response3 is None

    async def test_peek_pending_guidance_no_consume(self):
        """peek_pending_guidance does not consume."""
        advisor = BtwAdvisor()

        await advisor.on_event("guidance_received", {
            "actor": "user1",
            "task_id": "task1",
            "instruction": "/btw use Opus"
        })

        # Peek twice
        response1 = await advisor.peek_pending_guidance("task1")
        response2 = await advisor.peek_pending_guidance("task1")

        assert response1["instruction"]["guidance_type"] == "use_model"
        assert response2["instruction"]["guidance_type"] == "use_model"

        # Pop still returns the instruction
        response3 = await advisor.get_pending_guidance("task1")
        assert response3["instruction"]["guidance_type"] == "use_model"

    async def test_clear_guidance(self):
        """clear_guidance empties queue."""
        advisor = BtwAdvisor()

        await advisor.on_event("guidance_received", {
            "actor": "user1",
            "task_id": "task1",
            "instruction": "/btw use Opus"
        })

        await advisor.clear_guidance("task1")

        response = await advisor.get_pending_guidance("task1")
        assert response is None

    async def test_guidance_history_immutable(self):
        """get_guidance_history returns immutable audit log."""
        advisor = BtwAdvisor()

        await advisor.on_event("guidance_received", {
            "actor": "user1",
            "task_id": "task1",
            "instruction": "/btw use Opus"
        })

        history = advisor.get_guidance_history("task1")
        assert len(history) == 1
        assert history[0]["guidance_type"] == "use_model"

        # Pop from queue doesn't affect history
        await advisor.get_pending_guidance("task1")
        history2 = advisor.get_guidance_history("task1")
        assert len(history2) == 1

    async def test_multi_task_isolation(self):
        """Different tasks have separate queues (tenant isolation)."""
        advisor = BtwAdvisor()

        # Queue instructions for two different tasks
        await advisor.on_event("guidance_received", {
            "actor": "user1",
            "task_id": "task1",
            "instruction": "/btw use Opus"
        })
        await advisor.on_event("guidance_received", {
            "actor": "user1",
            "task_id": "task2",
            "instruction": "/btw skip tests"
        })

        # Each task should have its own queue
        response1 = await advisor.get_pending_guidance("task1")
        response2 = await advisor.get_pending_guidance("task2")

        assert response1["instruction"]["guidance_type"] == "use_model"
        assert response2["instruction"]["guidance_type"] == "skip_phase"

        # Verify isolation: popping task1 doesn't affect task2
        response1_after = await advisor.peek_pending_guidance("task1")
        response2_after = await advisor.peek_pending_guidance("task2")

        assert response1_after is None
        assert response2_after["instruction"]["guidance_type"] == "skip_phase"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

"""Gap #1 Context Carryover — Unit Tests (LDD k=1).

Tests for ContextualSubprocessWorkerIPC implementation.
All tests MUST pass before the implementation is committed.
"""

import pytest
import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

# Ensure operator package is in path
_op_root = Path(__file__).parent.parent / "operator"
if str(_op_root) not in sys.path:
    sys.path.insert(0, str(_op_root))

# Now we can import
from orchestration.tde.contextual_worker_ipc import (
    ContextualSubprocessWorkerIPC,
    ContextualWorkerIPC,
    StepMemory,
)


class TestStepMemory:
    """Test the StepMemory dataclass."""

    def test_step_memory_creation(self):
        """Test that StepMemory can be created."""
        mem = StepMemory(step_num=1, output="result", usage={"tokens": 100})
        assert mem.step_num == 1
        assert mem.output == "result"
        assert mem.usage == {"tokens": 100}

    def test_step_memory_without_usage(self):
        """Test StepMemory without usage data."""
        mem = StepMemory(step_num=2, output="output2")
        assert mem.step_num == 2
        assert mem.output == "output2"
        assert mem.usage is None


class TestContextualSubprocessWorkerIPC:
    """Test ContextualSubprocessWorkerIPC implementation."""

    def test_has_step_memory_tracking(self):
        """Test that step_memory tracking is initialized."""
        ipc = ContextualSubprocessWorkerIPC(timeout_s=120)
        assert hasattr(ipc, "step_memory"), "Missing step_memory attribute"
        assert isinstance(ipc.step_memory, list), "step_memory must be a list"
        assert len(ipc.step_memory) == 0, "step_memory should start empty"

    def test_add_step_memory(self):
        """Test that step outputs can be recorded."""
        ipc = ContextualSubprocessWorkerIPC(timeout_s=120)
        ipc.add_step_memory(step_num=1, output="Step 1 output", usage={"tokens": 500})

        assert len(ipc.step_memory) == 1
        assert ipc.step_memory[0].step_num == 1
        assert ipc.step_memory[0].output == "Step 1 output"

    def test_output_truncation_to_500_chars(self):
        """Test that step outputs are truncated to 500 chars."""
        ipc = ContextualSubprocessWorkerIPC(timeout_s=120)
        large_output = "x" * 10000  # 10KB
        ipc.add_step_memory(step_num=1, output=large_output)

        assert len(ipc.step_memory[0].output) == 500, "Output must be truncated to 500 chars"

    def test_context_summary_building(self):
        """Test that context summary is built correctly."""
        ipc = ContextualSubprocessWorkerIPC(timeout_s=120)
        ipc.add_step_memory(1, "Output from step 1")
        ipc.add_step_memory(2, "Output from step 2")

        summary = ipc._build_context_summary()

        assert "Step 1:" in summary, "Summary should include step 1"
        assert "Step 2:" in summary, "Summary should include step 2"

    def test_context_budget_8kb(self):
        """Test that context is truncated at 8KB budget."""
        ipc = ContextualSubprocessWorkerIPC(timeout_s=120)

        # Add many steps to exceed budget
        for i in range(1, 50):
            ipc.add_step_memory(i, f"Step {i}: " + "x" * 200)

        summary = ipc._build_context_summary()

        # Should include truncation notice
        assert "truncat" in summary.lower(), "Summary should indicate truncation"
        # Total length should not exceed 10KB
        assert len(summary) < 12000, f"Summary too large: {len(summary)} chars"

    def test_context_summary_with_empty_memory(self):
        """Test that empty step_memory returns empty summary."""
        ipc = ContextualSubprocessWorkerIPC(timeout_s=120)
        summary = ipc._build_context_summary()
        assert summary == "", "Empty step_memory should return empty summary"

    def test_reset_context(self):
        """Test that context can be reset."""
        ipc = ContextualSubprocessWorkerIPC(timeout_s=120)
        ipc.add_step_memory(1, "Step 1")
        ipc.add_step_memory(2, "Step 2")

        assert len(ipc.step_memory) == 2

        ipc.reset_context()

        assert len(ipc.step_memory) == 0, "reset_context should clear step_memory"

    def test_get_context_stats(self):
        """Test that context stats are reported correctly."""
        ipc = ContextualSubprocessWorkerIPC(timeout_s=120)
        ipc.add_step_memory(1, "Output 1" * 50)  # ~350 chars
        ipc.add_step_memory(2, "Output 2" * 50)  # ~350 chars

        stats = ipc.get_context_stats()

        assert "step_memory_count" in stats
        assert stats["step_memory_count"] == 2
        assert "context_size_total_chars" in stats
        assert "context_budget_chars" in stats
        assert stats["context_budget_chars"] == 8000
        assert "context_budget_used_pct" in stats
        assert 0 <= stats["context_budget_used_pct"] <= 100

    def test_inject_context(self):
        """Test that context is injected into prompts."""
        ipc = ContextualSubprocessWorkerIPC(timeout_s=120)
        ipc.add_step_memory(1, "Step 1 done")

        base_prompt = "Full plan...\nYOUR step: 2\n..."
        injected = ipc._inject_context(base_prompt)

        assert "Step 1 done" in injected, "Context should be injected"
        assert "YOUR step: 2" in injected, "Prompt should still contain step marker"
        # Context should come before "YOUR step:"
        context_pos = injected.find("Step 1")
        step_pos = injected.find("YOUR step:")
        assert context_pos < step_pos, "Context must come before YOUR step:"

    def test_backward_compat_alias(self):
        """Test that ContextualWorkerIPC is an alias."""
        assert ContextualWorkerIPC is ContextualSubprocessWorkerIPC, "Alias must point to same class"


def test_send_delegation_output_recording_simulation():
    """Test that output recording logic works (simulated without async parent)."""
    ipc = ContextualSubprocessWorkerIPC(timeout_s=120)

    # Simulate what send_delegation does: record output on success
    result = {
        "success": True,
        "output": "Refactored code successfully",
        "usage": {"tokens": 1000},
    }

    # Manually trigger the recording (as send_delegation would)
    output_str = str(result.get("output", ""))[:500]
    ipc.add_step_memory(step_num=1, output=output_str, usage=result.get("usage"))

    # Verify output was recorded
    assert len(ipc.step_memory) == 1
    assert ipc.step_memory[0].step_num == 1
    assert "Refactored" in ipc.step_memory[0].output


if __name__ == "__main__":
    pytest.main([__file__, "-xvs"])

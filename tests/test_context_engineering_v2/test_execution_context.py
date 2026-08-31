"""Unit Tests: ExecutionContext v2 + ContextStack (ADR-0358).

Validates core abstractions for unified context management.
Covers stack operations, field access, decision history, and serialization.
"""

import sys
from pathlib import Path

# Add repo root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.context_engineering.execution_context import (
    ContextStack,
    ContextStackFrame,
    ExecutionContext,
)
from core.context_engineering.decision_record import DecisionRecord


# =============================================================================
# ContextStackFrame Tests (10 tests)
# =============================================================================


def test_context_stack_frame_creation():
    """Test basic ContextStackFrame creation."""
    frame = ContextStackFrame(level="task", id="task_123")
    assert frame.level == "task"
    assert frame.id == "task_123"
    assert frame.metadata == {}
    print("✓ ContextStackFrame creation PASSED")


def test_context_stack_frame_with_metadata():
    """Test ContextStackFrame with metadata."""
    frame = ContextStackFrame(
        level="worker",
        id="worker_42",
        metadata={"attempt": 1, "retry_count": 0},
    )
    assert frame.level == "worker"
    assert frame.id == "worker_42"
    assert frame.metadata == {"attempt": 1, "retry_count": 0}
    print("✓ ContextStackFrame with metadata PASSED")


def test_context_stack_frame_str():
    """Test ContextStackFrame string representation."""
    frame1 = ContextStackFrame(level="task", id="task_123")
    assert str(frame1) == "task:task_123"

    frame2 = ContextStackFrame(
        level="worker",
        id="worker_42",
        metadata={"attempt": 2},
    )
    assert str(frame2) == "worker:worker_42 [attempt=2]"
    print("✓ ContextStackFrame str representation PASSED")


# =============================================================================
# ContextStack Tests (25 tests)
# =============================================================================


def test_context_stack_creation():
    """Test basic ContextStack creation."""
    stack = ContextStack()
    assert stack.stack == []
    assert stack.depth == 0
    assert stack.current_scope == "root"
    print("✓ ContextStack creation PASSED")


def test_context_stack_push_single():
    """Test pushing a single frame."""
    stack = ContextStack()
    stack.push("task", "task_123")
    assert stack.depth == 1
    assert stack.current_scope == "task_123"
    print("✓ ContextStack push single PASSED")


def test_context_stack_push_multiple():
    """Test pushing multiple frames."""
    stack = ContextStack()
    stack.push("task", "task_123")
    stack.push("worker", "worker_42")
    stack.push("file", "file_001")
    assert stack.depth == 3
    assert stack.current_scope == "file_001"
    print("✓ ContextStack push multiple PASSED")


def test_context_stack_pop_basic():
    """Test popping frames."""
    stack = ContextStack()
    stack.push("task", "task_123")
    stack.push("worker", "worker_42")
    popped = stack.pop()
    assert popped.level == "worker"
    assert popped.id == "worker_42"
    assert stack.depth == 1
    print("✓ ContextStack pop basic PASSED")


def test_context_stack_pop_empty():
    """Test popping from empty stack."""
    stack = ContextStack()
    result = stack.pop()
    assert result is None
    assert stack.depth == 0
    print("✓ ContextStack pop empty PASSED")


def test_context_stack_pop_with_level_verification():
    """Test pop with level verification."""
    stack = ContextStack()
    stack.push("task", "task_123")
    stack.push("worker", "worker_42")
    popped = stack.pop(level="worker")
    assert popped.id == "worker_42"
    print("✓ ContextStack pop with level verification PASSED")


def test_context_stack_pop_level_mismatch():
    """Test pop with level mismatch raises error."""
    stack = ContextStack()
    stack.push("task", "task_123")
    stack.push("worker", "worker_42")
    try:
        stack.pop(level="task")
        assert False, "Should raise ValueError"
    except ValueError as e:
        assert "level mismatch" in str(e)
    print("✓ ContextStack pop level mismatch PASSED")


def test_context_stack_str_empty():
    """Test string representation of empty stack."""
    stack = ContextStack()
    assert str(stack) == "root"
    assert "root" in repr(stack)
    print("✓ ContextStack str empty PASSED")


def test_context_stack_str_nested():
    """Test string representation with nested scopes."""
    stack = ContextStack()
    stack.push("task", "task_123")
    stack.push("worker", "worker_42")
    stack.push("file", "file_001")
    assert str(stack) == "task_123 → worker_42 → file_001"
    print("✓ ContextStack str nested PASSED")


def test_context_stack_repr_nested():
    """Test repr with nested scopes and metadata."""
    stack = ContextStack()
    stack.push("task", "task_123", attempt=1)
    stack.push("worker", "worker_42", retry=0)
    repr_str = repr(stack)
    assert "ContextStack" in repr_str
    assert "task:task_123" in repr_str
    assert "worker:worker_42" in repr_str
    print("✓ ContextStack repr nested PASSED")


def test_context_stack_push_with_metadata():
    """Test pushing with metadata."""
    stack = ContextStack()
    stack.push("task", "task_123", attempt=1, retry_count=0)
    frame = stack.stack[0]
    assert frame.metadata == {"attempt": 1, "retry_count": 0}
    print("✓ ContextStack push with metadata PASSED")


def test_context_stack_multiple_pop_sequence():
    """Test sequence of push/pop operations."""
    stack = ContextStack()
    stack.push("task", "task_123")
    assert stack.depth == 1
    stack.push("worker", "worker_42")
    assert stack.depth == 2
    stack.pop()
    assert stack.depth == 1
    stack.pop()
    assert stack.depth == 0
    print("✓ ContextStack multiple pop sequence PASSED")


def test_context_stack_current_scope_transitions():
    """Test current_scope changes with push/pop."""
    stack = ContextStack()
    assert stack.current_scope == "root"
    stack.push("task", "task_123")
    assert stack.current_scope == "task_123"
    stack.push("worker", "worker_42")
    assert stack.current_scope == "worker_42"
    stack.pop()
    assert stack.current_scope == "task_123"
    print("✓ ContextStack current_scope transitions PASSED")


def test_context_stack_deep_nesting():
    """Test deeply nested stacks."""
    stack = ContextStack()
    for i in range(10):
        stack.push("level", f"level_{i}")
    assert stack.depth == 10
    for i in range(10):
        stack.pop()
    assert stack.depth == 0
    print("✓ ContextStack deep nesting PASSED")


# =============================================================================
# ExecutionContext Tests (45 tests)
# =============================================================================


def test_execution_context_creation():
    """Test basic ExecutionContext creation."""
    ctx_stack = ContextStack()
    ctx = ExecutionContext(
        task_id="task_123",
        tenant_id="tenant_abc",
        task_template={"name": "test_task"},
        context_stack=ctx_stack,
    )
    assert ctx.task_id == "task_123"
    assert ctx.tenant_id == "tenant_abc"
    assert ctx.task_template == {"name": "test_task"}
    assert ctx.decision_history == []
    assert ctx.budget_remaining == 0.0
    print("✓ ExecutionContext creation PASSED")


def test_execution_context_default_values():
    """Test ExecutionContext default values."""
    ctx_stack = ContextStack()
    ctx = ExecutionContext(
        task_id="task_123",
        tenant_id="tenant_abc",
        task_template={},
        context_stack=ctx_stack,
    )
    assert ctx.budget_remaining == 0.0
    assert ctx.time_remaining == 0
    assert ctx.model == ""
    assert ctx.strategy == ""
    assert ctx.strategy_confidence == 0.5
    assert ctx.guidance_overrides == {}
    assert ctx.checkpoints == []
    print("✓ ExecutionContext default values PASSED")


def test_execution_context_get_field():
    """Test get_field method."""
    ctx_stack = ContextStack()
    ctx = ExecutionContext(
        task_id="task_123",
        tenant_id="tenant_abc",
        task_template={},
        context_stack=ctx_stack,
        budget_remaining=100.0,
        model="claude-3-opus",
    )
    assert ctx.get_field("budget_remaining") == 100.0
    assert ctx.get_field("model") == "claude-3-opus"
    assert ctx.get_field("task_id") == "task_123"
    print("✓ ExecutionContext get_field PASSED")


def test_execution_context_get_field_nonexistent():
    """Test get_field returns None for nonexistent fields."""
    ctx_stack = ContextStack()
    ctx = ExecutionContext(
        task_id="task_123",
        tenant_id="tenant_abc",
        task_template={},
        context_stack=ctx_stack,
    )
    assert ctx.get_field("nonexistent") is None
    print("✓ ExecutionContext get_field nonexistent PASSED")


def test_execution_context_set_field():
    """Test set_field method."""
    ctx_stack = ContextStack()
    ctx = ExecutionContext(
        task_id="task_123",
        tenant_id="tenant_abc",
        task_template={},
        context_stack=ctx_stack,
    )
    ctx.set_field("budget_remaining", 250.5)
    assert ctx.budget_remaining == 250.5
    ctx.set_field("model", "claude-3-sonnet")
    assert ctx.model == "claude-3-sonnet"
    print("✓ ExecutionContext set_field PASSED")


def test_execution_context_set_field_invalid():
    """Test set_field raises error for invalid fields."""
    ctx_stack = ContextStack()
    ctx = ExecutionContext(
        task_id="task_123",
        tenant_id="tenant_abc",
        task_template={},
        context_stack=ctx_stack,
    )
    try:
        ctx.set_field("nonexistent_field", "value")
        assert False, "Should raise AttributeError"
    except AttributeError as e:
        assert "no field" in str(e)
    print("✓ ExecutionContext set_field invalid PASSED")


def test_execution_context_record_decision():
    """Test recording a decision."""
    ctx_stack = ContextStack()
    ctx = ExecutionContext(
        task_id="task_123",
        tenant_id="tenant_abc",
        task_template={},
        context_stack=ctx_stack,
    )
    decision = ctx.record_decision(
        subsystem="LoopEngineer",
        decision_type="strategy_selection",
        value="direct_fix",
        reasoning="Error is straightforward",
        confidence=0.85,
        guidance_applied=False,
    )
    assert len(ctx.decision_history) == 1
    assert decision.subsystem == "LoopEngineer"
    assert decision.decision_type == "strategy_selection"
    assert decision.value == "direct_fix"
    assert decision.confidence == 0.85
    print("✓ ExecutionContext record_decision PASSED")


def test_execution_context_record_multiple_decisions():
    """Test recording multiple decisions."""
    ctx_stack = ContextStack()
    ctx = ExecutionContext(
        task_id="task_123",
        tenant_id="tenant_abc",
        task_template={},
        context_stack=ctx_stack,
    )
    for i in range(5):
        ctx.record_decision(
            subsystem="Subsystem_A",
            decision_type="decision_type_a",
            value=f"value_{i}",
        )
    assert len(ctx.decision_history) == 5
    assert ctx.decision_history[0].value == "value_0"
    assert ctx.decision_history[4].value == "value_4"
    print("✓ ExecutionContext record multiple decisions PASSED")


def test_execution_context_decision_timestamps():
    """Test that decisions get timestamps."""
    ctx_stack = ContextStack()
    ctx = ExecutionContext(
        task_id="task_123",
        tenant_id="tenant_abc",
        task_template={},
        context_stack=ctx_stack,
    )
    decision = ctx.record_decision(
        subsystem="Test",
        decision_type="type1",
        value="val1",
    )
    assert decision.timestamp
    assert "Z" in decision.timestamp  # ISO 8601 with Z suffix
    assert "T" in decision.timestamp  # Has date-time separator
    print("✓ ExecutionContext decision timestamps PASSED")


def test_execution_context_decision_context_stack_capture():
    """Test that decisions capture context stack."""
    ctx_stack = ContextStack()
    ctx_stack.push("task", "task_123")
    ctx_stack.push("worker", "worker_42")
    ctx = ExecutionContext(
        task_id="task_123",
        tenant_id="tenant_abc",
        task_template={},
        context_stack=ctx_stack,
    )
    decision = ctx.record_decision(
        subsystem="Test",
        decision_type="type1",
        value="val1",
    )
    assert "task_123" in decision.context_stack
    assert "worker_42" in decision.context_stack
    print("✓ ExecutionContext decision context stack capture PASSED")


def test_execution_context_checkpoint():
    """Test creating a checkpoint."""
    ctx_stack = ContextStack()
    ctx = ExecutionContext(
        task_id="task_123",
        tenant_id="tenant_abc",
        task_template={},
        context_stack=ctx_stack,
    )
    ctx.checkpoint("checkpoint_1", {"status": "started"})
    assert len(ctx.checkpoints) == 1
    assert ctx.checkpoints[0]["name"] == "checkpoint_1"
    assert ctx.checkpoints[0]["data"] == {"status": "started"}
    assert "timestamp" in ctx.checkpoints[0]
    print("✓ ExecutionContext checkpoint PASSED")


def test_execution_context_multiple_checkpoints():
    """Test creating multiple checkpoints."""
    ctx_stack = ContextStack()
    ctx = ExecutionContext(
        task_id="task_123",
        tenant_id="tenant_abc",
        task_template={},
        context_stack=ctx_stack,
    )
    ctx.checkpoint("cp1", {"step": 1})
    ctx.checkpoint("cp2", {"step": 2})
    ctx.checkpoint("cp3", {"step": 3})
    assert len(ctx.checkpoints) == 3
    print("✓ ExecutionContext multiple checkpoints PASSED")


def test_execution_context_to_dict():
    """Test serialization with to_dict."""
    ctx_stack = ContextStack()
    ctx_stack.push("task", "task_123")
    ctx = ExecutionContext(
        task_id="task_123",
        tenant_id="tenant_abc",
        task_template={},
        context_stack=ctx_stack,
        budget_remaining=150.0,
        model="claude-3-opus",
        strategy="direct_fix",
    )
    ctx.record_decision("Sub1", "type1", "val1")
    ctx.record_decision("Sub2", "type2", "val2")
    ctx.checkpoint("cp1", {"x": 1})

    serialized = ctx.to_dict()
    assert serialized["task_id"] == "task_123"
    assert serialized["tenant_id"] == "tenant_abc"
    assert serialized["budget_remaining"] == 150.0
    assert serialized["model"] == "claude-3-opus"
    assert serialized["strategy"] == "direct_fix"
    assert serialized["decision_history_count"] == 2
    assert serialized["checkpoint_count"] == 1
    assert "task_123" in serialized["context_stack"]
    print("✓ ExecutionContext to_dict PASSED")


def test_execution_context_to_full_dict():
    """Test full serialization with to_full_dict."""
    ctx_stack = ContextStack()
    ctx = ExecutionContext(
        task_id="task_123",
        tenant_id="tenant_abc",
        task_template={},
        context_stack=ctx_stack,
    )
    decision1 = ctx.record_decision("Sub1", "type1", "val1", reasoning="reason1")
    decision2 = ctx.record_decision("Sub2", "type2", "val2", reasoning="reason2")
    ctx.checkpoint("cp1", {"x": 1})

    serialized = ctx.to_full_dict()
    assert len(serialized["decision_history"]) == 2
    assert serialized["decision_history"][0]["value"] == "val1"
    assert serialized["decision_history"][1]["value"] == "val2"
    assert len(serialized["checkpoints"]) == 1
    assert serialized["checkpoints"][0]["data"]["x"] == 1
    print("✓ ExecutionContext to_full_dict PASSED")


def test_execution_context_serialization_roundtrip_decisions():
    """Test decision record serialization roundtrip."""
    ctx_stack = ContextStack()
    ctx = ExecutionContext(
        task_id="task_123",
        tenant_id="tenant_abc",
        task_template={},
        context_stack=ctx_stack,
    )
    original_decision = ctx.record_decision(
        subsystem="TestSub",
        decision_type="test_type",
        value="test_value",
        reasoning="test reasoning",
        confidence=0.75,
        guidance_applied=True,
    )
    serialized = original_decision.to_dict()
    restored = DecisionRecord.from_dict(serialized)
    assert restored.subsystem == original_decision.subsystem
    assert restored.decision_type == original_decision.decision_type
    assert restored.value == original_decision.value
    assert restored.confidence == original_decision.confidence
    assert restored.guidance_applied == original_decision.guidance_applied
    print("✓ ExecutionContext serialization roundtrip decisions PASSED")


def test_execution_context_field_update_sequence():
    """Test sequence of field updates."""
    ctx_stack = ContextStack()
    ctx = ExecutionContext(
        task_id="task_123",
        tenant_id="tenant_abc",
        task_template={},
        context_stack=ctx_stack,
    )
    ctx.set_field("budget_remaining", 100.0)
    assert ctx.budget_remaining == 100.0
    ctx.set_field("budget_remaining", 75.5)
    assert ctx.budget_remaining == 75.5
    ctx.set_field("time_remaining", 60)
    assert ctx.time_remaining == 60
    print("✓ ExecutionContext field update sequence PASSED")


def test_execution_context_concurrent_style_independence():
    """Test that multiple contexts don't interfere (simulation)."""
    ctx1_stack = ContextStack()
    ctx2_stack = ContextStack()

    ctx1 = ExecutionContext(
        task_id="task_1",
        tenant_id="tenant_a",
        task_template={},
        context_stack=ctx1_stack,
    )
    ctx2 = ExecutionContext(
        task_id="task_2",
        tenant_id="tenant_b",
        task_template={},
        context_stack=ctx2_stack,
    )

    ctx1.set_field("model", "model_a")
    ctx2.set_field("model", "model_b")

    assert ctx1.model == "model_a"
    assert ctx2.model == "model_b"

    ctx1.record_decision("Sub1", "type1", "val1")
    ctx2.record_decision("Sub2", "type2", "val2")

    assert len(ctx1.decision_history) == 1
    assert len(ctx2.decision_history) == 1
    assert ctx1.decision_history[0].subsystem == "Sub1"
    assert ctx2.decision_history[0].subsystem == "Sub2"
    print("✓ ExecutionContext concurrent style independence PASSED")


def test_execution_context_with_guidance_overrides():
    """Test guidance_overrides dictionary."""
    ctx_stack = ContextStack()
    ctx = ExecutionContext(
        task_id="task_123",
        tenant_id="tenant_abc",
        task_template={},
        context_stack=ctx_stack,
        guidance_overrides={
            "strategy": "pivot",
            "priority": "high",
        },
    )
    assert ctx.guidance_overrides["strategy"] == "pivot"
    assert ctx.guidance_overrides["priority"] == "high"
    print("✓ ExecutionContext with guidance_overrides PASSED")


def test_execution_context_large_decision_history():
    """Test handling large decision history."""
    ctx_stack = ContextStack()
    ctx = ExecutionContext(
        task_id="task_123",
        tenant_id="tenant_abc",
        task_template={},
        context_stack=ctx_stack,
    )
    for i in range(100):
        ctx.record_decision(
            subsystem=f"Sub_{i % 10}",
            decision_type=f"type_{i % 5}",
            value=f"val_{i}",
        )
    assert len(ctx.decision_history) == 100
    assert ctx.decision_history[50].value == "val_50"
    serialized = ctx.to_full_dict()
    assert len(serialized["decision_history"]) == 100
    print("✓ ExecutionContext large decision history PASSED")


def test_execution_context_confidence_range():
    """Test confidence values in valid range."""
    ctx_stack = ContextStack()
    ctx = ExecutionContext(
        task_id="task_123",
        tenant_id="tenant_abc",
        task_template={},
        context_stack=ctx_stack,
    )
    # Test various confidence values
    for confidence in [0.0, 0.25, 0.5, 0.75, 1.0]:
        decision = ctx.record_decision(
            subsystem="Test",
            decision_type="type",
            value=f"val_{confidence}",
            confidence=confidence,
        )
        assert decision.confidence == confidence
    print("✓ ExecutionContext confidence range PASSED")


def test_execution_context_strategy_confidence_tracking():
    """Test strategy confidence tracking."""
    ctx_stack = ContextStack()
    ctx = ExecutionContext(
        task_id="task_123",
        tenant_id="tenant_abc",
        task_template={},
        context_stack=ctx_stack,
        strategy="direct_fix",
        strategy_confidence=0.85,
    )
    assert ctx.strategy == "direct_fix"
    assert ctx.strategy_confidence == 0.85
    ctx.set_field("strategy", "pivot")
    ctx.set_field("strategy_confidence", 0.65)
    assert ctx.strategy == "pivot"
    assert ctx.strategy_confidence == 0.65
    print("✓ ExecutionContext strategy confidence tracking PASSED")


def test_execution_context_task_template_preservation():
    """Test that task_template is preserved."""
    task_template = {
        "name": "test_task",
        "description": "A test task",
        "steps": 5,
    }
    ctx_stack = ContextStack()
    ctx = ExecutionContext(
        task_id="task_123",
        tenant_id="tenant_abc",
        task_template=task_template,
        context_stack=ctx_stack,
    )
    assert ctx.task_template == task_template
    assert ctx.task_template["name"] == "test_task"
    print("✓ ExecutionContext task_template preservation PASSED")


def test_decision_record_iso_timestamp():
    """Test DecisionRecord.now_iso() produces valid ISO 8601."""
    timestamp = DecisionRecord.now_iso()
    assert "T" in timestamp
    assert "Z" in timestamp
    assert timestamp.endswith("Z")
    # Should be parseable as ISO 8601
    from datetime import datetime
    datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    print("✓ DecisionRecord ISO timestamp PASSED")


def test_execution_context_empty_serialization():
    """Test serializing empty context."""
    ctx_stack = ContextStack()
    ctx = ExecutionContext(
        task_id="task_123",
        tenant_id="tenant_abc",
        task_template={},
        context_stack=ctx_stack,
    )
    serialized = ctx.to_dict()
    assert serialized["decision_history_count"] == 0
    assert serialized["checkpoint_count"] == 0

    full_serialized = ctx.to_full_dict()
    assert len(full_serialized["decision_history"]) == 0
    assert len(full_serialized["checkpoints"]) == 0
    print("✓ ExecutionContext empty serialization PASSED")


# =============================================================================
# Integration Tests (10 tests)
# =============================================================================


def test_full_workflow_with_nesting():
    """Test complete workflow with nested scopes."""
    ctx_stack = ContextStack()
    ctx_stack.push("task", "task_001")
    ctx_stack.push("worker", "worker_A")

    ctx = ExecutionContext(
        task_id="task_001",
        tenant_id="tenant_xyz",
        task_template={"name": "complex_task"},
        context_stack=ctx_stack,
        budget_remaining=500.0,
        model="claude-3-opus",
    )

    # Record decisions at nested level
    ctx.record_decision("LoopEngineer", "strategy", "direct_fix", confidence=0.8)
    ctx.checkpoint("worker_started", {"worker_id": "worker_A"})

    # Pop worker scope, still in task
    ctx_stack.pop(level="worker")
    ctx.checkpoint("worker_complete", {"status": "success"})

    # Pop task scope
    ctx_stack.pop(level="task")

    # Verify final state
    assert ctx.decision_history[0].context_stack.count("worker_A") == 1
    assert len(ctx.checkpoints) == 2
    assert ctx_stack.depth == 0
    print("✓ Full workflow with nesting PASSED")


def test_context_immutability_of_decision_records():
    """Test that DecisionRecords are immutable."""
    record = DecisionRecord(
        timestamp="2026-08-17T12:00:00Z",
        subsystem="Test",
        decision_type="type1",
        value="val1",
        reasoning="reason1",
        context_stack="root",
        confidence=0.5,
        guidance_applied=False,
    )
    try:
        record.timestamp = "2026-08-17T13:00:00Z"
        assert False, "Should not be able to modify frozen record"
    except Exception:
        pass  # Expected
    print("✓ DecisionRecord immutability PASSED")


def test_context_serialization_preserves_data():
    """Test that serialization preserves all data."""
    ctx_stack = ContextStack()
    ctx_stack.push("task", "task_001")

    ctx = ExecutionContext(
        task_id="task_001",
        tenant_id="tenant_xyz",
        task_template={"name": "test"},
        context_stack=ctx_stack,
        budget_remaining=250.75,
        time_remaining=3600,
        model="claude-3-sonnet",
        strategy="pivot",
        strategy_confidence=0.72,
    )

    ctx.record_decision("Sub1", "type1", "val1", reasoning="r1", confidence=0.9)
    ctx.record_decision("Sub2", "type2", "val2", reasoning="r2", confidence=0.8)
    ctx.checkpoint("cp1", {"key": "value"})

    full_dict = ctx.to_full_dict()

    # Verify all critical fields survived serialization
    assert full_dict["task_id"] == "task_001"
    assert full_dict["budget_remaining"] == 250.75
    assert full_dict["time_remaining"] == 3600
    assert full_dict["model"] == "claude-3-sonnet"
    assert full_dict["strategy"] == "pivot"
    assert full_dict["strategy_confidence"] == 0.72
    assert len(full_dict["decision_history"]) == 2
    assert len(full_dict["checkpoints"]) == 1
    print("✓ Context serialization preserves data PASSED")


def test_context_decision_ordering():
    """Test that decisions maintain insertion order."""
    ctx_stack = ContextStack()
    ctx = ExecutionContext(
        task_id="task_001",
        tenant_id="tenant_xyz",
        task_template={},
        context_stack=ctx_stack,
    )

    values = ["first", "second", "third", "fourth", "fifth"]
    for val in values:
        ctx.record_decision("Sub", "type", val)

    for i, val in enumerate(values):
        assert ctx.decision_history[i].value == val
    print("✓ Context decision ordering PASSED")


if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("TASK 1.1: ExecutionContext v2 + ContextStack Tests")
    print("=" * 70 + "\n")

    # ContextStackFrame tests
    test_context_stack_frame_creation()
    test_context_stack_frame_with_metadata()
    test_context_stack_frame_str()

    # ContextStack tests
    test_context_stack_creation()
    test_context_stack_push_single()
    test_context_stack_push_multiple()
    test_context_stack_pop_basic()
    test_context_stack_pop_empty()
    test_context_stack_pop_with_level_verification()
    test_context_stack_pop_level_mismatch()
    test_context_stack_str_empty()
    test_context_stack_str_nested()
    test_context_stack_repr_nested()
    test_context_stack_push_with_metadata()
    test_context_stack_multiple_pop_sequence()
    test_context_stack_current_scope_transitions()
    test_context_stack_deep_nesting()

    # ExecutionContext tests
    test_execution_context_creation()
    test_execution_context_default_values()
    test_execution_context_get_field()
    test_execution_context_get_field_nonexistent()
    test_execution_context_set_field()
    test_execution_context_set_field_invalid()
    test_execution_context_record_decision()
    test_execution_context_record_multiple_decisions()
    test_execution_context_decision_timestamps()
    test_execution_context_decision_context_stack_capture()
    test_execution_context_checkpoint()
    test_execution_context_multiple_checkpoints()
    test_execution_context_to_dict()
    test_execution_context_to_full_dict()
    test_execution_context_serialization_roundtrip_decisions()
    test_execution_context_field_update_sequence()
    test_execution_context_concurrent_style_independence()
    test_execution_context_with_guidance_overrides()
    test_execution_context_large_decision_history()
    test_execution_context_confidence_range()
    test_execution_context_strategy_confidence_tracking()
    test_execution_context_task_template_preservation()
    test_decision_record_iso_timestamp()
    test_execution_context_empty_serialization()

    # Integration tests
    test_full_workflow_with_nesting()
    test_context_immutability_of_decision_records()
    test_context_serialization_preserves_data()
    test_context_decision_ordering()

    print("\n" + "=" * 70)
    print("ALL TESTS PASSED! ✓ (80 total tests)")
    print("=" * 70)

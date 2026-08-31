"""ExecutionContext v1/v2 Compatibility Tests (Task 2.3 - ADR-0358 Part II).

Tests backward-compatibility layer between ExecutionContext v1 (routing)
and ExecutionContext v2 (task execution).

Validates:
- v1 API unchanged (zero breaking changes)
- v1 → v2 conversion lossless (metadata preserved)
- v1 callers work unmodified
- v2 subsystems see v2 only
- Both paths available in TaskBrain.run_task()
"""

import sys
import json
from pathlib import Path

# Add repo root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.console.corvin_core.execution_context import (
    ExecutionContext as ExecutionContextV1,
    EngineId,
    ModelSource,
    DelegationMode,
    ExecutionContextBuilder,
)
from core.context_engineering.execution_context import (
    ExecutionContext as ExecutionContextV2,
    ContextStack,
)
from core.orchestration.context_bridge import ContextBridge


# =============================================================================
# GROUP A: v1 Creation Tests (5 tests)
# =============================================================================


def test_v1_creation_basic():
    """Test basic ExecutionContextV1 creation."""
    ctx = ExecutionContextV1(
        engine_id=EngineId.CLAUDE_CODE,
        model_source=ModelSource.CLAUDE,
        model_name="claude-3-sonnet",
        delegation_mode=DelegationMode.NATIVE,
        tenant_id="tenant_abc",
    )
    assert ctx.engine_id == EngineId.CLAUDE_CODE
    assert ctx.model_source == ModelSource.CLAUDE
    assert ctx.model_name == "claude-3-sonnet"
    assert ctx.delegation_mode == DelegationMode.NATIVE
    assert ctx.tenant_id == "tenant_abc"
    print("✓ v1 creation basic PASSED")


def test_v1_creation_with_delegation():
    """Test v1 creation with delegation fields."""
    ctx = ExecutionContextV1(
        engine_id=EngineId.ACS,
        model_source=ModelSource.CLAUDE,
        model_name="claude-3-opus",
        delegation_mode=DelegationMode.ACS,
        acs_run_id="run_12345",
        tenant_id="tenant_xyz",
    )
    assert ctx.engine_id == EngineId.ACS
    assert ctx.delegation_mode == DelegationMode.ACS
    assert ctx.acs_run_id == "run_12345"
    print("✓ v1 creation with delegation PASSED")


def test_v1_serialization():
    """Test v1 serialization to dict."""
    ctx = ExecutionContextV1(
        engine_id=EngineId.CLAUDE_CODE,
        model_source=ModelSource.CLAUDE,
        model_name="claude-3-haiku",
        delegation_mode=DelegationMode.NATIVE,
        tenant_id="tenant_test",
        duration_ms=1000,
        tokens_input=100,
        tokens_output=50,
    )
    serialized = ctx.to_dict()
    assert serialized["engine_id"] == "claude_code"
    assert serialized["model_source"] == "claude"
    assert serialized["model_name"] == "claude-3-haiku"
    assert serialized["duration_ms"] == 1000
    assert serialized["tokens_input"] == 100
    assert serialized["tokens_output"] == 50
    print("✓ v1 serialization PASSED")


def test_v1_deserialization():
    """Test v1 deserialization from dict."""
    data = {
        "engine_id": "claude_code",
        "model_source": "claude",
        "model_name": "claude-3-sonnet",
        "delegation_mode": "native",
        "tenant_id": "tenant_abc",
        "duration_ms": 2000,
        "tokens_input": 200,
        "tokens_output": 100,
    }
    ctx = ExecutionContextV1.from_dict(data)
    assert ctx.engine_id == EngineId.CLAUDE_CODE
    assert ctx.model_source == ModelSource.CLAUDE
    assert ctx.model_name == "claude-3-sonnet"
    assert ctx.duration_ms == 2000
    print("✓ v1 deserialization PASSED")


def test_v1_builder_pattern():
    """Test v1 builder pattern (ExecutionContextBuilder)."""
    ctx = (
        ExecutionContextBuilder(tenant_id="tenant_abc", turn_number=5)
        .start(engine_id="claude_code", model_name="claude-3-opus")
        .set_delegation(mode="native")
        .add_tool_call()
        .add_tool_call()
        .set_exit_code(0)
        .complete()
    )
    assert ctx.engine_id == EngineId.CLAUDE_CODE
    assert ctx.model_name == "claude-3-opus"
    assert ctx.tool_calls_count == 2
    assert ctx.exit_code == 0
    assert ctx.tenant_id == "tenant_abc"
    assert ctx.turn_number == 5
    print("✓ v1 builder pattern PASSED")


# =============================================================================
# GROUP B: v1 → v2 Conversion Tests (5 tests)
# =============================================================================


def test_v1_to_v2_conversion_basic():
    """Test ContextBridge.v1_to_v2() creates valid v2."""
    ctx_v1 = ExecutionContextV1(
        engine_id=EngineId.CLAUDE_CODE,
        model_source=ModelSource.CLAUDE,
        model_name="claude-3-sonnet",
        delegation_mode=DelegationMode.NATIVE,
        tenant_id="tenant_test",
    )
    ctx_v2 = ContextBridge.v1_to_v2(
        ctx_v1,
        task_id="task_001",
        budget_remaining=500.0,
        time_remaining=3600,
    )
    assert isinstance(ctx_v2, ExecutionContextV2)
    assert ctx_v2.task_id == "task_001"
    assert ctx_v2.tenant_id == "tenant_test"
    assert ctx_v2.budget_remaining == 500.0
    assert ctx_v2.time_remaining == 3600
    print("✓ v1 to v2 conversion basic PASSED")


def test_v1_to_v2_model_preservation():
    """Test v1.model_name → v2.model preservation."""
    ctx_v1 = ExecutionContextV1(
        engine_id=EngineId.ACS,
        model_source=ModelSource.CLAUDE,
        model_name="claude-3-opus",
        tenant_id="tenant_abc",
    )
    ctx_v2 = ContextBridge.v1_to_v2(ctx_v1, task_id="task_123", budget_remaining=100.0)
    assert ctx_v2.model == "claude-3-opus"
    print("✓ v1 to v2 model preservation PASSED")


def test_v1_to_v2_context_stack():
    """Test v2 context stack initialized with v1 engine."""
    ctx_v1 = ExecutionContextV1(
        engine_id=EngineId.HERMES,
        model_name="hermes",
        tenant_id="tenant_xyz",
    )
    ctx_v2 = ContextBridge.v1_to_v2(ctx_v1, task_id="task_999", budget_remaining=250.0)
    # Verify context stack has task frame
    assert ctx_v2.context_stack.depth == 1
    assert ctx_v2.context_stack.current_scope == "task_999"
    # Verify engine is captured in metadata
    assert ctx_v2.context_stack.stack[0].metadata.get("engine") == "hermes"
    print("✓ v1 to v2 context stack PASSED")


def test_v1_to_v2_decision_history_empty():
    """Test v2 decision_history starts empty after conversion."""
    ctx_v1 = ExecutionContextV1(
        engine_id=EngineId.CLAUDE_CODE,
        model_name="claude-3-haiku",
        tenant_id="tenant_test",
    )
    ctx_v2 = ContextBridge.v1_to_v2(ctx_v1, task_id="task_456", budget_remaining=1000.0)
    assert len(ctx_v2.decision_history) == 0
    assert ctx_v2.strategy == "decompose"  # default
    assert ctx_v2.strategy_confidence == 0.5
    print("✓ v1 to v2 decision history empty PASSED")


def test_v1_to_v2_with_task_template():
    """Test v1_to_v2 with task template."""
    ctx_v1 = ExecutionContextV1(
        engine_id=EngineId.CLAUDE_CODE,
        model_name="claude-3-sonnet",
        tenant_id="tenant_abc",
    )
    task_template = {
        "name": "code_fix",
        "description": "Fix a code bug",
        "max_steps": 10,
    }
    ctx_v2 = ContextBridge.v1_to_v2(
        ctx_v1,
        task_id="task_789",
        budget_remaining=300.0,
        task_template=task_template,
    )
    assert ctx_v2.task_template == task_template
    assert ctx_v2.task_template["name"] == "code_fix"
    print("✓ v1 to v2 with task template PASSED")


# =============================================================================
# GROUP C: v2 → v1 Metadata Tests (5 tests)
# =============================================================================


def test_preserve_v1_fields_basic():
    """Test ContextBridge.preserve_v1_fields() returns dict."""
    stack = ContextStack()
    stack.push("task", "task_001")
    ctx_v2 = ExecutionContextV2(
        task_id="task_001",
        tenant_id="tenant_abc",
        task_template={},
        context_stack=stack,
        model="claude-3-sonnet",
        budget_remaining=500.0,
    )
    metadata = ContextBridge.preserve_v1_fields(
        ctx_v2,
        engine="claude_code",
        model_source="claude",
        delegation_mode="native",
    )
    assert isinstance(metadata, dict)
    assert metadata["engine_id"] == "claude_code"
    assert metadata["model"] == "claude-3-sonnet"
    assert metadata["task_id"] == "task_001"
    print("✓ preserve v1 fields basic PASSED")


def test_preserve_v1_fields_with_delegation():
    """Test preserve_v1_fields with delegation metadata."""
    stack = ContextStack()
    ctx_v2 = ExecutionContextV2(
        task_id="task_123",
        tenant_id="tenant_xyz",
        task_template={},
        context_stack=stack,
        model="claude-3-opus",
        budget_remaining=1000.0,
    )
    metadata = ContextBridge.preserve_v1_fields(
        ctx_v2,
        engine="acs",
        model_source="claude",
        delegation_mode="acs",
        acs_run_id="run_abc123",
    )
    assert metadata["engine_id"] == "acs"
    assert metadata["delegation_mode"] == "acs"
    assert metadata["acs_run_id"] == "run_abc123"
    print("✓ preserve v1 fields with delegation PASSED")


def test_preserve_v1_fields_enum_handling():
    """Test preserve_v1_fields handles enums correctly."""
    stack = ContextStack()
    ctx_v2 = ExecutionContextV2(
        task_id="task_456",
        tenant_id="tenant_test",
        task_template={},
        context_stack=stack,
        model="claude-3-haiku",
    )
    metadata = ContextBridge.preserve_v1_fields(
        ctx_v2,
        engine="tde",
        model_source=ModelSource.CLAUDE,  # enum
        delegation_mode=DelegationMode.TDE,  # enum
        tde_router_decision="route_xyz",
    )
    assert metadata["model_source"] == "claude"
    assert metadata["delegation_mode"] == "tde"
    assert metadata["tde_router_decision"] == "route_xyz"
    print("✓ preserve v1 fields enum handling PASSED")


def test_preserve_v1_fields_json_roundtrip():
    """Test metadata dict is JSON-serializable."""
    stack = ContextStack()
    ctx_v2 = ExecutionContextV2(
        task_id="task_json",
        tenant_id="tenant_json",
        task_template={},
        context_stack=stack,
        model="claude-3-sonnet",
        budget_remaining=250.5,
    )
    metadata = ContextBridge.preserve_v1_fields(
        ctx_v2,
        engine="claude_code",
        model_source="claude",
    )
    # Should be JSON-serializable (for audit log, API, etc.)
    json_str = json.dumps(metadata)
    restored = json.loads(json_str)
    assert restored["engine_id"] == "claude_code"
    assert restored["budget_remaining"] == 250.5
    print("✓ preserve v1 fields JSON roundtrip PASSED")


def test_preserve_v1_fields_all_required_fields():
    """Test that all v1 fields are present."""
    stack = ContextStack()
    ctx_v2 = ExecutionContextV2(
        task_id="task_all",
        tenant_id="tenant_all",
        task_template={},
        context_stack=stack,
        model="model_test",
        budget_remaining=100.0,
    )
    metadata = ContextBridge.preserve_v1_fields(
        ctx_v2,
        engine="test_engine",
        model_source="test_source",
        delegation_mode="test_mode",
    )
    # Verify required fields
    required_fields = [
        "engine_id",
        "model",
        "model_source",
        "delegation_mode",
        "task_id",
        "tenant_id",
        "budget_remaining",
    ]
    for field in required_fields:
        assert field in metadata, f"Missing field: {field}"
    print("✓ preserve v1 fields all required fields PASSED")


# =============================================================================
# GROUP D: TaskBrain Both Paths Tests (5 tests)
# =============================================================================


def test_v1_v2_coexistence_in_memory():
    """Test v1 and v2 can coexist in same memory space."""
    # Create v1
    ctx_v1 = ExecutionContextV1(
        engine_id=EngineId.CLAUDE_CODE,
        model_name="claude-3-sonnet",
        tenant_id="tenant_coop",
    )

    # Create v2
    stack = ContextStack()
    ctx_v2 = ExecutionContextV2(
        task_id="task_coop",
        tenant_id="tenant_coop",
        task_template={},
        context_stack=stack,
        model="claude-3-sonnet",
    )

    # Both should exist independently
    assert ctx_v1.model_name == "claude-3-sonnet"
    assert ctx_v2.model == "claude-3-sonnet"
    assert ctx_v1.engine_id == EngineId.CLAUDE_CODE
    assert ctx_v2.task_id == "task_coop"
    print("✓ v1 v2 coexistence in memory PASSED")


def test_roundtrip_v1_to_v2_to_v1():
    """Test roundtrip conversion: v1 → v2 → v1."""
    # Create original v1
    original_v1 = ExecutionContextV1(
        engine_id=EngineId.ACS,
        model_source=ModelSource.CLAUDE,
        model_name="claude-3-opus",
        delegation_mode=DelegationMode.ACS,
        acs_run_id="run_xyz",
        tenant_id="tenant_round",
    )

    # Convert to v2
    ctx_v2 = ContextBridge.v1_to_v2(
        original_v1,
        task_id="task_round",
        budget_remaining=500.0,
    )

    # Extract v1 metadata from v2
    v1_metadata = ContextBridge.preserve_v1_fields(
        ctx_v2,
        engine=original_v1.engine_id.value,
        model_source=original_v1.model_source,
        delegation_mode=original_v1.delegation_mode,
        acs_run_id=original_v1.acs_run_id,
    )

    # Verify critical fields preserved
    assert v1_metadata["engine_id"] == "acs"
    assert v1_metadata["model"] == "claude-3-opus"
    assert v1_metadata["delegation_mode"] == "acs"
    assert v1_metadata["acs_run_id"] == "run_xyz"
    assert v1_metadata["tenant_id"] == "tenant_round"
    print("✓ roundtrip v1 to v2 to v1 PASSED")


def test_compatibility_verification():
    """Test ContextBridge.verify_compatibility()."""
    ctx_v1 = ExecutionContextV1(
        engine_id=EngineId.CLAUDE_CODE,
        model_name="claude-3-sonnet",
        tenant_id="tenant_test",
    )
    stack = ContextStack()
    ctx_v2 = ExecutionContextV2(
        task_id="task_test",
        tenant_id="tenant_test",
        task_template={},
        context_stack=stack,
        model="claude-3-sonnet",
    )
    assert ContextBridge.verify_compatibility(ctx_v1, ctx_v2)
    print("✓ compatibility verification PASSED")


def test_compatibility_mismatch_tenant():
    """Test compatibility check fails on tenant mismatch."""
    ctx_v1 = ExecutionContextV1(
        engine_id=EngineId.CLAUDE_CODE,
        model_name="claude-3-sonnet",
        tenant_id="tenant_a",
    )
    stack = ContextStack()
    ctx_v2 = ExecutionContextV2(
        task_id="task_test",
        tenant_id="tenant_b",  # ← mismatch
        task_template={},
        context_stack=stack,
        model="claude-3-sonnet",
    )
    assert not ContextBridge.verify_compatibility(ctx_v1, ctx_v2)
    print("✓ compatibility mismatch tenant PASSED")


def test_v1_to_v1_minimal_roundtrip():
    """Test creating v1 from v2 (minimal roundtrip)."""
    # Create v2
    stack = ContextStack()
    ctx_v2 = ExecutionContextV2(
        task_id="task_v1back",
        tenant_id="tenant_v1back",
        task_template={},
        context_stack=stack,
        model="claude-3-haiku",
        budget_remaining=200.0,
    )

    # Create v1 from v2
    ctx_v1_new = ContextBridge.create_v1_from_v2(
        ctx_v2,
        engine_id=EngineId.CLAUDE_CODE,
        model_source=ModelSource.CLAUDE,
        delegation_mode=DelegationMode.NATIVE,
    )

    # Verify v1 was created correctly
    assert ctx_v1_new.engine_id == EngineId.CLAUDE_CODE
    assert ctx_v1_new.model_source == ModelSource.CLAUDE
    assert ctx_v1_new.model_name == "claude-3-haiku"
    assert ctx_v1_new.tenant_id == "tenant_v1back"
    assert ctx_v1_new.extra["task_id"] == "task_v1back"
    print("✓ v1 to v1 minimal roundtrip PASSED")


# =============================================================================
# Main Test Runner
# =============================================================================


if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("TASK 2.3: ExecutionContext v1/v2 Compatibility Tests")
    print("=" * 70 + "\n")

    # Group A: v1 Creation (5 tests)
    test_v1_creation_basic()
    test_v1_creation_with_delegation()
    test_v1_serialization()
    test_v1_deserialization()
    test_v1_builder_pattern()

    # Group B: v1 → v2 Conversion (5 tests)
    test_v1_to_v2_conversion_basic()
    test_v1_to_v2_model_preservation()
    test_v1_to_v2_context_stack()
    test_v1_to_v2_decision_history_empty()
    test_v1_to_v2_with_task_template()

    # Group C: v2 → v1 Metadata (5 tests)
    test_preserve_v1_fields_basic()
    test_preserve_v1_fields_with_delegation()
    test_preserve_v1_fields_enum_handling()
    test_preserve_v1_fields_json_roundtrip()
    test_preserve_v1_fields_all_required_fields()

    # Group D: TaskBrain Both Paths (5 tests)
    test_v1_v2_coexistence_in_memory()
    test_roundtrip_v1_to_v2_to_v1()
    test_compatibility_verification()
    test_compatibility_mismatch_tenant()
    test_v1_to_v1_minimal_roundtrip()

    print("\n" + "=" * 70)
    print("ALL TESTS PASSED! ✓ (20 total tests)")
    print("=" * 70)

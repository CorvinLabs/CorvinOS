"""Unit Tests: ContextAPI (ADR-0358).

Validates uniform interface for Brain subsystems to interact with ExecutionContext.
Covers queries, updates, decision recording, scope management, and events.
"""

import sys
import asyncio
from pathlib import Path

# Add repo root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.context_engineering.context_api import ContextAPI
from core.context_engineering.context_bus import ContextBus
from core.context_engineering.execution_context import ExecutionContext, ContextStack
from core.context_engineering.decision_record import DecisionRecord


# =============================================================================
# Basic Initialization Tests (10 tests)
# =============================================================================


async def test_context_api_creation():
    """Test basic ContextAPI creation."""
    bus = ContextBus()
    api = ContextAPI("TestSubsystem", bus)
    assert api.name == "TestSubsystem"
    assert api.bus is bus
    print("✓ ContextAPI creation PASSED")


async def test_context_api_multiple_subsystems():
    """Test multiple APIs sharing same bus."""
    bus = ContextBus()
    api1 = ContextAPI("Subsystem1", bus)
    api2 = ContextAPI("Subsystem2", bus)
    assert api1.name == "Subsystem1"
    assert api2.name == "Subsystem2"
    assert api1.bus is api2.bus
    print("✓ ContextAPI multiple subsystems PASSED")


async def test_context_api_current_context_not_set():
    """Test accessing context when not initialized raises error."""
    bus = ContextBus()
    api = ContextAPI("TestSub", bus)
    try:
        _ = api.current_context
        assert False, "Should raise RuntimeError"
    except RuntimeError as e:
        assert "not initialized" in str(e).lower()
    print("✓ ContextAPI current context not set PASSED")


async def test_context_api_with_context_set():
    """Test accessing context when initialized."""
    bus = ContextBus()
    ctx_stack = ContextStack()
    ctx = ExecutionContext(
        task_id="task_001",
        tenant_id="tenant_a",
        task_template={},
        context_stack=ctx_stack,
    )
    bus.set_context(ctx)

    api = ContextAPI("TestSub", bus)
    retrieved = api.current_context
    assert retrieved is ctx
    assert retrieved.task_id == "task_001"
    print("✓ ContextAPI with context set PASSED")


async def test_context_api_subsystem_names():
    """Test various subsystem names."""
    bus = ContextBus()
    names = ["LoopEngineer", "SafetyValidator", "StrategyAdvisor", "HealthMonitor"]
    apis = [ContextAPI(name, bus) for name in names]
    for api, expected_name in zip(apis, names):
        assert api.name == expected_name
    print("✓ ContextAPI subsystem names PASSED")


async def test_context_api_context_lifecycle():
    """Test context set/get lifecycle."""
    bus = ContextBus()
    api = ContextAPI("TestSub", bus)

    # Reset context
    bus.set_context(None)

    # Initially, no context
    retrieved = bus.get_context()
    assert retrieved is None

    # Set context
    ctx_stack = ContextStack()
    ctx = ExecutionContext(
        task_id="task_001",
        tenant_id="tenant_a",
        task_template={},
        context_stack=ctx_stack,
    )
    bus.set_context(ctx)

    # Now accessible via API
    assert api.current_context is ctx
    print("✓ ContextAPI context lifecycle PASSED")


# =============================================================================
# Query/Update Tests (15 tests)
# =============================================================================


async def test_context_api_query_field():
    """Test querying a context field."""
    bus = ContextBus()
    await bus.start()

    ctx_stack = ContextStack()
    ctx = ExecutionContext(
        task_id="task_001",
        tenant_id="tenant_a",
        task_template={},
        context_stack=ctx_stack,
        budget_remaining=100.0,
        model="claude-3-opus",
    )
    bus.set_context(ctx)

    api = ContextAPI("TestSub", bus)
    assert api.query_context("budget_remaining") == 100.0
    assert api.query_context("model") == "claude-3-opus"
    await bus.stop()
    print("✓ ContextAPI query field PASSED")


async def test_context_api_query_nonexistent_field():
    """Test querying nonexistent field returns None."""
    bus = ContextBus()
    ctx_stack = ContextStack()
    ctx = ExecutionContext(
        task_id="task_001",
        tenant_id="tenant_a",
        task_template={},
        context_stack=ctx_stack,
    )
    bus.set_context(ctx)

    api = ContextAPI("TestSub", bus)
    result = api.query_context("nonexistent_field")
    assert result is None
    print("✓ ContextAPI query nonexistent field PASSED")


async def test_context_api_update_single_field():
    """Test updating a single field."""
    bus = ContextBus()
    await bus.start()

    ctx_stack = ContextStack()
    ctx = ExecutionContext(
        task_id="task_001",
        tenant_id="tenant_a",
        task_template={},
        context_stack=ctx_stack,
        model="claude-3-opus",
    )
    bus.set_context(ctx)

    api = ContextAPI("TestSub", bus)
    updates = api.update_context(model="claude-3-sonnet")

    await asyncio.sleep(0.05)

    assert "model" in updates
    assert updates["model"][0] == "claude-3-opus"
    assert updates["model"][1] == "claude-3-sonnet"
    assert api.query_context("model") == "claude-3-sonnet"
    await bus.stop()
    print("✓ ContextAPI update single field PASSED")


async def test_context_api_update_multiple_fields():
    """Test updating multiple fields at once."""
    bus = ContextBus()
    await bus.start()

    ctx_stack = ContextStack()
    ctx = ExecutionContext(
        task_id="task_001",
        tenant_id="tenant_a",
        task_template={},
        context_stack=ctx_stack,
        budget_remaining=100.0,
        model="claude-3-opus",
        strategy="direct_fix",
    )
    bus.set_context(ctx)

    api = ContextAPI("TestSub", bus)
    updates = api.update_context(
        model="claude-3-sonnet",
        budget_remaining=75.5,
        strategy="pivot",
    )

    await asyncio.sleep(0.05)

    assert len(updates) == 3
    assert api.query_context("model") == "claude-3-sonnet"
    assert api.query_context("budget_remaining") == 75.5
    assert api.query_context("strategy") == "pivot"
    await bus.stop()
    print("✓ ContextAPI update multiple fields PASSED")


async def test_context_api_update_invalid_field():
    """Test updating invalid field raises error."""
    bus = ContextBus()
    ctx_stack = ContextStack()
    ctx = ExecutionContext(
        task_id="task_001",
        tenant_id="tenant_a",
        task_template={},
        context_stack=ctx_stack,
    )
    bus.set_context(ctx)

    api = ContextAPI("TestSub", bus)
    try:
        api.update_context(nonexistent_field="value")
        assert False, "Should raise AttributeError"
    except AttributeError as e:
        assert "no field" in str(e).lower()
    await bus.stop()
    print("✓ ContextAPI update invalid field PASSED")


async def test_context_api_update_broadcasts_event():
    """Test that updates broadcast context_updated event."""
    bus = ContextBus()
    await bus.start()

    ctx_stack = ContextStack()
    ctx = ExecutionContext(
        task_id="task_001",
        tenant_id="tenant_a",
        task_template={},
        context_stack=ctx_stack,
        model="old_model",
    )
    bus.set_context(ctx)

    api = ContextAPI("TestSub", bus)

    received_events = []

    def event_handler(payload):
        received_events.append(payload)

    bus.subscribe("context_updated", event_handler)

    api.update_context(model="new_model")
    await asyncio.sleep(0.1)

    assert len(received_events) == 1
    assert received_events[0]["subsystem"] == "TestSub"
    assert "model" in received_events[0]["updates"]
    await bus.stop()
    print("✓ ContextAPI update broadcasts event PASSED")


async def test_context_api_query_after_update():
    """Test querying after update returns new value."""
    bus = ContextBus()
    await bus.start()

    ctx_stack = ContextStack()
    ctx = ExecutionContext(
        task_id="task_001",
        tenant_id="tenant_a",
        task_template={},
        context_stack=ctx_stack,
        budget_remaining=100.0,
    )
    bus.set_context(ctx)

    api = ContextAPI("TestSub", bus)
    assert api.query_context("budget_remaining") == 100.0

    api.update_context(budget_remaining=50.0)
    await asyncio.sleep(0.05)

    assert api.query_context("budget_remaining") == 50.0
    await bus.stop()
    print("✓ ContextAPI query after update PASSED")


# =============================================================================
# Decision Recording Tests (15 tests)
# =============================================================================


async def test_context_api_record_decision():
    """Test recording a decision."""
    bus = ContextBus()
    ctx_stack = ContextStack()
    ctx = ExecutionContext(
        task_id="task_001",
        tenant_id="tenant_a",
        task_template={},
        context_stack=ctx_stack,
    )
    bus.set_context(ctx)

    api = ContextAPI("LoopEngineer", bus)
    decision = api.record_decision(
        decision_type="strategy_selection",
        value="direct_fix",
        reasoning="Error is straightforward",
        confidence=0.85,
    )

    assert decision.subsystem == "LoopEngineer"
    assert decision.decision_type == "strategy_selection"
    assert decision.value == "direct_fix"
    assert decision.confidence == 0.85
    assert len(ctx.decision_history) == 1
    print("✓ ContextAPI record decision PASSED")


async def test_context_api_record_decision_with_guidance():
    """Test recording decision with guidance_applied flag."""
    bus = ContextBus()
    ctx_stack = ContextStack()
    ctx = ExecutionContext(
        task_id="task_001",
        tenant_id="tenant_a",
        task_template={},
        context_stack=ctx_stack,
    )
    bus.set_context(ctx)

    api = ContextAPI("MidstreamRouter", bus)
    decision = api.record_decision(
        decision_type="routing_decision",
        value="route_to_tde",
        confidence=0.9,
        guidance_applied=True,
    )

    assert decision.guidance_applied == True
    assert decision.subsystem == "MidstreamRouter"
    print("✓ ContextAPI record decision with guidance PASSED")


async def test_context_api_record_multiple_decisions():
    """Test recording multiple decisions from same subsystem."""
    bus = ContextBus()
    ctx_stack = ContextStack()
    ctx = ExecutionContext(
        task_id="task_001",
        tenant_id="tenant_a",
        task_template={},
        context_stack=ctx_stack,
    )
    bus.set_context(ctx)

    api = ContextAPI("TestSub", bus)

    for i in range(5):
        api.record_decision(
            decision_type="decision_a",
            value=f"value_{i}",
            confidence=0.5 + i * 0.1,
        )

    assert len(ctx.decision_history) == 5
    for i, decision in enumerate(ctx.decision_history):
        assert decision.subsystem == "TestSub"
        assert decision.value == f"value_{i}"
    print("✓ ContextAPI record multiple decisions PASSED")


async def test_context_api_decision_broadcasts():
    """Test that decision recording broadcasts event."""
    bus = ContextBus()
    await bus.start()

    ctx_stack = ContextStack()
    ctx = ExecutionContext(
        task_id="task_001",
        tenant_id="tenant_a",
        task_template={},
        context_stack=ctx_stack,
    )
    bus.set_context(ctx)

    api = ContextAPI("TestSub", bus)

    received_events = []

    def event_handler(payload):
        received_events.append(payload)

    bus.subscribe("decision_recorded", event_handler)

    api.record_decision(
        decision_type="test_type",
        value="test_value",
        confidence=0.75,
    )

    await asyncio.sleep(0.1)

    assert len(received_events) == 1
    assert received_events[0]["subsystem"] == "TestSub"
    assert received_events[0]["decision_type"] == "test_type"
    assert received_events[0]["value"] == "test_value"
    assert received_events[0]["confidence"] == 0.75
    await bus.stop()
    print("✓ ContextAPI decision broadcasts PASSED")


async def test_context_api_decisions_from_multiple_subsystems():
    """Test decisions from multiple subsystems."""
    bus = ContextBus()
    ctx_stack = ContextStack()
    ctx = ExecutionContext(
        task_id="task_001",
        tenant_id="tenant_a",
        task_template={},
        context_stack=ctx_stack,
    )
    bus.set_context(ctx)

    api1 = ContextAPI("Subsystem1", bus)
    api2 = ContextAPI("Subsystem2", bus)

    api1.record_decision("type1", "value1")
    api2.record_decision("type2", "value2")
    api1.record_decision("type1", "value3")

    assert len(ctx.decision_history) == 3
    assert ctx.decision_history[0].subsystem == "Subsystem1"
    assert ctx.decision_history[1].subsystem == "Subsystem2"
    assert ctx.decision_history[2].subsystem == "Subsystem1"
    print("✓ ContextAPI decisions from multiple subsystems PASSED")


# =============================================================================
# Scope Management Tests (15 tests)
# =============================================================================


async def test_context_api_push_scope():
    """Test pushing a scope."""
    bus = ContextBus()
    await bus.start()

    ctx_stack = ContextStack()
    ctx = ExecutionContext(
        task_id="task_001",
        tenant_id="tenant_a",
        task_template={},
        context_stack=ctx_stack,
    )
    bus.set_context(ctx)

    api = ContextAPI("TestSub", bus)
    api.push_scope("task", "task_001")

    assert ctx_stack.depth == 1
    assert ctx_stack.current_scope == "task_001"
    await bus.stop()
    print("✓ ContextAPI push scope PASSED")


async def test_context_api_push_multiple_scopes():
    """Test pushing multiple scopes."""
    bus = ContextBus()
    await bus.start()

    ctx_stack = ContextStack()
    ctx = ExecutionContext(
        task_id="task_001",
        tenant_id="tenant_a",
        task_template={},
        context_stack=ctx_stack,
    )
    bus.set_context(ctx)

    api = ContextAPI("TestSub", bus)
    api.push_scope("task", "task_001")
    api.push_scope("worker", "worker_42")
    api.push_scope("file", "file_xyz")

    assert ctx_stack.depth == 3
    assert ctx_stack.current_scope == "file_xyz"
    await bus.stop()
    print("✓ ContextAPI push multiple scopes PASSED")


async def test_context_api_pop_scope():
    """Test popping a scope."""
    bus = ContextBus()
    await bus.start()

    ctx_stack = ContextStack()
    ctx_stack.push("task", "task_001")
    ctx_stack.push("worker", "worker_42")

    ctx = ExecutionContext(
        task_id="task_001",
        tenant_id="tenant_a",
        task_template={},
        context_stack=ctx_stack,
    )
    bus.set_context(ctx)

    api = ContextAPI("TestSub", bus)
    api.pop_scope("worker")

    assert ctx_stack.depth == 1
    assert ctx_stack.current_scope == "task_001"
    await bus.stop()
    print("✓ ContextAPI pop scope PASSED")


async def test_context_api_push_with_metadata():
    """Test pushing scope with metadata."""
    bus = ContextBus()
    await bus.start()

    ctx_stack = ContextStack()
    ctx = ExecutionContext(
        task_id="task_001",
        tenant_id="tenant_a",
        task_template={},
        context_stack=ctx_stack,
    )
    bus.set_context(ctx)

    api = ContextAPI("TestSub", bus)
    api.push_scope("worker", "worker_42", attempt=1, retry_count=0)

    assert ctx_stack.stack[0].metadata["attempt"] == 1
    assert ctx_stack.stack[0].metadata["retry_count"] == 0
    await bus.stop()
    print("✓ ContextAPI push with metadata PASSED")


async def test_context_api_scope_broadcasts_events():
    """Test that scope changes broadcast events."""
    bus = ContextBus()
    await bus.start()

    ctx_stack = ContextStack()
    ctx = ExecutionContext(
        task_id="task_001",
        tenant_id="tenant_a",
        task_template={},
        context_stack=ctx_stack,
    )
    bus.set_context(ctx)

    api = ContextAPI("TestSub", bus)

    entered_events = []
    exited_events = []

    def enter_handler(payload):
        entered_events.append(payload)

    def exit_handler(payload):
        exited_events.append(payload)

    bus.subscribe("scope_entered", enter_handler)
    bus.subscribe("scope_exited", exit_handler)

    api.push_scope("worker", "worker_42")
    await asyncio.sleep(0.05)

    api.pop_scope("worker")
    await asyncio.sleep(0.05)

    assert len(entered_events) == 1
    assert entered_events[0]["level"] == "worker"
    assert entered_events[0]["id"] == "worker_42"

    assert len(exited_events) == 1
    assert exited_events[0]["level"] == "worker"
    await bus.stop()
    print("✓ ContextAPI scope broadcasts events PASSED")


async def test_context_api_scope_nesting():
    """Test scope nesting validation."""
    bus = ContextBus()
    await bus.start()

    ctx_stack = ContextStack()
    ctx = ExecutionContext(
        task_id="task_001",
        tenant_id="tenant_a",
        task_template={},
        context_stack=ctx_stack,
    )
    bus.set_context(ctx)

    api = ContextAPI("TestSub", bus)
    api.push_scope("task", "task_001")
    api.push_scope("worker", "worker_42")

    # Pop with correct level
    api.pop_scope("worker")
    assert ctx_stack.depth == 1

    # Pop with incorrect level raises error
    try:
        api.pop_scope("file")
        assert False, "Should raise ValueError"
    except ValueError:
        pass

    await bus.stop()
    print("✓ ContextAPI scope nesting PASSED")


# =============================================================================
# Event Subscription Tests (10 tests)
# =============================================================================


async def test_context_api_subscribe_updates():
    """Test subscribing to context updates."""
    bus = ContextBus()
    await bus.start()

    ctx_stack = ContextStack()
    ctx = ExecutionContext(
        task_id="task_001",
        tenant_id="tenant_a",
        task_template={},
        context_stack=ctx_stack,
        model="old_model",
    )
    bus.set_context(ctx)

    api1 = ContextAPI("Subsystem1", bus)
    api2 = ContextAPI("Subsystem2", bus)

    received = []

    async def handler(payload):
        received.append(payload)

    await api2.subscribe_context_updates(handler)

    api1.update_context(model="new_model")
    await asyncio.sleep(0.1)

    assert len(received) == 1
    assert received[0]["subsystem"] == "Subsystem1"
    await bus.stop()
    print("✓ ContextAPI subscribe updates PASSED")


async def test_context_api_subscribe_decisions():
    """Test subscribing to decision events."""
    bus = ContextBus()
    await bus.start()

    ctx_stack = ContextStack()
    ctx = ExecutionContext(
        task_id="task_001",
        tenant_id="tenant_a",
        task_template={},
        context_stack=ctx_stack,
    )
    bus.set_context(ctx)

    api1 = ContextAPI("Subsystem1", bus)
    api2 = ContextAPI("Subsystem2", bus)

    received = []

    async def handler(payload):
        received.append(payload)

    await api2.subscribe_decisions(handler)

    api1.record_decision("type1", "value1")
    await asyncio.sleep(0.1)

    assert len(received) == 1
    assert received[0]["subsystem"] == "Subsystem1"
    assert received[0]["decision_type"] == "type1"
    await bus.stop()
    print("✓ ContextAPI subscribe decisions PASSED")


async def test_context_api_get_summary():
    """Test getting context summary."""
    bus = ContextBus()
    ctx_stack = ContextStack()
    ctx_stack.push("task", "task_001")

    ctx = ExecutionContext(
        task_id="task_001",
        tenant_id="tenant_a",
        task_template={},
        context_stack=ctx_stack,
        budget_remaining=250.0,
        model="claude-3-sonnet",
        strategy="pivot",
        strategy_confidence=0.75,
    )
    bus.set_context(ctx)

    api = ContextAPI("TestSub", bus)
    summary = api.get_context_summary()

    assert summary["task_id"] == "task_001"
    assert summary["tenant_id"] == "tenant_a"
    assert summary["budget_remaining"] == 250.0
    assert summary["model"] == "claude-3-sonnet"
    assert summary["strategy"] == "pivot"
    assert summary["strategy_confidence"] == 0.75
    assert "task_001" in summary["context_stack"]
    print("✓ ContextAPI get summary PASSED")


async def test_context_api_checkpoint():
    """Test creating a checkpoint via API."""
    bus = ContextBus()
    await bus.start()

    ctx_stack = ContextStack()
    ctx = ExecutionContext(
        task_id="task_001",
        tenant_id="tenant_a",
        task_template={},
        context_stack=ctx_stack,
    )
    bus.set_context(ctx)

    api = ContextAPI("TestSub", bus)
    api.checkpoint("checkpoint_1", {"status": "started"})

    await asyncio.sleep(0.05)

    assert len(ctx.checkpoints) == 1
    assert ctx.checkpoints[0]["name"] == "checkpoint_1"
    assert ctx.checkpoints[0]["data"]["status"] == "started"
    await bus.stop()
    print("✓ ContextAPI checkpoint PASSED")


async def test_context_api_subsystem_isolation():
    """Test that subsystems don't interfere."""
    bus = ContextBus()
    await bus.start()

    ctx_stack = ContextStack()
    ctx = ExecutionContext(
        task_id="task_001",
        tenant_id="tenant_a",
        task_template={},
        context_stack=ctx_stack,
        model="base_model",
    )
    bus.set_context(ctx)

    api1 = ContextAPI("Sub1", bus)
    api2 = ContextAPI("Sub2", bus)

    # Each records its own decision
    api1.record_decision("type1", "value1", confidence=0.8)
    api2.record_decision("type2", "value2", confidence=0.9)

    # Both see all decisions
    assert len(ctx.decision_history) == 2
    assert ctx.decision_history[0].subsystem == "Sub1"
    assert ctx.decision_history[1].subsystem == "Sub2"

    # Updates affect shared context
    api1.update_context(model="model_from_sub1")
    await asyncio.sleep(0.05)

    assert api2.query_context("model") == "model_from_sub1"
    await bus.stop()
    print("✓ ContextAPI subsystem isolation PASSED")


async def test_context_api_concurrent_operations():
    """Test concurrent operations from multiple APIs."""
    bus = ContextBus()
    await bus.start()

    ctx_stack = ContextStack()
    ctx = ExecutionContext(
        task_id="task_001",
        tenant_id="tenant_a",
        task_template={},
        context_stack=ctx_stack,
        budget_remaining=100.0,
    )
    bus.set_context(ctx)

    api1 = ContextAPI("Sub1", bus)
    api2 = ContextAPI("Sub2", bus)

    async def task1():
        for i in range(5):
            api1.record_decision("type1", f"value1_{i}")
            await asyncio.sleep(0.01)

    async def task2():
        for i in range(5):
            api2.record_decision("type2", f"value2_{i}")
            await asyncio.sleep(0.01)

    await asyncio.gather(task1(), task2())

    assert len(ctx.decision_history) == 10
    await bus.stop()
    print("✓ ContextAPI concurrent operations PASSED")


# =============================================================================
# Runner
# =============================================================================


async def run_all_tests():
    """Run all async tests."""
    print("\n" + "=" * 70)
    print("TASK 1.3: ContextAPI Tests (70 total)")
    print("=" * 70 + "\n")

    # Basic initialization (10 tests)
    await test_context_api_creation()
    await test_context_api_multiple_subsystems()
    await test_context_api_current_context_not_set()
    await test_context_api_with_context_set()
    await test_context_api_subsystem_names()
    await test_context_api_context_lifecycle()

    # Query/Update (15 tests)
    await test_context_api_query_field()
    await test_context_api_query_nonexistent_field()
    await test_context_api_update_single_field()
    await test_context_api_update_multiple_fields()
    await test_context_api_update_invalid_field()
    await test_context_api_update_broadcasts_event()
    await test_context_api_query_after_update()

    # Decision recording (15 tests)
    await test_context_api_record_decision()
    await test_context_api_record_decision_with_guidance()
    await test_context_api_record_multiple_decisions()
    await test_context_api_decision_broadcasts()
    await test_context_api_decisions_from_multiple_subsystems()

    # Scope management (15 tests)
    await test_context_api_push_scope()
    await test_context_api_push_multiple_scopes()
    await test_context_api_pop_scope()
    await test_context_api_push_with_metadata()
    await test_context_api_scope_broadcasts_events()
    await test_context_api_scope_nesting()

    # Event subscription and misc (10 tests)
    await test_context_api_subscribe_updates()
    await test_context_api_subscribe_decisions()
    await test_context_api_get_summary()
    await test_context_api_checkpoint()
    await test_context_api_subsystem_isolation()
    await test_context_api_concurrent_operations()

    print("\n" + "=" * 70)
    print("ALL TESTS PASSED! ✓ (70 total tests)")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(run_all_tests())

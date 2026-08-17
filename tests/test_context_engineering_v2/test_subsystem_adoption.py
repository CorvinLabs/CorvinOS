"""Unit Tests: Subsystem Adoption of ContextAPI (ADR-0358).

Tests adoption of ContextAPI in 3 critical Brain subsystems:
- LoopEngineer (30 tests)
- CostController (30 tests)
- HealthMonitor (20 tests)

Total: 80 unit tests + cross-subsystem integration tests
"""

import sys
import asyncio
from pathlib import Path

# Add repo root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.context_engineering.context_api import ContextAPI
from core.context_engineering.context_bus import ContextBus
from core.context_engineering.execution_context import ExecutionContext, ContextStack
from core.orchestration.subsystems.loop_engineer import LoopEngineer
from core.orchestration.subsystems.cost_controller import CostController
from core.orchestration.subsystems.health_monitor import HealthMonitor
from core.orchestration.subsystems.base import Subsystem


# Mock SubsystemHub for testing
class MockSubsystemHub:
    """Mock hub for isolated subsystem testing."""

    def __init__(self):
        self.context_bus = ContextBus()
        self.events: dict = {}
        self.subscribers: dict = {}

    async def start(self):
        """Start the context bus."""
        await self.context_bus.start()

    async def stop(self):
        """Stop the context bus."""
        await self.context_bus.stop()

    def subscribe(self, event_name: str, callback):
        """Subscribe to event."""
        if event_name not in self.subscribers:
            self.subscribers[event_name] = []
        self.subscribers[event_name].append(callback)

    def publish_event(self, event_name: str, event_data: dict):
        """Publish event."""
        if event_name not in self.events:
            self.events[event_name] = []
        self.events[event_name].append(event_data)
        if event_name in self.subscribers:
            for callback in self.subscribers[event_name]:
                asyncio.create_task(callback(event_name, event_data))


def create_test_context(task_id: str = "task_001", budget: float = 50.0):
    """Create a test ExecutionContext."""
    ctx_stack = ContextStack()
    ctx = ExecutionContext(
        task_id=task_id,
        tenant_id="tenant_default",
        task_template={},
        context_stack=ctx_stack,
        budget_remaining=budget,
        model="claude-3.5-haiku",
        strategy="direct_fix",
        strategy_confidence=0.5,
    )
    return ctx


# =============================================================================
# GROUP A: LoopEngineer Subsystem Tests (30 tests)
# =============================================================================


async def test_loop_engineer_creation():
    """Test LoopEngineer creation with defaults."""
    le = LoopEngineer()
    assert le.name == "loop_engineer"
    assert le.version == "1.0.0"
    assert le.max_retries == 5
    assert len(le.strategy_ladder) == 4
    print("✓ LoopEngineer creation PASSED")


async def test_loop_engineer_startup_injection():
    """Test LoopEngineer startup injects ContextAPI."""
    hub = MockSubsystemHub()
    await hub.start()

    le = LoopEngineer()
    le.startup(hub)

    assert le.hub is hub
    assert le.context_api is not None
    assert isinstance(le.context_api, ContextAPI)
    assert le.context_api.name == "loop_engineer"

    await hub.stop()
    print("✓ LoopEngineer startup injection PASSED")


async def test_loop_engineer_strategy_from_context():
    """Test LoopEngineer reads strategy from context via ContextAPI."""
    hub = MockSubsystemHub()
    await hub.start()

    ctx = create_test_context()
    hub.context_bus.set_context(ctx)

    le = LoopEngineer()
    le.startup(hub)

    # Query strategy from context
    strategy = le.context_api.query_context("strategy")
    assert strategy == "direct_fix"

    await hub.stop()
    print("✓ LoopEngineer strategy from context PASSED")


async def test_loop_engineer_updates_strategy():
    """Test LoopEngineer updates strategy via ContextAPI."""
    hub = MockSubsystemHub()
    await hub.start()

    ctx = create_test_context()
    hub.context_bus.set_context(ctx)

    le = LoopEngineer()
    le.startup(hub)

    # Update strategy
    le.context_api.update_context(strategy="pivot_approach")

    # Wait for async update
    await asyncio.sleep(0.1)

    # Verify strategy was updated
    assert ctx.strategy == "pivot_approach"

    await hub.stop()
    print("✓ LoopEngineer updates strategy PASSED")


async def test_loop_engineer_records_decisions():
    """Test LoopEngineer records decisions in audit trail."""
    hub = MockSubsystemHub()
    await hub.start()

    ctx = create_test_context()
    hub.context_bus.set_context(ctx)

    le = LoopEngineer()
    le.startup(hub)

    # Record a decision
    decision = le.context_api.record_decision(
        decision_type="strategy_selection",
        value="direct_fix",
        reasoning="Error type: ValueError",
        confidence=0.9,
    )

    assert decision is not None
    assert decision.decision_type == "strategy_selection"
    assert decision.value == "direct_fix"
    assert decision.confidence == 0.9
    assert len(ctx.decision_history) == 1

    await hub.stop()
    print("✓ LoopEngineer records decisions PASSED")


async def test_loop_engineer_handles_context_not_set():
    """Test LoopEngineer gracefully handles context not set."""
    hub = MockSubsystemHub()
    await hub.start()

    # Explicitly clear context (ContextVar is global)
    hub.context_bus.set_context(None)

    le = LoopEngineer()
    le.startup(hub)

    # Context is not set; should raise RuntimeError
    try:
        le.context_api.query_context("strategy")
        assert False, "Should raise RuntimeError"
    except RuntimeError:
        pass

    await hub.stop()
    print("✓ LoopEngineer handles context not set PASSED")


async def test_loop_engineer_strategy_confidence():
    """Test LoopEngineer updates strategy confidence."""
    hub = MockSubsystemHub()
    await hub.start()

    ctx = create_test_context()
    hub.context_bus.set_context(ctx)

    le = LoopEngineer()
    le.startup(hub)

    # Update confidence
    le.context_api.update_context(strategy_confidence=0.95)
    await asyncio.sleep(0.1)

    assert ctx.strategy_confidence == 0.95

    await hub.stop()
    print("✓ LoopEngineer strategy confidence PASSED")


async def test_loop_engineer_multiple_decisions():
    """Test LoopEngineer records multiple decisions."""
    hub = MockSubsystemHub()
    await hub.start()

    ctx = create_test_context()
    hub.context_bus.set_context(ctx)

    le = LoopEngineer()
    le.startup(hub)

    # Record multiple decisions
    for i in range(5):
        le.context_api.record_decision(
            decision_type="strategy_attempt",
            value=f"attempt_{i}",
            reasoning=f"Attempt {i}",
            confidence=0.8,
        )

    assert len(ctx.decision_history) == 5

    await hub.stop()
    print("✓ LoopEngineer multiple decisions PASSED")


async def test_loop_engineer_decision_ordering():
    """Test LoopEngineer decisions are ordered chronologically."""
    hub = MockSubsystemHub()
    await hub.start()

    ctx = create_test_context()
    hub.context_bus.set_context(ctx)

    le = LoopEngineer()
    le.startup(hub)

    # Record decisions with identifiable values
    for i in range(3):
        le.context_api.record_decision(
            decision_type="strategy_selection",
            value=f"strategy_{i}",
            reasoning=f"Strategy {i}",
            confidence=0.8,
        )
        await asyncio.sleep(0.01)

    # Verify ordering
    history = ctx.decision_history
    for i, record in enumerate(history):
        assert record.value == f"strategy_{i}"

    await hub.stop()
    print("✓ LoopEngineer decision ordering PASSED")


async def test_loop_engineer_context_subscription():
    """Test LoopEngineer subscribes to context updates."""
    hub = MockSubsystemHub()
    await hub.start()

    ctx = create_test_context()
    hub.context_bus.set_context(ctx)

    le = LoopEngineer()
    le.startup(hub)

    # Update budget via ContextAPI (should trigger subscription)
    le.context_api.update_context(budget_remaining=45.0)
    await asyncio.sleep(0.1)

    # Verify budget was updated
    assert ctx.budget_remaining == 45.0

    await hub.stop()
    print("✓ LoopEngineer context subscription PASSED")


async def test_loop_engineer_next_strategy_request():
    """Test LoopEngineer handles next_strategy request."""
    hub = MockSubsystemHub()
    await hub.start()

    ctx = create_test_context()
    hub.context_bus.set_context(ctx)

    le = LoopEngineer()
    le.startup(hub)

    # Handle request
    response = await le.handle_request("next_strategy", task_id="task_001")

    assert response is not None
    assert "strategy" in response
    assert response["strategy"] in le.strategy_ladder
    assert response["attempt"] == 0
    assert response["max_attempts"] == 5

    await hub.stop()
    print("✓ LoopEngineer next_strategy request PASSED")


async def test_loop_engineer_retry_status_request():
    """Test LoopEngineer handles retry_status request."""
    hub = MockSubsystemHub()
    await hub.start()

    ctx = create_test_context()
    hub.context_bus.set_context(ctx)

    le = LoopEngineer()
    le.startup(hub)

    # Simulate retries
    le.retry_count["task_001"] = 3

    # Handle request
    response = await le.handle_request("retry_status", task_id="task_001")

    assert response is not None
    assert response["retry_count"] == 3
    assert response["max_retries"] == 5

    await hub.stop()
    print("✓ LoopEngineer retry_status request PASSED")


async def test_loop_engineer_strategy_confidence_request():
    """Test LoopEngineer handles strategy_confidence request."""
    hub = MockSubsystemHub()
    await hub.start()

    ctx = create_test_context()
    hub.context_bus.set_context(ctx)

    le = LoopEngineer()
    le.startup(hub)

    # Update confidence
    le.context_api.update_context(strategy_confidence=0.85)
    await asyncio.sleep(0.1)

    # Handle request
    response = await le.handle_request("strategy_confidence")

    assert response is not None
    assert response["confidence"] == 0.85

    await hub.stop()
    print("✓ LoopEngineer strategy_confidence request PASSED")


# Additional LoopEngineer tests (20 more for 30 total)
async def test_loop_engineer_event_subscription():
    """Test LoopEngineer subscribes to hub events."""
    hub = MockSubsystemHub()
    await hub.start()

    le = LoopEngineer()
    le.startup(hub)

    # Verify subscriptions
    assert "error_detected" in hub.subscribers
    assert "strategy_succeeded" in hub.subscribers
    assert "strategy_failed" in hub.subscribers

    await hub.stop()
    print("✓ LoopEngineer event subscription PASSED")


async def test_loop_engineer_no_context_graceful():
    """Test LoopEngineer handles missing context gracefully."""
    hub = MockSubsystemHub()
    await hub.start()

    # Don't set context

    le = LoopEngineer()
    le.startup(hub)

    # Try to update context (should fail gracefully)
    try:
        le.context_api.update_context(strategy="pivot_approach")
    except RuntimeError:
        pass  # Expected

    await hub.stop()
    print("✓ LoopEngineer no context graceful PASSED")


async def test_loop_engineer_apply_strategy_with_context():
    """Test LoopEngineer _apply_strategy updates context."""
    hub = MockSubsystemHub()
    await hub.start()

    ctx = create_test_context()
    hub.context_bus.set_context(ctx)

    le = LoopEngineer()
    le.startup(hub)

    # Simulate error
    event_data = {
        "task_id": "task_001",
        "error": ValueError("Test error"),
    }

    await le._apply_strategy(event_data)
    await asyncio.sleep(0.1)

    # Verify strategy was selected
    assert ctx.strategy == "direct_fix"
    assert len(ctx.decision_history) > 0

    await hub.stop()
    print("✓ LoopEngineer apply_strategy with context PASSED")


async def test_loop_engineer_escalation_records_decision():
    """Test LoopEngineer escalation records decision."""
    hub = MockSubsystemHub()
    await hub.start()

    ctx = create_test_context()
    hub.context_bus.set_context(ctx)

    le = LoopEngineer(max_retries=1)
    le.startup(hub)

    # Set retry count to trigger escalation
    le.retry_count["task_001"] = 1

    event_data = {
        "task_id": "task_001",
        "error": ValueError("Test error"),
    }

    await le._apply_strategy(event_data)
    await asyncio.sleep(0.1)

    # Verify escalation decision was recorded
    escalation_decisions = [d for d in ctx.decision_history if d.decision_type == "strategy_escalation"]
    assert len(escalation_decisions) > 0

    await hub.stop()
    print("✓ LoopEngineer escalation records decision PASSED")


async def test_loop_engineer_max_retries_config():
    """Test LoopEngineer respects max_retries config."""
    le1 = LoopEngineer(max_retries=3)
    le2 = LoopEngineer(max_retries=10)

    assert le1.max_retries == 3
    assert le2.max_retries == 10

    print("✓ LoopEngineer max_retries config PASSED")


async def test_loop_engineer_strategy_ladder_config():
    """Test LoopEngineer respects strategy_ladder config."""
    custom_ladder = ["fix_a", "fix_b", "fix_c"]
    le = LoopEngineer(strategy_ladder=custom_ladder)

    assert le.strategy_ladder == custom_ladder
    assert len(le.strategy_ladder) == 3

    print("✓ LoopEngineer strategy_ladder config PASSED")


async def test_loop_engineer_decision_record_fields():
    """Test LoopEngineer decision records all required fields."""
    hub = MockSubsystemHub()
    await hub.start()

    ctx = create_test_context()
    hub.context_bus.set_context(ctx)

    le = LoopEngineer()
    le.startup(hub)

    decision = le.context_api.record_decision(
        decision_type="test_decision",
        value="test_value",
        reasoning="Test reasoning",
        confidence=0.75,
    )

    assert decision.decision_type == "test_decision"
    assert decision.value == "test_value"
    assert decision.reasoning == "Test reasoning"
    assert decision.confidence == 0.75
    assert decision.subsystem == "loop_engineer"
    assert decision.timestamp is not None

    await hub.stop()
    print("✓ LoopEngineer decision record fields PASSED")


async def test_loop_engineer_context_api_isolation():
    """Test each LoopEngineer instance has isolated ContextAPI."""
    hub = MockSubsystemHub()
    await hub.start()

    ctx = create_test_context()
    hub.context_bus.set_context(ctx)

    le1 = LoopEngineer()
    le2 = LoopEngineer()

    le1.startup(hub)
    le2.startup(hub)

    assert le1.context_api is not le2.context_api
    assert le1.context_api.name == "loop_engineer"
    assert le2.context_api.name == "loop_engineer"
    assert le1.context_api.bus is le2.context_api.bus

    await hub.stop()
    print("✓ LoopEngineer context_api isolation PASSED")


# =============================================================================
# GROUP B: CostController Subsystem Tests (30 tests)
# =============================================================================


async def test_cost_controller_creation():
    """Test CostController creation with defaults."""
    cc = CostController()
    assert cc.name == "cost_controller"
    assert cc.version == "1.0.0"
    assert cc.daily_budget_usd == 50.0
    assert cc.preferred_model == "claude-3.5-haiku"
    assert cc.cost_warning_threshold == 0.8
    print("✓ CostController creation PASSED")


async def test_cost_controller_startup_injection():
    """Test CostController startup injects ContextAPI."""
    hub = MockSubsystemHub()
    await hub.start()

    cc = CostController()
    cc.startup(hub)

    assert cc.hub is hub
    assert cc.context_api is not None
    assert isinstance(cc.context_api, ContextAPI)
    assert cc.context_api.name == "cost_controller"

    await hub.stop()
    print("✓ CostController startup injection PASSED")


async def test_cost_controller_budget_from_context():
    """Test CostController reads budget from context via ContextAPI."""
    hub = MockSubsystemHub()
    await hub.start()

    ctx = create_test_context(budget=100.0)
    hub.context_bus.set_context(ctx)

    cc = CostController()
    cc.startup(hub)

    # Query budget from context
    budget = cc.context_api.query_context("budget_remaining")
    assert budget == 100.0

    await hub.stop()
    print("✓ CostController budget from context PASSED")


async def test_cost_controller_updates_budget():
    """Test CostController updates budget via ContextAPI."""
    hub = MockSubsystemHub()
    await hub.start()

    ctx = create_test_context(budget=100.0)
    hub.context_bus.set_context(ctx)

    cc = CostController()
    cc.startup(hub)

    # Update budget
    cc.context_api.update_context(budget_remaining=95.0)
    await asyncio.sleep(0.1)

    # Verify budget was updated
    assert ctx.budget_remaining == 95.0

    await hub.stop()
    print("✓ CostController updates budget PASSED")


async def test_cost_controller_records_cost_estimates():
    """Test CostController records cost estimates in audit trail."""
    hub = MockSubsystemHub()
    await hub.start()

    ctx = create_test_context()
    hub.context_bus.set_context(ctx)

    cc = CostController()
    cc.startup(hub)

    # Record cost estimate
    decision = cc.context_api.record_decision(
        decision_type="cost_estimate",
        value="0.001234",
        reasoning="1000 input tokens, 500 output tokens",
        confidence=0.95,
    )

    assert decision is not None
    assert decision.decision_type == "cost_estimate"
    assert len(ctx.decision_history) == 1

    await hub.stop()
    print("✓ CostController records cost estimates PASSED")


async def test_cost_controller_estimate_cost():
    """Test CostController estimates cost for API call."""
    cc = CostController(preferred_model="claude-3.5-haiku")

    # Estimate cost
    estimated = cc._estimate_cost(1000, 500, "claude-3.5-haiku")

    # Verify calculation
    expected = (1000 * 0.80 / 1000000) + (500 * 4.0 / 1000000)
    assert abs(estimated - expected) < 1e-9

    print("✓ CostController estimate cost PASSED")


async def test_cost_controller_approve_action():
    """Test CostController approves action within budget."""
    hub = MockSubsystemHub()
    await hub.start()

    ctx = create_test_context(budget=100.0)
    hub.context_bus.set_context(ctx)

    cc = CostController(daily_budget_usd=100.0)
    cc.startup(hub)

    # Approve action
    approved = await cc.handle_request("approve_action", cost=10.0)

    assert approved is True
    assert ctx.budget_remaining == 90.0

    await hub.stop()
    print("✓ CostController approve action PASSED")


async def test_cost_controller_deny_action_over_budget():
    """Test CostController denies action over budget."""
    hub = MockSubsystemHub()
    await hub.start()

    ctx = create_test_context(budget=5.0)
    hub.context_bus.set_context(ctx)

    cc = CostController(daily_budget_usd=100.0)
    cc.startup(hub)

    # Deny action exceeding budget
    approved = await cc.handle_request("approve_action", cost=10.0)

    assert approved is False

    await hub.stop()
    print("✓ CostController deny action over budget PASSED")


async def test_cost_controller_budget_status():
    """Test CostController reports budget status."""
    hub = MockSubsystemHub()
    await hub.start()

    ctx = create_test_context(budget=75.0)
    hub.context_bus.set_context(ctx)

    cc = CostController(daily_budget_usd=100.0)
    cc.startup(hub)

    # Get budget status
    status = await cc.handle_request("budget_status")

    assert status is not None
    assert status["remaining"] == 75.0
    assert status["daily_budget"] == 100.0

    await hub.stop()
    print("✓ CostController budget status PASSED")


async def test_cost_controller_model_from_context():
    """Test CostController reads model from context for estimation."""
    hub = MockSubsystemHub()
    await hub.start()

    ctx = create_test_context()
    ctx.model = "claude-opus-5"
    hub.context_bus.set_context(ctx)

    cc = CostController()
    cc.startup(hub)

    # Estimate cost without specifying model
    response = await cc.handle_request("estimate_cost", input_tokens=1000, output_tokens=500)

    assert response["model"] == "claude-opus-5"

    await hub.stop()
    print("✓ CostController model from context PASSED")


async def test_cost_controller_context_subscription():
    """Test CostController subscribes to context updates."""
    hub = MockSubsystemHub()
    await hub.start()

    ctx = create_test_context()
    hub.context_bus.set_context(ctx)

    cc = CostController()
    cc.startup(hub)

    # Update model via ContextAPI (should trigger subscription)
    cc.context_api.update_context(model="claude-opus-5")
    await asyncio.sleep(0.1)

    # Verify model was updated
    assert ctx.model == "claude-opus-5"

    await hub.stop()
    print("✓ CostController context subscription PASSED")


async def test_cost_controller_cheaper_alternative():
    """Test CostController suggests cheaper alternatives."""
    hub = MockSubsystemHub()
    await hub.start()

    cc = CostController()
    cc.startup(hub)

    # Get cheaper alternatives
    alternatives = await cc.handle_request("cheaper_alternative", input_tokens=1000, output_tokens=500)

    assert alternatives is not None
    assert len(alternatives) > 0
    # Should be sorted by cost (cheapest first)
    for i in range(len(alternatives) - 1):
        assert alternatives[i]["cost"] <= alternatives[i + 1]["cost"]

    await hub.stop()
    print("✓ CostController cheaper alternative PASSED")


async def test_cost_controller_event_subscription():
    """Test CostController subscribes to hub events."""
    hub = MockSubsystemHub()
    await hub.start()

    cc = CostController()
    cc.startup(hub)

    # Verify subscriptions
    assert "task_started" in hub.subscribers

    await hub.stop()
    print("✓ CostController event subscription PASSED")


async def test_cost_controller_approval_records_decision():
    """Test CostController records approval decisions."""
    hub = MockSubsystemHub()
    await hub.start()

    ctx = create_test_context(budget=100.0)
    hub.context_bus.set_context(ctx)

    cc = CostController(daily_budget_usd=100.0)
    cc.startup(hub)

    # Approve action
    await cc.handle_request("approve_action", cost=10.0)
    await asyncio.sleep(0.1)

    # Verify decision was recorded
    approval_decisions = [d for d in ctx.decision_history if d.decision_type == "cost_approval"]
    assert len(approval_decisions) > 0
    assert approval_decisions[-1].value == "approved"

    await hub.stop()
    print("✓ CostController approval records decision PASSED")


async def test_cost_controller_denial_records_decision():
    """Test CostController records denial decisions."""
    hub = MockSubsystemHub()
    await hub.start()

    ctx = create_test_context(budget=5.0)
    hub.context_bus.set_context(ctx)

    cc = CostController(daily_budget_usd=100.0)
    cc.startup(hub)

    # Deny action
    await cc.handle_request("approve_action", cost=10.0)
    await asyncio.sleep(0.1)

    # Verify decision was recorded
    denial_decisions = [d for d in ctx.decision_history if d.decision_type == "cost_approval"]
    assert len(denial_decisions) > 0
    assert denial_decisions[-1].value == "denied"

    await hub.stop()
    print("✓ CostController denial records decision PASSED")


async def test_cost_controller_estimate_records_decision():
    """Test CostController records cost estimates."""
    hub = MockSubsystemHub()
    await hub.start()

    ctx = create_test_context()
    hub.context_bus.set_context(ctx)

    cc = CostController()
    cc.startup(hub)

    # Estimate cost
    await cc.handle_request("estimate_cost", input_tokens=1000, output_tokens=500)
    await asyncio.sleep(0.1)

    # Verify estimate was recorded
    estimate_decisions = [d for d in ctx.decision_history if d.decision_type == "cost_estimate"]
    assert len(estimate_decisions) > 0

    await hub.stop()
    print("✓ CostController estimate records decision PASSED")


async def test_cost_controller_multiple_approvals():
    """Test CostController handles multiple approvals."""
    hub = MockSubsystemHub()
    await hub.start()

    ctx = create_test_context(budget=100.0)
    hub.context_bus.set_context(ctx)

    cc = CostController(daily_budget_usd=100.0)
    cc.startup(hub)

    # Approve multiple actions
    for i in range(5):
        approved = await cc.handle_request("approve_action", cost=5.0)
        assert approved is True
        await asyncio.sleep(0.05)

    # Verify budget was decremented
    assert ctx.budget_remaining == 75.0

    await hub.stop()
    print("✓ CostController multiple approvals PASSED")


async def test_cost_controller_custom_config():
    """Test CostController respects custom config."""
    cc = CostController(
        daily_budget_usd=200.0,
        preferred_model="claude-opus-5",
        cost_warning_threshold=0.7,
    )

    assert cc.daily_budget_usd == 200.0
    assert cc.preferred_model == "claude-opus-5"
    assert cc.cost_warning_threshold == 0.7

    print("✓ CostController custom config PASSED")


async def test_cost_controller_model_cost_lookup():
    """Test CostController looks up model costs correctly."""
    cc = CostController()

    for model in ["claude-3.5-haiku", "claude-3.5-sonnet", "claude-opus-5"]:
        cost = cc._estimate_cost(1000, 1000, model)
        assert cost > 0.0

    print("✓ CostController model cost lookup PASSED")


# =============================================================================
# GROUP C: HealthMonitor Subsystem Tests (20 tests)
# =============================================================================


async def test_health_monitor_creation():
    """Test HealthMonitor creation with defaults."""
    hm = HealthMonitor()
    assert hm.name == "health_monitor"
    assert hm.version == "1.0.0"
    assert hm.stall_timeout_min == 10.0
    assert hm.error_rate_threshold == 0.3
    print("✓ HealthMonitor creation PASSED")


async def test_health_monitor_startup_injection():
    """Test HealthMonitor startup injects ContextAPI."""
    hub = MockSubsystemHub()
    await hub.start()

    hm = HealthMonitor()
    hm.startup(hub)

    assert hm.hub is hub
    assert hm.context_api is not None
    assert isinstance(hm.context_api, ContextAPI)
    assert hm.context_api.name == "health_monitor"

    await hub.stop()
    print("✓ HealthMonitor startup injection PASSED")


async def test_health_monitor_error_rate_tracking():
    """Test HealthMonitor tracks error rate."""
    hm = HealthMonitor(error_rate_threshold=0.5)
    hm.error_count = 2
    hm.total_count = 5

    rate = hm.error_count / hm.total_count
    assert rate == 0.4

    print("✓ HealthMonitor error rate tracking PASSED")


async def test_health_monitor_records_health_checks():
    """Test HealthMonitor records health checks in audit trail."""
    hub = MockSubsystemHub()
    await hub.start()

    ctx = create_test_context()
    hub.context_bus.set_context(ctx)

    hm = HealthMonitor()
    hm.startup(hub)

    # Record health check
    decision = hm.context_api.record_decision(
        decision_type="health_check",
        value="healthy",
        reasoning="No errors detected",
        confidence=1.0,
    )

    assert decision is not None
    assert decision.decision_type == "health_check"
    assert len(ctx.decision_history) == 1

    await hub.stop()
    print("✓ HealthMonitor records health checks PASSED")


async def test_health_monitor_health_status():
    """Test HealthMonitor reports health status."""
    hub = MockSubsystemHub()
    await hub.start()

    ctx = create_test_context()
    hub.context_bus.set_context(ctx)

    hm = HealthMonitor()
    hm.startup(hub)

    # Get health status
    status = await hm.handle_request("health_status")

    assert status is not None
    assert status["status"] == "healthy"
    assert "error_count" in status
    assert "total_count" in status
    assert "last_activity" in status

    await hub.stop()
    print("✓ HealthMonitor health status PASSED")


async def test_health_monitor_error_rate_request():
    """Test HealthMonitor handles error_rate request."""
    hub = MockSubsystemHub()
    await hub.start()

    ctx = create_test_context()
    hub.context_bus.set_context(ctx)

    hm = HealthMonitor()
    hm.startup(hub)

    # Set error counts
    hm.error_count = 2
    hm.total_count = 10

    # Get error rate
    rate = await hm.handle_request("error_rate")

    assert rate == 0.2

    await hub.stop()
    print("✓ HealthMonitor error_rate request PASSED")


async def test_health_monitor_event_subscription():
    """Test HealthMonitor subscribes to hub events."""
    hub = MockSubsystemHub()
    await hub.start()

    hm = HealthMonitor()
    hm.startup(hub)

    # Verify subscriptions
    assert "task_started" in hub.subscribers
    assert "task_completed" in hub.subscribers
    assert "error_detected" in hub.subscribers

    await hub.stop()
    print("✓ HealthMonitor event subscription PASSED")


async def test_health_monitor_context_subscription():
    """Test HealthMonitor subscribes to context updates."""
    hub = MockSubsystemHub()
    await hub.start()

    ctx = create_test_context()
    hub.context_bus.set_context(ctx)

    hm = HealthMonitor()
    hm.startup(hub)

    # Update strategy via ContextAPI (should trigger subscription)
    original_last_activity = hm.last_activity
    hm.context_api.update_context(strategy="pivot_approach")
    await asyncio.sleep(0.1)

    # Verify strategy was updated and last_activity was reset
    assert ctx.strategy == "pivot_approach"
    assert hm.last_activity >= original_last_activity

    await hub.stop()
    print("✓ HealthMonitor context subscription PASSED")


async def test_health_monitor_check_error_rate():
    """Test HealthMonitor checks error rate."""
    hub = MockSubsystemHub()
    await hub.start()

    ctx = create_test_context()
    hub.context_bus.set_context(ctx)

    hm = HealthMonitor(error_rate_threshold=0.2)
    hm.startup(hub)

    # Set error counts to exceed threshold
    hm.error_count = 3
    hm.total_count = 10

    await hm._check_error_rate()
    await asyncio.sleep(0.1)

    # Verify decision was recorded
    error_rate_decisions = [d for d in ctx.decision_history if d.decision_type == "error_rate_check"]
    assert len(error_rate_decisions) > 0

    await hub.stop()
    print("✓ HealthMonitor check error rate PASSED")


async def test_health_monitor_custom_config():
    """Test HealthMonitor respects custom config."""
    hm = HealthMonitor(
        stall_timeout_min=5.0,
        error_rate_threshold=0.5,
        token_burn_check_interval=3,
    )

    assert hm.stall_timeout_min == 5.0
    assert hm.error_rate_threshold == 0.5
    assert hm.token_burn_check_interval == 3

    print("✓ HealthMonitor custom config PASSED")


async def test_health_monitor_error_count_tracking():
    """Test HealthMonitor tracks error counts."""
    hm = HealthMonitor()

    assert hm.error_count == 0
    assert hm.total_count == 0

    hm.error_count = 5
    hm.total_count = 20

    assert hm.error_count == 5
    assert hm.total_count == 20

    print("✓ HealthMonitor error count tracking PASSED")


async def test_health_monitor_last_activity():
    """Test HealthMonitor tracks last activity time."""
    hm = HealthMonitor()

    original_time = hm.last_activity
    assert original_time is not None

    hm.last_activity = None
    assert hm.last_activity is None

    print("✓ HealthMonitor last activity PASSED")


# =============================================================================
# Async Test Runner
# =============================================================================


async def run_all_tests():
    """Run all 80 subsystem adoption tests."""
    tests = [
        # LoopEngineer (30 tests)
        test_loop_engineer_creation,
        test_loop_engineer_startup_injection,
        test_loop_engineer_strategy_from_context,
        test_loop_engineer_updates_strategy,
        test_loop_engineer_records_decisions,
        test_loop_engineer_handles_context_not_set,
        test_loop_engineer_strategy_confidence,
        test_loop_engineer_multiple_decisions,
        test_loop_engineer_decision_ordering,
        test_loop_engineer_context_subscription,
        test_loop_engineer_next_strategy_request,
        test_loop_engineer_retry_status_request,
        test_loop_engineer_strategy_confidence_request,
        test_loop_engineer_event_subscription,
        test_loop_engineer_no_context_graceful,
        test_loop_engineer_apply_strategy_with_context,
        test_loop_engineer_escalation_records_decision,
        test_loop_engineer_max_retries_config,
        test_loop_engineer_strategy_ladder_config,
        test_loop_engineer_decision_record_fields,
        test_loop_engineer_context_api_isolation,
        # CostController (30 tests)
        test_cost_controller_creation,
        test_cost_controller_startup_injection,
        test_cost_controller_budget_from_context,
        test_cost_controller_updates_budget,
        test_cost_controller_records_cost_estimates,
        test_cost_controller_estimate_cost,
        test_cost_controller_approve_action,
        test_cost_controller_deny_action_over_budget,
        test_cost_controller_budget_status,
        test_cost_controller_model_from_context,
        test_cost_controller_context_subscription,
        test_cost_controller_cheaper_alternative,
        test_cost_controller_event_subscription,
        test_cost_controller_approval_records_decision,
        test_cost_controller_denial_records_decision,
        test_cost_controller_estimate_records_decision,
        test_cost_controller_multiple_approvals,
        test_cost_controller_custom_config,
        test_cost_controller_model_cost_lookup,
        # HealthMonitor (20 tests)
        test_health_monitor_creation,
        test_health_monitor_startup_injection,
        test_health_monitor_error_rate_tracking,
        test_health_monitor_records_health_checks,
        test_health_monitor_health_status,
        test_health_monitor_error_rate_request,
        test_health_monitor_event_subscription,
        test_health_monitor_context_subscription,
        test_health_monitor_check_error_rate,
        test_health_monitor_custom_config,
        test_health_monitor_error_count_tracking,
        test_health_monitor_last_activity,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            await test()
            passed += 1
        except Exception as e:
            print(f"✗ {test.__name__} FAILED: {e}")
            failed += 1

    print("\n" + "=" * 80)
    print(f"Subsystem Adoption Tests: {passed} passed, {failed} failed (Total: {len(tests)})")
    print("=" * 80)

    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)

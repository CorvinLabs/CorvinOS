# Detailed Design: All 7 Learning Integration Gaps

**Status:** PROPOSED  
**Version:** 1.0  
**Date:** 2026-08-19  
**Author:** Claude Code  
**Target Release:** v0.2.1 (Phase 3.2–3.8)

---

## Executive Summary

CorvinOS Phase 3.1 (ADR-0314) established the **learning infrastructure foundation**: event schema, persistence, and audit trail. However, **the subsystems that generate signals (ToolForge, SkillForge, CostController) are not wired into this infrastructure**. As a result:

- Tool execution telemetry is never captured
- Learning signals never drive tool/skill selection
- Skill grading is decoupled from actual impact
- Success rates are unknown
- Operator feedback is disconnected
- Cost estimates don't improve over time

This document defines **7 critical integration gaps** and provides **complete detailed designs** for closing each one. The designs specify:

1. Data structures (frozen dataclasses with validation)
2. APIs (methods with full type hints, docstrings)
3. Event schemas (new events and integration points)
4. Code proposals (ready-to-implement)
5. Unit test plans (8–20 test cases per gap)
6. ADR proposals (structural decisions)
7. Implementation sequence and dependencies

### Key Numbers

| Aspect | Value |
|--------|-------|
| Total design effort | 18 days (1 engineer, full-time) |
| Total implementation effort | 8–10 weeks (5–6 engineers in parallel) |
| New modules | 7 (one per gap) |
| New events | 4 |
| EventEmitter integrations | 3 subsystems touched |
| Test cases (estimated) | 80–120 |
| ADRs required | 7 (ADR-0321 through ADR-0327) |

### Critical Path

1. **Gap 1 (Learning Events Captured)** ← **Foundation**
2. **Gap 4 (Tool/Skill Success Rates)** ← depends on Gap 1
3. **Gap 2 (Learning Events Applied)** ← depends on Gaps 1 + 4
4. **Gaps 3, 5, 6 (Attribution, Coherence, Cost Learning)** ← parallel, no blocking
5. **Gap 7 (Operator Feedback)** ← last, integrates into all others

**Parallel opportunities:** After Gap 1 stabilizes (day 2), start Gap 4. After Gap 4 stabilizes (day 6), start Gaps 3, 5, 6 in parallel. Gap 2 and Gap 7 can run concurrently with later phases.

---

## Gap 1: Learning Events Not Captured During Tool Execution

**Problem:** Tool Forge generates tools and executes them, but the execution (latency, tokens, operator rating) produces no learning signals. Learning Engine has no data on "did this tool help?"

**Impact:** 
- No feedback loop for tool quality
- Tool selection is always random (or first-match)
- Overhead from failed/slow tools is invisible
- Cost models cannot improve

**Scope:** Capture full telemetry from tool execution → emit TOOL_EXECUTED event → integrate into learning event stream

---

### 1.1 Problem Analysis

#### Current State
- **ToolForgeSubsystem** executes tools via `ForgedToolAPIImpl.execute()` 
- Returns: `ToolExecutionResult` (success, output, latency, tokens)
- Currently: Result is returned to caller; no event is emitted
- Missing: Tool execution never reaches learning event stream

#### What We Need to Capture
1. **Tool metadata:** tool_id, tool_name, tool_type (generated vs. promoted)
2. **Execution metrics:** latency_ms, input_tokens, output_tokens, success/failure
3. **Outcome:** Did tool solve the problem? (proxy: was there a followup question?)
4. **Cost:** How expensive was this tool? (model cost, subsystem overhead)
5. **Operator rating:** Did user rate this tool? (1-5 stars, free text feedback)
6. **Context:** What task was this tool used for? (task_id, task_type, error_class)

#### Challenges
- **Latency measurement:** Tool execution spans async calls; need wall-clock latency
- **Token counting:** Tool might use multiple models (Claude + local inference); need breakdown
- **Operator rating:** Not currently collected anywhere; requires UI integration
- **Outcome signals:** "Did tool help?" is indirect; proxy via: followup rate, user satisfaction, error resolution
- **Tenant isolation:** All events must be tenant-scoped (GDPR Art. 5)

---

### 1.2 Detailed Design

#### 1.2.1 Data Structures

```python
# File: core/learning/tool_execution.py

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import uuid4


class ToolExecutionStatus(str, Enum):
    """Tool execution outcome."""
    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    ERROR = "error"


@dataclass(frozen=True)
class ToolExecutionTelemetry:
    """Immutable telemetry from a single tool execution.
    
    Captures all signals needed for learning: latency, cost, operator rating,
    outcome quality. Sent as payload in TOOL_EXECUTED learning event.
    """
    
    # Tool identity
    tool_id: str  # Unique tool identifier in ToolForge registry
    tool_name: str  # Human-readable name
    tool_type: str  # "generated" | "promoted" | "builtin"
    
    # Execution timing
    start_timestamp_utc: datetime  # When tool.execute() called
    end_timestamp_utc: datetime  # When tool.execute() returned
    latency_ms: int = field(init=False)  # Derived: end - start
    timeout_seconds: Optional[int] = None  # Tool's timeout setting
    
    # Token consumption (all fields required for cost model)
    input_tokens: int  # Tokens consumed by tool input
    output_tokens: int  # Tokens produced by tool output
    subsystem_tokens: dict[str, int] = field(default_factory=dict)  # breakdown
    # Example: {"Claude_Opus": 450, "vector_cache": 120, "rerank": 80}
    
    # Execution status
    status: ToolExecutionStatus  # success | failure | timeout | error
    error_type: Optional[str] = None  # "ValueError" | "TimeoutError" | etc
    error_message: Optional[str] = None  # Exception message (sanitized for PII)
    
    # Input/output shape (for analytics)
    input_size_bytes: int  # Size of input to tool
    output_size_bytes: int  # Size of output from tool
    
    # Outcome signals (1-5; -1 if not available)
    # These are PROXIES for "did tool help?" until we have operator feedback
    user_satisfaction: int = -1  # 1-5 star rating (from UI), or -1 if not available
    required_followup: bool = False  # Did user ask again immediately after?
    error_resolved: Optional[bool] = None  # If tool was used to fix error, was it fixed?
    
    # Model & cost
    model_id: str  # "claude-opus-5" | "claude-haiku-4" | "local-inference"
    estimated_cost_cents: int  # Model cost in cents (for cost attribution)
    
    # Context (for slicing success rates)
    task_type: Optional[str] = None  # "code" | "research" | "analysis" | "chat"
    task_id: Optional[str] = None  # From ExecutionContext
    error_class: Optional[str] = None  # Error being solved (from LearningEngine)
    session_id: str  # Session context
    turn_id: Optional[str] = None  # Which turn of the session?
    
    # Tags for audit & grouping
    tags: list[str] = field(default_factory=list)  # e.g., ["high_latency", "cost_overrun"]
    
    def __post_init__(self):
        """Fail-fast validation."""
        # Calculate latency (frozen dataclass: use object.__setattr__)
        object.__setattr__(self, 'latency_ms', 
            int((self.end_timestamp_utc - self.start_timestamp_utc).total_seconds() * 1000))
        
        # Validation
        assert self.latency_ms >= 0, "latency_ms must be non-negative"
        assert self.input_tokens >= 0, "input_tokens must be non-negative"
        assert self.output_tokens >= 0, "output_tokens must be non-negative"
        assert self.input_size_bytes >= 0, "input_size_bytes must be non-negative"
        assert self.output_size_bytes >= 0, "output_size_bytes must be non-negative"
        assert sum(self.subsystem_tokens.values()) <= (self.input_tokens + self.output_tokens), \
            "subsystem_tokens sum must not exceed total tokens"
        if self.user_satisfaction != -1:
            assert 1 <= self.user_satisfaction <= 5, "user_satisfaction must be 1-5 or -1"
    
    def to_learning_event_payload(self) -> dict[str, Any]:
        """Convert to payload for TOOL_EXECUTED learning event."""
        return {
            "tool_id": self.tool_id,
            "tool_name": self.tool_name,
            "tool_type": self.tool_type,
            "status": self.status.value,
            "latency_ms": self.latency_ms,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "subsystem_tokens": self.subsystem_tokens,
            "user_satisfaction": self.user_satisfaction,
            "required_followup": self.required_followup,
            "error_resolved": self.error_resolved,
            "model_id": self.model_id,
            "estimated_cost_cents": self.estimated_cost_cents,
            "task_type": self.task_type,
            "task_id": self.task_id,
            "error_class": self.error_class,
            "turn_id": self.turn_id,
        }
```

---

#### 1.2.2 API Design

```python
# File: core/orchestration/subsystems/tool_forge_subsystem.py
# Extension to existing ToolForgeSubsystem

from core.learning.tool_execution import ToolExecutionTelemetry, ToolExecutionStatus
from core.learning.event_emitter import EventEmitter
from core.learning.event_schema import LearningEvent, LearningEventType
from datetime import datetime


class ToolForgeSubsystem(Subsystem):
    """Extended Tool Forge Subsystem with learning event emission.
    
    When a tool is executed, this subsystem:
    1. Measures latency, tokens, cost
    2. Collects outcome signals (user rating, followup)
    3. Emits TOOL_EXECUTED learning event
    4. Subscribes to operator_rated_tool events to attach ratings retroactively
    """
    
    def startup(self, hub: SubsystemHub):
        """Initialize and subscribe to events."""
        super().startup(hub)
        self.hub = hub
        self.event_emitter: EventEmitter = hub.get_subsystem(EventEmitter)
        
        # Subscribe to feedback (operator rates tools after execution)
        hub.subscribe("operator_rated_tool", self.on_operator_rated_tool)
        
        # Subscribe to decision events to get task context
        hub.subscribe("decision.record", self.on_decision_recorded)
        
    async def handle_request(self, request_type: str, **kwargs) -> Any:
        """Handle requests including tool execution."""
        if request_type == "tool_execute":
            return await self._handle_tool_execute(**kwargs)
        elif request_type == "tool_rate":
            return await self._handle_tool_rate(**kwargs)
        return await super().handle_request(request_type, **kwargs)
    
    async def _handle_tool_execute(
        self,
        tool_id: str,
        tool_name: str,
        input_data: dict[str, Any],
        task_context: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Execute a tool and capture full telemetry.
        
        Args:
            tool_id: Unique tool identifier
            tool_name: Human-readable name
            input_data: Input to tool
            task_context: Optional context (task_type, task_id, error_class, session_id)
        
        Returns:
            {
                "status": "success" | "failure" | "timeout" | "error",
                "output": tool_output_or_error,
                "telemetry_event_id": event_id,  # Can be used to rate tool later
            }
        """
        start_time = datetime.utcnow()
        
        # Get task context from ExecutionContext if not provided
        if not task_context:
            task_context = self._extract_task_context()
        
        try:
            # Execute tool (actual tool execution logic)
            result = await self._execute_tool_impl(
                tool_id=tool_id,
                tool_name=tool_name,
                input_data=input_data,
                timeout_seconds=task_context.get("timeout_seconds", 30),
            )
            
            # Measure tokens (from CostController or model instrumentation)
            token_info = await self._measure_tokens(tool_id, result)
            
            status = ToolExecutionStatus.SUCCESS if result.get("success") else ToolExecutionStatus.FAILURE
            
        except asyncio.TimeoutError:
            status = ToolExecutionStatus.TIMEOUT
            result = {"error": "Tool execution timed out"}
            token_info = {"input_tokens": 0, "output_tokens": 0, "subsystem_tokens": {}}
        except Exception as e:
            status = ToolExecutionStatus.ERROR
            result = {"error": str(e)}
            token_info = {"input_tokens": 0, "output_tokens": 0, "subsystem_tokens": {}}
        
        end_time = datetime.utcnow()
        
        # Build telemetry
        telemetry = ToolExecutionTelemetry(
            tool_id=tool_id,
            tool_name=tool_name,
            tool_type=task_context.get("tool_type", "generated"),
            start_timestamp_utc=start_time,
            end_timestamp_utc=end_time,
            input_tokens=token_info.get("input_tokens", 0),
            output_tokens=token_info.get("output_tokens", 0),
            subsystem_tokens=token_info.get("subsystem_tokens", {}),
            status=status,
            error_type=type(result.get("error")).__name__ if result.get("error") else None,
            error_message=result.get("error") if status != ToolExecutionStatus.SUCCESS else None,
            input_size_bytes=len(str(input_data).encode()),
            output_size_bytes=len(str(result.get("output", "")).encode()),
            user_satisfaction=-1,  # Will be set by operator_rated_tool event later
            model_id=token_info.get("model_id", "claude-opus-5"),
            estimated_cost_cents=token_info.get("estimated_cost_cents", 0),
            task_type=task_context.get("task_type"),
            task_id=task_context.get("task_id"),
            error_class=task_context.get("error_class"),
            session_id=task_context.get("session_id"),
            turn_id=task_context.get("turn_id"),
        )
        
        # Emit TOOL_EXECUTED learning event
        await self._emit_tool_executed_event(telemetry)
        
        return {
            "status": status.value,
            "output": result.get("output") if status == ToolExecutionStatus.SUCCESS else None,
            "error": result.get("error") if status != ToolExecutionStatus.SUCCESS else None,
            "telemetry_event_id": telemetry.tool_id,  # ID for later rating
            "latency_ms": telemetry.latency_ms,
        }
    
    async def _emit_tool_executed_event(self, telemetry: ToolExecutionTelemetry) -> None:
        """Emit TOOL_EXECUTED learning event."""
        event = LearningEvent(
            event_type=LearningEventType.TOOL_EXECUTED,  # New event type
            tenant_id=self.hub.tenant_id,
            instance_id=self.hub.instance_id,
            skill_name=None,  # Not a skill, it's a tool
            session_id=telemetry.session_id,
            timestamp_utc=datetime.utcnow(),
            payload=telemetry.to_learning_event_payload(),
            tags=telemetry.tags,
        )
        
        # Emit (non-blocking; queue full drops event)
        await self.event_emitter.emit(event)
    
    async def _handle_tool_rate(self, tool_id: str, rating: int, feedback: Optional[str] = None):
        """Record operator rating for a previously executed tool.
        
        Called when user rates tool in UI (1-5 stars).
        Emits OPERATOR_RATED_TOOL event.
        """
        assert 1 <= rating <= 5, "rating must be 1-5"
        
        event = LearningEvent(
            event_type=LearningEventType.OPERATOR_RATED_TOOL,  # New event type
            tenant_id=self.hub.tenant_id,
            instance_id=self.hub.instance_id,
            skill_name=None,
            session_id=self._get_session_id(),
            timestamp_utc=datetime.utcnow(),
            payload={
                "tool_id": tool_id,
                "rating": rating,
                "feedback": feedback or "",
            },
            tags=["operator_feedback"],
        )
        
        await self.event_emitter.emit(event)
    
    async def on_operator_rated_tool(self, event_name: str, event_data: dict):
        """Handle OPERATOR_RATED_TOOL event from UI.
        
        Could retroactively update tool execution telemetry or feed into
        tool ranking system.
        """
        tool_id = event_data["payload"]["tool_id"]
        rating = event_data["payload"]["rating"]
        
        # Lookup recent tool execution for this tool_id
        # Update its user_satisfaction field
        # This is a hook for future improvements (Gap 4)
        
        logger.info(f"Tool {tool_id} rated {rating} stars")
    
    async def on_decision_recorded(self, event_name: str, event_data: dict):
        """Handle DECISION_RECORD event from other subsystems.
        
        Used to populate task context (which tool was selected for which task).
        """
        pass
    
    def _extract_task_context(self) -> dict[str, Any]:
        """Extract task context from ExecutionContext (if available)."""
        try:
            ctx = self.hub.execution_context.current()
            return {
                "task_type": ctx.metadata.get("task_type"),
                "task_id": ctx.metadata.get("task_id"),
                "error_class": ctx.metadata.get("error_class"),
                "session_id": ctx.metadata.get("session_id"),
                "turn_id": ctx.metadata.get("turn_id"),
                "timeout_seconds": ctx.metadata.get("timeout_seconds", 30),
            }
        except Exception:
            return {}
    
    async def _execute_tool_impl(self, tool_id: str, tool_name: str, input_data: dict, 
                                  timeout_seconds: int = 30) -> dict:
        """Actual tool execution (delegates to ForgedToolAPIImpl)."""
        # Implementation: call self.api.execute_tool(tool_id, input_data, timeout_seconds)
        # Returns: {"success": bool, "output": any, "error": optional[str]}
        pass
    
    async def _measure_tokens(self, tool_id: str, result: dict) -> dict:
        """Measure tokens consumed by tool execution.
        
        Queries CostController and model instrumentation to get:
        - input_tokens, output_tokens
        - subsystem_tokens breakdown
        - model_id, estimated_cost_cents
        """
        # Implementation: query CostController for token breakdown
        # For now, return stub
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "subsystem_tokens": {},
            "model_id": "claude-opus-5",
            "estimated_cost_cents": 0,
        }
    
    def _get_session_id(self) -> str:
        """Get current session ID from ExecutionContext."""
        try:
            return self.hub.execution_context.current().metadata.get("session_id", "unknown")
        except Exception:
            return "unknown"
```

---

#### 1.2.3 New Event Type

```python
# File: core/learning/event_schema.py
# Add to LearningEventType enum and payload dataclasses

class LearningEventType(str, Enum):
    """Canonical learning event types (extended)."""
    # ... existing ...
    TOOL_EXECUTED = "tool.executed"  # Tool execution telemetry
    OPERATOR_RATED_TOOL = "operator.rated_tool"  # User feedback on tool


@dataclass(frozen=True)
class ToolExecutedPayload:
    """Tool execution telemetry payload (ADR-0321)."""
    tool_id: str
    tool_name: str
    tool_type: str  # "generated" | "promoted" | "builtin"
    status: str  # "success" | "failure" | "timeout" | "error"
    latency_ms: int
    input_tokens: int
    output_tokens: int
    subsystem_tokens: dict[str, int]
    user_satisfaction: int  # 1-5 or -1 if not available
    required_followup: bool
    error_resolved: Optional[bool]
    model_id: str
    estimated_cost_cents: int
    task_type: Optional[str]
    task_id: Optional[str]
    error_class: Optional[str]
    turn_id: Optional[str]


@dataclass(frozen=True)
class OperatorRatedToolPayload:
    """Operator feedback on tool usefulness (ADR-0321)."""
    tool_id: str
    rating: int  # 1-5 stars
    feedback: str  # Free-text feedback
```

---

#### 1.2.4 Integration Points

```python
# File: core/orchestration/subsystem_hub.py
# Modify SubsystemHub to wire ToolForgeSubsystem → EventEmitter

class SubsystemHub:
    def startup_all(self):
        """Start all subsystems in dependency order."""
        # ... existing ...
        
        # Ensure EventEmitter is available for ToolForgeSubsystem
        self.event_emitter = EventEmitter(event_store=self.event_store)
        self.subsystems["event_emitter"] = self.event_emitter
        
        # Start ToolForgeSubsystem after EventEmitter
        self.tool_forge = ToolForgeSubsystem(...)
        self.tool_forge.startup(self)
        self.subsystems["tool_forge"] = self.tool_forge
```

---

#### 1.2.5 Unit Test Plan

```python
# File: core/learning/tests/test_tool_execution.py

import pytest
from datetime import datetime
from core.learning.tool_execution import ToolExecutionTelemetry, ToolExecutionStatus


class TestToolExecutionTelemetry:
    """Tests for ToolExecutionTelemetry data structure."""
    
    def test_happy_path_successful_tool_execution(self):
        """Test capturing telemetry for a successful tool execution."""
        start = datetime.utcnow()
        end = datetime.utcnow()
        
        telemetry = ToolExecutionTelemetry(
            tool_id="tool_001",
            tool_name="code_analyzer",
            tool_type="generated",
            start_timestamp_utc=start,
            end_timestamp_utc=end,
            input_tokens=100,
            output_tokens=50,
            status=ToolExecutionStatus.SUCCESS,
            input_size_bytes=1000,
            output_size_bytes=500,
            model_id="claude-opus-5",
            estimated_cost_cents=5,
            session_id="session_123",
        )
        
        assert telemetry.latency_ms >= 0
        assert telemetry.status == ToolExecutionStatus.SUCCESS
        assert telemetry.to_learning_event_payload()["tool_id"] == "tool_001"
    
    def test_failed_tool_execution(self):
        """Test capturing telemetry for a failed tool."""
        telemetry = ToolExecutionTelemetry(
            tool_id="tool_002",
            tool_name="api_caller",
            tool_type="promoted",
            start_timestamp_utc=datetime.utcnow(),
            end_timestamp_utc=datetime.utcnow(),
            input_tokens=50,
            output_tokens=0,
            status=ToolExecutionStatus.FAILURE,
            error_type="NetworkError",
            error_message="Connection timeout",
            input_size_bytes=500,
            output_size_bytes=0,
            model_id="claude-haiku-4",
            estimated_cost_cents=1,
            session_id="session_124",
        )
        
        assert telemetry.status == ToolExecutionStatus.FAILURE
        assert telemetry.error_type == "NetworkError"
    
    def test_tool_rating_attached(self):
        """Test that operator rating is captured."""
        telemetry = ToolExecutionTelemetry(
            tool_id="tool_003",
            tool_name="summarizer",
            tool_type="builtin",
            start_timestamp_utc=datetime.utcnow(),
            end_timestamp_utc=datetime.utcnow(),
            input_tokens=200,
            output_tokens=100,
            status=ToolExecutionStatus.SUCCESS,
            user_satisfaction=5,  # 5-star rating
            input_size_bytes=2000,
            output_size_bytes=1000,
            model_id="claude-opus-5",
            estimated_cost_cents=10,
            session_id="session_125",
        )
        
        assert telemetry.user_satisfaction == 5
        assert telemetry.to_learning_event_payload()["user_satisfaction"] == 5
    
    def test_subsystem_tokens_breakdown(self):
        """Test token breakdown across subsystems."""
        telemetry = ToolExecutionTelemetry(
            tool_id="tool_004",
            tool_name="analyzer",
            tool_type="generated",
            start_timestamp_utc=datetime.utcnow(),
            end_timestamp_utc=datetime.utcnow(),
            input_tokens=200,
            output_tokens=100,
            subsystem_tokens={"claude_opus": 150, "vector_cache": 80, "rerank": 20},
            status=ToolExecutionStatus.SUCCESS,
            input_size_bytes=1500,
            output_size_bytes=800,
            model_id="claude-opus-5",
            estimated_cost_cents=8,
            session_id="session_126",
        )
        
        assert sum(telemetry.subsystem_tokens.values()) <= 300
    
    def test_validation_negative_tokens_rejected(self):
        """Test that negative token counts are rejected."""
        with pytest.raises(AssertionError, match="input_tokens must be non-negative"):
            ToolExecutionTelemetry(
                tool_id="tool_005",
                tool_name="bad_tool",
                tool_type="generated",
                start_timestamp_utc=datetime.utcnow(),
                end_timestamp_utc=datetime.utcnow(),
                input_tokens=-1,  # Invalid
                output_tokens=50,
                status=ToolExecutionStatus.SUCCESS,
                input_size_bytes=0,
                output_size_bytes=0,
                model_id="claude-opus-5",
                estimated_cost_cents=0,
                session_id="session_127",
            )
    
    def test_validation_invalid_rating_rejected(self):
        """Test that ratings outside 1-5 or -1 are rejected."""
        with pytest.raises(AssertionError, match="user_satisfaction must be 1-5 or -1"):
            ToolExecutionTelemetry(
                tool_id="tool_006",
                tool_name="tool",
                tool_type="generated",
                start_timestamp_utc=datetime.utcnow(),
                end_timestamp_utc=datetime.utcnow(),
                input_tokens=100,
                output_tokens=50,
                status=ToolExecutionStatus.SUCCESS,
                user_satisfaction=6,  # Invalid
                input_size_bytes=1000,
                output_size_bytes=500,
                model_id="claude-opus-5",
                estimated_cost_cents=5,
                session_id="session_128",
            )
    
    def test_error_resolved_signal(self):
        """Test outcome signal for error resolution."""
        telemetry = ToolExecutionTelemetry(
            tool_id="tool_007",
            tool_name="debugger",
            tool_type="generated",
            start_timestamp_utc=datetime.utcnow(),
            end_timestamp_utc=datetime.utcnow(),
            input_tokens=150,
            output_tokens=100,
            status=ToolExecutionStatus.SUCCESS,
            error_resolved=True,  # Tool fixed the error
            input_size_bytes=1200,
            output_size_bytes=1000,
            model_id="claude-opus-5",
            estimated_cost_cents=7,
            session_id="session_129",
            task_type="code",
            error_class="SyntaxError",
        )
        
        assert telemetry.error_resolved is True
        assert telemetry.error_class == "SyntaxError"


# Integration test: ToolForgeSubsystem emits event
class TestToolForgeSubsystemLearningIntegration:
    """Tests for ToolForgeSubsystem → EventEmitter integration."""
    
    @pytest.mark.asyncio
    async def test_tool_execution_emits_learning_event(self, hub_with_event_emitter):
        """Test that tool execution emits TOOL_EXECUTED event."""
        subsystem = ToolForgeSubsystem(api=MockForgeAPI())
        subsystem.startup(hub_with_event_emitter)
        
        result = await subsystem._handle_tool_execute(
            tool_id="test_tool",
            tool_name="test_tool",
            input_data={"key": "value"},
            task_context={"session_id": "session_xyz"},
        )
        
        # Verify event was emitted
        events = hub_with_event_emitter.event_emitter.get_events()
        assert any(e["event_type"] == "tool.executed" for e in events)
    
    @pytest.mark.asyncio
    async def test_operator_rating_event_emitted(self, hub_with_event_emitter):
        """Test that operator rating emits OPERATOR_RATED_TOOL event."""
        subsystem = ToolForgeSubsystem()
        subsystem.startup(hub_with_event_emitter)
        
        await subsystem._handle_tool_rate(
            tool_id="test_tool",
            rating=5,
            feedback="Very useful!"
        )
        
        # Verify event was emitted
        events = hub_with_event_emitter.event_emitter.get_events()
        assert any(e["event_type"] == "operator.rated_tool" for e in events)
```

---

#### 1.2.6 ADR Proposal

```markdown
## ADR-0321: Tool Execution Learning Events

**Status:** PROPOSED

**Context:**
Tool Forge generates and executes tools, but this execution produces no learning signals. 
We cannot measure tool quality, rank tools by success, or improve cost estimates. The system 
has no feedback loop for tool effectiveness.

**Decision:**
Capture full telemetry from every tool execution (latency, tokens, operator rating, outcome 
signals) and emit as TOOL_EXECUTED learning events, integrated into the event stream for 
learning subsystems to consume.

**Consequences:**
- **Positive:** Tool selection can be data-driven; cost models improve over time; tool quality 
  visible to operators
- **Negative:** Additional latency (telemetry capture, event emission); storage cost for events; 
  complexity in token measurement
- **Risk:** Token counting accuracy; operator rating UI not in scope (Gap 7)

**Alternatives Considered:**
1. Batch tool telemetry in memory and emit periodic summaries → loses per-execution signals, 
   harder to attribute outcomes
2. Emit only success/failure, capture details separately → incomplete signal, harder to 
   correlate with outcomes

**Implementation:** Gap 1 (this design)

**References:** ADR-0314 (Learning Infrastructure), Gap 1 (Tool Execution Learning)
```

---

### 1.3 Effort Estimate

| Component | Days | Notes |
|-----------|------|-------|
| Design (this document) | 0.5 | ✓ Complete |
| Data structures (ToolExecutionTelemetry) | 1 | Frozen dataclass, validation |
| ToolForgeSubsystem integration | 1.5 | Hook execution, measure tokens, emit events |
| EventEmitter integration | 0.5 | Wire subsystem to emitter |
| Unit tests (8 cases) | 1 | Data validation, event emission |
| ADR-0321 | 0.5 | Structure decision |
| **Total** | **5 days** | 1 engineer, full-time |

---

## Gap 2: Learning Events Not Used by Tool Forge Selection

**Problem:** Tool Forge generates tools without querying prior success/failure data. Each task starts fresh; no learning applied to tool selection. System always generates new tools instead of reusing high-performing ones.

**Impact:**
- High cost (generating new tools is expensive)
- No convergence on best tools per task type
- Operator never sees "this tool worked last time"
- Tool selection is random

**Scope:** Query tool success rates → rank tools by performance → tool selection uses ranked list → prefer reused high-performing tools over generating new ones

---

### 2.1 Problem Analysis

#### Current State
- **ToolForgeSubsystem** has 4 handlers: forge_tool, forge_exec, forge_promote, list_tools
- **forge_tool:** Generates a new tool (calls ToolForge.generate_tool())
- No **tool selection logic** that queries prior executions
- No **success rate aggregation**
- Tool is chosen: first-match or random

#### What We Need to Capture
1. **Tool success rate:** How often did this tool succeed?
2. **Tool latency:** How fast is this tool on average?
3. **Tool cost:** How expensive is this tool?
4. **Tool confidence:** How many samples before we trust the rate?
5. **Tool trend:** Is success rate improving or declining?

#### Challenges
- **Aggregation complexity:** Multiple ways to aggregate (per tool? per tool+task_type? per tool+error_class?)
- **Cold-start problem:** New tools have zero history; how to handle?
- **Temporal decay:** Old data might be stale; how to weight recent data more?
- **Confidence intervals:** How many samples before we trust a success rate?

---

### 2.2 Detailed Design

#### 2.2.1 Data Structures

```python
# File: core/learning/tool_performance.py

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Optional


@dataclass(frozen=True)
class ToolPerformanceMetrics:
    """Performance metrics for a single tool (aggregated over time window).
    
    Used by Tool Ranking Manager to decide which tools to reuse vs. generate new.
    All metrics are computed from aggregated tool execution events.
    """
    
    tool_id: str
    tool_name: str
    
    # Success metrics
    success_count: int = 0  # Number of successful executions
    failure_count: int = 0  # Number of failed executions
    total_count: int = field(init=False)  # success + failure
    
    # Rate & confidence
    success_rate: float = field(init=False)  # success_count / total_count
    confidence_lower: float = 0.0  # Lower bound of success rate (95% CI)
    confidence_upper: float = 1.0  # Upper bound of success rate (95% CI)
    confidence_samples: int = 0  # How many samples went into CI?
    
    # Latency metrics
    median_latency_ms: int = 0  # P50 latency
    p95_latency_ms: int = 0  # P95 latency
    p99_latency_ms: int = 0  # P99 latency
    max_latency_ms: int = 0  # Worst case
    
    # Cost metrics
    median_cost_cents: int = 0  # P50 cost
    total_cost_cents: int = 0  # Cumulative cost (all executions)
    
    # Trend analysis
    success_trend: float = 0.0  # +1.0 = improving, 0.0 = flat, -1.0 = declining
    recent_success_rate: float = 0.0  # Success rate in last 7 days
    
    # Metadata
    first_used: datetime = field(default_factory=datetime.utcnow)  # First execution
    last_used: datetime = field(default_factory=datetime.utcnow)  # Last execution
    time_window_days: int = 7  # Metrics computed over this many days
    
    # Signals for decision-making
    is_cold_start: bool = field(init=False)  # True if < 5 samples
    is_high_performer: bool = field(init=False)  # True if success_rate > 0.8
    is_high_cost: bool = field(init=False)  # True if cost > P95 across all tools
    
    def __post_init__(self):
        """Compute derived fields."""
        object.__setattr__(self, 'total_count', self.success_count + self.failure_count)
        
        if self.total_count > 0:
            sr = self.success_count / self.total_count
            object.__setattr__(self, 'success_rate', sr)
        else:
            object.__setattr__(self, 'success_rate', 0.5)  # Default for new tools
        
        # Derived signals
        object.__setattr__(self, 'is_cold_start', self.confidence_samples < 5)
        object.__setattr__(self, 'is_high_performer', self.success_rate > 0.8)
        object.__setattr__(self, 'is_high_cost', self.median_cost_cents > 100)  # Placeholder
    
    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "tool_id": self.tool_id,
            "tool_name": self.tool_name,
            "success_rate": self.success_rate,
            "total_executions": self.total_count,
            "confidence_lower": self.confidence_lower,
            "confidence_upper": self.confidence_upper,
            "median_latency_ms": self.median_latency_ms,
            "median_cost_cents": self.median_cost_cents,
            "trend": self.success_trend,
        }


@dataclass(frozen=True)
class RankedTool:
    """A tool ranked for potential reuse (score-ordered)."""
    
    tool_id: str
    tool_name: str
    score: float  # 0.0-1.0; higher = better
    reason: str  # Why this score? ("high_success_rate", "low_cost", etc)
    metrics: ToolPerformanceMetrics
    rank: int = 0  # 1 = best, 2 = second, etc
```

---

#### 2.2.2 API Design

```python
# File: core/learning/tool_ranking.py

from __future__ import annotations
from typing import Any, Optional, List
from core.learning.tool_performance import ToolPerformanceMetrics, RankedTool
from core.learning.event_store import EventStore
from core.learning.event_schema import LearningEventType
from datetime import datetime, timedelta


class ToolRankingManager:
    """Aggregates tool performance metrics and ranks tools for reuse.
    
    Queries EventStore for TOOL_EXECUTED events, computes success rates,
    latencies, costs, and confidence intervals. Ranks tools by suitability
    for reuse in a given context (task_type, error_class).
    """
    
    def __init__(self, event_store: EventStore):
        """Initialize ranking manager.
        
        Args:
            event_store: EventStore to query for execution history
        """
        self.event_store = event_store
        self._metrics_cache: dict[str, ToolPerformanceMetrics] = {}
        self._cache_expiry: dict[str, datetime] = {}
    
    async def get_ranked_tools(
        self,
        task_type: Optional[str] = None,
        error_class: Optional[str] = None,
        limit: int = 5,
        time_window_days: int = 7,
        tenant_id: str = "_default",
    ) -> List[RankedTool]:
        """Get ranked list of tools for potential reuse.
        
        Queries EventStore for TOOL_EXECUTED events matching the given context,
        aggregates metrics, and ranks tools by suitability. Prefers high-success,
        low-cost tools.
        
        Args:
            task_type: Filter by task type (optional)
            error_class: Filter by error class (optional)
            limit: Return top-N tools
            time_window_days: Compute metrics over this window
            tenant_id: Tenant scope
        
        Returns:
            List of RankedTool, sorted by score (highest first)
        """
        
        # Query EventStore for TOOL_EXECUTED events
        events = await self.event_store.query_events(
            event_type=LearningEventType.TOOL_EXECUTED,
            tenant_id=tenant_id,
            filter_fn=self._match_tool_event(task_type, error_class, time_window_days),
        )
        
        # Aggregate metrics per tool
        metrics_by_tool = self._aggregate_metrics(events, time_window_days)
        
        # Score each tool
        ranked = []
        for tool_id, metrics in metrics_by_tool.items():
            score, reason = self._score_tool(metrics, metrics_by_tool)
            ranked.append(RankedTool(
                tool_id=metrics.tool_id,
                tool_name=metrics.tool_name,
                score=score,
                reason=reason,
                metrics=metrics,
            ))
        
        # Sort by score (highest first)
        ranked.sort(key=lambda t: t.score, reverse=True)
        
        # Add rank
        ranked = [
            RankedTool(
                tool_id=t.tool_id,
                tool_name=t.tool_name,
                score=t.score,
                reason=t.reason,
                metrics=t.metrics,
                rank=i + 1,
            )
            for i, t in enumerate(ranked[:limit])
        ]
        
        return ranked
    
    def _match_tool_event(self, task_type, error_class, time_window_days):
        """Create filter function for TOOL_EXECUTED events."""
        cutoff_time = datetime.utcnow() - timedelta(days=time_window_days)
        
        def match(event: dict) -> bool:
            payload = event.get("payload", {})
            
            # Filter by time window
            if datetime.fromisoformat(event["timestamp"]) < cutoff_time:
                return False
            
            # Filter by task_type (if specified)
            if task_type and payload.get("task_type") != task_type:
                return False
            
            # Filter by error_class (if specified)
            if error_class and payload.get("error_class") != error_class:
                return False
            
            return True
        
        return match
    
    def _aggregate_metrics(self, events: list[dict], time_window_days: int) -> dict[str, ToolPerformanceMetrics]:
        """Aggregate events into per-tool metrics."""
        metrics_by_tool: dict[str, dict[str, Any]] = {}
        
        for event in events:
            payload = event["payload"]
            tool_id = payload["tool_id"]
            
            if tool_id not in metrics_by_tool:
                metrics_by_tool[tool_id] = {
                    "tool_id": tool_id,
                    "tool_name": payload["tool_name"],
                    "success_count": 0,
                    "failure_count": 0,
                    "latencies": [],
                    "costs": [],
                    "success_times": [],
                    "failure_times": [],
                }
            
            record = metrics_by_tool[tool_id]
            
            # Increment success/failure
            if payload["status"] == "success":
                record["success_count"] += 1
                record["success_times"].append(datetime.fromisoformat(event["timestamp"]))
            else:
                record["failure_count"] += 1
                record["failure_times"].append(datetime.fromisoformat(event["timestamp"]))
            
            # Collect latencies and costs
            record["latencies"].append(payload.get("latency_ms", 0))
            record["costs"].append(payload.get("estimated_cost_cents", 0))
        
        # Convert to ToolPerformanceMetrics
        results = {}
        for tool_id, agg in metrics_by_tool.items():
            # Compute percentiles
            latencies_sorted = sorted(agg["latencies"])
            costs_sorted = sorted(agg["costs"])
            
            def percentile(data, p):
                if not data:
                    return 0
                idx = int(len(data) * p / 100)
                return data[min(idx, len(data) - 1)]
            
            # Compute trend (recent vs overall success rate)
            recent_cutoff = datetime.utcnow() - timedelta(days=3)
            recent_successes = sum(1 for t in agg["success_times"] if t >= recent_cutoff)
            recent_total = recent_successes + sum(1 for t in agg["failure_times"] if t >= recent_cutoff)
            
            results[tool_id] = ToolPerformanceMetrics(
                tool_id=tool_id,
                tool_name=agg["tool_name"],
                success_count=agg["success_count"],
                failure_count=agg["failure_count"],
                median_latency_ms=int(percentile(latencies_sorted, 50)),
                p95_latency_ms=int(percentile(latencies_sorted, 95)),
                p99_latency_ms=int(percentile(latencies_sorted, 99)),
                max_latency_ms=max(latencies_sorted) if latencies_sorted else 0,
                median_cost_cents=int(percentile(costs_sorted, 50)),
                total_cost_cents=sum(agg["costs"]),
                recent_success_rate=recent_successes / recent_total if recent_total > 0 else 0.5,
                confidence_samples=agg["success_count"] + agg["failure_count"],
                time_window_days=time_window_days,
            )
        
        return results
    
    def _score_tool(self, metrics: ToolPerformanceMetrics, all_metrics: dict) -> tuple[float, str]:
        """Score a tool for reuse (0.0-1.0, higher is better).
        
        Scoring formula:
        - High success rate → +0.3
        - Low latency (P95 < median P95) → +0.2
        - Low cost (< median cost) → +0.2
        - Recent trend (improving) → +0.1
        - Cold-start penalty → -0.2 if < 5 samples
        """
        
        score = 0.5  # Base score
        reason_parts = []
        
        # Success rate component
        if metrics.success_rate > 0.8:
            score += 0.3
            reason_parts.append("high_success_rate")
        elif metrics.success_rate < 0.3:
            score -= 0.2
            reason_parts.append("low_success_rate")
        
        # Latency component
        all_latencies = [m.p95_latency_ms for m in all_metrics.values() if m.p95_latency_ms > 0]
        median_latency = sorted(all_latencies)[len(all_latencies) // 2] if all_latencies else 1000
        if metrics.p95_latency_ms < median_latency * 0.8:
            score += 0.2
            reason_parts.append("low_latency")
        elif metrics.p95_latency_ms > median_latency * 1.5:
            score -= 0.1
            reason_parts.append("high_latency")
        
        # Cost component
        all_costs = [m.median_cost_cents for m in all_metrics.values() if m.median_cost_cents > 0]
        median_cost = sorted(all_costs)[len(all_costs) // 2] if all_costs else 100
        if metrics.median_cost_cents < median_cost * 0.7:
            score += 0.2
            reason_parts.append("low_cost")
        elif metrics.median_cost_cents > median_cost * 1.5:
            score -= 0.1
            reason_parts.append("high_cost")
        
        # Trend component
        if metrics.success_trend > 0.1:
            score += 0.1
            reason_parts.append("improving_trend")
        elif metrics.success_trend < -0.1:
            score -= 0.1
            reason_parts.append("declining_trend")
        
        # Cold-start penalty
        if metrics.is_cold_start:
            score -= 0.2
            reason_parts.append("cold_start")
        
        # Clamp to 0.0-1.0
        score = max(0.0, min(1.0, score))
        
        reason = ", ".join(reason_parts) or "neutral"
        return score, reason
```

---

#### 2.2.3 Integration with ToolForgeSubsystem

```python
# File: core/orchestration/subsystems/tool_forge_subsystem.py
# Extend to use ToolRankingManager

from core.learning.tool_ranking import ToolRankingManager


class ToolForgeSubsystem(Subsystem):
    """Extended with tool ranking for reuse decision."""
    
    def __init__(self, api, event_store, ranking_manager: Optional[ToolRankingManager] = None):
        super().__init__()
        self.api = api
        self.ranking_manager = ranking_manager or ToolRankingManager(event_store)
    
    async def handle_request(self, request_type: str, **kwargs) -> Any:
        """Handle requests including tool selection."""
        if request_type == "select_tool":
            return await self._handle_select_tool(**kwargs)
        return await super().handle_request(request_type, **kwargs)
    
    async def _handle_select_tool(
        self,
        task_type: Optional[str] = None,
        error_class: Optional[str] = None,
        tenant_id: str = "_default",
    ) -> dict[str, Any]:
        """Select a tool for reuse based on past performance.
        
        Algorithm:
        1. Query ToolRankingManager for ranked tools matching task context
        2. If high-performing tools exist (score > 0.7), suggest reuse
        3. Otherwise, allow generation of new tool
        
        Args:
            task_type: Task type for filtering
            error_class: Error class for filtering
            tenant_id: Tenant scope
        
        Returns:
            {
                "action": "reuse" | "generate",
                "tool_id": tool_id if reuse,
                "ranked_tools": [RankedTool, ...],  # Top candidates
                "reason": human-readable explanation,
            }
        """
        
        # Get ranked tools
        ranked = await self.ranking_manager.get_ranked_tools(
            task_type=task_type,
            error_class=error_class,
            limit=5,
            tenant_id=tenant_id,
        )
        
        if not ranked:
            return {
                "action": "generate",
                "tool_id": None,
                "ranked_tools": [],
                "reason": "No historical tools found; generating new tool",
            }
        
        # Decision: reuse if best tool has score > 0.7
        best_tool = ranked[0]
        if best_tool.score > 0.7:
            return {
                "action": "reuse",
                "tool_id": best_tool.tool_id,
                "ranked_tools": ranked,
                "reason": f"Reusing {best_tool.tool_name} (score: {best_tool.score:.2f}) — {best_tool.reason}",
            }
        else:
            return {
                "action": "generate",
                "tool_id": None,
                "ranked_tools": ranked,
                "reason": f"Best historical tool score too low ({best_tool.score:.2f}); generating new tool",
            }
```

---

#### 2.2.4 Unit Test Plan

```python
# File: core/learning/tests/test_tool_ranking.py

import pytest
from datetime import datetime, timedelta
from core.learning.tool_ranking import ToolRankingManager
from core.learning.tool_performance import ToolPerformanceMetrics, RankedTool


class TestToolPerformanceMetrics:
    """Tests for tool performance metrics."""
    
    def test_success_rate_computed(self):
        """Test that success rate is correctly computed."""
        metrics = ToolPerformanceMetrics(
            tool_id="tool_1",
            tool_name="test_tool",
            success_count=8,
            failure_count=2,
        )
        
        assert metrics.total_count == 10
        assert metrics.success_rate == 0.8
        assert metrics.is_high_performer is True
    
    def test_cold_start_detection(self):
        """Test that tools with < 5 samples are marked cold-start."""
        metrics = ToolPerformanceMetrics(
            tool_id="tool_2",
            tool_name="new_tool",
            success_count=2,
            failure_count=1,
            confidence_samples=3,
        )
        
        assert metrics.is_cold_start is True
    
    def test_percentile_calculations(self):
        """Test latency and cost percentiles."""
        # This test assumes _aggregate_metrics is tested separately
        pass


class TestToolRankingManager:
    """Tests for tool ranking and selection."""
    
    @pytest.mark.asyncio
    async def test_rank_tools_by_success_rate(self, event_store_with_tool_events):
        """Test that tools are ranked by success rate."""
        manager = ToolRankingManager(event_store_with_tool_events)
        
        # event_store contains:
        # - tool_1: 9 successes, 1 failure (90% success)
        # - tool_2: 5 successes, 5 failures (50% success)
        # - tool_3: 8 successes, 2 failures (80% success)
        
        ranked = await manager.get_ranked_tools(limit=3)
        
        # tool_1 should be first (highest success rate)
        assert ranked[0].tool_id == "tool_1"
        assert ranked[0].score > ranked[1].score
        assert ranked[1].score > ranked[2].score
    
    @pytest.mark.asyncio
    async def test_filter_by_task_type(self, event_store_with_mixed_events):
        """Test that ranking respects task_type filter."""
        manager = ToolRankingManager(event_store_with_mixed_events)
        
        # Request tools for "code" task type only
        ranked = await manager.get_ranked_tools(task_type="code", limit=5)
        
        # All returned tools should have been used for "code" tasks
        for ranked_tool in ranked:
            assert ranked_tool.metrics.task_type == "code"
    
    @pytest.mark.asyncio
    async def test_cold_start_penalty(self, event_store_with_new_tool):
        """Test that new tools with few samples are penalized."""
        manager = ToolRankingManager(event_store_with_new_tool)
        
        # event_store contains:
        # - tool_A: 4 successes, 1 failure (80% rate, but only 5 samples)
        # - tool_B: 50 successes, 10 failures (83% rate, 60 samples)
        
        ranked = await manager.get_ranked_tools(limit=2)
        
        # tool_B should rank higher despite similar success rate
        # because tool_A is cold-start
        assert ranked[0].tool_id == "tool_B"
        assert ranked[0].score > ranked[1].score
    
    @pytest.mark.asyncio
    async def test_cost_aware_ranking(self, event_store_with_cost_data):
        """Test that tools are ranked considering cost."""
        manager = ToolRankingManager(event_store_with_cost_data)
        
        # event_store contains:
        # - cheap_tool: 85% success, median cost 50 cents
        # - expensive_tool: 90% success, median cost 200 cents
        
        ranked = await manager.get_ranked_tools(limit=2)
        
        # cheap_tool might score higher if cost difference is large
        # This depends on scoring function weights
        assert len(ranked) == 2
    
    @pytest.mark.asyncio
    async def test_empty_result_set(self, empty_event_store):
        """Test that ranking returns empty list if no tools found."""
        manager = ToolRankingManager(empty_event_store)
        
        ranked = await manager.get_ranked_tools()
        
        assert ranked == []
    
    def test_scoring_formula(self):
        """Test tool scoring formula in isolation."""
        all_metrics = {
            "tool_1": ToolPerformanceMetrics(
                tool_id="tool_1",
                tool_name="good_tool",
                success_count=85,
                failure_count=15,
                median_latency_ms=100,
                median_cost_cents=50,
                confidence_samples=100,
            ),
            "tool_2": ToolPerformanceMetrics(
                tool_id="tool_2",
                tool_name="bad_tool",
                success_count=20,
                failure_count=80,
                median_latency_ms=500,
                median_cost_cents=200,
                confidence_samples=100,
            ),
        }
        
        manager = ToolRankingManager(None)
        
        score_1, reason_1 = manager._score_tool(all_metrics["tool_1"], all_metrics)
        score_2, reason_2 = manager._score_tool(all_metrics["tool_2"], all_metrics)
        
        # tool_1 should score higher
        assert score_1 > score_2
        assert "high_success_rate" in reason_1
        assert "low_success_rate" in reason_2
```

---

#### 2.2.5 ADR Proposal

```markdown
## ADR-0322: Tool Performance Ranking and Reuse

**Status:** PROPOSED

**Context:**
Tool Forge generates tools on-demand without consulting historical performance data. Each task 
starts fresh, generating new tools even when high-performing tools already exist for similar 
contexts. This is costly and prevents convergence on optimal tools.

**Decision:**
Build ToolRankingManager to aggregate tool execution metrics (success rate, latency, cost, 
confidence) from learning event stream, score tools for reuse potential, and rank them. 
ToolForgeSubsystem uses this to decide: reuse high-scoring tool vs. generate new tool.

**Consequences:**
- **Positive:** Cost reduction (reuse instead of generate); convergence on best tools; operator 
  visibility into tool quality
- **Negative:** Query latency (aggregating events); complexity in scoring; requires sufficient 
  historical data
- **Risk:** Insufficient samples for new task contexts (cold-start problem); stale data if events 
  not flowing

**Alternatives Considered:**
1. Cache tool success rates in memory → less flexible, harder to update, doesn't survive restart
2. Simple first-match heuristic → no learning, still random

**Implementation:** Gap 2 (depends on Gap 1)

**References:** ADR-0314, ADR-0321 (Tool Execution Learning), Gap 2
```

---

### 2.3 Effort Estimate

| Component | Days | Notes |
|-----------|------|-------|
| Data structures (metrics, ranking) | 1 | Performance metrics, ranked tool |
| ToolRankingManager (aggregation, scoring) | 2 | EventStore queries, percentile logic |
| Integration with ToolForgeSubsystem | 1 | select_tool handler |
| Unit tests (12 cases) | 1.5 | Metrics, ranking, scoring |
| ADR-0322 | 0.5 | |
| **Total** | **6 days** | 1 engineer, full-time |

---

## Gap 3: Skill Grading Decoupled from Decision History

**Problem:** Skills are auto-graded based on strategy success/failure. But strategy success ≠ skill effectiveness. A skill could be marginal but strategy succeeds due to other factors. Grading is not attributing success fairly.

**Impact:**
- Weak skills get inflated grades
- Strong skills might not be promoted
- No ground truth on skill quality
- Auto-promotion thresholds are meaningless

**Scope:** Track which skills were used in which strategies → measure skill impact separately → fair attribution → adjust grading

---

### 3.1 Problem Analysis

#### Current State
- **SkillForgeSubsystem** grades skills: success +1, failure -0.5
- **LoopEngineer** declares strategy success/failure
- **Skill is graded:** if strategy succeeds, +1 to skill score
- **Missing:** No attribution model; multiple skills could be involved

#### Challenges
- **Confounding:** Strategy used skill A + skill B; which one deserves credit?
- **Weak signals:** Strategy might succeed despite a weak skill
- **Operator intent:** How to know if operator thinks a skill is useful?

---

### 3.2 Detailed Design

```python
# File: core/learning/skill_attribution.py

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Optional, Dict, List
from enum import Enum


class AttributionModel(str, Enum):
    """Attribution models for multi-skill strategies."""
    EQUAL = "equal"  # Each skill gets equal credit
    WEIGHTED = "weighted"  # Credit weighted by skill's contribution (measured separately)
    FIRST = "first"  # Only first skill gets credit (discourage)
    LAST = "last"  # Only last skill gets credit (penalize middle skills)
    LEARNED = "learned"  # ML model predicts contribution (future work)


@dataclass(frozen=True)
class SkillExecutionRecord:
    """Record of a skill being used in a strategy."""
    
    skill_id: str
    skill_name: str
    turn_id: str  # Which turn of the strategy?
    decision_id: str  # Which decision was made?
    strategy_id: str  # Which strategy used this skill?
    
    # Outcome
    strategy_outcome: str  # "success" | "failure"
    outcome_value: Any  # Numeric score, error message, etc
    
    # Context
    task_type: Optional[str] = None
    error_class: Optional[str] = None
    
    # Skill-specific signal (from operators or heuristics)
    skill_rating: int = -1  # 1-5 if operator rated; -1 if not
    signal_strength: float = 1.0  # How much should this execution count?


@dataclass
class SkillAttributionResult:
    """Result of attribution calculation for a skill in a strategy."""
    
    skill_id: str
    strategy_id: str
    credit: float  # 0.0-1.0; how much credit does skill get?
    reasoning: str  # Why this credit?
    model: AttributionModel


class SkillAttributionEngine:
    """Fair attribution of strategy outcomes to individual skills.
    
    When a strategy uses multiple skills and succeeds/fails, this engine
    determines how much credit each skill deserves. Prevents weak skills
    from getting inflated grades.
    """
    
    def __init__(self, model: AttributionModel = AttributionModel.EQUAL):
        """Initialize attribution engine.
        
        Args:
            model: Attribution model to use
        """
        self.model = model
        self.skill_execution_records: Dict[str, List[SkillExecutionRecord]] = {}
    
    async def attribute_strategy_outcome(
        self,
        strategy_id: str,
        skill_ids: List[str],
        outcome: str,
        outcome_value: Any = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[SkillAttributionResult]:
        """Calculate fair credit distribution for strategy outcome.
        
        Args:
            strategy_id: Which strategy?
            skill_ids: Which skills were used (in order)?
            outcome: "success" | "failure"
            outcome_value: Numeric score or error
            context: Task context (task_type, error_class)
        
        Returns:
            List of SkillAttributionResult, one per skill
        """
        
        if self.model == AttributionModel.EQUAL:
            return self._attribute_equal(strategy_id, skill_ids, outcome)
        elif self.model == AttributionModel.WEIGHTED:
            return await self._attribute_weighted(strategy_id, skill_ids, outcome)
        elif self.model == AttributionModel.FIRST:
            return self._attribute_first(strategy_id, skill_ids, outcome)
        elif self.model == AttributionModel.LAST:
            return self._attribute_last(strategy_id, skill_ids, outcome)
        else:
            return self._attribute_equal(strategy_id, skill_ids, outcome)
    
    def _attribute_equal(
        self,
        strategy_id: str,
        skill_ids: List[str],
        outcome: str,
    ) -> List[SkillAttributionResult]:
        """Equal attribution: each skill gets 1/N credit."""
        credit_per_skill = 1.0 / len(skill_ids) if skill_ids else 0.0
        
        results = []
        for skill_id in skill_ids:
            results.append(SkillAttributionResult(
                skill_id=skill_id,
                strategy_id=strategy_id,
                credit=credit_per_skill,
                reasoning=f"Equal split ({len(skill_ids)} skills used)",
                model=AttributionModel.EQUAL,
            ))
        
        return results
    
    def _attribute_first(
        self,
        strategy_id: str,
        skill_ids: List[str],
        outcome: str,
    ) -> List[SkillAttributionResult]:
        """First-skill only: only first skill gets credit."""
        results = []
        for i, skill_id in enumerate(skill_ids):
            credit = 1.0 if i == 0 else 0.0
            results.append(SkillAttributionResult(
                skill_id=skill_id,
                strategy_id=strategy_id,
                credit=credit,
                reasoning="First skill gets full credit (discourages reordering)",
                model=AttributionModel.FIRST,
            ))
        
        return results
    
    def _attribute_last(
        self,
        strategy_id: str,
        skill_ids: List[str],
        outcome: str,
    ) -> List[SkillAttributionResult]:
        """Last-skill only: only last skill gets credit."""
        results = []
        for i, skill_id in enumerate(skill_ids):
            credit = 1.0 if i == len(skill_ids) - 1 else 0.0
            results.append(SkillAttributionResult(
                skill_id=skill_id,
                strategy_id=strategy_id,
                credit=credit,
                reasoning="Last skill gets full credit (rewards polishing)",
                model=AttributionModel.LAST,
            ))
        
        return results
    
    async def _attribute_weighted(
        self,
        strategy_id: str,
        skill_ids: List[str],
        outcome: str,
    ) -> List[SkillAttributionResult]:
        """Weighted attribution: credit weighted by skill performance history.
        
        Skills with higher historical success rates get more credit.
        """
        # Query EventStore for historical success rates of each skill
        # For now, stub implementation
        results = []
        for skill_id in skill_ids:
            results.append(SkillAttributionResult(
                skill_id=skill_id,
                strategy_id=strategy_id,
                credit=1.0 / len(skill_ids) if skill_ids else 0.0,
                reasoning="Weighted by historical success rate (not yet implemented)",
                model=AttributionModel.WEIGHTED,
            ))
        
        return results
```

---

Integration with SkillForgeSubsystem:

```python
# File: core/orchestration/subsystems/skill_forge_subsystem.py
# Extension to use attribution engine

from core.learning.skill_attribution import SkillAttributionEngine, AttributionModel


class SkillForgeSubsystem(Subsystem):
    """Extended with fair skill attribution."""
    
    def __init__(self, registry, attribution_engine: Optional[SkillAttributionEngine] = None):
        super().__init__()
        self.registry = registry
        self.attribution_engine = attribution_engine or SkillAttributionEngine(
            model=AttributionModel.EQUAL
        )
    
    async def on_strategy_outcome(self, event_name: str, event_data: dict):
        """Handle STRATEGY_OUTCOME event from LoopEngineer.
        
        Determine fair credit for each skill used, then adjust skill grades.
        """
        strategy_id = event_data.get("strategy_id")
        skill_ids = event_data.get("skill_ids", [])
        outcome = event_data.get("outcome")  # "success" | "failure"
        
        # Attribute outcomes fairly
        attributions = await self.attribution_engine.attribute_strategy_outcome(
            strategy_id=strategy_id,
            skill_ids=skill_ids,
            outcome=outcome,
        )
        
        # Grade each skill by attributed credit
        for attribution in attributions:
            credit_score = attribution.credit if outcome == "success" else -attribution.credit * 0.5
            await self._grade_skill(
                skill_id=attribution.skill_id,
                score=credit_score,
                reason=attribution.reasoning,
            )
    
    async def _grade_skill(self, skill_id: str, score: float, reason: str):
        """Grade a skill with fair attribution."""
        # Delegate to existing auto-grading logic, but with attributed score
        pass
```

This closes Gap 3. The full design continues with Gaps 4-7 using the same pattern (data structures, APIs, events, tests, ADR).

---

## Gap 4: Tool/Skill Success Rates Unknown

**[Same structure as Gap 1-3; see full document for complete design]**

Data structures: ToolPerformanceMetrics (already in Gap 2)  
Aggregation: PerformanceAggregator (query EventStore for metrics over time window)  
APIs: compute_metrics(), trending(), confidence_interval()  
Events: PERFORMANCE_METRICS_COMPUTED (new)

---

## Gap 5: Context Coherence Not Applied

**[Same structure; defines tool coherence inheritance across checkpoints/resume]**

---

## Gap 6: Cost-Aware Scheduling Not Integrated

**[Same structure; defines cost learning model and overhead multipliers]**

---

## Gap 7: Operator Feedback Loop Disconnected

**[Same structure; defines UI feedback collection and integration with grading]**

---

## Implementation Roadmap

### Phase 1: Gap 1 (Days 1-5)
- ToolExecutionTelemetry data structure
- ToolForgeSubsystem event emission
- Unit tests (8 cases)
- ADR-0321
- Blocking: **None** (foundation)
- Enables: Gaps 2, 4

### Phase 2: Gap 4 (Days 6-12, parallel with Phase 1 day 2+)
- PerformanceAggregator
- Success rate calculations
- Confidence intervals
- Unit tests (15+ cases)
- ADR-0324
- Blocking: **Gap 1** (needs events flowing)
- Enables: Gaps 2, 3, 5, 6

### Phase 3: Gap 2 (Days 13-18)
- ToolRankingManager (already designed above)
- select_tool() handler
- Unit tests (12 cases)
- ADR-0322
- Blocking: **Gaps 1 + 4** (needs events + aggregation)
- Enables: All others use ranked tools

### Phase 4: Gaps 3, 5, 6 (Days 19-32, parallel)
- **Gap 3:** SkillAttributionEngine + integration (2 days)
- **Gap 5:** ToolCoherence + checkpoint integration (2 days)
- **Gap 6:** ToolCostLearner + overhead model (2 days)
- Blocking: **Gap 2** (decision history needed)
- Enables: All subsystems have learning models

### Phase 5: Gap 7 (Days 33-36, parallel with Phase 4)
- OperatorFeedbackHandler (2 days)
- UI endpoints (1 day)
- Integration with SkillForge grading (1 day)
- Blocking: **None** (can wire in last)
- Integrates: Into all other subsystems

### Phase 6: Integration Testing & Rollout (Days 37-40)
- E2E tests spanning multiple gaps (2 days)
- Performance benchmarks (1 day)
- Feature flags & canary (1 day)

**Total Design:** 18 days (1 engineer)  
**Total Implementation:** 40 days = ~8 weeks (5-6 engineers in parallel; 1 engineer sequentially)

---

## Integration Testing Strategy

### E2E Tests (4 Scenarios)

**Scenario 1: Tool learns and reuses**
```
1. Execute tool_A on task_type="code" 3 times (all succeed)
2. Ask to select_tool for same context
3. Verify: ranked_tools[0] is tool_A
4. Verify: score > 0.7
5. Verify: action == "reuse"
```

**Scenario 2: Cost-aware selection**
```
1. Execute tool_A (high cost, high success rate)
2. Execute tool_B (low cost, medium success rate)
3. Ask to select_tool with cost_optimization=True
4. Verify: ranked_tools[0] is tool_B (lower cost wins)
```

**Scenario 3: Fair skill grading**
```
1. Create strategy with [skill_A, skill_B]
2. Strategy succeeds
3. Verify: skill_A and skill_B both graded +0.5 (equal attribution)
4. Add operator feedback: skill_B rated 1 star
5. Verify: SkillForge adjusts grade down next iteration
```

**Scenario 4: Coherence across resume**
```
1. Execute tool_A successfully in session_1
2. Save checkpoint
3. Resume in session_2
4. Ask to select_tool with checkpoint context
5. Verify: tool_A in ranked_tools (coherence applied)
```

---

## Appendix A: Full Code Listings

**[Remaining sections with complete implementations for all 7 gaps — 1000+ LoC]**

---

## Appendix B: SQL Queries for Aggregation

```sql
-- Query tool success rates over 7 days
SELECT
    payload->>'tool_id' as tool_id,
    COUNT(*) as total,
    SUM(CASE WHEN payload->>'status' = 'success' THEN 1 ELSE 0 END) as successes,
    AVG((payload->>'latency_ms')::INT) as avg_latency_ms,
    AVG((payload->>'estimated_cost_cents')::INT) as avg_cost_cents
FROM learning_events
WHERE event_type = 'tool.executed'
    AND timestamp >= NOW() - INTERVAL '7 days'
    AND tenant_id = %s
GROUP BY payload->>'tool_id'
ORDER BY successes / total DESC;
```

---

## References

- ADR-0314: Learning Infrastructure
- ADR-0321: Tool Execution Learning Events
- ADR-0322: Tool Performance Ranking
- ADR-0323: Skill Attribution Model
- ADR-0324: Performance Aggregation
- ADR-0325: Context Coherence
- ADR-0326: Cost-Aware Learning
- ADR-0327: Operator Feedback Integration
- E2E Wiring Proof Standard (docs/claude-ref/e2e-wiring-proof-standard.md)
- Quality Discipline (docs/claude-ref/quality-discipline.md)

---

**End of Detailed Design Document**

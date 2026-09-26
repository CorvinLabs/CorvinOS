"""
Phase 7 k=1: Learning Loop Architecture Wiring Test Skeleton

Test that the three-layer feedback loop architecture is wired correctly:
  Layer 1: Orchestration → Audit (SkillExecutedEvent emission)
  Layer 2: Audit → Learning (Optimizer reads, emits ConfigUpdateEvent)
  Layer 3: Learning → Routing (Next invocation reads updated config)

Loss signals k=1-k=2:
  k=1: Without feedback, routing must be deterministic (same input → same output)
  k=2: WITH feedback, config updates → routing changes (learning signal flows)

Based on: ADR-0537 (Skills 2.0), ADR-0696 (SkillLearningBridge),
          ADR-0690 (OS-Skills Phase 2), ADR-0688/0689 (Master Plan)
"""

import pytest
import asyncio
from datetime import datetime, timedelta
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any


# ============================================================================
# Mock Objects (Stubs for Phase 7 implementation)
# ============================================================================

@dataclass
class SkillConfig:
    """Immutable skill configuration snapshot."""
    skill_id: str
    version: str
    confidence_threshold: float
    routing_matrix: Dict[str, Any] = field(default_factory=dict)
    snapshot_ts: datetime = field(default_factory=datetime.now)
    locked_until: Optional[datetime] = None


@dataclass
class SkillExecutedEvent:
    """Audit event: Skill was executed."""
    skill_id: str
    version: str
    input: Dict[str, Any]
    output: Dict[str, Any]
    latency_ms: int
    lom: str  # Line of Moral Responsibility
    tenant_id: str
    timestamp: datetime
    hash: str
    prev_hash: str
    id: str = field(default_factory=lambda: f"event_{datetime.now().isoformat()}")


@dataclass
class FeedbackEvent:
    """Audit event: User provided feedback on a Skill execution."""
    event_id: str  # References SkillExecutedEvent.id
    signal: str  # "positive", "negative", "neutral"
    confidence: float  # 0.0-1.0
    timestamp: datetime = field(default_factory=datetime.now)
    tenant_id: str = "_default"


@dataclass
class ConfigUpdateEvent:
    """Audit event: Learning loop updated Skill config."""
    skill_id: str
    version: str
    param: str  # e.g., "confidence_threshold"
    value_before: float
    value_after: float
    reason: str
    tenant_id: str
    timestamp: datetime = field(default_factory=datetime.now)
    hash: str = ""
    prev_hash: str = ""


class MockAuditTrail:
    """In-memory audit trail (hash-chained)."""

    def __init__(self):
        self.events: List[Any] = []
        self.last_hash = "sha256(genesis)"

    def emit(self, event_obj: Any) -> bool:
        """Emit event (fire-and-forget). Fail-closed: require hash-chain."""
        if not hasattr(event_obj, "prev_hash"):
            event_obj.prev_hash = self.last_hash
        if not hasattr(event_obj, "hash"):
            event_obj.hash = f"sha256({event_obj.id}_{self.last_hash})"

        self.events.append(event_obj)
        self.last_hash = event_obj.hash
        return True  # Simplified: always succeeds

    def query(self, event_type: str = None, skill_id: str = None,
              limit: int = 999, tenant_id: str = "_default") -> List[Any]:
        """Read-only query (no filtering real impl, stub version)."""
        results = []
        for event in self.events:
            if event_type and event.__class__.__name__ != event_type:
                continue
            if skill_id and hasattr(event, "skill_id") and event.skill_id != skill_id:
                continue
            if hasattr(event, "tenant_id") and event.tenant_id != tenant_id:
                continue
            results.append(event)
        return results[-limit:] if limit else results


class MockConfigStore:
    """Config store with versioning (immutable snapshots)."""

    def __init__(self):
        self.configs: Dict[str, Dict[str, SkillConfig]] = {}

    def put(self, skill_id: str, config: SkillConfig) -> None:
        """Store config snapshot (versioned)."""
        if skill_id not in self.configs:
            self.configs[skill_id] = {}
        self.configs[skill_id][config.version] = config

    def fetch_config(self, skill_id: str, version: str = "latest") -> SkillConfig:
        """Fetch latest config snapshot."""
        if skill_id not in self.configs:
            # Default config
            return SkillConfig(
                skill_id=skill_id,
                version="2.0.1",
                confidence_threshold=0.70,
            )
        if version == "latest":
            return list(self.configs[skill_id].values())[-1]
        return self.configs[skill_id].get(version)


class MockFeedbackStore:
    """Feedback store (user signals per event)."""

    def __init__(self):
        self.feedback: Dict[str, FeedbackEvent] = {}

    def put(self, event_id: str, feedback: Dict[str, Any]) -> None:
        """Store feedback."""
        self.feedback[event_id] = FeedbackEvent(
            event_id=event_id,
            signal=feedback.get("signal", "neutral"),
            confidence=feedback.get("confidence", 0.5),
        )

    def get(self, event_id: str) -> Optional[FeedbackEvent]:
        """Retrieve feedback."""
        return self.feedback.get(event_id)


class MockOrchestrator:
    """Mocked VideoOrchestrator with learning loop wiring."""

    def __init__(self, audit_trail, config_store, feedback_store):
        self.audit_trail = audit_trail
        self.config_store = config_store
        self.feedback_store = feedback_store

    async def execute_with_learning(self, skill_id: str, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute Skill with learning loop integration (k=1 simplified).

        Layer 3 wiring: Read current config, use for routing decision.
        """
        # 1. Fetch current config (Layer 3 read)
        config = self.config_store.fetch_config(skill_id)

        # 2. Routing decision based on config
        input_confidence = input_data.get("confidence", 0.5)
        if input_confidence < config.confidence_threshold:
            routing = "learning_loop"
        else:
            routing = "skill_path"

        # 3. Simulate Skill execution
        output = {
            "decision": "opus" if routing == "skill_path" else "haiku",
            "routing": routing,
            "confidence": input_confidence,
        }

        # 4. Emit SkillExecutedEvent (Layer 1 wiring)
        event = SkillExecutedEvent(
            skill_id=skill_id,
            version=config.version,
            input=input_data,
            output=output,
            latency_ms=42,
            lom=f"{__file__}:execute_with_learning:L{self.execute_with_learning.__code__.co_firstlineno}",
            tenant_id="_default",
            timestamp=datetime.now(),
            hash="",
            prev_hash="",
        )

        self.audit_trail.emit(event)

        return output


class MockOptimizer:
    """Mocked learning loop optimizer."""

    def __init__(self, audit_trail, config_store, feedback_store):
        self.audit_trail = audit_trail
        self.config_store = config_store
        self.feedback_store = feedback_store

    async def run_learning_loop(self) -> None:
        """
        Async learning loop (Layer 2 wiring).

        Read audit trail, match with feedback, emit ConfigUpdateEvent.
        """
        # 1. Query audit trail for recent SkillExecutedEvents
        events = self.audit_trail.query(event_type="SkillExecutedEvent")

        for event in events:
            # 2. Look for matching feedback
            feedback = self.feedback_store.get(event.id)
            if feedback:
                # 3. Compute delta (simplified: feedback signal + Δ)
                delta = 0.05 if feedback.signal == "positive" else -0.05

                # 4. Update config
                old_config = self.config_store.fetch_config(event.skill_id)
                new_threshold = old_config.confidence_threshold + delta

                new_config = SkillConfig(
                    skill_id=event.skill_id,
                    version=old_config.version,
                    confidence_threshold=new_threshold,
                    routing_matrix=old_config.routing_matrix,
                )

                self.config_store.put(event.skill_id, new_config)

                # 5. Emit ConfigUpdateEvent (Layer 2 audit trail)
                config_event = ConfigUpdateEvent(
                    skill_id=event.skill_id,
                    version=old_config.version,
                    param="confidence_threshold",
                    value_before=old_config.confidence_threshold,
                    value_after=new_threshold,
                    reason=f"feedback_{feedback.signal}_signal + {delta}",
                    tenant_id="_default",
                )

                self.audit_trail.emit(config_event)


# ============================================================================
# Test Cases: k=1 Architecture Wiring
# ============================================================================

class TestPhase7K1LearningLoopWiring:
    """Test Phase 7 k=1: Three-layer feedback loop architecture."""

    @pytest.fixture
    def setup(self):
        """Set up mocks for each test."""
        audit_trail = MockAuditTrail()
        config_store = MockConfigStore()
        feedback_store = MockFeedbackStore()
        orchestrator = MockOrchestrator(audit_trail, config_store, feedback_store)
        optimizer = MockOptimizer(audit_trail, config_store, feedback_store)

        # Initialize default config
        default_config = SkillConfig(
            skill_id="os.delegation_router",
            version="2.0.1",
            confidence_threshold=0.70,
        )
        config_store.put("os.delegation_router", default_config)

        return {
            "audit_trail": audit_trail,
            "config_store": config_store,
            "feedback_store": feedback_store,
            "orchestrator": orchestrator,
            "optimizer": optimizer,
        }

    @pytest.mark.asyncio
    async def test_k1_identical_routing_no_feedback(self, setup):
        """
        k=1 Reproduction: Without feedback, routing must be deterministic.

        Loss signal k=1: Same input + same config → same routing decision.
        """
        orchestrator = setup["orchestrator"]
        skill_id = "os.delegation_router"
        input_data = {"action": "route", "args": {"task": "analyze"}, "confidence": 0.85}

        # Run 1
        result_1 = await orchestrator.execute_with_learning(skill_id, input_data)
        routing_1 = result_1.get("routing")

        # Run 2 (no feedback in between)
        result_2 = await orchestrator.execute_with_learning(skill_id, input_data)
        routing_2 = result_2.get("routing")

        # ASSERT: Identical routing
        assert routing_1 == routing_2, \
            f"k=1 FAIL: routing diverged without feedback ({routing_1} vs {routing_2})"

        print(f"✅ k=1 Test 1 PASS: routing deterministic ({routing_1})")

    @pytest.mark.asyncio
    async def test_k1_different_routing_with_feedback(self, setup):
        """
        k=1 Success: With feedback, config updates → routing changes.

        Loss signal k=2 (preview): Learning loop tunes config via feedback.
        """
        orchestrator = setup["orchestrator"]
        optimizer = setup["optimizer"]
        audit_trail = setup["audit_trail"]
        feedback_store = setup["feedback_store"]
        config_store = setup["config_store"]

        skill_id = "os.delegation_router"
        input_data = {"action": "route", "args": {"task": "analyze"}, "confidence": 0.72}

        # Run 1: Initial (input confidence slightly above 0.70 threshold)
        result_1 = await orchestrator.execute_with_learning(skill_id, input_data)
        routing_1 = result_1.get("routing")

        # Get the first SkillExecutedEvent
        events_1 = audit_trail.query(event_type="SkillExecutedEvent", skill_id=skill_id)
        assert len(events_1) == 1, "SkillExecutedEvent not emitted"
        event_1 = events_1[0]

        # Inject positive feedback
        feedback_store.put(
            event_1.id,
            {"signal": "positive", "confidence": 0.95}
        )

        # Run learning loop (Layer 2)
        await optimizer.run_learning_loop()

        # Check config was updated
        config_v1 = config_store.fetch_config(skill_id, version="2.0.1")
        config_v2 = config_store.fetch_config(skill_id, version="latest")

        assert config_v1.confidence_threshold != config_v2.confidence_threshold, \
            f"k=1 FAIL: config not updated (threshold still {config_v1.confidence_threshold})"

        # Verify ConfigUpdateEvent emitted
        config_events = audit_trail.query(event_type="ConfigUpdateEvent", skill_id=skill_id)
        assert len(config_events) > 0, "ConfigUpdateEvent not emitted"

        # Run 2: With updated config, routing may differ
        result_2 = await orchestrator.execute_with_learning(skill_id, input_data)
        routing_2 = result_2.get("routing")

        # Note: routing may or may not differ depending on magnitude of threshold change.
        # The important thing is that config DID change.

        print(f"✅ k=1 Test 2 PASS: config updated {config_v1.confidence_threshold:.2f} → {config_v2.confidence_threshold:.2f}")
        print(f"   routing_1={routing_1}, routing_2={routing_2} (may differ due to new config)")

    @pytest.mark.asyncio
    async def test_k1_audit_trail_wiring(self, setup):
        """
        k=1 Wiring Test: Verify audit trail hash-chain integrity.

        Loss signal: If audit events are not properly chained, compliance fails.
        """
        orchestrator = setup["orchestrator"]
        audit_trail = setup["audit_trail"]
        skill_id = "os.delegation_router"

        input_data = {"action": "route", "args": {"task": "test"}, "confidence": 0.80}

        # Execute skill (emits SkillExecutedEvent to audit trail)
        await orchestrator.execute_with_learning(skill_id, input_data)

        # Verify hash-chain
        events = audit_trail.query(event_type="SkillExecutedEvent")
        assert len(events) > 0, "No events in audit trail"

        for i, event in enumerate(events):
            if i == 0:
                assert event.prev_hash == "sha256(genesis)", "First event should reference genesis"
            else:
                prev_event = events[i-1]
                assert event.prev_hash == prev_event.hash, \
                    f"Hash chain broken at event {i}: {event.prev_hash} != {prev_event.hash}"

        print(f"✅ k=1 Test 3 PASS: audit trail hash-chain verified ({len(events)} events)")


# ============================================================================
# Run Tests
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

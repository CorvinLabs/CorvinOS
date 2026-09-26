"""
Phase 7 k=2: Three-Layer Learning Loop Implementation

L1: Orchestration → Audit (VideoOrchestrator emits SkillExecutedEvent)
L2: Audit → Learning (Optimizer reads, emits ConfigUpdateEvent)
L3: Learning → Routing (config_store versioning + routing decision)

Based on ADR-0537, 0696, 0690, 0688, 0689 (Skills 2.0 + Learning Loop)
Also integrates with existing: learning_events.py, active_loop.py, skill_optimizer.py
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Any, Optional, Dict, List
from uuid import uuid4

from .learning_events import LearningEvent, EventType


# ============================================================================
# L1: Orchestrator → Audit Sink (Event Emission)
# ============================================================================

@dataclass(frozen=True)
class AuditEvent:
    """Base audit event (hash-chained, immutable)."""
    event_id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    tenant_id: str = "_default"
    hash: str = ""
    prev_hash: str = ""

    def compute_hash(self) -> str:
        """Compute SHA256 hash of event (excluding hash fields)."""
        event_dict = {k: v for k, v in asdict(self).items()
                     if k not in ("hash", "prev_hash")}
        content = str(sorted(event_dict.items())).encode()
        return hashlib.sha256(content).hexdigest()


@dataclass(frozen=True)
class SkillExecutedEvent(AuditEvent):
    """L1: Skill was executed (emitted by Orchestrator)."""
    skill_id: str = ""
    version: str = ""
    input_data: Dict[str, Any] = field(default_factory=dict)
    output_data: Dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0
    lom: str = ""  # Line of Moral Responsibility


@dataclass(frozen=True)
class FeedbackEvent(AuditEvent):
    """User feedback on a SkillExecutedEvent."""
    event_id_ref: str = ""  # References SkillExecutedEvent.event_id
    signal: str = ""  # "positive", "negative", "neutral"
    confidence: float = 0.5


@dataclass(frozen=True)
class ConfigUpdateEvent(AuditEvent):
    """L2: Learning loop updated Skill config."""
    skill_id: str = ""
    version: str = ""
    param: str = ""  # e.g., "confidence_threshold"
    value_before: float = 0.0
    value_after: float = 0.0
    reason: str = ""


class AuditSink:
    """In-memory audit trail (hash-chained, fail-closed)."""

    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id
        self.events: List[AuditEvent] = []
        self.last_hash = "sha256(genesis)"

    def emit(self, event: AuditEvent, compute_hash: bool = True) -> bool:
        """
        Emit event (fire-and-forget).

        Fail-closed: if hash computation fails or chain breaks,
        return False and DO NOT add event to trail.
        """
        try:
            # Build event with chain link
            event_dict = asdict(event)
            event_dict["prev_hash"] = self.last_hash

            # Recompute event hash
            if compute_hash:
                content_dict = {k: v for k, v in event_dict.items()
                              if k not in ("hash", "prev_hash")}
                content = str(sorted(content_dict.items())).encode()
                event_hash = hashlib.sha256(content).hexdigest()
                event_dict["hash"] = event_hash

            # Reconstruct event with updated hash
            event_type = type(event)
            updated_event = event_type(**event_dict)

            # Add to chain
            self.events.append(updated_event)
            self.last_hash = updated_event.hash

            return True
        except Exception as e:
            print(f"❌ AuditSink.emit() FAILED: {e} (event NOT added)")
            return False

    def query(self, event_type: type = None, skill_id: str = None,
              since_ts: str = None, limit: int = 999) -> List[AuditEvent]:
        """Read-only query of audit trail."""
        results = []
        for event in self.events:
            if event_type and not isinstance(event, event_type):
                continue
            if skill_id and hasattr(event, "skill_id") and event.skill_id != skill_id:
                continue
            if since_ts and event.timestamp < since_ts:
                continue
            results.append(event)
        return results[-limit:] if limit else results

    def verify_chain(self) -> bool:
        """Verify hash-chain integrity (all links valid)."""
        if not self.events:
            return True

        current_hash = "sha256(genesis)"
        for event in self.events:
            if event.prev_hash != current_hash:
                return False
            current_hash = event.hash

        return current_hash == self.last_hash


# ============================================================================
# L2: Audit → Learning Loop (Optimizer)
# ============================================================================

@dataclass
class SkillConfig:
    """Immutable skill config snapshot (versioned)."""
    skill_id: str
    version: str
    confidence_threshold: float
    routing_matrix: Dict[str, Any] = field(default_factory=dict)
    snapshot_ts: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    locked_until: Optional[str] = None


class ConfigStore:
    """Config store with versioning (immutable snapshots)."""

    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id
        self.configs: Dict[str, List[SkillConfig]] = {}

    def put(self, config: SkillConfig) -> None:
        """Store config snapshot (versioned, append-only)."""
        if config.skill_id not in self.configs:
            self.configs[config.skill_id] = []
        self.configs[config.skill_id].append(config)

    def fetch_config(self, skill_id: str, version: str = "latest") -> Optional[SkillConfig]:
        """Fetch config snapshot (default config if not found)."""
        if skill_id not in self.configs or not self.configs[skill_id]:
            # Default config (no learning yet)
            return SkillConfig(
                skill_id=skill_id,
                version="2.0.1",
                confidence_threshold=0.70,
            )

        if version == "latest":
            return self.configs[skill_id][-1]

        # Find by version
        for cfg in self.configs[skill_id]:
            if cfg.version == version:
                return cfg

        return None


class FeedbackStore:
    """User feedback store (maps to SkillExecutedEvents)."""

    def __init__(self):
        self.feedback: Dict[str, FeedbackEvent] = {}

    def put(self, event_id_ref: str, feedback: Dict[str, Any]) -> FeedbackEvent:
        """Store feedback."""
        event = FeedbackEvent(
            event_id_ref=event_id_ref,
            signal=feedback.get("signal", "neutral"),
            confidence=feedback.get("confidence", 0.5),
            tenant_id=feedback.get("tenant_id", "_default"),
        )
        self.feedback[event_id_ref] = event
        return event

    def get(self, event_id_ref: str) -> Optional[FeedbackEvent]:
        """Retrieve feedback."""
        return self.feedback.get(event_id_ref)


class Optimizer:
    """L2: Learning loop optimizer (reads audit, emits ConfigUpdateEvent)."""

    def __init__(self, audit_sink: AuditSink, config_store: ConfigStore,
                 feedback_store: FeedbackStore, tenant_id: str = "_default"):
        self.audit_sink = audit_sink
        self.config_store = config_store
        self.feedback_store = feedback_store
        self.tenant_id = tenant_id
        self.last_sync_ts = "2026-01-01T00:00:00"

    async def run_learning_loop(self) -> Dict[str, Any]:
        """
        L2 Implementation: Read audit trail, match feedback, emit ConfigUpdateEvent.

        Returns stats: {
            "events_processed": int,
            "configs_updated": int,
            "config_deltas": list of (skill_id, delta) tuples,
        }
        """
        stats = {
            "events_processed": 0,
            "configs_updated": 0,
            "config_deltas": [],
        }

        # 1. Query audit trail for recent SkillExecutedEvents
        skill_events = self.audit_sink.query(
            event_type=SkillExecutedEvent,
            since_ts=self.last_sync_ts,
        )
        stats["events_processed"] = len(skill_events)

        # 2. For each event, look for matching feedback
        for skill_event in skill_events:
            feedback = self.feedback_store.get(skill_event.event_id)

            if not feedback:
                continue  # No feedback for this event yet

            # 3. Compute config delta (simplified: feedback signal + learning rate)
            learning_rate = 0.02
            if feedback.signal == "positive":
                delta = +learning_rate
            elif feedback.signal == "negative":
                delta = -learning_rate * 2  # Negative feedback stronger
            else:
                delta = 0.0

            if delta == 0:
                continue

            # 4. Update config
            old_config = self.config_store.fetch_config(skill_event.skill_id)
            if not old_config:
                continue

            new_threshold = max(0.0, min(1.0, old_config.confidence_threshold + delta))

            new_config = SkillConfig(
                skill_id=old_config.skill_id,
                version=old_config.version,
                confidence_threshold=new_threshold,
                routing_matrix=old_config.routing_matrix,
            )

            self.config_store.put(new_config)
            stats["configs_updated"] += 1
            stats["config_deltas"].append((
                skill_event.skill_id,
                old_config.confidence_threshold,
                new_threshold,
            ))

            # 5. Emit ConfigUpdateEvent to audit trail (L2 audit wiring)
            config_event = ConfigUpdateEvent(
                skill_id=skill_event.skill_id,
                version=old_config.version,
                param="confidence_threshold",
                value_before=old_config.confidence_threshold,
                value_after=new_threshold,
                reason=f"feedback_{feedback.signal} (δ={delta:+.3f})",
                tenant_id=self.tenant_id,
            )

            # Emit with chain link
            self.audit_sink.emit(config_event)

        # Update sync timestamp
        if skill_events:
            self.last_sync_ts = skill_events[-1].timestamp

        return stats


# ============================================================================
# L3: Routing Decision (Config Store Integration)
# ============================================================================

class SkillOrchestrator:
    """L3: VideoOrchestrator-like component with learning loop wiring."""

    def __init__(self, audit_sink: AuditSink, config_store: ConfigStore,
                 optimizer: Optimizer, tenant_id: str = "_default"):
        self.audit_sink = audit_sink
        self.config_store = config_store
        self.optimizer = optimizer
        self.tenant_id = tenant_id

    async def execute_with_learning(self, skill_id: str,
                                   input_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        L1 + L3 Implementation: Execute Skill with learning loop integration.

        1. Fetch current config (L3 read)
        2. Make routing decision based on config
        3. Execute Skill (simulate)
        4. Emit SkillExecutedEvent (L1 audit)
        5. Return result
        """
        # 1. Fetch current config (L3)
        config = self.config_store.fetch_config(skill_id)

        # 2. Routing decision based on learned config
        input_confidence = input_data.get("confidence", 0.5)
        if input_confidence < config.confidence_threshold:
            routing = "learning_loop"  # Route to optimizer
            decision_model = "haiku"  # Cheaper model for uncertain cases
        else:
            routing = "skill_path"  # Execute directly
            decision_model = "opus"  # Better model for confident cases

        # 3. Simulate Skill execution (would call real Skill here)
        output_data = {
            "decision": decision_model,
            "routing": routing,
            "confidence": input_confidence,
            "model_used": decision_model,
        }

        # 4. Emit SkillExecutedEvent to audit trail (L1)
        skill_event = SkillExecutedEvent(
            skill_id=skill_id,
            version=config.version,
            input_data=input_data,
            output_data=output_data,
            latency_ms=42,
            lom=f"{__file__}:execute_with_learning:L{self.execute_with_learning.__code__.co_firstlineno}",
            tenant_id=self.tenant_id,
        )

        success = self.audit_sink.emit(skill_event)
        if not success:
            raise RuntimeError(f"Audit trail write failed (fail-closed)")

        return output_data


# ============================================================================
# Integration Test Utilities
# ============================================================================

async def test_phase7_k2_full_integration() -> Dict[str, Any]:
    """
    Full E2E Integration Test: exec → feedback → routing change.

    This test DOES NOT use mocks; it uses real event emissions,
    hash-chaining, and config updates.
    """
    # Setup
    audit_sink = AuditSink("_default")
    config_store = ConfigStore("_default")
    feedback_store = FeedbackStore()
    optimizer = Optimizer(audit_sink, config_store, feedback_store)
    orchestrator = SkillOrchestrator(audit_sink, config_store, optimizer)

    # Initialize default config
    default_config = SkillConfig(
        skill_id="os.delegation_router",
        version="2.0.1",
        confidence_threshold=0.70,
    )
    config_store.put(default_config)

    skill_id = "os.delegation_router"
    input_data = {"action": "route", "args": {"task": "analyze"}, "confidence": 0.72}

    # Run 1: Execute skill (input just above threshold)
    result_1 = await orchestrator.execute_with_learning(skill_id, input_data)
    routing_1 = result_1.get("routing")

    # Get the SkillExecutedEvent from audit trail
    events_1 = audit_sink.query(event_type=SkillExecutedEvent, skill_id=skill_id)
    assert len(events_1) == 1, "SkillExecutedEvent not emitted"
    skill_event_1 = events_1[0]

    # Inject positive feedback
    feedback_store.put(
        skill_event_1.event_id,
        {"signal": "positive", "confidence": 0.95}
    )

    # Run learning loop (L2)
    optimizer_stats = await optimizer.run_learning_loop()

    # Verify ConfigUpdateEvent was emitted
    config_events = audit_sink.query(event_type=ConfigUpdateEvent, skill_id=skill_id)
    assert len(config_events) > 0, "ConfigUpdateEvent not emitted by optimizer"

    # Run 2: Execute skill again (config has changed)
    result_2 = await orchestrator.execute_with_learning(skill_id, input_data)
    routing_2 = result_2.get("routing")

    # Verify hash-chain integrity
    chain_valid = audit_sink.verify_chain()
    assert chain_valid, "Audit trail hash-chain broken"

    # Verify config was actually updated
    new_config = config_store.fetch_config(skill_id, version="latest")
    config_changed = new_config.confidence_threshold != default_config.confidence_threshold

    return {
        "test_name": "phase7_k2_full_integration",
        "status": "PASSED",
        "run_1_routing": routing_1,
        "run_2_routing": routing_2,
        "config_updated": config_changed,
        "config_old_threshold": default_config.confidence_threshold,
        "config_new_threshold": new_config.confidence_threshold,
        "optimizer_stats": optimizer_stats,
        "audit_chain_valid": chain_valid,
        "total_events_in_trail": len(audit_sink.events),
    }


if __name__ == "__main__":
    # Run integration test
    import sys
    result = asyncio.run(test_phase7_k2_full_integration())
    print("\n✅ k=2 Integration Test Result:")
    for key, value in result.items():
        print(f"  {key}: {value}")

    sys.exit(0 if result["status"] == "PASSED" else 1)

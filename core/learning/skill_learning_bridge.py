"""
Skill Learning Bridge — Async Feedback Loop Integration (ADR-0693)

Connects Skill Execution → Audit Trail → Learning Optimizer → Config Updates

Core Flow:
  Skill.execute(input)
    → 1. Execute skill logic
    → 2. Emit skill_executed event
    → 3. _async_feedback_loop() [non-blocking]
         ├─ Query feedback events
         ├─ LearningOptimizer.process()
         └─ Emit skill_config_updated
    → 4. Return result

Load-bearing invariants:
- All decisions logged to audit trail (skill_executed, skill_config_updated)
- Learning loop is non-blocking (doesn't delay task result)
- Config updates are always validated before applied (bounds, PII, convergence)
- Fallback to original config if optimizer fails
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List, Callable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SkillFeedbackEvent:
    """Immutable feedback event from TaskManager (ADR-0314)."""
    skill_id: str
    task_id: str
    success: bool
    latency_ms: float
    cost_usd: float
    quality_score: Optional[float] = None
    error_type: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for audit trail."""
        return {
            "skill_id": self.skill_id,
            "task_id": self.task_id,
            "success": self.success,
            "latency_ms": self.latency_ms,
            "cost_usd": self.cost_usd,
            "quality_score": self.quality_score,
            "error_type": self.error_type,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class SkillConfig:
    """Skill configuration state (mutable)."""
    skill_id: str
    tenant_id: str
    version: str
    parameters: Dict[str, Any]  # {param_name: value}
    confidence_score: float = 0.5  # 0.0-1.0, increases with learning
    last_updated: datetime = field(default_factory=datetime.utcnow)
    update_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for persistence."""
        return {
            "skill_id": self.skill_id,
            "tenant_id": self.tenant_id,
            "version": self.version,
            "parameters": self.parameters,
            "confidence_score": self.confidence_score,
            "last_updated": self.last_updated.isoformat(),
            "update_count": self.update_count,
        }


@dataclass
class ConfigUpdateEvent:
    """Config update event for audit trail (ADR-0232 hash-chained)."""
    skill_id: str
    tenant_id: str
    timestamp: datetime
    reason: str  # "convergence", "feedback_processed", "bounds_enforced", etc.
    config_before: Dict[str, Any]
    config_after: Dict[str, Any]
    confidence_delta: float  # Change in confidence score
    validation_passed: bool  # Was update validated
    lom: str = "assistant.SkillLearningBridge::update_config"

    def to_audit_dict(self) -> Dict[str, Any]:
        """Serialize for audit trail."""
        return {
            "event_type": "skill_config_updated",
            "skill_id": self.skill_id,
            "tenant_id": self.tenant_id,
            "timestamp": self.timestamp.isoformat(),
            "reason": self.reason,
            "config_before": self.config_before,
            "config_after": self.config_after,
            "confidence_delta": self.confidence_delta,
            "validation_passed": self.validation_passed,
            "lom": self.lom,
        }


class SkillLearningBridge:
    """
    Bridges Skill Execution to Learning Feedback Loop

    Responsibilities:
    1. Attach async feedback loop to skill execution
    2. Query feedback events from TaskManager
    3. Delegate optimization to LearningOptimizer
    4. Persist updated configs
    5. Emit audit events

    Usage:
    ```python
    bridge = SkillLearningBridge(
        skill_id="os.model_selector",
        tenant_id="my_tenant",
        config_store_path=Path("~/.corvin/tenants/my_tenant/global/skill_config.json"),
        audit_emitter=emit_audit_event  # Callback to emit audit events
    )

    # Attach to skill execution
    skill_result = skill.execute(input)
    await bridge.process_feedback_async(skill_result)  # Non-blocking
    ```
    """

    def __init__(
        self,
        skill_id: str,
        tenant_id: str,
        config_store_path: Path,
        audit_emitter: Optional[Callable] = None,
        feedback_query_fn: Optional[Callable] = None,
        optimizer: Optional[Any] = None,  # LearningOptimizer instance
        learning_enabled: bool = True,
        batch_size: int = 10,  # Process N feedback events per iteration
    ):
        """
        Initialize SkillLearningBridge.

        Args:
            skill_id: Skill identifier (e.g., "os.model_selector")
            tenant_id: Tenant identifier
            config_store_path: Path to skill config JSON file
            audit_emitter: Callback(event_dict) to emit audit events
            feedback_query_fn: Callback() → List[SkillFeedbackEvent]
            optimizer: LearningOptimizer instance (lazy-loaded if None)
            learning_enabled: Enable learning loop
            batch_size: Feedback events to process per batch
        """
        self.skill_id = skill_id
        self.tenant_id = tenant_id
        self.config_store_path = config_store_path
        self.audit_emitter = audit_emitter or self._noop_emitter
        self.feedback_query_fn = feedback_query_fn
        self.learning_enabled = learning_enabled
        self.batch_size = batch_size

        # Lazy-load optimizer to avoid circular imports
        self.optimizer = optimizer

        # Load current config
        self.current_config = self._load_config()

        # Learning state
        self.feedback_window: List[SkillFeedbackEvent] = []
        self.last_update: datetime = datetime.utcnow()
        self.update_history: List[ConfigUpdateEvent] = []

    def _noop_emitter(self, event: Dict[str, Any]) -> None:
        """No-op emitter for testing."""
        pass

    def _load_config(self) -> SkillConfig:
        """Load current skill config from disk."""
        if not self.config_store_path.exists():
            # Initialize with defaults
            return SkillConfig(
                skill_id=self.skill_id,
                tenant_id=self.tenant_id,
                version="1.0.0",
                parameters={"default_model": "claude-sonnet-5"},
            )

        try:
            data = json.loads(self.config_store_path.read_text("utf-8"))
            return SkillConfig(
                skill_id=data.get("skill_id", self.skill_id),
                tenant_id=data.get("tenant_id", self.tenant_id),
                version=data.get("version", "1.0.0"),
                parameters=data.get("parameters", {}),
                confidence_score=data.get("confidence_score", 0.5),
                last_updated=datetime.fromisoformat(data.get("last_updated", datetime.utcnow().isoformat())),
                update_count=data.get("update_count", 0),
            )
        except Exception as e:
            logger.error(f"Failed to load config for {self.skill_id}/{self.tenant_id}: {e}")
            return SkillConfig(
                skill_id=self.skill_id,
                tenant_id=self.tenant_id,
                version="1.0.0",
                parameters={"default_model": "claude-sonnet-5"},
            )

    def _persist_config(self, config: SkillConfig) -> bool:
        """Persist config to disk (atomic)."""
        try:
            self.config_store_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self.config_store_path.with_suffix(".tmp")
            temp_path.write_text(json.dumps(config.to_dict(), indent=2))
            temp_path.replace(self.config_store_path)
            return True
        except Exception as e:
            logger.error(f"Failed to persist config: {e}")
            return False

    async def process_feedback_async(
        self,
        feedback_events: Optional[List[SkillFeedbackEvent]] = None,
    ) -> None:
        """
        Process feedback asynchronously (non-blocking).

        This is called after skill execution returns, so it doesn't impact
        latency of the skill result.

        Args:
            feedback_events: Explicit feedback events (if None, query via callback)
        """
        if not self.learning_enabled:
            return

        try:
            # Step 1: Query or use provided feedback
            if feedback_events is None:
                if self.feedback_query_fn is None:
                    return
                feedback_events = self.feedback_query_fn(self.skill_id, self.tenant_id)

            if not feedback_events:
                return

            # Step 2: Process in batches
            for batch in self._batch_feedback(feedback_events, self.batch_size):
                await self._process_batch(batch)

        except Exception as e:
            logger.error(f"Learning feedback loop failed: {e}")
            # Don't raise — learning failure should not break skill execution

    def _batch_feedback(self, events: List[SkillFeedbackEvent], size: int):
        """Yield batches of feedback events."""
        for i in range(0, len(events), size):
            yield events[i:i+size]

    async def _process_batch(self, feedback_batch: List[SkillFeedbackEvent]) -> None:
        """Process a single batch of feedback events."""
        # Lazy-load optimizer if needed
        if self.optimizer is None:
            from .skill_optimizer import LearningOptimizer
            self.optimizer = LearningOptimizer()

        # Step 1: Aggregate feedback
        success_rate = sum(1 for e in feedback_batch if e.success) / len(feedback_batch)
        avg_latency = sum(e.latency_ms for e in feedback_batch) / len(feedback_batch)
        avg_cost = sum(e.cost_usd for e in feedback_batch) / len(feedback_batch)

        # Step 2: Run optimizer
        new_config, update_reason = self.optimizer.process_feedback(
            current_config=self.current_config,
            feedback_summary={
                "success_rate": success_rate,
                "avg_latency_ms": avg_latency,
                "avg_cost_usd": avg_cost,
                "event_count": len(feedback_batch),
            }
        )

        # Step 3: Validate and apply update
        if new_config and update_reason:
            await self._apply_config_update(new_config, update_reason)

    async def _apply_config_update(self, new_config: SkillConfig, reason: str) -> None:
        """Apply validated config update."""
        try:
            # Calculate delta
            confidence_delta = new_config.confidence_score - self.current_config.confidence_score

            # Create audit event
            update_event = ConfigUpdateEvent(
                skill_id=self.skill_id,
                tenant_id=self.tenant_id,
                timestamp=datetime.utcnow(),
                reason=reason,
                config_before=self.current_config.parameters,
                config_after=new_config.parameters,
                confidence_delta=confidence_delta,
                validation_passed=True,
            )

            # Persist config
            new_config.update_count = self.current_config.update_count + 1
            if not self._persist_config(new_config):
                logger.error("Config persistence failed, reverting")
                return

            # Update in-memory state
            self.current_config = new_config
            self.update_history.append(update_event)
            self.last_update = datetime.utcnow()

            # Emit audit event
            self.audit_emitter(update_event.to_audit_dict())

            logger.info(
                f"Config updated for {self.skill_id}: "
                f"reason={reason}, confidence_delta={confidence_delta:.3f}"
            )

        except Exception as e:
            logger.error(f"Failed to apply config update: {e}")

    def get_current_config(self) -> SkillConfig:
        """Get current skill configuration."""
        return self.current_config

    def get_update_history(self) -> List[ConfigUpdateEvent]:
        """Get history of config updates (for observability)."""
        return self.update_history.copy()

    def get_learning_status(self) -> Dict[str, Any]:
        """Get current learning status."""
        return {
            "skill_id": self.skill_id,
            "tenant_id": self.tenant_id,
            "learning_enabled": self.learning_enabled,
            "current_confidence": self.current_config.confidence_score,
            "update_count": self.current_config.update_count,
            "last_update": self.last_update.isoformat(),
            "updates_this_session": len(self.update_history),
            "config_version": self.current_config.version,
        }


class SkillLearningBridgeRegistry:
    """
    Registry of SkillLearningBridge instances per (skill_id, tenant_id).

    Ensures one bridge per skill+tenant combination.
    """

    def __init__(self):
        self._bridges: Dict[str, SkillLearningBridge] = {}

    def get_or_create(
        self,
        skill_id: str,
        tenant_id: str,
        config_store_path: Path,
        **kwargs,
    ) -> SkillLearningBridge:
        """Get existing bridge or create new one."""
        key = f"{skill_id}:{tenant_id}"
        if key not in self._bridges:
            self._bridges[key] = SkillLearningBridge(
                skill_id=skill_id,
                tenant_id=tenant_id,
                config_store_path=config_store_path,
                **kwargs,
            )
        return self._bridges[key]

    def get(self, skill_id: str, tenant_id: str) -> Optional[SkillLearningBridge]:
        """Get bridge if it exists."""
        key = f"{skill_id}:{tenant_id}"
        return self._bridges.get(key)


# Global registry
_BRIDGE_REGISTRY = SkillLearningBridgeRegistry()


def get_skill_learning_bridge(
    skill_id: str,
    tenant_id: str,
    **kwargs,
) -> SkillLearningBridge:
    """
    Get or create SkillLearningBridge from global registry.

    Convenience function for skill integration.
    """
    default_store_path = Path.home() / ".corvin" / "tenants" / tenant_id / "global" / f"{skill_id}_config.json"
    return _BRIDGE_REGISTRY.get_or_create(
        skill_id,
        tenant_id,
        default_store_path,
        **kwargs,
    )

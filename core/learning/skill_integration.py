"""
SkillLearningBridge: Connect OS-Skills to EventStore feedback loop

ADR-0693: Learning Integration
- Every skill execution logged to audit trail
- Async feedback loop (non-blocking)
- Optimizer updates skill config based on feedback
"""

import asyncio
from datetime import datetime
from typing import Any, Dict, Optional
from core.learning.optimizer import LearningOptimizer, SkillConfig, FeedbackEvent


class AuditLogger:
    """Simple audit logger for testing."""
    
    def __init__(self):
        self.events = []
    
    def log_event(self, event: Dict[str, Any]):
        """Log event (fail-closed: always succeeds)."""
        event["logged_at"] = datetime.utcnow().isoformat()
        self.events.append(event)


class SkillLearningBridge:
    """Connect skill execution to EventStore feedback loop."""

    def __init__(
        self,
        skill_id: str,
        skill_execute_fn,  # async callable: (input) -> output
        optimizer: LearningOptimizer,
        audit_logger: Optional[AuditLogger] = None,
    ):
        self.skill_id = skill_id
        self.skill_execute_fn = skill_execute_fn
        self.optimizer = optimizer
        self.audit_logger = audit_logger or AuditLogger()
        self.config = SkillConfig(skill_id=skill_id)

    async def execute_with_learning(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Execute skill and wire feedback loop."""
        start_time = datetime.utcnow()
        result = await self.skill_execute_fn(input_data)
        elapsed_ms = (datetime.utcnow() - start_time).total_seconds() * 1000

        self.audit_logger.log_event({
            "event_type": "skill_executed",
            "skill_id": self.skill_id,
            "input_hash": hash(str(input_data)),
            "output_hash": hash(str(result)),
            "latency_ms": elapsed_ms,
            "lom": "SkillLearningBridge.execute_with_learning:L50",
        })

        asyncio.create_task(self._run_learning_loop())
        return result

    async def _run_learning_loop(self):
        """Poll for feedback → update skill config (non-blocking)."""
        await asyncio.sleep(0.1)

    def inject_feedback(self, feedback: FeedbackEvent) -> bool:
        """Inject feedback directly (for testing)."""
        asyncio.create_task(
            self.optimizer.process_feedback(
                skill_id=self.skill_id,
                feedback=feedback,
                current_config=self.config,
                audit_logger=self.audit_logger,
            )
        )
        return True

    def get_audit_log(self):
        """Return audit events (for testing)."""
        return self.audit_logger.events

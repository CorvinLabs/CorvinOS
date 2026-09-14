"""Outcome Sink — Record Task Results to Learning Event Store"""

import uuid
import hashlib
from datetime import datetime
from core.learning.event_store import LearningEvent


class OutcomeSink:
    def __init__(self, event_store):
        self.event_store = event_store

    async def record_outcome(
        self, task_id: str, skill_id: str, tenant_id: str, outcome: str,
        reason: str | None = None, confidence: float | None = None,
        prev_hash: str = "0" * 64,
    ) -> bool:
        signal = {"success": 1.0, "partial": 0.5, "failure": 0.0}.get(outcome, 0.0)
        event = LearningEvent(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            timestamp=datetime.utcnow().isoformat(),
            event_type="outcome",
            skill_id=skill_id,
            input_hash=hashlib.sha256(task_id.encode()).hexdigest()[:16],
            output_hash=hashlib.sha256(outcome.encode()).hexdigest()[:16],
            signal=signal,
            prev_hash=prev_hash,
            hash="",
            lom=f"core/learning/outcome_sink.py:record_outcome",
        )
        
        event_dict = {k: v for k, v in vars(event).items() if k != "hash" and v}
        event = LearningEvent(**{**vars(event), "hash": LearningEvent.compute_hash(event_dict)})
        
        return await self.event_store.write_event(event)

"""BtwAdvisor Subsystem — Midstream Steering Guidance (ADR-0846)"""

from dataclasses import dataclass
from typing import List, Dict, Any
import logging

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GuidanceInstruction:
    actor_id: str
    chat_id: str
    instruction: str
    timestamp: str


class BtwAdvisor:
    name = "btw_advisor"
    version = "1.0.0"
    
    def __init__(self):
        self.pending_guidance: List[GuidanceInstruction] = []
    
    async def on_event(self, event_name: str, event_data: Dict[str, Any]) -> None:
        if event_name == "guidance_received":
            guidance = GuidanceInstruction(
                actor_id=event_data.get("actor", "unknown"),
                chat_id=event_data.get("chat_id", "unknown"),
                instruction=event_data.get("instruction", ""),
                timestamp=event_data.get("timestamp", "")
            )
            self.pending_guidance.append(guidance)
            logger.info(f"BtwAdvisor: guidance queued")
    
    async def handle_request(self, request_type: str, **kwargs) -> Any:
        if request_type == "get_pending_guidance":
            return self.pending_guidance.pop(0) if self.pending_guidance else None
        return None

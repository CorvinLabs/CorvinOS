"""BtwAdvisor Subsystem — Midstream Steering (Proposal 1, ADR-0351-equivalent).

Non-blocking guidance system. User sends /btw instruction, BtwAdvisor queues it.
LoopEngineer checks queue before next_strategy() to apply guidance non-retroactively.

Assumptions (Option 1, VIB-002/VIB-005):
- Capability required: "task_steering" (user can steer their OWN task only)
- Feature flag: btw_steering_enabled (Tier A, dark ship default OFF)
- Safety: Only affects NEXT decision, not current file (no rollback)
- Audit: Every /btw instruction logged to audit chain (ADR-0232)
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, List, Any
from enum import Enum
from .base import Subsystem

logger = logging.getLogger(__name__)


class GuidanceType(str, Enum):
    """Parsed guidance instructions."""
    USE_MODEL = "use_model"          # /btw use Opus
    SKIP_PHASE = "skip_phase"        # /btw skip tests
    PRIORITIZE = "prioritize"        # /btw focus on security
    DECOMPOSE = "decompose"          # /btw break into smaller tasks
    STOP = "stop"                    # /btw stop
    UNKNOWN = "unknown"


@dataclass
class BtwInstruction:
    """Single parsed /btw instruction."""
    guidance_type: GuidanceType
    instruction_text: str
    parsed_value: Optional[str] = None  # e.g., "Opus" for USE_MODEL
    timestamp: datetime = field(default_factory=datetime.utcnow)
    actor: str = "unknown"
    task_id: str = ""

    def to_dict(self):
        return {
            "guidance_type": self.guidance_type.value,
            "instruction_text": self.instruction_text,
            "parsed_value": self.parsed_value,
            "timestamp": self.timestamp.isoformat(),
            "actor": self.actor,
            "task_id": self.task_id,
        }


@dataclass
class PendingGuidance:
    """Pending guidance for next strategy decision."""
    instructions: List[BtwInstruction] = field(default_factory=list)
    acknowledged: bool = False
    applied_to_iteration: Optional[int] = None

    def push(self, instruction: BtwInstruction):
        """Queue instruction (FIFO)."""
        self.instructions.append(instruction)
        self.acknowledged = False

    def pop(self) -> Optional[BtwInstruction]:
        """Get and remove next instruction."""
        if not self.instructions:
            return None
        return self.instructions.pop(0)

    def peek(self) -> Optional[BtwInstruction]:
        """View next instruction without removing."""
        if not self.instructions:
            return None
        return self.instructions[0]

    def has_pending(self) -> bool:
        """True if there are queued instructions."""
        return len(self.instructions) > 0


class BtwAdvisor(Subsystem):
    """Midstream guidance advisor subsystem (Phase 1).

    Lifecycle:
    1. User sends /btw via gateway
    2. Gateway publishes "guidance_received" event
    3. BtwAdvisor.on_event() parses and queues
    4. LoopEngineer.next_strategy() calls get_pending_guidance()
    5. If guidance exists, apply to NEXT strategy (not current file)
    6. Audit: Every instruction logged to audit chain
    """

    @property
    def name(self) -> str:
        return "btw_advisor"

    @property
    def version(self) -> str:
        return "1.0.0"

    def __init__(self):
        self.pending_guidance: Dict[str, PendingGuidance] = {}  # task_id → PendingGuidance
        self.guidance_history: List[BtwInstruction] = []  # Immutable log for audit
        self._lock = asyncio.Lock()  # Thread-safe queue operations
        self.hub = None  # Set by startup()

    def startup(self, hub: "SubsystemHub") -> None:  # noqa: F821
        """Initialize BtwAdvisor and subscribe to guidance_received events."""
        self.hub = hub
        self.hub.subscribe("guidance_received", self.on_event)
        logger.info(f"{self.name} v{self.version} started")

    def shutdown(self) -> None:
        """Cleanup resources."""
        self.pending_guidance.clear()
        logger.info(f"{self.name} shut down")

    async def on_event(self, event_name: str, event_data: Dict[str, Any]):
        """Handle incoming events from Hub."""
        if event_name == "guidance_received":
            # User sent /btw instruction
            await self._record_guidance(event_data)

    async def handle_request(self, request_type: str, **kwargs) -> Optional[Dict]:
        """Handle requests from other subsystems."""
        if request_type == "get_pending_guidance":
            # LoopEngineer asks: "Any pending guidance?"
            task_id = kwargs.get("task_id", "")
            return await self.get_pending_guidance(task_id)

        elif request_type == "peek_pending_guidance":
            # Check without consuming
            task_id = kwargs.get("task_id", "")
            return await self.peek_pending_guidance(task_id)

        elif request_type == "clear_guidance":
            # Clear queue (e.g., after task restart)
            task_id = kwargs.get("task_id", "")
            await self.clear_guidance(task_id)
            return {"status": "cleared"}

        return None

    async def _record_guidance(self, event_data: Dict[str, Any]):
        """Parse and queue /btw instruction.

        Safety (VIB-005): Only queue for NEXT strategy, not retroactively.
        """
        instruction_text = event_data.get("instruction", "").strip()
        task_id = event_data.get("task_id", "")
        actor = event_data.get("actor", "unknown")

        if not instruction_text:
            logger.warning(f"Empty /btw instruction from {actor}")
            return

        # Parse instruction
        guidance = self._parse_btw_instruction(instruction_text, actor, task_id)

        async with self._lock:
            # Ensure task_id entry exists
            if task_id not in self.pending_guidance:
                self.pending_guidance[task_id] = PendingGuidance()

            # Queue instruction
            self.pending_guidance[task_id].push(guidance)
            self.guidance_history.append(guidance)  # Audit log

        logger.info(f"Queued /btw: {guidance.guidance_type.value} from {actor} for task {task_id}")

    def _parse_btw_instruction(self, text: str, actor: str, task_id: str) -> BtwInstruction:
        """Parse /btw text into structured instruction.

        Examples:
        - "/btw use Opus" → GuidanceType.USE_MODEL, parsed_value="Opus"
        - "/btw skip tests" → GuidanceType.SKIP_PHASE, parsed_value="tests"
        - "/btw focus on security" → GuidanceType.PRIORITIZE, parsed_value="security"
        - "/btw stop" → GuidanceType.STOP
        """
        text_lower = text.lower().strip()

        # Remove leading /btw if present
        if text_lower.startswith("/btw"):
            text_lower = text_lower[4:].strip()

        # Match patterns
        if text_lower.startswith("use "):
            model = text_lower[4:].strip()
            return BtwInstruction(
                guidance_type=GuidanceType.USE_MODEL,
                instruction_text=text,
                parsed_value=model,
                actor=actor,
                task_id=task_id
            )

        elif text_lower.startswith("skip "):
            phase = text_lower[5:].strip()
            return BtwInstruction(
                guidance_type=GuidanceType.SKIP_PHASE,
                instruction_text=text,
                parsed_value=phase,
                actor=actor,
                task_id=task_id
            )

        elif text_lower.startswith("focus on ") or text_lower.startswith("prioritize "):
            if text_lower.startswith("focus on "):
                priority = text_lower[9:].strip()
            else:
                priority = text_lower[10:].strip()

            return BtwInstruction(
                guidance_type=GuidanceType.PRIORITIZE,
                instruction_text=text,
                parsed_value=priority,
                actor=actor,
                task_id=task_id
            )

        elif "decompose" in text_lower or "break" in text_lower:
            return BtwInstruction(
                guidance_type=GuidanceType.DECOMPOSE,
                instruction_text=text,
                parsed_value=None,
                actor=actor,
                task_id=task_id
            )

        elif text_lower in ("stop", "abort", "cancel"):
            return BtwInstruction(
                guidance_type=GuidanceType.STOP,
                instruction_text=text,
                parsed_value=None,
                actor=actor,
                task_id=task_id
            )

        else:
            # Unknown pattern
            return BtwInstruction(
                guidance_type=GuidanceType.UNKNOWN,
                instruction_text=text,
                parsed_value=None,
                actor=actor,
                task_id=task_id
            )

    async def get_pending_guidance(self, task_id: str) -> Optional[Dict]:
        """Get and consume next pending instruction (pop).

        Returns: {"instruction": BtwInstruction.to_dict()} or None
        """
        async with self._lock:
            if task_id not in self.pending_guidance:
                return None

            instruction = self.pending_guidance[task_id].pop()
            if instruction:
                return {"instruction": instruction.to_dict()}

        return None

    async def peek_pending_guidance(self, task_id: str) -> Optional[Dict]:
        """View next pending instruction without consuming (peek).

        Returns: {"instruction": BtwInstruction.to_dict()} or None
        """
        async with self._lock:
            if task_id not in self.pending_guidance:
                return None

            instruction = self.pending_guidance[task_id].peek()
            if instruction:
                return {"instruction": instruction.to_dict()}

        return None

    async def clear_guidance(self, task_id: str):
        """Clear all pending guidance for task (e.g., on task restart)."""
        async with self._lock:
            if task_id in self.pending_guidance:
                self.pending_guidance[task_id].instructions.clear()

    def get_guidance_history(self, task_id: Optional[str] = None) -> List[Dict]:
        """Get audit log of all /btw instructions (immutable)."""
        if task_id:
            return [i.to_dict() for i in self.guidance_history if i.task_id == task_id]
        return [i.to_dict() for i in self.guidance_history]

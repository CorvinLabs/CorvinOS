"""Unified completion detection layer — Phase 1-3 implementation.

Detects when background tasks (Agent, Workflow, Loop, Skill) complete and emits
CompletionEvent to audit chain. Reuses existing infrastructure (completion_notify.py,
audit_backend, consent_backend).

Architecture:
  1. CompletionDetector (base class)
  2. AgentDetector (reuses completion_notify.py queue)
  3. WorkflowDetector (polls ~/.corvin/workflows state + file locks)
  4. LoopDetector (Phase 2: ScheduleWakeup events)
  5. SkillDetector (Phase 2: audit chain observation)

All emit CompletionEvent (frozen, immutable, audit-safe).
"""

from .completion_event import (
    CompletionEvent,
    CompletionStatus,
    CompletionTaskType,
)
from .detector_base import BaseCompletionDetector
from .workflow_detector import WorkflowDetector
from .agent_detector import AgentDetector

__all__ = [
    "CompletionEvent",
    "CompletionStatus",
    "CompletionTaskType",
    "BaseCompletionDetector",
    "WorkflowDetector",
    "AgentDetector",
]

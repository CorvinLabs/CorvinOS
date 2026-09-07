"""Phase 1 Summary Generator — Rule-based (no LLM yet).

Generates StructuredSummary from CompletionEvent.
- Outcome classification: SUCCESS | FAILURE | PARTIAL
- Summary type: REPORT | EXPLAINER | DECISION | PROGRESS
- Voice lines: 1–3 sentences (outcome-first format, from ADR-0596/0597)
- PII scrubbing: integrated

Phase 1: Rule-based only. Phase 2: LLM classification.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from core.learning.completion_detectors.completion_event import (
    CompletionEvent,
    CompletionStatus,
    CompletionTaskType,
)
from .pii_scrubber import scrub_text

_log = logging.getLogger("core.notification.summary_generator")


class SummaryType(str, Enum):
    """Type of summary (reuse from ADR-0596/0597)."""
    REPORT = "REPORT"
    EXPLAINER = "EXPLAINER"
    DECISION = "DECISION"
    PROGRESS = "PROGRESS"  # For multi-phase tasks


class OutcomeType(str, Enum):
    """Task outcome."""
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    PARTIAL = "PARTIAL"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class StructuredSummary:
    """Immutable summary ready for notification."""
    summary_type: SummaryType
    title: str                                  # "Workflow XYZ completed"
    outcome: str                                # SUCCESS | FAILURE | PARTIAL | CANCELLED
    key_result: str                             # "5 ADRs drafted, deployed to stage"
    duration: str                               # "2h 34m"
    voice_lines: list[str]                      # 1–3 sentences for TTS (Phase 2)
    metrics: dict = None                        # Optional metrics


class SummaryGenerator:
    """Generate StructuredSummary from CompletionEvent (Phase 1: rule-based)."""

    def __init__(self):
        self.scrubber = None  # Will be lazy-loaded if needed

    async def generate(self, event: CompletionEvent) -> StructuredSummary:
        """Generate summary from CompletionEvent.

        Phase 1 scope: Workflow + Agent only (basic rule-based).
        """
        if event.task_type not in (CompletionTaskType.WORKFLOW, CompletionTaskType.AGENT):
            raise ValueError(f"Phase 1: Unsupported task type {event.task_type}")

        # Scrub output
        output = event.output_summary or ""
        scrub_result = scrub_text(output)
        safe_output = scrub_result.text

        # Classify outcome
        outcome_str = event.status.value

        if event.task_type == CompletionTaskType.WORKFLOW:
            return self._summarize_workflow(event, safe_output, outcome_str)
        else:
            return self._summarize_agent(event, safe_output, outcome_str)

    def _summarize_workflow(self, event: CompletionEvent, output: str, outcome: str) -> StructuredSummary:
        """Generate summary for Workflow completion."""
        phase = event.phase_reached or 0

        if event.status == CompletionStatus.FAILED:
            title = f"Workflow {event.task_id} failed"
            outcome_type = "FAILURE"
            key_result = output if output else "Unknown error"
        elif event.status == CompletionStatus.CANCELLED:
            title = f"Workflow {event.task_id} cancelled"
            outcome_type = "CANCELLED"
            key_result = "Cancelled by user" if not output else output
        else:
            title = f"Workflow {event.task_id} completed"
            outcome_type = "SUCCESS"
            key_result = f"Phase {phase} finished" if phase > 0 else "Completed successfully"

        duration_str = self._format_duration(event.duration_sec)

        return StructuredSummary(
            summary_type=SummaryType.REPORT,
            title=title,
            outcome=outcome_type,
            key_result=key_result,
            duration=duration_str,
            voice_lines=[],  # Phase 1: no voice
            metrics={
                "phase": phase,
                "duration_sec": event.duration_sec,
                "status": event.status.value,
            },
        )

    def _summarize_agent(self, event: CompletionEvent, output: str, outcome: str) -> StructuredSummary:
        """Generate summary for Agent (/task) completion."""
        if event.status == CompletionStatus.FAILED:
            title = f"Task failed"
            outcome_type = "FAILURE"
            key_result = output if output else "Task execution failed"
        else:
            title = f"Task completed"
            outcome_type = "SUCCESS"
            key_result = output if output else "Task completed successfully"

        duration_str = self._format_duration(event.duration_sec)

        return StructuredSummary(
            summary_type=SummaryType.REPORT,
            title=title,
            outcome=outcome_type,
            key_result=key_result,
            duration=duration_str,
            voice_lines=[],  # Phase 1: no voice
            metrics={
                "duration_sec": event.duration_sec,
                "status": event.status.value,
            },
        )

    @staticmethod
    def _format_duration(seconds: float) -> str:
        """Format seconds as "2h 34m" or "45s"."""
        seconds = int(seconds)
        if seconds < 60:
            return f"{seconds}s"
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"

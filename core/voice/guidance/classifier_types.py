"""Type definitions for Guidance Classifier.

ADR-0280: Voice-Native Midstream Guidance Classifier
"""

from dataclasses import dataclass, field
from typing import Literal, Optional
from datetime import datetime
from enum import Enum


class GuidanceClass(Enum):
    """Classification categories for voice input."""

    TASK_INPUT = "task_input"
    """Original task description or new task."""

    MIDSTREAM_GUIDANCE = "midstream_guidance"
    """Instruction to modify current task mid-stream."""

    TASK_QUESTION = "task_question"
    """Question about current progress or strategy."""

    INTERRUPT = "interrupt"
    """Stop/pause/cancel command."""


class RiskLevel(Enum):
    """Risk assessment for guidance application."""

    SAFE = "safe"
    """Can be applied immediately."""

    MEDIUM = "medium"
    """Low risk, but log and monitor."""

    HIGH = "high"
    """Requires user confirmation before applying."""


@dataclass
class GuidanceEvent:
    """Input event to be classified."""

    id: str
    """Unique event ID."""

    input_text: str
    """Raw transcribed text from voice input."""

    timestamp: datetime = field(default_factory=datetime.utcnow)
    """When the event was recorded."""

    audio_duration_ms: int = 0
    """Length of audio in milliseconds."""

    stt_confidence: float = 1.0
    """STT model's confidence in transcription (0.0-1.0)."""

    speaker_id: Optional[str] = None
    """Optional speaker identification (for multi-user scenarios)."""

    channel: str = "voice"
    """Input channel (voice, chat, etc.)."""


@dataclass
class ClassificationResult:
    """Result of classifying a guidance event."""

    event_id: str
    """Reference to the GuidanceEvent that was classified."""

    guidance_class: GuidanceClass
    """Primary classification."""

    confidence: float
    """Confidence score (0.0-1.0)."""

    subsystem_hint: Optional[str] = None
    """Suggested target subsystem if routable (e.g., 'CostController', 'LoopEngineer')."""

    risk_level: RiskLevel = RiskLevel.SAFE
    """Risk assessment."""

    explanation: str = ""
    """Human-readable explanation of classification."""

    model_used: Literal["llm", "heuristic"] = "heuristic"
    """Which classifier was used."""

    latency_ms: float = 0.0
    """Milliseconds to classify."""

    matched_keywords: list[str] = field(default_factory=list)
    """Keywords that triggered classification."""

    timestamp: datetime = field(default_factory=datetime.utcnow)
    """When classification was computed."""

    def is_confident(self, threshold: float = 0.70) -> bool:
        """Check if confidence exceeds threshold."""
        return self.confidence >= threshold

    def is_safe_to_apply(self) -> bool:
        """Check if safe to apply without confirmation."""
        return self.risk_level in [RiskLevel.SAFE, RiskLevel.MEDIUM]

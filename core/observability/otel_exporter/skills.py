"""Phase 2: Skill Execution Tracing (OTEL Spans + Metrics).

ADR-0681 (Metrics Schema) + ADR-0682 (Multi-Tenant Learning):
- Skill execution → OTEL Span (latency, status, error)
- Input/Output size → OTEL Histograms
- Errors → OTEL Counter
- Trace correlation: delegation_router (root) → skill_exec (child)
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)


class SkillStatus(Enum):
    """Skill execution status (for OTEL events)."""
    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"


@dataclass(frozen=True)
class SkillExecutionMetrics:
    """Immutable metrics from a Skill execution.

    Maps to OTEL Metrics:
    - corvin.skill.execution_duration (Histogram, ms)
    - corvin.skill.input_size (Histogram, bytes)
    - corvin.skill.output_size (Histogram, bytes)
    - corvin.skill.errors_total (Counter)
    """
    skill_id: str
    skill_version: str
    execution_duration_ms: float  # Latency
    input_size_bytes: int
    output_size_bytes: int
    status: SkillStatus  # success | error | timeout
    error_type: Optional[str] = None  # If status=error
    error_message: Optional[str] = None  # Scrubbed for PII

    tenant_id: Optional[str] = None
    instance_id: Optional[str] = None


class SkillExecutionTracer:
    """Trace Skill executions (OTEL Spans + Metrics).

    Phase 2: Wires Skill execution into OTEL tracing.
    - Creates Span: skill_execute_<skill_id>
    - Records Histogram metrics (latency, sizes)
    - Records Counter metric (errors_total)
    - Correlates with delegation_router span (parent trace)

    TODO: Real OTEL SDK integration (when collector available)
    """

    def __init__(self, tenant_id: str, instance_id: str):
        self.tenant_id = tenant_id
        self.instance_id = instance_id
        # TODO: Initialize OTEL TracerProvider

    def start_skill_span(self, skill_id: str, skill_version: str, parent_trace_id: Optional[str] = None):
        """Create OTEL Span for skill execution.

        Args:
            skill_id: Name of the skill (e.g., "os.delegation_router")
            skill_version: Skill version (semver)
            parent_trace_id: Trace ID from delegation_router (for correlation)

        Returns: Span context (use with record_metrics)
        """
        # TODO: Real OTEL SDK
        # span = tracer.start_as_current_span(f"skill_execute_{skill_id}")
        # span.set_attribute("skill_id", skill_id)
        # span.set_attribute("skill_version", skill_version)
        # span.set_attribute("tenant_id", self.tenant_id)
        # span.set_attribute("trace_id", parent_trace_id) if parent_trace_id else None
        pass

    def record_metrics(self, metrics: SkillExecutionMetrics):
        """Record execution metrics as OTEL Metrics.

        Args:
            metrics: SkillExecutionMetrics from execution

        Emits:
            - corvin.skill.execution_duration (Histogram)
            - corvin.skill.input_size (Histogram)
            - corvin.skill.output_size (Histogram)
            - corvin.skill.errors_total (Counter, if error)
        """
        # TODO: Real OTEL SDK
        # meter.create_histogram("corvin.skill.execution_duration").record(
        #     metrics.execution_duration_ms,
        #     attributes={"skill_id": metrics.skill_id, "status": metrics.status.value}
        # )
        logger.info(
            f"Skill execution: {metrics.skill_id} v{metrics.skill_version} "
            f"duration={metrics.execution_duration_ms}ms status={metrics.status.value}"
        )

    def end_span(self):
        """End current OTEL Span."""
        # TODO: Real OTEL SDK
        pass


@dataclass(frozen=True)
class LearningFeedbackEvent:
    """Immutable learning feedback event (Phase 2).

    Maps to OTEL Event:
    - Event name: corvin.learning.feedback_received
    - Attributes: skill_id, feedback_type, signal, timestamp
    - Links: trace_id (correlate to skill execution)
    """
    tenant_id: str
    skill_id: str
    feedback_type: str  # "outcome" | "preference" | "confidence" | "metric"
    signal: float  # Numeric signal (0-1 for outcome, confidence; any for metric)
    instance_id: Optional[str] = None
    trace_id: Optional[str] = None  # Link to skill execution span

    def __post_init__(self):
        """Validate feedback_type."""
        allowed = {"outcome", "preference", "confidence", "metric"}
        if self.feedback_type not in allowed:
            raise ValueError(f"Invalid feedback_type: {self.feedback_type}. Must be one of {allowed}")


class LearningFeedbackSink:
    """Sink for learning feedback events (Phase 2).

    Receives feedback from multiple sources (Console, Discord, API)
    and emits OTEL Events for the learning optimizer to read.
    """

    def __init__(self, tenant_id: str):
        self.tenant_id = tenant_id
        # TODO: Initialize OTEL EventEmitter

    def receive_feedback(self, event: LearningFeedbackEvent) -> None:
        """Receive and emit learning feedback event.

        Args:
            event: LearningFeedbackEvent (immutable)

        Emits OTEL Event: corvin.learning.feedback_received
        """
        # TODO: Real OTEL SDK
        # event_emitter.emit_event(
        #     "corvin.learning.feedback_received",
        #     attributes={
        #         "tenant_id": event.tenant_id,
        #         "skill_id": event.skill_id,
        #         "feedback_type": event.feedback_type,
        #         "signal": event.signal,
        #         "trace_id": event.trace_id,
        #     }
        # )
        logger.info(
            f"Feedback: {event.skill_id} feedback_type={event.feedback_type} signal={event.signal}"
        )

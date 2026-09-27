"""Metrics emitter for learning loop observability (ADR-0637, Feature 3).

Provides:
- Task completion metrics (by task_type, engine, outcome)
- Cost per turn gauge (USD, by model)
- Skill confidence trends
- Integration with outcome_sink and SkillAdapter

This module wires the learning loop to OpenTelemetry observability, enabling
operators to track learning effectiveness via Prometheus/Grafana dashboards.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)


def get_otel_meter():
    """Get the OpenTelemetry meter (lazy init, fail-safe)."""
    try:
        from opentelemetry import metrics
        return metrics.get_meter(__name__)
    except ImportError:
        logger.warning("opentelemetry.metrics not available, metrics disabled")
        return None
    except Exception as e:
        logger.error(f"Failed to get OTEL meter: {e}", exc_info=True)
        return None


@dataclass
class TaskCompletionMetrics:
    """Metrics for a single task completion."""
    task_id: str
    task_type: Optional[str]
    engine: Optional[str]
    status: str  # "completed", "failed", "cancelled"
    duration_ms: Optional[int]
    exit_code: Optional[int]
    model_used: Optional[str] = None
    tokens_input: Optional[int] = None
    tokens_output: Optional[int] = None
    cost_usd: Optional[float] = None
    tenant_id: Optional[str] = None


def emit_task_completion_metrics(metrics_data: TaskCompletionMetrics) -> bool:
    """Emit task completion metrics to OTEL.

    Metrics emitted:
    - Counter: learning.task_completed (increments by 1)
      Attributes: task_type, engine, status (completed/failed/cancelled)
    - Histogram: learning.task_duration_ms
      Attributes: task_type, engine, status
    - Gauge: learning.task_success_rate (calculated per status)

    Args:
        metrics_data: TaskCompletionMetrics with task completion info

    Returns:
        True if metrics were successfully emitted
    """
    meter = get_otel_meter()
    if meter is None:
        return False

    try:
        # Task completion counter
        task_counter = meter.create_counter(
            name="learning.task_completed",
            description="Count of task completions",
            unit="1",
        )

        # Set attributes for this task
        attributes = {
            "task_type": metrics_data.task_type or "unknown",
            "engine": metrics_data.engine or "unknown",
            "status": metrics_data.status,
            "tenant_id": metrics_data.tenant_id or "default",
        }

        # Record task completion
        task_counter.add(1, attributes)

        # Task duration histogram
        if metrics_data.duration_ms is not None:
            duration_histogram = meter.create_histogram(
                name="learning.task_duration_ms",
                description="Task execution duration in milliseconds",
                unit="ms",
            )
            duration_histogram.record(metrics_data.duration_ms, attributes)

        # Cost gauge
        if metrics_data.cost_usd is not None:
            cost_gauge = meter.create_gauge(
                name="learning.cost_per_turn",
                description="Cost in USD per task turn",
                unit="USD",
            )
            cost_gauge.record(metrics_data.cost_usd, attributes)

        # Token count histogram
        if metrics_data.tokens_input is not None:
            input_histogram = meter.create_histogram(
                name="learning.tokens_input",
                description="Input tokens per task",
                unit="1",
            )
            input_histogram.record(metrics_data.tokens_input, attributes)

        if metrics_data.tokens_output is not None:
            output_histogram = meter.create_histogram(
                name="learning.tokens_output",
                description="Output tokens per task",
                unit="1",
            )
            output_histogram.record(metrics_data.tokens_output, attributes)

        return True

    except Exception as e:
        logger.error(f"Failed to emit task completion metrics: {e}", exc_info=True)
        return False


def emit_skill_confidence_metrics(
    skill_id: str,
    confidence_score: float,
    tenant_id: Optional[str] = None,
    version: Optional[str] = None,
) -> bool:
    """Emit skill confidence metrics to OTEL.

    Metrics emitted:
    - Gauge: learning.skill_confidence (0.0–1.0)
      Attributes: skill_id, version, tenant_id

    Args:
        skill_id: Skill identifier
        confidence_score: Confidence score (0.0–1.0)
        tenant_id: Tenant identifier
        version: Skill version

    Returns:
        True if metrics were successfully emitted
    """
    meter = get_otel_meter()
    if meter is None:
        return False

    try:
        if not 0.0 <= confidence_score <= 1.0:
            logger.warning(f"Invalid confidence score: {confidence_score}, clamping to [0.0, 1.0]")
            confidence_score = max(0.0, min(1.0, confidence_score))

        confidence_gauge = meter.create_gauge(
            name="learning.skill_confidence",
            description="Confidence score for a Skill (0.0–1.0)",
            unit="1",
        )

        attributes = {
            "skill_id": skill_id,
            "version": version or "unknown",
            "tenant_id": tenant_id or "default",
        }

        confidence_gauge.record(confidence_score, attributes)
        return True

    except Exception as e:
        logger.error(f"Failed to emit skill confidence metrics: {e}", exc_info=True)
        return False


def emit_optimizer_metrics(
    skill_id: str,
    epoch: int,
    improvement_pct: float,
    hypothesis_accepted: bool,
    tenant_id: Optional[str] = None,
) -> bool:
    """Emit optimizer iteration metrics to OTEL.

    Metrics emitted:
    - Counter: learning.optimizer_epoch (increments per epoch)
    - Histogram: learning.optimizer_improvement_pct
      Attributes: skill_id, epoch, hypothesis_accepted, tenant_id

    Args:
        skill_id: Skill identifier
        epoch: Current optimizer epoch
        improvement_pct: Improvement percentage from hypothesis
        hypothesis_accepted: Whether hypothesis was accepted
        tenant_id: Tenant identifier

    Returns:
        True if metrics were successfully emitted
    """
    meter = get_otel_meter()
    if meter is None:
        return False

    try:
        # Epoch counter
        epoch_counter = meter.create_counter(
            name="learning.optimizer_epoch",
            description="Optimizer epoch counter",
            unit="1",
        )

        attributes = {
            "skill_id": skill_id,
            "hypothesis_accepted": str(hypothesis_accepted).lower(),
            "tenant_id": tenant_id or "default",
        }

        epoch_counter.add(1, attributes)

        # Improvement histogram
        improvement_histogram = meter.create_histogram(
            name="learning.optimizer_improvement_pct",
            description="Improvement percentage per optimizer epoch",
            unit="%",
        )
        improvement_histogram.record(improvement_pct, attributes)

        return True

    except Exception as e:
        logger.error(f"Failed to emit optimizer metrics: {e}", exc_info=True)
        return False


def emit_learning_loop_metrics(
    *,
    task_id: str,
    task_type: Optional[str] = None,
    engine: Optional[str] = None,
    status: str = "completed",
    duration_ms: Optional[int] = None,
    exit_code: Optional[int] = None,
    cost_usd: Optional[float] = None,
    model_used: Optional[str] = None,
    tokens_input: Optional[int] = None,
    tokens_output: Optional[int] = None,
    tenant_id: Optional[str] = None,
) -> bool:
    """High-level API: emit all learning loop metrics at once.

    This is the primary entry point for task completion metrics. It consolidates
    all metric types into a single call (task completion, cost, tokens).

    Args:
        task_id: Task identifier
        task_type: Task classification (e.g., "code_review", "routing")
        engine: Engine used (e.g., "opus", "sonnet")
        status: Task status ("completed", "failed", "cancelled")
        duration_ms: Execution duration in milliseconds
        exit_code: Task exit code (0 for success, non-zero for failure)
        cost_usd: Task cost in USD
        model_used: Model ID used for computation
        tokens_input: Input tokens used
        tokens_output: Output tokens generated
        tenant_id: Tenant identifier

    Returns:
        True if all metrics were successfully emitted
    """
    metrics_data = TaskCompletionMetrics(
        task_id=task_id,
        task_type=task_type,
        engine=engine,
        status=status,
        duration_ms=duration_ms,
        exit_code=exit_code,
        model_used=model_used,
        tokens_input=tokens_input,
        tokens_output=tokens_output,
        cost_usd=cost_usd,
        tenant_id=tenant_id,
    )
    return emit_task_completion_metrics(metrics_data)


__all__ = [
    "TaskCompletionMetrics",
    "emit_task_completion_metrics",
    "emit_skill_confidence_metrics",
    "emit_optimizer_metrics",
    "emit_learning_loop_metrics",
    "get_otel_meter",
]

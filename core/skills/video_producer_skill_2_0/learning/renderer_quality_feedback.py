"""Learning Loop Integration — ADR-0314 Feedback for Renderers

Captures quality metrics from each renderer and emits feedback for model optimization.
"""

import logging
from dataclasses import dataclass
from typing import Dict, Optional
import time

logger = logging.getLogger(__name__)


@dataclass
class RendererMetrics:
    """Metrics from renderer execution"""
    renderer_type: str  # svg, effects, screencast, blender
    duration_ms: float
    frame_count: int
    output_size_bytes: int
    success: bool
    error: Optional[str] = None
    quality_score: Optional[float] = None  # 0-1


class RendererQualityFeedback:
    """Capture quality metrics for learning loop (ADR-0314)"""

    def __init__(self, tenant_id: str):
        self.tenant_id = tenant_id
        self.metrics_history = []

    def record_render_execution(self, metrics: RendererMetrics) -> None:
        """Record renderer execution metrics for learning

        Args:
            metrics: RendererMetrics from completed render
        """
        self.metrics_history.append(metrics)

        # Emit event for ADR-0314 learning loop
        event = {
            "event_type": "renderer_executed",
            "renderer": metrics.renderer_type,
            "duration_ms": metrics.duration_ms,
            "frame_count": metrics.frame_count,
            "success": metrics.success,
            "quality_score": metrics.quality_score,
            "tenant_id": self.tenant_id,
            "timestamp": time.time(),
        }

        logger.info(f"📊 Renderer metrics: {event}")

        # TODO: Emit to ADR-0314 event store
        # from core.learning.event_persistence import EventStore
        # EventStore.write_event("renderer_metrics", event)

    def compute_quality_score(self, metrics: RendererMetrics) -> float:
        """Compute quality score based on renderer metrics

        Args:
            metrics: RendererMetrics

        Returns:
            Quality score 0-1
        """
        if not metrics.success:
            return 0.0

        # Simple heuristic: combine duration + output size
        # (faster + larger = better quality in most cases)
        duration_score = max(0, 1.0 - metrics.duration_ms / 10000)  # Normalize to 10s
        size_score = min(1.0, metrics.output_size_bytes / 10_000_000)  # Normalize to 10MB

        quality = (duration_score * 0.3 + size_score * 0.7)
        return quality

    def suggest_renderer_config(self, renderer_type: str) -> Dict:
        """Suggest config changes based on feedback history

        Args:
            renderer_type: svg, effects, screencast, or blender

        Returns:
            Dict of suggested config changes
        """
        # Filter metrics for this renderer
        relevant = [m for m in self.metrics_history if m.renderer_type == renderer_type]

        if not relevant:
            return {}

        # Analyze patterns
        avg_duration = sum(m.duration_ms for m in relevant) / len(relevant)
        success_rate = sum(1 for m in relevant if m.success) / len(relevant)
        avg_quality = sum(m.quality_score or 0 for m in relevant) / len(relevant)

        suggestions = {}

        # For Blender: if duration too high, suggest lower samples
        if renderer_type == "blender" and avg_duration > 30000:  # 30s per frame
            suggestions["suggested_samples"] = 64  # Lower from 100

        # For Screencast: if quality low, suggest higher quality preset
        if renderer_type == "screencast" and avg_quality < 0.5:
            suggestions["suggested_quality"] = "high"

        # For Effects: if duration high, suggest fewer effects per frame
        if renderer_type == "effects" and avg_duration > 5000:  # 5s per frame sequence
            suggestions["max_effects_per_frame"] = 2

        return suggestions


__all__ = ["RendererQualityFeedback", "RendererMetrics"]

"""Confidence metrics aggregation for dashboard (k=4 Track A, ADR-0683 Phase 3).

Calculates:
- Confidence over time (convergence curves)
- Per-model trust scores
- Regression alerts
- Convergence rate (weeks to stable ±5%)
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from uuid import uuid4

from core.compliance.audit_chain_writer import AuditChainWriter, AuditEvent
from core.skills.models.learning_event import LearningEventStore


@dataclass
class ConfidenceMetric:
    """Aggregated confidence metric."""
    timestamp: datetime
    skill_id: str
    confidence: float
    success_rate: float
    feedback_engagement: float
    model_id: Optional[str] = None


class ConfidenceMetricsCalculator:
    """Aggregates confidence data for dashboard visualization.

    Audit-FIRST: dashboard_view and metric_accessed events written to audit chain,
    fail-closed on commit failure.
    """

    def __init__(
        self,
        tenant_id: str,
        audit_chain: AuditChainWriter,
        event_store: LearningEventStore,
    ):
        """Initialize metrics calculator.

        Args:
            tenant_id: Tenant scope
            audit_chain: AuditChainWriter for audit trail
            event_store: LearningEventStore for metric source
        """
        self.tenant_id = tenant_id
        self.audit_chain = audit_chain
        self.event_store = event_store
        self._cache: Dict[str, Dict] = {}
        self._cache_ttl_seconds = 60

    def get_confidence_history(
        self,
        skill_id: str,
        hours: int = 24,
    ) -> Dict:
        """Get confidence history for a skill.

        Args:
            skill_id: Skill to retrieve
            hours: Hours of history (default 24)

        Returns:
            {
                "skill_id": str,
                "data_points": [
                    {"timestamp": str, "confidence": float, "convergence_band_upper": float, "convergence_band_lower": float},
                    ...
                ],
                "current_confidence": float,
                "converged": bool,
                "peak_confidence": float,
                "regression_alert": bool,
            }

        Raises:
            RuntimeError: If audit chain write fails (fail-closed)
        """
        start_time = time.time()

        # Check cache
        cache_key = f"{skill_id}:{hours}h"
        if cache_key in self._cache:
            cached_time, cached_data = self._cache[cache_key]
            if time.time() - cached_time < self._cache_ttl_seconds:
                return cached_data

        # Aggregate from event store
        events = self.event_store.get_events()
        cutoff = datetime.utcnow() - timedelta(hours=hours)

        data_points = []
        for event in events:
            if event.timestamp < cutoff:
                continue
            if event.skill_id != skill_id:
                continue

            confidence = event.output.get("confidence", 0.0)
            data_points.append({
                "timestamp": event.timestamp.isoformat(),
                "confidence": confidence,
                "convergence_band_upper": confidence + 0.05,
                "convergence_band_lower": confidence - 0.05,
            })

        # Compute aggregates
        confidences = [dp["confidence"] for dp in data_points]
        current_confidence = confidences[-1] if confidences else 0.0
        peak_confidence = max(confidences) if confidences else 0.0
        converged = len(confidences) >= 10  # Placeholder

        result = {
            "skill_id": skill_id,
            "data_points": data_points,
            "current_confidence": current_confidence,
            "converged": converged,
            "peak_confidence": peak_confidence,
            "regression_alert": False,  # Placeholder
        }

        # **AUDIT-FIRST:** Write dashboard_view event
        try:
            self._write_audit_event("dashboard_view", skill_id, result, start_time)
        except Exception as e:
            raise RuntimeError(f"Audit chain write failed for dashboard view: {e}")

        # Cache result
        self._cache[cache_key] = (time.time(), result)
        return result

    def get_per_model_confidence(self) -> Dict[str, float]:
        """Get trust scores for all models (Opus, Sonnet, Haiku).

        Returns:
            {
                "claude-opus-4": 0.85,
                "claude-sonnet-3": 0.72,
                "claude-haiku-4-5": 0.68,
            }

        Raises:
            RuntimeError: If audit chain write fails (fail-closed)
        """
        start_time = time.time()

        events = self.event_store.get_events()
        model_scores: Dict[str, List[float]] = {}

        for event in events:
            model_id = event.output.get("model_id")
            if model_id:
                if model_id not in model_scores:
                    model_scores[model_id] = []
                confidence = event.output.get("confidence", 0.0)
                model_scores[model_id].append(confidence)

        # Average per model
        result = {
            model_id: sum(scores) / len(scores)
            for model_id, scores in model_scores.items()
        }

        # **AUDIT-FIRST:** Write metric_accessed event
        try:
            self._write_audit_event("metric_accessed", "per_model_confidence", result, start_time)
        except Exception as e:
            raise RuntimeError(f"Audit chain write failed for per-model metrics: {e}")

        return result

    def get_convergence_rate(self, skill_id: str) -> Dict:
        """Estimate weeks until convergence (±5% stable band).

        Args:
            skill_id: Skill to analyze

        Returns:
            {
                "skill_id": str,
                "estimated_weeks": float,
                "current_variance": float,
                "convergence_threshold": float,
            }

        Raises:
            RuntimeError: If audit chain write fails (fail-closed)
        """
        start_time = time.time()

        events = self.event_store.get_events()
        confidences = [
            e.output.get("confidence", 0.0)
            for e in events
            if e.skill_id == skill_id
        ]

        if len(confidences) < 2:
            result = {
                "skill_id": skill_id,
                "estimated_weeks": 0.0,
                "current_variance": 0.0,
                "convergence_threshold": 0.05,
            }
        else:
            mean = sum(confidences) / len(confidences)
            variance = sum((x - mean) ** 2 for x in confidences) / len(confidences)
            std_dev = variance ** 0.5

            # Estimate weeks to converge (placeholder formula)
            estimated_weeks = max(0.0, (std_dev - 0.05) * 4)

            result = {
                "skill_id": skill_id,
                "estimated_weeks": estimated_weeks,
                "current_variance": variance,
                "convergence_threshold": 0.05,
            }

        # **AUDIT-FIRST:** Write metric_accessed event
        try:
            self._write_audit_event("metric_accessed", "convergence_rate", result, start_time)
        except Exception as e:
            raise RuntimeError(f"Audit chain write failed for convergence rate: {e}")

        return result

    def _write_audit_event(
        self,
        event_type: str,
        skill_or_metric: str,
        result: Dict,
        start_time: float,
    ) -> None:
        """Write audit event (fail-closed)."""
        latency_ms = (time.time() - start_time) * 1000

        audit_event = AuditEvent(
            event_id=str(uuid4()),
            event_type=event_type,
            tenant_id=self.tenant_id,
            user_id=None,
            timestamp=datetime.utcnow().isoformat(),
            details={
                "skill_or_metric": skill_or_metric,
                "event_type": event_type,
                **result,
                "latency_ms": latency_ms,
            },
            severity="INFO",
        )

        try:
            self.audit_chain.write_event(audit_event)
        except IOError as e:
            raise IOError(f"Failed to write {event_type} event to audit chain: {e}")

    def clear_cache(self) -> None:
        """Clear metric cache (for testing)."""
        self._cache.clear()

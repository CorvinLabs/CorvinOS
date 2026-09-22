"""Feedback Triage & Prioritization Engine (Phase 9).

Automatically assigns priority to feedback based on:
  - Severity (for bugs)
  - NPS score (for surveys)
  - Component criticality
  - Environmental impact
  - Reproduction complexity
"""

import logging
from typing import Optional
from .feedback_models import (
    FeedbackReport,
    FeedbackType,
    FeedbackSeverity,
    FeedbackPriority,
    TriagedFeedback,
)

logger = logging.getLogger(__name__)


class TriageEngine:
    """Automatic triage prioritization (fail-closed, deterministic)."""

    # Component severity mapping
    COMPONENT_CRITICALITY = {
        "console": 1.0,      # Core interface
        "voice": 0.95,       # Voice interaction critical
        "video_producer": 0.8,  # High-value feature
        "learning": 0.7,     # Important but non-blocking
        "marketplace": 0.6,  # Nice-to-have
        "settings": 0.5,     # Configuration
        "docs": 0.3,         # Documentation
    }

    # Environment multiplier
    ENVIRONMENT_MULTIPLIER = {
        "production": 1.5,  # Production = highest priority
        "staging": 1.2,
        "local": 1.0,
    }

    def triage(self, report: FeedbackReport) -> TriagedFeedback:
        """Assign priority to feedback report (deterministic).

        Returns:
            TriagedFeedback with P0-P3 priority and reasoning
        """
        if not report.tenant_id:
            raise ValueError("Feedback report must have tenant_id")

        # Calculate priority score (0-10 scale)
        priority_score = self._calculate_priority_score(report)

        # Estimate effort
        estimated_effort = self._estimate_effort(report)

        # Convert score to priority
        if priority_score >= 9.0:
            priority = FeedbackPriority.P0
            reason = "Critical issue affecting core functionality"
        elif priority_score >= 7.0:
            priority = FeedbackPriority.P1
            reason = "High-priority issue affecting important features"
        elif priority_score >= 4.0:
            priority = FeedbackPriority.P2
            reason = "Medium-priority issue for backlog"
        else:
            priority = FeedbackPriority.P3
            reason = "Low-priority issue or nice-to-have feature"

        return TriagedFeedback(
            feedback_report=report,
            priority=priority,
            triage_reason=reason,
            estimated_effort=estimated_effort,
            assigned_to=None,  # Auto-assignment TBD
        )

    def _calculate_priority_score(self, report: FeedbackReport) -> float:
        """Calculate priority score (0-10).

        Factors:
          - Bug severity (8-10 for critical)
          - NPS score (<4 = critical, 4-6 = medium, 7+ = low)
          - Component criticality
          - Environment impact
          - Feature request momentum (TBD: multi-report count)
        """
        score = 5.0  # Base score

        # Factor 1: Feedback type
        if report.feedback_type == FeedbackType.BUG_REPORT:
            if report.severity == FeedbackSeverity.CRITICAL:
                score += 3.5
            elif report.severity == FeedbackSeverity.HIGH:
                score += 2.5
            elif report.severity == FeedbackSeverity.MEDIUM:
                score += 1.5
            else:
                score += 0.5
        elif report.feedback_type == FeedbackType.NPS_SURVEY:
            if report.nps_score is not None:
                if report.nps_score <= 3:  # Detractors
                    score += 3.0
                elif report.nps_score <= 6:  # Passives
                    score += 1.0
                else:  # Promoters
                    score -= 1.0
        elif report.feedback_type == FeedbackType.FEATURE_REQUEST:
            score += 1.0  # Base feature request

        # Factor 2: Component criticality
        component_weight = self.COMPONENT_CRITICALITY.get(report.component, 0.5)
        score += (component_weight * 2.0)

        # Factor 3: Environment multiplier
        env_multiplier = self.ENVIRONMENT_MULTIPLIER.get(report.environment, 1.0)
        score *= env_multiplier

        # Factor 4: Reproduction steps provided
        if report.reproduction_steps:
            score += 0.5  # Easier to fix = prioritize

        # Cap score at 10.0
        return min(score, 10.0)

    def _estimate_effort(self, report: FeedbackReport) -> str:
        """Estimate effort to fix (fail-closed, conservative)."""
        if report.feedback_type == FeedbackType.NPS_SURVEY:
            return "feedback_only"  # No fix needed

        if report.feedback_type == FeedbackType.FEATURE_REQUEST:
            return "multi_day"  # Features take time

        # Bug effort estimation
        if report.reproduction_steps:
            # Reproducible = faster fix
            if "simple" in report.reproduction_steps.lower():
                return "quick_fix"  # <30 min
            elif "console" in report.component:
                return "1-2h"
            else:
                return "half_day"  # 2-4h
        else:
            # Non-reproducible bugs = harder
            return "half_day"

    def batch_triage(self, reports: list[FeedbackReport]) -> list[TriagedFeedback]:
        """Triage multiple reports (parallel-safe)."""
        results = []
        for report in reports:
            try:
                triaged = self.triage(report)
                results.append(triaged)
            except Exception as e:
                logger.error(f"triage_error: {report.feedback_id}: {e}")
                continue
        return results


__all__ = ["TriageEngine"]

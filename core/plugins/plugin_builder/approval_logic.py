"""Phase 4: Tiered Approval Logic — Rules engine for test review decisions.

Three tiers of approval based on risk:
- Tier 1 (Low Risk): Simple unit tests → Auto-approve if coverage > threshold
- Tier 2 (Medium Risk): Integration tests → Require senior engineer review
- Tier 3 (High Risk): E2E tests → Require two approvers + risk assessment

ADR-0262 Extended: Approval Rules (Phase 4.2)
"""
from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class ReviewerRole(str, Enum):
    """Reviewer roles for approval."""

    JUNIOR_ENGINEER = "junior"
    SENIOR_ENGINEER = "senior"
    TECH_LEAD = "tech_lead"
    MAINTAINER = "maintainer"


class DecisionReason(str, Enum):
    """Reason codes for approval decisions."""

    AUTO_APPROVED = "auto_approved"  # Tier 1 only, high coverage
    APPROVED_BY_SENIOR = "approved_by_senior"  # Tier 2/3
    APPROVED_BY_TECH_LEAD = "approved_by_tech_lead"  # Tier 3 override
    REJECTED_LOW_COVERAGE = "rejected_low_coverage"
    REJECTED_RISKY_PATTERN = "rejected_risky_pattern"
    REJECTED_BY_REVIEWER = "rejected_by_reviewer"
    ESCALATED = "escalated"


@dataclass(frozen=True)
class ReviewDecision:
    """Immutable approval decision record."""

    status: str  # "approved", "rejected", "escalated", "pending"
    reason: DecisionReason
    reviewer: Optional[str] = None
    reviewer_role: Optional[ReviewerRole] = None
    comment: str = ""
    timestamp: str = dataclasses.field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        """Serialize to JSON-compatible dict."""
        return {
            "status": self.status,
            "reason": self.reason.value,
            "reviewer": self.reviewer,
            "reviewer_role": self.reviewer_role.value if self.reviewer_role else None,
            "comment": self.comment,
            "timestamp": self.timestamp,
        }


@dataclass
class ApprovalRequirements:
    """Approval requirements for each tier."""

    min_coverage_percent: float
    min_reviewers: int
    min_reviewer_role: ReviewerRole
    requires_risk_assessment: bool = False
    auto_approve_enabled: bool = False


class ApprovalRulesEngine:
    """Rules-based approval decision engine."""

    # Default requirements per tier
    TIER_1_REQUIREMENTS = ApprovalRequirements(
        min_coverage_percent=80.0,
        min_reviewers=0,  # Can auto-approve
        min_reviewer_role=ReviewerRole.JUNIOR_ENGINEER,
        auto_approve_enabled=True,
    )

    TIER_2_REQUIREMENTS = ApprovalRequirements(
        min_coverage_percent=75.0,
        min_reviewers=1,
        min_reviewer_role=ReviewerRole.SENIOR_ENGINEER,
        requires_risk_assessment=False,
    )

    TIER_3_REQUIREMENTS = ApprovalRequirements(
        min_coverage_percent=85.0,
        min_reviewers=2,
        min_reviewer_role=ReviewerRole.TECH_LEAD,
        requires_risk_assessment=True,
    )

    def __init__(self):
        """Initialize rules engine with default requirements."""
        self.requirements = {
            "tier_1": self.TIER_1_REQUIREMENTS,
            "tier_2": self.TIER_2_REQUIREMENTS,
            "tier_3": self.TIER_3_REQUIREMENTS,
        }

    def evaluate_tier_1(
        self,
        coverage_percent: float,
        test_count: int,
        has_risky_patterns: bool = False,
    ) -> ReviewDecision:
        """Evaluate Tier 1 (Simple Unit Tests) — may auto-approve."""
        req = self.requirements["tier_1"]

        if has_risky_patterns:
            logger.warning("Tier 1 test has risky patterns, escalating")
            return ReviewDecision(
                status="escalated",
                reason=DecisionReason.REJECTED_RISKY_PATTERN,
                comment="Risky patterns detected (e.g., mocking system boundaries, weak assertions)",
            )

        if coverage_percent < req.min_coverage_percent:
            return ReviewDecision(
                status="rejected",
                reason=DecisionReason.REJECTED_LOW_COVERAGE,
                comment=f"Coverage {coverage_percent:.1f}% below threshold {req.min_coverage_percent}%",
            )

        if req.auto_approve_enabled:
            logger.info(f"Tier 1 auto-approved: {test_count} tests, {coverage_percent:.1f}% coverage")
            return ReviewDecision(
                status="approved",
                reason=DecisionReason.AUTO_APPROVED,
                comment=f"Auto-approved: {test_count} unit tests with {coverage_percent:.1f}% coverage",
            )

        return ReviewDecision(
            status="pending",
            reason=DecisionReason.ESCALATED,
            comment="Manual review required",
        )

    def evaluate_tier_2(
        self,
        coverage_percent: float,
        test_count: int,
        reviewer: Optional[str] = None,
        reviewer_role: Optional[ReviewerRole] = None,
    ) -> ReviewDecision:
        """Evaluate Tier 2 (Integration Tests) — require senior review."""
        req = self.requirements["tier_2"]

        if coverage_percent < req.min_coverage_percent:
            return ReviewDecision(
                status="rejected",
                reason=DecisionReason.REJECTED_LOW_COVERAGE,
                comment=f"Coverage {coverage_percent:.1f}% below threshold {req.min_coverage_percent}%",
                reviewer=reviewer,
                reviewer_role=reviewer_role,
            )

        if not reviewer:
            return ReviewDecision(
                status="pending",
                reason=DecisionReason.ESCALATED,
                comment=f"Requires review from {req.min_reviewer_role.value} or higher",
            )

        if not reviewer_role or reviewer_role.value < req.min_reviewer_role.value:
            return ReviewDecision(
                status="pending",
                reason=DecisionReason.ESCALATED,
                comment=f"Reviewer must be {req.min_reviewer_role.value} or higher",
                reviewer=reviewer,
                reviewer_role=reviewer_role,
            )

        logger.info(f"Tier 2 approved by {reviewer_role.value}: {reviewer}")
        return ReviewDecision(
            status="approved",
            reason=DecisionReason.APPROVED_BY_SENIOR,
            reviewer=reviewer,
            reviewer_role=reviewer_role,
            comment=f"Integration tests reviewed and approved by {reviewer_role.value}",
        )

    def evaluate_tier_3(
        self,
        coverage_percent: float,
        test_count: int,
        reviewers: list[tuple[str, ReviewerRole]] | None = None,
        risk_assessment: Optional[str] = None,
    ) -> ReviewDecision:
        """Evaluate Tier 3 (End-to-End Tests) — require 2 approvals + risk assessment."""
        req = self.requirements["tier_3"]

        if coverage_percent < req.min_coverage_percent:
            return ReviewDecision(
                status="rejected",
                reason=DecisionReason.REJECTED_LOW_COVERAGE,
                comment=f"Coverage {coverage_percent:.1f}% below threshold {req.min_coverage_percent}%",
            )

        if not reviewers or len(reviewers) < req.min_reviewers:
            return ReviewDecision(
                status="pending",
                reason=DecisionReason.ESCALATED,
                comment=f"Requires {req.min_reviewers} approvers (at least {req.min_reviewer_role.value})",
            )

        # Verify all reviewers have required role
        tech_leads = [r for r in reviewers if r[1] == ReviewerRole.TECH_LEAD]
        if not tech_leads:
            return ReviewDecision(
                status="pending",
                reason=DecisionReason.ESCALATED,
                comment=f"Requires at least one {ReviewerRole.TECH_LEAD.value} approval",
            )

        if req.requires_risk_assessment and not risk_assessment:
            return ReviewDecision(
                status="pending",
                reason=DecisionReason.ESCALATED,
                comment="Risk assessment required before E2E approval",
            )

        reviewer_names = [r[0] for r in reviewers]
        logger.info(f"Tier 3 approved by {len(reviewers)} reviewers: {', '.join(reviewer_names)}")

        return ReviewDecision(
            status="approved",
            reason=DecisionReason.APPROVED_BY_TECH_LEAD,
            reviewer="; ".join(reviewer_names),
            reviewer_role=ReviewerRole.TECH_LEAD,
            comment=f"E2E tests approved by {len(reviewers)} reviewers (risk: {risk_assessment})",
        )

    def check_rubber_stamp(
        self,
        decisions: list[ReviewDecision],
        time_window_seconds: int = 300,
    ) -> dict:
        """Detect rubber-stamp approvals: multiple approvals in short time."""
        if len(decisions) < 2:
            return {"is_rubber_stamp": False, "confidence": 0.0}

        # Parse timestamps
        try:
            timestamps = [datetime.fromisoformat(d.timestamp) for d in decisions]
        except (ValueError, AttributeError):
            logger.warning("Cannot parse decision timestamps for rubber-stamp detection")
            return {"is_rubber_stamp": False, "confidence": 0.0}

        # Check if all approvals are in a narrow time window
        time_diffs = []
        for i in range(1, len(timestamps)):
            diff = (timestamps[i] - timestamps[i - 1]).total_seconds()
            time_diffs.append(diff)

        if not time_diffs:
            return {"is_rubber_stamp": False, "confidence": 0.0}

        max_diff = max(time_diffs)
        is_suspicious = max_diff < time_window_seconds

        # Calculate confidence: closer to zero = more suspicious
        confidence = 1.0 - (max_diff / time_window_seconds) if is_suspicious else 0.0
        confidence = max(0.0, min(1.0, confidence))

        return {
            "is_rubber_stamp": is_suspicious,
            "confidence": confidence,
            "max_time_delta_seconds": max_diff,
            "decision_count": len(decisions),
        }

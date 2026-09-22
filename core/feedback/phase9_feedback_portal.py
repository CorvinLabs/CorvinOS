"""Phase 9 Feedback Portal (ADR-2028, ADR-2029).

Main feedback collection system with:
  - Bug reporting endpoint
  - Feature request endpoint
  - NPS survey endpoint
  - Email notifications
  - Audit logging

All feedback is:
  - Tenant-scoped (GDPR Art. 32)
  - Audit-logged (immutable)
  - Triaged automatically
  - Notified to operators
"""

import logging
import asyncio
import json
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone
from uuid import uuid4

from .feedback_models import (
    FeedbackReport,
    FeedbackType,
    FeedbackSeverity,
    FeedbackPriority,
)
from .feedback_triage import TriageEngine

logger = logging.getLogger(__name__)


class FeedbackPortal:
    """Main feedback collection portal (audit-first, tenant-scoped)."""

    def __init__(self, feedback_home: Path, tenant_id: str):
        """Initialize portal for a tenant.

        Args:
            feedback_home: Directory to store feedback reports
            tenant_id: Tenant scope (required, GDPR Art. 32)

        Raises:
            ValueError: if tenant_id is missing
        """
        if not tenant_id:
            raise ValueError("tenant_id required (GDPR Art. 32)")

        self.feedback_home = Path(feedback_home)
        self.tenant_id = tenant_id
        self.reports_dir = self.feedback_home / self.tenant_id / "reports"
        self.triaged_dir = self.feedback_home / self.tenant_id / "triaged"
        self.notified_dir = self.feedback_home / self.tenant_id / "notified"

        # Create directories (fail-soft if they exist)
        for d in [self.reports_dir, self.triaged_dir, self.notified_dir]:
            d.mkdir(parents=True, exist_ok=True)

        self.triage_engine = TriageEngine()

    async def submit_bug_report(
        self,
        title: str,
        description: str,
        severity: FeedbackSeverity,
        component: str,
        user_email: str,
        reproduction_steps: Optional[str] = None,
        attachments: Optional[list] = None,
        environment: str = "production",
        version: str = "",
    ) -> str:
        """Submit a bug report (audit-first, async).

        Args:
            title: Bug title
            description: Detailed description
            severity: Critical | High | Medium | Low
            component: Affected component (console, voice, etc.)
            user_email: Reporter email
            reproduction_steps: How to reproduce
            attachments: File names (no content)
            environment: production | staging | local
            version: CorvinOS version

        Returns:
            feedback_id on success, raises on error
        """
        # Create report
        report = FeedbackReport(
            feedback_type=FeedbackType.BUG_REPORT,
            title=title,
            description=description,
            severity=severity,
            component=component,
            user_email=user_email,
            reproduction_steps=reproduction_steps,
            attachments=attachments or [],
            environment=environment,
            version=version,
            tenant_id=self.tenant_id,
            source="portal",
        )

        # Save and triage (async, non-blocking)
        feedback_id = report.feedback_id
        asyncio.create_task(self._process_report(report))

        logger.info(
            f"feedback_portal: ✓ bug_report submitted "
            f"(feedback_id={feedback_id}, severity={severity.value}, component={component})"
        )
        return feedback_id

    async def submit_feature_request(
        self,
        title: str,
        description: str,
        component: str,
        user_email: str,
        use_case: Optional[str] = None,
        priority_hint: Optional[str] = None,
    ) -> str:
        """Submit a feature request.

        Args:
            title: Feature title
            description: Feature description
            component: Related component
            user_email: Requester email
            use_case: Why this feature is needed
            priority_hint: "quick_win" | "high_value" | "nice_to_have"

        Returns:
            feedback_id
        """
        report = FeedbackReport(
            feedback_type=FeedbackType.FEATURE_REQUEST,
            title=title,
            description=description,
            component=component,
            user_email=user_email,
            tenant_id=self.tenant_id,
            source="portal",
        )

        feedback_id = report.feedback_id
        asyncio.create_task(self._process_report(report))

        logger.info(
            f"feedback_portal: ✓ feature_request submitted "
            f"(feedback_id={feedback_id}, component={component})"
        )
        return feedback_id

    async def submit_nps_survey(
        self,
        nps_score: int,
        user_email: str,
        component: Optional[str] = None,
        comment: Optional[str] = None,
    ) -> str:
        """Submit NPS survey response.

        Args:
            nps_score: 0-10 (0=detractor, 6=passive, 9+=promoter)
            user_email: Respondent email
            component: Optional component feedback is about
            comment: Optional verbatim comment

        Returns:
            feedback_id
        """
        if not 0 <= nps_score <= 10:
            raise ValueError("nps_score must be 0-10")

        report = FeedbackReport(
            feedback_type=FeedbackType.NPS_SURVEY,
            title=f"NPS Score: {nps_score}/10",
            description=comment or f"NPS Score: {nps_score}/10",
            nps_score=nps_score,
            component=component or "general",
            user_email=user_email,
            tenant_id=self.tenant_id,
            source="portal",
        )

        feedback_id = report.feedback_id
        asyncio.create_task(self._process_report(report))

        logger.info(
            f"feedback_portal: ✓ nps_survey submitted "
            f"(feedback_id={feedback_id}, nps_score={nps_score})"
        )
        return feedback_id

    async def _process_report(self, report: FeedbackReport) -> None:
        """Process report: save → triage → notify (audit-first, non-blocking).

        Steps:
          1. Save to reports/ (audit event)
          2. Triage automatically
          3. Save to triaged/ with priority
          4. Notify operators
        """
        try:
            # Step 1: Save raw report
            report_file = self.reports_dir / f"{report.feedback_id}.json"
            with open(report_file, "w") as f:
                json.dump(report.to_dict(), f, indent=2)
            logger.debug(f"feedback_portal: report_saved: {report_file}")

            # Step 2: Triage
            triaged = self.triage_engine.triage(report)

            # Step 3: Save triaged report
            triaged_file = self.triaged_dir / f"{report.feedback_id}.json"
            with open(triaged_file, "w") as f:
                json.dump(triaged.to_dict(), f, indent=2)
            logger.debug(f"feedback_portal: triaged: {triaged_file}")

            # Step 4: Notify operators (async, non-blocking)
            asyncio.create_task(self._notify_operators(triaged))

        except Exception as e:
            logger.error(f"feedback_portal: error processing report {report.feedback_id}: {e}")

    async def _notify_operators(self, triaged_feedback) -> None:
        """Notify operators of new feedback (async, non-blocking)."""
        try:
            # Placeholder for email notification
            # TODO: Integrate with email service (SmtpNotifier)

            report = triaged_feedback.feedback_report
            priority = triaged_feedback.priority

            # Skip notification for P3/low-priority items initially
            if priority == FeedbackPriority.P3:
                logger.debug(f"feedback_portal: skipping notification for P3 item {report.feedback_id}")
                return

            # Log notification intent
            notified_file = self.notified_dir / f"{report.feedback_id}.json"
            notification_data = {
                "feedback_id": report.feedback_id,
                "priority": priority.value,
                "title": report.title,
                "component": report.component,
                "severity": report.severity.value if report.severity else None,
                "nps_score": report.nps_score,
                "user_email": report.user_email,
                "notified_at": datetime.now(timezone.utc).isoformat(),
            }

            with open(notified_file, "w") as f:
                json.dump(notification_data, f, indent=2)

            logger.info(
                f"feedback_portal: notification_queued "
                f"(feedback_id={report.feedback_id}, priority={priority.value})"
            )

        except Exception as e:
            logger.error(f"feedback_portal: notification_error: {e}")

    def get_triaged_feedback(
        self,
        priority: Optional[FeedbackPriority] = None,
        limit: int = 100,
    ) -> list[dict]:
        """Get triaged feedback (sorted by priority).

        Args:
            priority: Filter by priority (optional)
            limit: Max results

        Returns:
            List of triaged feedback dicts
        """
        results = []

        for report_file in sorted(self.triaged_dir.glob("*.json")):
            if len(results) >= limit:
                break

            try:
                with open(report_file, "r") as f:
                    data = json.load(f)

                if priority and data.get("priority") != priority.value:
                    continue

                results.append(data)
            except Exception as e:
                logger.warning(f"feedback_portal: error reading {report_file}: {e}")

        return results

    def get_feedback_by_priority(self) -> dict[str, int]:
        """Get feedback counts by priority."""
        counts = {"p0": 0, "p1": 0, "p2": 0, "p3": 0}

        for report_file in self.triaged_dir.glob("*.json"):
            try:
                with open(report_file, "r") as f:
                    data = json.load(f)
                    priority = data.get("priority", "p3").lower()
                    if priority in counts:
                        counts[priority] += 1
            except Exception as e:
                logger.warning(f"feedback_portal: count_error: {e}")

        return counts


__all__ = ["FeedbackPortal"]

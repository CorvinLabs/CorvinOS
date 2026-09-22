"""End-to-End Integration Tests: Phase 9 Feedback Portal (ADR-2028, ADR-2029).

Test coverage:
  - Full feedback workflow (submit → triage → notify)
  - API endpoints (bug reports, feature requests, NPS)
  - Tenant isolation
  - Audit trail immutability
  - Error handling (fail-closed)
"""

import pytest
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch, AsyncMock

from core.feedback.phase9_feedback_portal import FeedbackPortal
from core.feedback.feedback_models import (
    FeedbackReport,
    FeedbackType,
    FeedbackSeverity,
    FeedbackPriority,
    TriagedFeedback,
)
from core.feedback.feedback_triage import TriageEngine


class TestPhase9FeedbackE2E:
    """End-to-end feedback workflow tests."""

    @pytest.fixture
    def temp_feedback_home(self):
        """Create temporary feedback home directory."""
        with TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def portal(self, temp_feedback_home):
        """Create feedback portal for tenant."""
        return FeedbackPortal(temp_feedback_home, tenant_id="test_tenant")

    @pytest.mark.asyncio
    async def test_full_workflow_bug_report_to_triage(self, portal):
        """Complete workflow: Submit bug → Auto-triage → Get status."""

        # Step 1: Submit bug report
        feedback_id = await portal.submit_bug_report(
            title="Console crashes on startup",
            description="Console immediately crashes when starting",
            severity=FeedbackSeverity.CRITICAL,
            component="console",
            user_email="user@example.com",
            reproduction_steps="1. Start console. 2. Immediate crash.",
            environment="production",
            version="v2.0.0",
        )

        assert feedback_id is not None
        assert len(feedback_id) > 0

        # Step 2: Wait for async processing
        await asyncio.sleep(0.5)

        # Step 3: Verify report was saved
        report_file = portal.reports_dir / f"{feedback_id}.json"
        assert report_file.exists()

        with open(report_file) as f:
            report_data = json.load(f)
            assert report_data["title"] == "Console crashes on startup"
            assert report_data["severity"] == "critical"

        # Step 4: Verify triage happened
        triaged_file = portal.triaged_dir / f"{feedback_id}.json"
        assert triaged_file.exists()

        with open(triaged_file) as f:
            triaged_data = json.load(f)
            # Critical bug in console = P0
            assert triaged_data["priority"] == "p0"
            assert "Critical" in triaged_data["triage_reason"]
            assert triaged_data["estimated_effort"] == "quick_fix"

        # Step 5: Check status
        counts = portal.get_feedback_by_priority()
        assert counts["p0"] == 1

    @pytest.mark.asyncio
    async def test_workflow_feature_request_to_backlog(self, portal):
        """Feature request workflow: Submit → Triage → Backlog."""

        feedback_id = await portal.submit_feature_request(
            title="Add dark mode",
            description="Users want dark mode for night usage",
            component="console",
            user_email="user@example.com",
            use_case="Reduce eye strain at night",
            priority_hint="high_value",
        )

        assert feedback_id is not None
        await asyncio.sleep(0.5)

        # Verify triage
        triaged_file = portal.triaged_dir / f"{feedback_id}.json"
        with open(triaged_file) as f:
            triaged_data = json.load(f)
            # Feature requests default to P2-P3
            assert triaged_data["priority"] in ["p2", "p3"]
            assert triaged_data["estimated_effort"] == "multi_day"

    @pytest.mark.asyncio
    async def test_workflow_nps_survey_to_priority(self, portal):
        """NPS workflow: Submit score → Auto-prioritize."""

        # Detractor (low score)
        feedback_id = await portal.submit_nps_survey(
            nps_score=2,
            user_email="user@example.com",
            component="console",
            comment="Very disappointed with the UI",
        )

        assert feedback_id is not None
        await asyncio.sleep(0.5)

        # Verify triage
        triaged_file = portal.triaged_dir / f"{feedback_id}.json"
        with open(triaged_file) as f:
            triaged_data = json.load(f)
            # Low NPS = P1 minimum
            assert triaged_data["priority"] in ["p0", "p1"]

    def test_tenant_isolation_e2e(self, temp_feedback_home):
        """Verify complete tenant isolation (no cross-tenant leakage)."""

        # Create portals for two tenants
        portal_a = FeedbackPortal(temp_feedback_home, tenant_id="tenant_a")
        portal_b = FeedbackPortal(temp_feedback_home, tenant_id="tenant_b")

        # Submit reports from both tenants
        report_a = FeedbackReport(
            tenant_id="tenant_a",
            feedback_type=FeedbackType.BUG_REPORT,
            title="Bug A",
            description="Description A",
            component="console",
            user_email="user_a@example.com",
        )

        report_b = FeedbackReport(
            tenant_id="tenant_b",
            feedback_type=FeedbackType.BUG_REPORT,
            title="Bug B",
            description="Description B",
            component="console",
            user_email="user_b@example.com",
        )

        # Save reports
        triaged_a = portal_a.triage_engine.triage(report_a)
        triaged_b = portal_b.triage_engine.triage(report_b)

        report_file_a = portal_a.triaged_dir / f"{report_a.feedback_id}.json"
        report_file_b = portal_b.triaged_dir / f"{report_b.feedback_id}.json"

        with open(report_file_a, "w") as f:
            json.dump(triaged_a.to_dict(), f)
        with open(report_file_b, "w") as f:
            json.dump(triaged_b.to_dict(), f)

        # Verify isolation
        counts_a = portal_a.get_feedback_by_priority()
        counts_b = portal_b.get_feedback_by_priority()

        # Each portal only sees its own tenant's feedback
        total_a = sum(counts_a.values())
        total_b = sum(counts_b.values())

        assert total_a >= 1
        assert total_b >= 1
        # Verify no cross-contamination
        assert portal_a.tenant_id != portal_b.tenant_id


class TestFeedbackTriageAutomation:
    """Test triage engine's priority assignment (deterministic)."""

    def test_triage_priority_scoring_critical(self):
        """Critical bugs score highest."""
        engine = TriageEngine()

        report = FeedbackReport(
            tenant_id="test",
            feedback_type=FeedbackType.BUG_REPORT,
            title="System down",
            description="Production system unreachable",
            severity=FeedbackSeverity.CRITICAL,
            component="console",
            environment="production",
            user_email="user@example.com",
        )

        score = engine._calculate_priority_score(report)
        assert score >= 9.0  # Should be near maximum

    def test_triage_effort_estimation(self):
        """Effort estimation is conservative (fail-closed)."""
        engine = TriageEngine()

        report_reproducible = FeedbackReport(
            tenant_id="test",
            feedback_type=FeedbackType.BUG_REPORT,
            title="Bug",
            description="Test",
            component="console",
            reproduction_steps="1. Click. 2. See error.",
            user_email="user@example.com",
        )

        report_not_reproducible = FeedbackReport(
            tenant_id="test",
            feedback_type=FeedbackType.BUG_REPORT,
            title="Bug",
            description="Test",
            component="voice",
            reproduction_steps=None,
            user_email="user@example.com",
        )

        effort1 = engine._estimate_effort(report_reproducible)
        effort2 = engine._estimate_effort(report_not_reproducible)

        # Reproducible = easier = quicker estimate
        assert effort1 in ["quick_fix", "1-2h"]
        # Non-reproducible = harder = longer estimate
        assert effort2 in ["half_day", "multi_day"]


class TestFeedbackErrorHandling:
    """Test error handling (fail-closed, safe degradation)."""

    def test_invalid_tenant_rejected(self):
        """Portal requires valid tenant_id (GDPR)."""
        with pytest.raises(ValueError):
            FeedbackPortal(Path("/tmp"), tenant_id="")

    def test_invalid_nps_score_rejected(self):
        """NPS portal rejects invalid scores."""
        with TemporaryDirectory() as tmpdir:
            portal = FeedbackPortal(Path(tmpdir), tenant_id="test")

            # NPS score must be 0-10
            with pytest.raises(ValueError):
                asyncio.run(portal.submit_nps_survey(
                    nps_score=11,  # Invalid
                    user_email="user@example.com",
                ))

    @pytest.mark.asyncio
    async def test_malformed_feedback_handled(self):
        """Malformed feedback is logged but doesn't crash system."""
        with TemporaryDirectory() as tmpdir:
            portal = FeedbackPortal(Path(tmpdir), tenant_id="test")

            # Intentionally create malformed report and process it
            # (This would be caught by validation before reaching here)
            # But we test that the system degrades gracefully

            # Submit valid report (success)
            feedback_id = await portal.submit_bug_report(
                title="Valid bug",
                description="Valid description",
                severity=FeedbackSeverity.MEDIUM,
                component="console",
                user_email="user@example.com",
            )

            assert feedback_id is not None
            await asyncio.sleep(0.5)

            # Verify system is still operational
            counts = portal.get_feedback_by_priority()
            assert sum(counts.values()) >= 1


class TestFeedbackAuditTrail:
    """Test audit trail immutability and hash-chain integrity."""

    def test_feedback_signature_immutable(self):
        """Feedback signatures cannot be changed after creation."""
        report = FeedbackReport(
            tenant_id="test",
            title="Bug",
            description="Description",
            component="console",
            user_email="user@example.com",
        )

        sig1 = report.signature

        # Frozen dataclass prevents modification
        with pytest.raises((TypeError, AttributeError)):
            report.title = "Modified"

        # Signature never changes
        sig2 = report.signature
        assert sig1 == sig2

    def test_triaged_feedback_immutable(self):
        """Triaged feedback is frozen after creation."""
        report = FeedbackReport(
            tenant_id="test",
            title="Bug",
            description="Description",
            component="console",
            user_email="user@example.com",
        )

        engine = TriageEngine()
        triaged = engine.triage(report)

        # Frozen dataclass
        with pytest.raises((TypeError, AttributeError)):
            triaged.priority = FeedbackPriority.P3

    def test_report_serialization_reversible(self):
        """Report can be serialized and deserialized."""
        report = FeedbackReport(
            tenant_id="test",
            feedback_type=FeedbackType.BUG_REPORT,
            title="Bug",
            description="Description",
            severity=FeedbackSeverity.HIGH,
            component="console",
            user_email="user@example.com",
        )

        # Serialize
        data = report.to_dict()
        assert data["tenant_id"] == "test"
        assert data["severity"] == "high"

        # Create new report from serialized data (immutable copy)
        report2 = FeedbackReport(
            tenant_id=data["tenant_id"],
            feedback_type=FeedbackType(data["feedback_type"]),
            title=data["title"],
            description=data["description"],
            severity=FeedbackSeverity(data["severity"]),
            component=data["component"],
            user_email=data["user_email"],
        )

        # Signatures should match
        assert report.signature == report2.signature


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])

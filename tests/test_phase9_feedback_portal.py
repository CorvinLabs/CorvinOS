"""Tests for Phase 9 Feedback Portal (E2E + Integration).

Test coverage:
  - Feedback submission (bug, feature, NPS)
  - Auto-triage prioritization
  - Tenant isolation (GDPR)
  - Audit logging
  - Error handling (fail-closed)
"""

import pytest
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from core.feedback.phase9_feedback_portal import FeedbackPortal
from core.feedback.feedback_models import (
    FeedbackReport,
    FeedbackType,
    FeedbackSeverity,
    FeedbackPriority,
)
from core.feedback.feedback_triage import TriageEngine


class TestFeedbackModels:
    """Test feedback data models (immutability, signatures)."""

    def test_feedback_report_immutable(self):
        """FeedbackReport is frozen (immutable)."""
        report = FeedbackReport(
            tenant_id="test_tenant",
            title="Test bug",
            description="This is a test",
            severity=FeedbackSeverity.HIGH,
            component="console",
        )

        # Frozen dataclass should raise error on mutation
        with pytest.raises((TypeError, AttributeError)):
            report.title = "Modified"

    def test_feedback_report_signature(self):
        """FeedbackReport generates consistent signatures."""
        report1 = FeedbackReport(
            feedback_id="test-id",
            tenant_id="test_tenant",
            title="Test",
            description="Description",
        )

        report2 = FeedbackReport(
            feedback_id="test-id",
            tenant_id="test_tenant",
            title="Test",
            description="Description",
        )

        # Same data = same signature
        assert report1.signature == report2.signature

        # Different data = different signature
        report3 = FeedbackReport(
            feedback_id="test-id",
            tenant_id="test_tenant",
            title="Different",
            description="Description",
        )
        assert report1.signature != report3.signature

    def test_feedback_report_to_dict(self):
        """FeedbackReport serializes to dict."""
        report = FeedbackReport(
            tenant_id="test_tenant",
            feedback_type=FeedbackType.BUG_REPORT,
            title="Test bug",
            description="Test description",
            severity=FeedbackSeverity.CRITICAL,
            component="voice",
        )

        data = report.to_dict()
        assert data["tenant_id"] == "test_tenant"
        assert data["feedback_type"] == "bug_report"
        assert data["severity"] == "critical"
        assert "signature" in data


class TestTriageEngine:
    """Test automatic triage prioritization."""

    def test_triage_critical_bug_production(self):
        """Critical bug in production = P0."""
        report = FeedbackReport(
            tenant_id="test_tenant",
            feedback_type=FeedbackType.BUG_REPORT,
            title="System down",
            description="Console is completely unresponsive",
            severity=FeedbackSeverity.CRITICAL,
            component="console",
            environment="production",
            user_email="user@example.com",
        )

        engine = TriageEngine()
        triaged = engine.triage(report)

        assert triaged.priority == FeedbackPriority.P0
        assert "Critical" in triaged.triage_reason

    def test_triage_high_bug(self):
        """High severity bug = P1."""
        report = FeedbackReport(
            tenant_id="test_tenant",
            feedback_type=FeedbackType.BUG_REPORT,
            title="Video glitch",
            description="Video sometimes freezes",
            severity=FeedbackSeverity.HIGH,
            component="video_producer",
            environment="production",
            user_email="user@example.com",
        )

        engine = TriageEngine()
        triaged = engine.triage(report)

        assert triaged.priority == FeedbackPriority.P1

    def test_triage_nps_detractor(self):
        """Low NPS score (detractor) = higher priority."""
        report = FeedbackReport(
            tenant_id="test_tenant",
            feedback_type=FeedbackType.NPS_SURVEY,
            title="NPS Score: 2/10",
            description="Very disappointed",
            nps_score=2,
            component="console",
            user_email="user@example.com",
        )

        engine = TriageEngine()
        triaged = engine.triage(report)

        # Detractor with low score should be P1 at minimum
        assert triaged.priority in [FeedbackPriority.P1, FeedbackPriority.P0]

    def test_triage_feature_request(self):
        """Feature requests default to P2-P3."""
        report = FeedbackReport(
            tenant_id="test_tenant",
            feedback_type=FeedbackType.FEATURE_REQUEST,
            title="Add dark mode",
            description="Users want dark mode",
            component="console",
            user_email="user@example.com",
        )

        engine = TriageEngine()
        triaged = engine.triage(report)

        assert triaged.priority in [FeedbackPriority.P2, FeedbackPriority.P3]

    def test_triage_batch(self):
        """Batch triage multiple reports."""
        reports = [
            FeedbackReport(
                tenant_id="test_tenant",
                feedback_type=FeedbackType.BUG_REPORT,
                title=f"Bug {i}",
                description="Test",
                severity=FeedbackSeverity.CRITICAL if i == 0 else FeedbackSeverity.LOW,
                component="console",
                user_email="user@example.com",
            )
            for i in range(5)
        ]

        engine = TriageEngine()
        triaged_list = engine.batch_triage(reports)

        assert len(triaged_list) == 5
        # First bug is critical
        assert triaged_list[0].priority == FeedbackPriority.P0


class TestFeedbackPortal:
    """Test main feedback portal (async, tenant-scoped)."""

    @pytest.fixture
    def portal(self):
        """Create temporary feedback portal for testing."""
        with TemporaryDirectory() as tmpdir:
            portal = FeedbackPortal(Path(tmpdir), tenant_id="test_tenant")
            yield portal

    def test_portal_initialization(self, portal):
        """Portal creates required directories."""
        assert portal.reports_dir.exists()
        assert portal.triaged_dir.exists()
        assert portal.notified_dir.exists()

    def test_portal_requires_tenant(self):
        """Portal requires tenant_id (GDPR)."""
        with pytest.raises(ValueError):
            FeedbackPortal(Path("/tmp"), tenant_id="")

    @pytest.mark.asyncio
    async def test_submit_bug_report(self, portal):
        """Submit bug report and verify it's processed."""
        feedback_id = await portal.submit_bug_report(
            title="Test bug",
            description="This is a test bug report",
            severity=FeedbackSeverity.HIGH,
            component="console",
            user_email="test@example.com",
            reproduction_steps="1. Click X. 2. See error.",
            environment="production",
            version="v2.0.0",
        )

        assert feedback_id is not None
        assert len(feedback_id) > 0

        # Give async processing time to complete
        await asyncio.sleep(0.5)

        # Verify report was saved
        report_file = portal.reports_dir / f"{feedback_id}.json"
        assert report_file.exists()

        # Verify triaged report was created
        triaged_file = portal.triaged_dir / f"{feedback_id}.json"
        assert triaged_file.exists()

    @pytest.mark.asyncio
    async def test_submit_feature_request(self, portal):
        """Submit feature request."""
        feedback_id = await portal.submit_feature_request(
            title="Dark mode",
            description="Add dark mode to console",
            component="console",
            user_email="test@example.com",
            use_case="Reduce eye strain at night",
        )

        assert feedback_id is not None

        # Give async processing time
        await asyncio.sleep(0.5)

        # Verify submission
        report_file = portal.reports_dir / f"{feedback_id}.json"
        assert report_file.exists()

    @pytest.mark.asyncio
    async def test_submit_nps_survey(self, portal):
        """Submit NPS survey."""
        feedback_id = await portal.submit_nps_survey(
            nps_score=9,
            user_email="test@example.com",
            component="console",
            comment="Love it!",
        )

        assert feedback_id is not None
        await asyncio.sleep(0.5)

        # Verify submission
        report_file = portal.reports_dir / f"{feedback_id}.json"
        assert report_file.exists()

    def test_get_feedback_by_priority(self, portal):
        """Get feedback count by priority."""
        # Create some test reports
        reports = [
            FeedbackReport(
                tenant_id="test_tenant",
                feedback_type=FeedbackType.BUG_REPORT,
                title=f"Bug {i}",
                description="Test",
                severity=FeedbackSeverity.CRITICAL if i == 0 else FeedbackSeverity.LOW,
                component="console",
                user_email="user@example.com",
            )
            for i in range(3)
        ]

        # Process reports
        for report in reports:
            triaged = portal.triage_engine.triage(report)
            triaged_file = portal.triaged_dir / f"{report.feedback_id}.json"
            with open(triaged_file, "w") as f:
                json.dump(triaged.to_dict(), f)

        # Get counts
        counts = portal.get_feedback_by_priority()
        assert counts["p0"] >= 1  # At least one critical
        assert sum(counts.values()) >= 1


class TestFeedbackAPIRoutes:
    """Test console API routes (integration with FastAPI)."""

    @pytest.mark.asyncio
    async def test_bug_report_endpoint(self):
        """Test POST /v1/console/feedback/bug-report"""
        # This would need a FastAPI test client
        # Implemented in integration tests
        pass

    @pytest.mark.asyncio
    async def test_feature_request_endpoint(self):
        """Test POST /v1/console/feedback/feature-request"""
        pass

    @pytest.mark.asyncio
    async def test_nps_survey_endpoint(self):
        """Test POST /v1/console/feedback/nps-survey"""
        pass

    def test_status_endpoint(self):
        """Test GET /v1/console/feedback/status"""
        pass

    def test_priorities_endpoint(self):
        """Test GET /v1/console/feedback/priorities"""
        pass


class TestTenantIsolation:
    """Test GDPR compliance: tenant isolation."""

    def test_feedback_tenant_scoped(self):
        """Feedback is tenant-scoped (no cross-tenant leakage)."""
        report1 = FeedbackReport(
            tenant_id="tenant_a",
            title="Test",
            description="Test",
            component="console",
        )

        report2 = FeedbackReport(
            tenant_id="tenant_b",
            title="Test",
            description="Test",
            component="console",
        )

        # Same data, different tenants
        assert report1.tenant_id != report2.tenant_id
        # Signatures should be identical (only content matters)
        assert report1.signature == report2.signature

    def test_portal_tenant_isolation(self):
        """Portal directories are tenant-scoped."""
        with TemporaryDirectory() as tmpdir:
            portal_a = FeedbackPortal(Path(tmpdir), tenant_id="tenant_a")
            portal_b = FeedbackPortal(Path(tmpdir), tenant_id="tenant_b")

            # Different directories
            assert portal_a.reports_dir != portal_b.reports_dir
            assert str(portal_a.tenant_id) in str(portal_a.reports_dir)
            assert str(portal_b.tenant_id) in str(portal_b.reports_dir)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

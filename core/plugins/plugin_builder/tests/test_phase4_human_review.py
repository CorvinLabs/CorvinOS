"""Phase 4: Human Review Tests — Unit + integration + E2E for all components.

Tests cover:
- GitHub workflow (PR comments, metadata)
- Tiered approval logic (Tier 1/2/3 rules)
- Audit trail (hash-chain, compliance export)
- Rubber-stamp detection (sampling)
- E2E with mock GitHub API
"""
from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, Mock, patch

import pytest

# Import the modules to test
from plugin_builder.github_workflow import (
    ApprovalTier,
    ApprovalStatus,
    GitHubClient,
    GitHubConfig,
    PRComment,
    TestSuite,
    WorkflowOrchestrator,
)
from plugin_builder.approval_logic import (
    ApprovalRequirements,
    ApprovalRulesEngine,
    DecisionReason,
    ReviewDecision,
    ReviewerRole,
)
from plugin_builder.audit_trail import AuditEvent, AuditTrail


class TestTestSuite(unittest.TestCase):
    """Tests for TestSuite dataclass."""

    def test_create_test_suite(self):
        """Create a test suite with metadata."""
        suite = TestSuite(
            plugin_id="my-plugin",
            test_path=Path("/tests/test_my_plugin.py"),
            test_count=5,
            tier=ApprovalTier.TIER_1,
            coverage_percent=85.5,
            generated_at="2026-08-29T10:00:00Z",
        )
        self.assertEqual(suite.plugin_id, "my-plugin")
        self.assertEqual(suite.test_count, 5)
        self.assertEqual(suite.tier, ApprovalTier.TIER_1)

    def test_test_suite_to_dict(self):
        """Serialize test suite to dict."""
        suite = TestSuite(
            plugin_id="my-plugin",
            test_path=Path("/tests/test_my_plugin.py"),
            test_count=3,
            tier=ApprovalTier.TIER_2,
            coverage_percent=75.0,
            generated_at="2026-08-29T10:00:00Z",
        )
        data = suite.to_dict()
        self.assertEqual(data["plugin_id"], "my-plugin")
        self.assertEqual(data["tier"], "tier_2")
        self.assertEqual(data["coverage_percent"], 75.0)


class TestPRComment(unittest.TestCase):
    """Tests for PR comment rendering."""

    def setUp(self):
        self.suite = TestSuite(
            plugin_id="router-backend",
            test_path=Path("/tests/test_router.py"),
            test_count=7,
            tier=ApprovalTier.TIER_2,
            coverage_percent=82.3,
            generated_at="2026-08-29T14:30:00Z",
        )

    def test_render_tier_1_comment(self):
        """Render PR comment for Tier 1."""
        suite = TestSuite(
            plugin_id="simple-plugin",
            test_path=Path("/tests/test_simple.py"),
            test_count=3,
            tier=ApprovalTier.TIER_1,
            coverage_percent=90.0,
            generated_at="2026-08-29T10:00:00Z",
        )
        comment = PRComment(pr_number=123, repository="corvin/corvinOS", test_suite=suite)
        body = comment.render_body()
        self.assertIn("🟢", body)  # Tier 1 emoji
        self.assertIn("simple-plugin", body)
        self.assertIn("90.0%", body)

    def test_render_tier_3_comment(self):
        """Render PR comment for Tier 3."""
        suite = TestSuite(
            plugin_id="risky-e2e",
            test_path=Path("/tests/test_risky_e2e.py"),
            test_count=12,
            tier=ApprovalTier.TIER_3,
            coverage_percent=88.5,
            generated_at="2026-08-29T10:00:00Z",
        )
        comment = PRComment(pr_number=456, repository="corvin/corvinOS", test_suite=suite)
        body = comment.render_body()
        self.assertIn("🔴", body)  # Tier 3 emoji
        self.assertIn("risky-e2e", body)

    def test_comment_has_approval_buttons(self):
        """Verify approval buttons are in rendered comment."""
        comment = PRComment(pr_number=99, repository="test/repo", test_suite=self.suite)
        body = comment.render_body()
        self.assertIn("Approve", body)
        self.assertIn("Request Review", body)
        self.assertIn("Reject", body)


class TestGitHubConfig(unittest.TestCase):
    """Tests for GitHub configuration validation."""

    def test_valid_config(self):
        """Valid GitHub config passes."""
        config = GitHubConfig(
            token="ghp_1234567890abcdef",
            owner="corvin",
            repo="corvinOS",
        )
        config.validate()  # Should not raise

    def test_missing_token_raises(self):
        """Missing token raises ValueError."""
        config = GitHubConfig(token="", owner="corvin", repo="corvinOS")
        with self.assertRaises(ValueError):
            config.validate()

    def test_missing_owner_raises(self):
        """Missing owner raises ValueError."""
        config = GitHubConfig(token="ghp_123", owner="", repo="corvinOS")
        with self.assertRaises(ValueError):
            config.validate()


class TestGitHubClient(unittest.TestCase):
    """Tests for GitHub API client (mocked)."""

    def setUp(self):
        self.config = GitHubConfig(
            token="test_token",
            owner="test-owner",
            repo="test-repo",
        )

    @patch("plugin_builder.github_workflow.requests.Session")
    def test_post_pr_comment_success(self, mock_session_class):
        """Post PR comment returns comment ID."""
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        mock_response = MagicMock()
        mock_response.json.return_value = {"id": 12345}
        mock_session.post.return_value = mock_response

        client = GitHubClient(self.config)
        suite = TestSuite(
            plugin_id="test",
            test_path=Path("/tests/test.py"),
            test_count=2,
            tier=ApprovalTier.TIER_1,
            coverage_percent=80.0,
            generated_at="2026-08-29T10:00:00Z",
        )
        comment = PRComment(pr_number=42, repository="test-owner/test-repo", test_suite=suite)

        comment_id = client.post_pr_comment(comment)
        self.assertEqual(comment_id, 12345)
        mock_session.post.assert_called_once()

    @patch("plugin_builder.github_workflow.requests.Session")
    def test_post_pr_comment_failure_raises(self, mock_session_class):
        """Post PR comment failure raises RequestException."""
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_session.post.side_effect = Exception("API Error")

        client = GitHubClient(self.config)
        suite = TestSuite(
            plugin_id="test",
            test_path=Path("/tests/test.py"),
            test_count=2,
            tier=ApprovalTier.TIER_1,
            coverage_percent=80.0,
            generated_at="2026-08-29T10:00:00Z",
        )
        comment = PRComment(pr_number=42, repository="test-owner/test-repo", test_suite=suite)

        with self.assertRaises(Exception):
            client.post_pr_comment(comment)


class TestReviewDecision(unittest.TestCase):
    """Tests for review decision records."""

    def test_create_approval_decision(self):
        """Create an approval decision."""
        decision = ReviewDecision(
            status="approved",
            reason=DecisionReason.AUTO_APPROVED,
            reviewer="system",
            reviewer_role=ReviewerRole.MAINTAINER,
            comment="Auto-approved: high coverage",
        )
        self.assertEqual(decision.status, "approved")
        self.assertEqual(decision.reason, DecisionReason.AUTO_APPROVED)

    def test_decision_to_dict(self):
        """Serialize decision to dict."""
        decision = ReviewDecision(
            status="approved",
            reason=DecisionReason.APPROVED_BY_SENIOR,
            reviewer="alice@example.com",
            reviewer_role=ReviewerRole.SENIOR_ENGINEER,
            comment="Looks good",
        )
        data = decision.to_dict()
        self.assertEqual(data["status"], "approved")
        self.assertEqual(data["reviewer"], "alice@example.com")

    def test_decision_timestamp_is_set(self):
        """Decision timestamp is automatically set."""
        decision = ReviewDecision(
            status="approved",
            reason=DecisionReason.AUTO_APPROVED,
        )
        self.assertIsNotNone(decision.timestamp)
        # Verify it's a valid ISO8601 timestamp
        datetime.fromisoformat(decision.timestamp)


class TestApprovalRulesEngine(unittest.TestCase):
    """Tests for approval rules engine."""

    def setUp(self):
        self.engine = ApprovalRulesEngine()

    # Tier 1 Tests
    def test_tier_1_auto_approve_high_coverage(self):
        """Tier 1: Auto-approve with high coverage."""
        decision = self.engine.evaluate_tier_1(
            coverage_percent=85.0,
            test_count=5,
            has_risky_patterns=False,
        )
        self.assertEqual(decision.status, "approved")
        self.assertEqual(decision.reason, DecisionReason.AUTO_APPROVED)

    def test_tier_1_reject_low_coverage(self):
        """Tier 1: Reject if coverage below threshold."""
        decision = self.engine.evaluate_tier_1(
            coverage_percent=75.0,
            test_count=5,
            has_risky_patterns=False,
        )
        self.assertEqual(decision.status, "rejected")
        self.assertEqual(decision.reason, DecisionReason.REJECTED_LOW_COVERAGE)

    def test_tier_1_escalate_risky_patterns(self):
        """Tier 1: Escalate if risky patterns detected."""
        decision = self.engine.evaluate_tier_1(
            coverage_percent=90.0,
            test_count=5,
            has_risky_patterns=True,
        )
        self.assertEqual(decision.status, "escalated")
        self.assertEqual(decision.reason, DecisionReason.REJECTED_RISKY_PATTERN)

    # Tier 2 Tests
    def test_tier_2_approve_with_senior_review(self):
        """Tier 2: Approve when senior engineer reviews."""
        decision = self.engine.evaluate_tier_2(
            coverage_percent=75.0,
            test_count=7,
            reviewer="alice@corvin.io",
            reviewer_role=ReviewerRole.SENIOR_ENGINEER,
        )
        self.assertEqual(decision.status, "approved")
        self.assertEqual(decision.reason, DecisionReason.APPROVED_BY_SENIOR)

    def test_tier_2_reject_low_coverage(self):
        """Tier 2: Reject if coverage below threshold."""
        decision = self.engine.evaluate_tier_2(
            coverage_percent=70.0,
            test_count=7,
            reviewer="alice@corvin.io",
            reviewer_role=ReviewerRole.SENIOR_ENGINEER,
        )
        self.assertEqual(decision.status, "rejected")

    def test_tier_2_escalate_no_reviewer(self):
        """Tier 2: Escalate if no reviewer assigned."""
        decision = self.engine.evaluate_tier_2(
            coverage_percent=80.0,
            test_count=7,
            reviewer=None,
        )
        self.assertEqual(decision.status, "pending")
        self.assertEqual(decision.reason, DecisionReason.ESCALATED)

    def test_tier_2_escalate_junior_reviewer(self):
        """Tier 2: Escalate if only junior reviewer."""
        decision = self.engine.evaluate_tier_2(
            coverage_percent=80.0,
            test_count=7,
            reviewer="bob@corvin.io",
            reviewer_role=ReviewerRole.JUNIOR_ENGINEER,
        )
        self.assertEqual(decision.status, "pending")

    # Tier 3 Tests
    def test_tier_3_approve_with_two_tech_leads(self):
        """Tier 3: Approve with 2 tech leads + risk assessment."""
        decision = self.engine.evaluate_tier_3(
            coverage_percent=85.0,
            test_count=12,
            reviewers=[
                ("alice@corvin.io", ReviewerRole.TECH_LEAD),
                ("charlie@corvin.io", ReviewerRole.TECH_LEAD),
            ],
            risk_assessment="low",
        )
        self.assertEqual(decision.status, "approved")

    def test_tier_3_reject_low_coverage(self):
        """Tier 3: Reject if coverage below threshold."""
        decision = self.engine.evaluate_tier_3(
            coverage_percent=80.0,
            test_count=12,
        )
        self.assertEqual(decision.status, "rejected")

    def test_tier_3_escalate_insufficient_reviewers(self):
        """Tier 3: Escalate if fewer than 2 reviewers."""
        decision = self.engine.evaluate_tier_3(
            coverage_percent=85.0,
            test_count=12,
            reviewers=[("alice@corvin.io", ReviewerRole.TECH_LEAD)],
        )
        self.assertEqual(decision.status, "pending")

    def test_tier_3_escalate_no_tech_lead(self):
        """Tier 3: Escalate if no tech lead among reviewers."""
        decision = self.engine.evaluate_tier_3(
            coverage_percent=85.0,
            test_count=12,
            reviewers=[
                ("alice@corvin.io", ReviewerRole.SENIOR_ENGINEER),
                ("bob@corvin.io", ReviewerRole.SENIOR_ENGINEER),
            ],
        )
        self.assertEqual(decision.status, "pending")

    def test_tier_3_escalate_no_risk_assessment(self):
        """Tier 3: Escalate if risk assessment missing."""
        decision = self.engine.evaluate_tier_3(
            coverage_percent=85.0,
            test_count=12,
            reviewers=[
                ("alice@corvin.io", ReviewerRole.TECH_LEAD),
                ("charlie@corvin.io", ReviewerRole.TECH_LEAD),
            ],
            risk_assessment=None,
        )
        self.assertEqual(decision.status, "pending")

    # Rubber-stamp detection
    def test_rubber_stamp_detection_suspicious(self):
        """Detect rubber-stamp: multiple approvals in 30 seconds."""
        now = datetime.now(timezone.utc)
        decisions = [
            ReviewDecision(
                status="approved",
                reason=DecisionReason.AUTO_APPROVED,
                timestamp=now.isoformat(),
            ),
            ReviewDecision(
                status="approved",
                reason=DecisionReason.APPROVED_BY_SENIOR,
                reviewer="bob",
                timestamp=(now + timedelta(seconds=15)).isoformat(),
            ),
            ReviewDecision(
                status="approved",
                reason=DecisionReason.APPROVED_BY_TECH_LEAD,
                reviewer="charlie",
                timestamp=(now + timedelta(seconds=25)).isoformat(),
            ),
        ]

        result = self.engine.check_rubber_stamp(decisions, time_window_seconds=300)
        self.assertTrue(result["is_rubber_stamp"])
        self.assertGreater(result["confidence"], 0.0)

    def test_rubber_stamp_detection_legitimate(self):
        """Legitimate approvals over time: not rubber-stamp."""
        now = datetime.now(timezone.utc)
        decisions = [
            ReviewDecision(
                status="approved",
                reason=DecisionReason.AUTO_APPROVED,
                timestamp=now.isoformat(),
            ),
            ReviewDecision(
                status="approved",
                reason=DecisionReason.APPROVED_BY_SENIOR,
                reviewer="bob",
                timestamp=(now + timedelta(minutes=5)).isoformat(),
            ),
        ]

        result = self.engine.check_rubber_stamp(decisions, time_window_seconds=300)
        self.assertFalse(result["is_rubber_stamp"])


class TestAuditTrail(unittest.TestCase):
    """Tests for audit trail logging."""

    def setUp(self):
        self.temp_dir = Path("/tmp/test_phase4_audit")
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.temp_dir / "audit.jsonl"
        self.trail = AuditTrail(self.log_path)

    def tearDown(self):
        """Clean up test files."""
        import shutil
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def test_append_submission_event(self):
        """Append a submission event to audit trail."""
        event_hash = self.trail.log_submission(
            plugin_id="test-plugin",
            pr_number=123,
            actor="alice@example.com",
            test_count=5,
            coverage_percent=85.0,
            tier="tier_1",
        )
        self.assertIsNotNone(event_hash)
        self.assertEqual(len(event_hash), 64)  # SHA256 hash

    def test_append_decision_event(self):
        """Append a decision event to audit trail."""
        event_hash = self.trail.log_decision(
            plugin_id="test-plugin",
            pr_number=123,
            actor="bob@example.com",
            status="approved",
            reason="auto_approved",
            tier="tier_1",
        )
        self.assertIsNotNone(event_hash)

    def test_hash_chain_integrity(self):
        """Verify hash-chain links events."""
        hash1 = self.trail.log_submission(
            plugin_id="plugin1",
            pr_number=100,
            actor="alice@example.com",
            test_count=3,
            coverage_percent=80.0,
            tier="tier_1",
        )

        hash2 = self.trail.log_decision(
            plugin_id="plugin1",
            pr_number=100,
            actor="bob@example.com",
            status="approved",
            reason="auto_approved",
        )

        # Both hashes should be different
        self.assertNotEqual(hash1, hash2)

        # Verify the chain
        result = self.trail.verify_chain()
        self.assertTrue(result["valid"])
        self.assertEqual(result["event_count"], 2)

    def test_verify_chain_empty_log(self):
        """Verify chain on empty log."""
        result = self.trail.verify_chain()
        self.assertTrue(result["valid"])
        self.assertEqual(result["event_count"], 0)

    def test_export_for_compliance(self):
        """Export events for compliance reporting."""
        self.trail.log_submission(
            plugin_id="plugin1",
            pr_number=100,
            actor="alice@example.com",
            test_count=5,
            coverage_percent=85.0,
            tier="tier_1",
        )

        self.trail.log_decision(
            plugin_id="plugin1",
            pr_number=100,
            actor="bob@example.com",
            status="approved",
            reason="auto_approved",
        )

        events = self.trail.export_for_compliance()
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["event_type"], "submission")
        self.assertEqual(events[1]["event_type"], "decision")


class TestWorkflowOrchestrator(unittest.TestCase):
    """Integration tests for workflow orchestrator."""

    def setUp(self):
        self.config = GitHubConfig(
            token="test_token",
            owner="test-owner",
            repo="test-repo",
        )
        self.temp_dir = Path("/tmp/test_phase4_orchestrator")
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.audit_path = self.temp_dir / "audit.jsonl"

    def tearDown(self):
        import shutil
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    @patch("plugin_builder.github_workflow.GitHubClient")
    def test_submit_for_review_posts_comment(self, mock_client_class):
        """Submit for review posts PR comment and logs audit event."""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_client.post_pr_comment.return_value = 67890

        orchestrator = WorkflowOrchestrator(self.config, self.audit_path)

        suite = TestSuite(
            plugin_id="my-plugin",
            test_path=Path("/tests/test_my_plugin.py"),
            test_count=5,
            tier=ApprovalTier.TIER_1,
            coverage_percent=85.0,
            generated_at="2026-08-29T10:00:00Z",
        )

        pr_comment = orchestrator.submit_for_review(
            test_suite=suite,
            pr_number=42,
            reviewer_tier=ApprovalTier.TIER_1,
        )

        self.assertEqual(pr_comment.comment_id, 67890)
        self.assertTrue(self.audit_path.exists())

        # Verify audit log
        with open(self.audit_path, "r") as f:
            event = json.loads(f.readline())
        self.assertEqual(event["event_type"], "phase4_submission")
        self.assertEqual(event["plugin_id"], "my-plugin")


# E2E Tests (require GitHub API mock)
class TestPhase4E2EWithMockGitHub(unittest.TestCase):
    """End-to-end tests with mocked GitHub API."""

    def setUp(self):
        self.config = GitHubConfig(
            token="test_token",
            owner="corvin",
            repo="corvinOS",
        )
        self.temp_dir = Path("/tmp/test_phase4_e2e")
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import shutil
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    @patch("plugin_builder.github_workflow.GitHubClient")
    def test_full_workflow_tier_1_auto_approve(self, mock_client_class):
        """Full workflow: submit → auto-approve Tier 1 tests."""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_client.post_pr_comment.return_value = 111

        # Step 1: Create test suite
        suite = TestSuite(
            plugin_id="simple-router",
            test_path=Path("/tests/test_router.py"),
            test_count=4,
            tier=ApprovalTier.TIER_1,
            coverage_percent=87.5,
            generated_at="2026-08-29T11:00:00Z",
        )

        # Step 2: Submit for review
        audit_path = self.temp_dir / "audit.jsonl"
        orchestrator = WorkflowOrchestrator(self.config, audit_path)
        pr_comment = orchestrator.submit_for_review(suite, 99, ApprovalTier.TIER_1)

        # Step 3: Evaluate with rules engine
        engine = ApprovalRulesEngine()
        decision = engine.evaluate_tier_1(
            coverage_percent=suite.coverage_percent,
            test_count=suite.test_count,
        )

        # Verify result
        self.assertEqual(decision.status, "approved")
        self.assertEqual(decision.reason, DecisionReason.AUTO_APPROVED)
        self.assertTrue(audit_path.exists())

    @patch("plugin_builder.github_workflow.GitHubClient")
    def test_full_workflow_tier_3_requires_review(self, mock_client_class):
        """Full workflow: submit → escalate → require 2 tech leads."""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_client.post_pr_comment.return_value = 222

        # Step 1: Create risky E2E test suite
        suite = TestSuite(
            plugin_id="payment-processor",
            test_path=Path("/tests/test_payment.py"),
            test_count=12,
            tier=ApprovalTier.TIER_3,
            coverage_percent=90.0,
            generated_at="2026-08-29T12:00:00Z",
        )

        # Step 2: Submit for review
        audit_path = self.temp_dir / "audit.jsonl"
        orchestrator = WorkflowOrchestrator(self.config, audit_path)
        pr_comment = orchestrator.submit_for_review(suite, 150, ApprovalTier.TIER_3)

        # Step 3: Evaluate (should be pending)
        engine = ApprovalRulesEngine()
        decision = engine.evaluate_tier_3(
            coverage_percent=suite.coverage_percent,
            test_count=suite.test_count,
            reviewers=None,
        )
        self.assertEqual(decision.status, "pending")

        # Step 4: After review by tech leads, should approve
        decision = engine.evaluate_tier_3(
            coverage_percent=suite.coverage_percent,
            test_count=suite.test_count,
            reviewers=[
                ("alice@corvin.io", ReviewerRole.TECH_LEAD),
                ("bob@corvin.io", ReviewerRole.TECH_LEAD),
            ],
            risk_assessment="medium",
        )
        self.assertEqual(decision.status, "approved")


if __name__ == "__main__":
    unittest.main(verbosity=2)

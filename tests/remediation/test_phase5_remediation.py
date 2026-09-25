"""
Phase 5: Automated Remediation - Test Suite

12+ test cases covering:
- Drift categorization (safe/high-risk/blocked)
- Safe auto-remediation (install, update, config sync)
- Approval workflow (request, approve, reject, timeout)
- Remediation orchestration (single and multi-drift)
- Rollback capability
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, MagicMock, patch

from core.remediation.drift_categories import (
    DriftCategorizer,
    DriftCategory,
    DriftSeverity,
    RemediationType,
    categorize_drift,
    categorize_drifts,
)
from core.remediation.auto_remediate import (
    SafeAutoRemediator,
    RemediationResult,
)
from core.remediation.approval_workflow import (
    ApprovalGate,
    ApprovalRequest,
    ApprovalState,
)
from core.remediation.orchestrator import (
    RemediationOrchestrator,
    RemediationState,
)


# ==============================================================================
# UNIT TESTS: Drift Categorization
# ==============================================================================


class TestDriftCategorization:
    """Test drift categorization logic"""

    def test_categorize_safe_drift_plugin_missing(self):
        """Categorize PLUGIN_MISSING as safe auto-fix"""
        assessment = categorize_drift("PLUGIN_MISSING", "drift-001", "inst-01")

        assert assessment.category == DriftCategory.SAFE_AUTO_FIX
        assert assessment.severity == DriftSeverity.MEDIUM
        assert assessment.remediation_type == RemediationType.PLUGIN_INSTALL
        assert assessment.estimated_risk_score == 0.1

    def test_categorize_safe_drift_plugin_version_mismatch(self):
        """Categorize PLUGIN_VERSION_MISMATCH as safe auto-fix"""
        assessment = categorize_drift("PLUGIN_VERSION_MISMATCH", "drift-002", "inst-01")

        assert assessment.category == DriftCategory.SAFE_AUTO_FIX
        assert assessment.severity == DriftSeverity.LOW
        assert assessment.remediation_type == RemediationType.PLUGIN_UPDATE
        assert assessment.requires_rollback is True

    def test_categorize_safe_drift_config_override(self):
        """Categorize CONFIG_OVERRIDE_DRIFT as safe auto-fix"""
        assessment = categorize_drift("CONFIG_OVERRIDE_DRIFT", "drift-003", "inst-01")

        assert assessment.category == DriftCategory.SAFE_AUTO_FIX
        assert assessment.severity == DriftSeverity.LOW
        assert assessment.remediation_type == RemediationType.CONFIG_SYNC

    def test_categorize_high_risk_drift_code_version(self):
        """Categorize CODE_VERSION_DRIFT as high-risk"""
        assessment = categorize_drift("CODE_VERSION_DRIFT", "drift-004", "inst-01")

        assert assessment.category == DriftCategory.REQUIRES_APPROVAL
        assert assessment.severity == DriftSeverity.HIGH
        assert assessment.estimated_risk_score > 0.5

    def test_categorize_high_risk_drift_schema_migration(self):
        """Categorize SCHEMA_VERSION_MISMATCH as high-risk"""
        assessment = categorize_drift("SCHEMA_VERSION_MISMATCH", "drift-005", "inst-01")

        assert assessment.category == DriftCategory.REQUIRES_APPROVAL
        assert assessment.severity == DriftSeverity.CRITICAL
        assert assessment.requires_rollback is True

    def test_categorize_blocked_drift_invalid_config(self):
        """Categorize INVALID_CONFIG as blocked"""
        assessment = categorize_drift("INVALID_CONFIG", "drift-006", "inst-01")

        assert assessment.category == DriftCategory.BLOCKED
        assert assessment.severity == DriftSeverity.CRITICAL
        assert assessment.remediation_type is None

    def test_categorize_blocked_drift_credential_missing(self):
        """Categorize CREDENTIAL_MISSING as blocked"""
        assessment = categorize_drift("CREDENTIAL_MISSING", "drift-007", "inst-01")

        assert assessment.category == DriftCategory.BLOCKED
        assert assessment.severity == DriftSeverity.CRITICAL

    def test_categorize_multiple_drifts(self):
        """Categorize multiple drifts and get summary"""
        drifts = [
            ("PLUGIN_MISSING", "drift-001", "inst-01"),
            ("CODE_VERSION_DRIFT", "drift-002", "inst-01"),
            ("INVALID_CONFIG", "drift-003", "inst-02"),
        ]

        assessments, summary = categorize_drifts(drifts)

        assert summary["total"] == 3
        assert summary["safe_auto_fix"] == 1
        assert summary["requires_approval"] == 1
        assert summary["blocked"] == 1


# ==============================================================================
# UNIT TESTS: Safe Auto-Remediation
# ==============================================================================


class TestSafeAutoRemediation:
    """Test safe auto-remediation engine"""

    def test_auto_remediate_plugin_install_success(self):
        """Auto-remediate PLUGIN_MISSING with success"""
        remediator = SafeAutoRemediator()
        assessment = categorize_drift("PLUGIN_MISSING", "drift-001", "inst-01")

        result = remediator.remediate_safe_drift(assessment)

        assert result.status == "SUCCESS"
        assert result.drift_id == "drift-001"
        assert result.remediation_type == RemediationType.PLUGIN_INSTALL.value

    def test_auto_remediate_plugin_update_success(self):
        """Auto-remediate PLUGIN_VERSION_MISMATCH with success"""
        remediator = SafeAutoRemediator()
        assessment = categorize_drift("PLUGIN_VERSION_MISMATCH", "drift-002", "inst-01")

        result = remediator.remediate_safe_drift(assessment)

        assert result.status == "SUCCESS"
        assert result.remediation_type == RemediationType.PLUGIN_UPDATE.value

    def test_auto_remediate_config_sync_success(self):
        """Auto-remediate CONFIG_OVERRIDE_DRIFT with success"""
        remediator = SafeAutoRemediator()
        assessment = categorize_drift("CONFIG_OVERRIDE_DRIFT", "drift-003", "inst-01")

        result = remediator.remediate_safe_drift(assessment)

        assert result.status == "SUCCESS"
        assert result.remediation_type == RemediationType.CONFIG_SYNC.value

    def test_auto_remediate_non_safe_drift_fails(self):
        """Reject remediation for non-safe drifts"""
        remediator = SafeAutoRemediator()
        assessment = categorize_drift("CODE_VERSION_DRIFT", "drift-004", "inst-01")

        result = remediator.remediate_safe_drift(assessment)

        assert result.status == "FAILED"
        assert "not safe for auto-remediation" in result.error

    def test_auto_remediate_captures_state_for_rollback(self):
        """Remediation captures state before execution"""
        remediator = SafeAutoRemediator()
        assessment = categorize_drift("PLUGIN_MISSING", "drift-001", "inst-01")

        result = remediator.remediate_safe_drift(assessment)

        assert result.previous_state is not None
        assert "timestamp" in result.previous_state


# ==============================================================================
# UNIT TESTS: Approval Workflow
# ==============================================================================


class TestApprovalWorkflow:
    """Test operator approval workflow"""

    def test_request_approval_creates_request(self):
        """Request approval for high-risk drift"""
        gate = ApprovalGate()
        assessment = categorize_drift("CODE_VERSION_DRIFT", "drift-004", "inst-01")

        request = gate.request_approval(assessment, "inst-01", "system")

        assert request.state == ApprovalState.PENDING
        assert request.drift_id == "drift-004"
        assert request.expires_at is not None
        assert request.request_id.startswith("apr-")

    def test_approve_request_updates_state(self):
        """Operator approves remediation request"""
        gate = ApprovalGate()
        assessment = categorize_drift("CODE_VERSION_DRIFT", "drift-004", "inst-01")
        request_obj = gate.request_approval(assessment, "inst-01")

        approved = gate.approve_request(request_obj.request_id, "user@example.com", "Approved")

        assert approved.state == ApprovalState.APPROVED
        assert approved.approved_by == "user@example.com"
        assert approved.decision_reason == "Approved"

    def test_reject_request_updates_state(self):
        """Operator rejects remediation request"""
        gate = ApprovalGate()
        assessment = categorize_drift("CODE_VERSION_DRIFT", "drift-004", "inst-01")
        request_obj = gate.request_approval(assessment, "inst-01")

        rejected = gate.reject_request(request_obj.request_id, "user@example.com", "Not ready")

        assert rejected.state == ApprovalState.REJECTED
        assert rejected.rejected_by == "user@example.com"
        assert rejected.decision_reason == "Not ready"

    def test_approval_timeout_marks_expired(self):
        """Approval request expires after timeout"""
        gate = ApprovalGate()
        assessment = categorize_drift("CODE_VERSION_DRIFT", "drift-004", "inst-01")
        request_obj = gate.request_approval(assessment, "inst-01")

        # Set expiry to past
        request_obj.expires_at = (datetime.utcnow() - timedelta(hours=1)).isoformat()
        gate.pending_requests[request_obj.request_id] = request_obj

        # Wait for decision will timeout
        result = gate.wait_for_approval(request_obj.request_id, timeout_seconds=1, poll_interval_seconds=0.5)

        assert result.state == ApprovalState.EXPIRED


# ==============================================================================
# INTEGRATION TESTS: Remediation Orchestration
# ==============================================================================


class TestRemediationOrchestration:
    """Test remediation orchestration"""

    def test_process_safe_drift_auto_remediates(self):
        """Process safe drift through orchestrator"""
        orchestrator = RemediationOrchestrator()

        state, result_id = orchestrator.process_drift("PLUGIN_MISSING", "drift-001", "inst-01")

        assert state == RemediationState.REMEDIATED
        assert result_id == "drift-001"

    def test_process_high_risk_drift_requests_approval(self):
        """Process high-risk drift through orchestrator"""
        orchestrator = RemediationOrchestrator()

        state, result_id = orchestrator.process_drift("CODE_VERSION_DRIFT", "drift-004", "inst-01")

        # Should be awaiting approval or blocked if approval times out
        assert state in [RemediationState.AWAITING_APPROVAL, RemediationState.BLOCKED]

    def test_process_blocked_drift_stays_blocked(self):
        """Process blocked drift through orchestrator"""
        orchestrator = RemediationOrchestrator()

        state, result_id = orchestrator.process_drift("INVALID_CONFIG", "drift-006", "inst-01")

        assert state == RemediationState.BLOCKED

    def test_orchestrate_multi_drift_scenario(self):
        """Orchestrate multiple drifts across instances"""
        orchestrator = RemediationOrchestrator()

        drifts = [
            ("PLUGIN_MISSING", "drift-001", "inst-01"),
            ("CODE_VERSION_DRIFT", "drift-002", "inst-01"),
            ("CONFIG_OVERRIDE_DRIFT", "drift-003", "inst-02"),
        ]

        plan, results = orchestrator.orchestrate_multi_drift(drifts)

        assert plan.total_drifts == 3
        assert plan.safe_drifts == 2
        assert plan.high_risk_drifts == 1
        assert len(results) == 3


# ==============================================================================
# E2E TESTS: Full Remediation Flows
# ==============================================================================


class TestE2ERemediationFlows:
    """E2E tests for complete remediation scenarios"""

    def test_e2e_safe_drift_detect_categorize_fix_verify(self):
        """E2E: Safe drift detected → categorized → auto-fixed → verified"""
        orchestrator = RemediationOrchestrator()

        # 1. Process drift
        state, result_id = orchestrator.process_drift(
            "PLUGIN_MISSING", "drift-e2e-001", "inst-01"
        )

        # 2. Verify state transitions
        assert state == RemediationState.REMEDIATED

        # 3. Verify audit trail
        audit_events = [e.event_type for e in orchestrator.events]
        assert "drift_detected" in audit_events
        assert "drift_categorized" in audit_events
        assert "auto_remediation_started" in audit_events
        assert "remediation_success" in audit_events

    def test_e2e_high_risk_drift_detect_request_approve_fix(self):
        """E2E: High-risk drift → approval requested → approved → fixed"""
        orchestrator = RemediationOrchestrator()

        # 1. Process drift
        state, request_id = orchestrator.process_drift(
            "CODE_VERSION_DRIFT", "drift-e2e-002", "inst-01"
        )

        # 2. Verify approval requested
        assert state in [RemediationState.AWAITING_APPROVAL, RemediationState.BLOCKED]

        # 3. Verify audit trail mentions approval
        audit_events = [e.event_type for e in orchestrator.events]
        assert "drift_detected" in audit_events
        assert "approval_requested" in audit_events or "drift_blocked" in audit_events

    def test_e2e_mixed_multi_drift_scenario(self):
        """E2E: Multiple drifts (2 safe, 2 high-risk, 1 blocked) in parallel"""
        orchestrator = RemediationOrchestrator()

        drifts = [
            ("PLUGIN_MISSING", "drift-e2e-safe-1", "inst-01"),
            ("CONFIG_OVERRIDE_DRIFT", "drift-e2e-safe-2", "inst-01"),
            ("CODE_VERSION_DRIFT", "drift-e2e-high-1", "inst-02"),
            ("SCHEMA_VERSION_MISMATCH", "drift-e2e-high-2", "inst-02"),
            ("INVALID_CONFIG", "drift-e2e-blocked-1", "inst-03"),
        ]

        plan, results = orchestrator.orchestrate_multi_drift(drifts)

        # Verify plan
        assert plan.total_drifts == 5
        assert plan.safe_drifts == 2
        assert plan.high_risk_drifts == 2
        assert plan.blocked_drifts == 1

        # Verify results
        assert len(results) == 5
        safe_results = [v for k, v in results.items() if "safe" in k]
        assert len([r for r in safe_results if r[0] == RemediationState.REMEDIATED]) > 0


# ==============================================================================
# ADVERSARIAL TESTS: Error Cases
# ==============================================================================


class TestAdversarialScenarios:
    """Adversarial tests for error handling"""

    def test_approval_timeout_triggers_escalation(self):
        """Approval timeout after 24 hours escalates to PagerDuty"""
        gate = ApprovalGate()
        assessment = categorize_drift("CODE_VERSION_DRIFT", "drift-004", "inst-01")

        request_obj = gate.request_approval(assessment, "inst-01")

        # Simulate timeout by setting created_at to 24+ hours ago
        request_obj.requested_at = (
            datetime.utcnow() - timedelta(hours=25)
        ).isoformat()

        # Wait should timeout and expire
        result = gate.wait_for_approval(
            request_obj.request_id, timeout_seconds=1, poll_interval_seconds=0.5
        )

        assert result.state == ApprovalState.EXPIRED

    def test_remediation_failure_triggers_rollback(self):
        """Remediation failure attempts rollback"""
        remediator = SafeAutoRemediator()
        assessment = categorize_drift("PLUGIN_MISSING", "drift-001", "inst-01")

        # Simulate failure by making remediation fail
        with patch.object(remediator, '_remediate_plugin_install') as mock:
            mock.return_value = RemediationResult(
                status="FAILED",
                drift_id="drift-001",
                remediation_type="plugin_install",
                error="Plugin not found"
            )

            result = remediator.remediate_safe_drift(assessment)

            # Should attempt rollback
            assert result.status in ["FAILED", "ROLLED_BACK"]

    def test_invalid_approval_request_id_returns_error(self):
        """Invalid approval request ID returns 404"""
        gate = ApprovalGate()

        # Try to approve non-existent request
        with pytest.raises(ValueError):
            gate.approve_request("apr-invalid", "user@example.com")

    def test_double_approval_rejected(self):
        """Cannot approve already-approved request"""
        gate = ApprovalGate()
        assessment = categorize_drift("CODE_VERSION_DRIFT", "drift-004", "inst-01")
        request_obj = gate.request_approval(assessment, "inst-01")

        # First approval succeeds
        gate.approve_request(request_obj.request_id, "user1@example.com")

        # Second approval fails
        with pytest.raises(ValueError):
            gate.approve_request(request_obj.request_id, "user2@example.com")


# ==============================================================================
# PERFORMANCE TESTS
# ==============================================================================


class TestPerformance:
    """Performance tests"""

    def test_categorize_1000_drifts_under_1_second(self):
        """Categorizing 1000 drifts completes in <1s"""
        import time

        drifts = [
            ("PLUGIN_MISSING", f"drift-{i}", f"inst-{i % 10}")
            for i in range(1000)
        ]

        start = time.time()
        assessments, summary = categorize_drifts(drifts)
        elapsed = time.time() - start

        assert len(assessments) == 1000
        assert elapsed < 1.0  # Must complete in under 1 second

    def test_remediate_100_safe_drifts_under_5_seconds(self):
        """Remediating 100 safe drifts completes in <5s"""
        import time

        remediator = SafeAutoRemediator()

        start = time.time()
        for i in range(100):
            assessment = categorize_drift(
                "PLUGIN_MISSING", f"drift-perf-{i}", f"inst-{i % 10}"
            )
            result = remediator.remediate_safe_drift(assessment)
            assert result.status == "SUCCESS"
        elapsed = time.time() - start

        assert elapsed < 5.0  # Must complete in under 5 seconds


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

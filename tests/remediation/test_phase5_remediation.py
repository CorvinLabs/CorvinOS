"""
Phase 5: Automated Remediation - Test Suite

Covers:
- Drift categorization (safe/high-risk/blocked)
- Safe auto-remediation — the executors do NOT exist, so every safe drift must
  report NOT_IMPLEMENTED (never a simulated SUCCESS / ROLLED_BACK)
- Approval workflow (request, approve, reject, expiry, tenant binding) — every
  transition is written to the tenant's core audit chain FIRST
- Orchestration — non-blocking for high-risk drifts; nothing reaches REMEDIATED
  without a real, verified fix

Every test runs against a scratch CORVIN_HOME (the autouse fixture) and reads
the audit records back from the real tenant chain file.
"""

import json
import time
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import pytest

from core.remediation.drift_categories import (
    DriftCategorizer,
    DriftCategory,
    DriftSeverity,
    RemediationType,
    categorize_drift,
    categorize_drifts,
)
from core.remediation.auto_remediate import (
    NOT_IMPLEMENTED,
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


@pytest.fixture(autouse=True)
def _scratch_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "corvin"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    # tests/conftest.py redirects the chain via VOICE_AUDIT_PATH; these tests
    # assert on the CANONICAL tenant chain, so drop the redirect.
    monkeypatch.delenv("VOICE_AUDIT_PATH", raising=False)
    monkeypatch.delenv("FORGE_ROOT", raising=False)
    (tmp_path / "home").mkdir()
    yield tmp_path / "corvin"


def _chain(home, tenant="_default"):
    path = home / "tenants" / tenant / "global" / "forge" / "audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def _events(home, prefix="remediation."):
    return [r for r in _chain(home) if r.get("event_type", "").startswith(prefix)]


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
# UNIT TESTS: Safe Auto-Remediation (fail-closed: no executor exists)
# ==============================================================================


class TestSafeAutoRemediation:
    """No fix executor exists — a safe drift is reported NOT_IMPLEMENTED."""

    @pytest.mark.parametrize("drift_type,rtype", [
        ("PLUGIN_MISSING", RemediationType.PLUGIN_INSTALL),
        ("PLUGIN_VERSION_MISMATCH", RemediationType.PLUGIN_UPDATE),
        ("CONFIG_OVERRIDE_DRIFT", RemediationType.CONFIG_SYNC),
    ])
    def test_safe_drift_is_not_implemented_never_success(self, _scratch_home, drift_type, rtype):
        remediator = SafeAutoRemediator(plugin_installer=Mock())
        assessment = categorize_drift(drift_type, "drift-001", "inst-01")

        result = remediator.remediate_safe_drift(assessment)

        assert result.status == NOT_IMPLEMENTED
        assert result.remediation_type == rtype.value
        assert result.error.endswith("_executor_not_implemented")
        recs = _events(_scratch_home)
        assert [r["details"]["status"] for r in recs] == [NOT_IMPLEMENTED]
        assert recs[0]["details"]["status_detail"] == "not_implemented"

    def test_plugin_drift_without_installer_is_failed(self):
        result = SafeAutoRemediator().remediate_safe_drift(
            categorize_drift("PLUGIN_MISSING", "drift-001", "inst-01"))
        assert result.status == "FAILED"

    def test_auto_remediate_non_safe_drift_fails(self):
        remediator = SafeAutoRemediator()
        assessment = categorize_drift("CODE_VERSION_DRIFT", "drift-004", "inst-01")

        result = remediator.remediate_safe_drift(assessment)

        assert result.status == "FAILED"
        assert "not safe for auto-remediation" in result.error

    def test_verification_is_never_assumed(self):
        remediator = SafeAutoRemediator()
        assessment = categorize_drift("CONFIG_OVERRIDE_DRIFT", "drift-003", "inst-01")
        fake = RemediationResult(status="SUCCESS", drift_id="drift-003", remediation_type="config_sync")
        assert remediator.verify_remediation_success(assessment, fake) is False

    def test_rollback_is_never_reported_as_done(self, _scratch_home):
        """A failed attempt that needs a rollback ends FAILED — there is no
        snapshot to restore, so ROLLED_BACK would be a fabrication."""
        remediator = SafeAutoRemediator(plugin_installer=Mock())
        assessment = categorize_drift("PLUGIN_MISSING", "drift-001", "inst-01")
        with patch.object(remediator, "_remediate_plugin_install") as mock:
            mock.return_value = RemediationResult(
                status="FAILED", drift_id="drift-001",
                remediation_type="plugin_install", error="Plugin not found /secret/path",
            )
            result = remediator.remediate_safe_drift(assessment)
        if assessment.requires_rollback:
            assert result.error == "rollback_not_implemented"
        assert result.status == "FAILED"
        recs = _events(_scratch_home)
        assert recs and recs[-1]["details"]["status"] == "FAILED"
        assert "/secret/path" not in json.dumps(recs)  # free-text errors never enter the chain


# ==============================================================================
# UNIT TESTS: Approval Workflow
# ==============================================================================


class TestApprovalWorkflow:
    """Operator approval workflow — audit-first, tenant-bound, expiry-checked."""

    def test_request_approval_creates_request_and_audits(self, _scratch_home):
        gate = ApprovalGate()
        assessment = categorize_drift("CODE_VERSION_DRIFT", "drift-004", "inst-01")

        request = gate.request_approval(assessment, "inst-01", "system")

        assert request.state == ApprovalState.PENDING
        assert request.drift_id == "drift-004"
        assert request.expires_at is not None
        assert request.request_id.startswith("apr-")
        assert request.tenant_id == "_default"
        recs = _events(_scratch_home)
        assert [r["event_type"] for r in recs] == ["remediation.approval_requested"]
        assert recs[0]["details"]["request_id"] == request.request_id

    def test_approve_request_updates_state_and_audits_without_reason_text(self, _scratch_home):
        gate = ApprovalGate()
        assessment = categorize_drift("CODE_VERSION_DRIFT", "drift-004", "inst-01")
        request_obj = gate.request_approval(assessment, "inst-01")

        approved = gate.approve_request(request_obj.request_id, "console:abc", "ok per ticket 42")

        assert approved.state == ApprovalState.APPROVED
        assert approved.approved_by == "console:abc"
        decided = [r for r in _events(_scratch_home) if r["event_type"] == "remediation.approval_decided"]
        assert len(decided) == 1
        d = decided[0]["details"]
        assert (d["decision"], d["decided_by"], d["has_reason"]) == ("approved", "console:abc", True)
        assert "ticket 42" not in json.dumps(decided)

    def test_reject_request_updates_state_and_audits(self, _scratch_home):
        gate = ApprovalGate()
        assessment = categorize_drift("CODE_VERSION_DRIFT", "drift-004", "inst-01")
        request_obj = gate.request_approval(assessment, "inst-01")

        rejected = gate.reject_request(request_obj.request_id, "console:abc", "Not ready")

        assert rejected.state == ApprovalState.REJECTED
        assert rejected.rejected_by == "console:abc"
        decided = [r for r in _events(_scratch_home) if r["event_type"] == "remediation.approval_decided"]
        assert decided[0]["details"]["decision"] == "rejected"

    def test_expired_request_cannot_be_approved(self, _scratch_home):
        gate = ApprovalGate()
        assessment = categorize_drift("CODE_VERSION_DRIFT", "drift-004", "inst-01")
        request_obj = gate.request_approval(assessment, "inst-01")
        request_obj.expires_at = (datetime.utcnow() - timedelta(minutes=1)).isoformat()

        with pytest.raises(ValueError, match="expired"):
            gate.approve_request(request_obj.request_id, "console:abc")

        assert request_obj.state == ApprovalState.EXPIRED
        kinds = [r["event_type"] for r in _events(_scratch_home)]
        assert "remediation.approval_expired" in kinds
        assert "remediation.approval_decided" not in kinds

    def test_wait_for_approval_marks_past_deadline_expired_without_waiting(self):
        gate = ApprovalGate()
        assessment = categorize_drift("CODE_VERSION_DRIFT", "drift-004", "inst-01")
        request_obj = gate.request_approval(assessment, "inst-01")
        request_obj.expires_at = (datetime.utcnow() - timedelta(hours=1)).isoformat()

        t0 = time.monotonic()
        result = gate.wait_for_approval(request_obj.request_id, timeout_seconds=5, poll_interval_seconds=0.1)

        assert result.state == ApprovalState.EXPIRED
        assert time.monotonic() - t0 < 1.0

    def test_other_tenant_cannot_decide(self, _scratch_home):
        gate = ApprovalGate()
        assessment = categorize_drift("CODE_VERSION_DRIFT", "drift-004", "inst-01")
        request_obj = gate.request_approval(assessment, "inst-01")

        assert gate.get_request_for_tenant(request_obj.request_id, "acme") is None
        assert gate.requests_for_tenant("acme") == []
        with pytest.raises(ValueError, match="not found"):
            gate.approve_request(request_obj.request_id, "console:x", tenant_id="acme")
        assert request_obj.state == ApprovalState.PENDING

    def test_decision_is_refused_when_audit_cannot_commit(self, _scratch_home):
        """Audit-FIRST: no chain record → the decision does not take effect."""
        gate = ApprovalGate()
        assessment = categorize_drift("CODE_VERSION_DRIFT", "drift-004", "inst-01")
        request_obj = gate.request_approval(assessment, "inst-01")

        with patch("core.remediation.approval_workflow.remediation_audit",
                   side_effect=RuntimeError("core audit write did not commit")):
            with pytest.raises(RuntimeError):
                gate.approve_request(request_obj.request_id, "console:abc")

        assert request_obj.state == ApprovalState.PENDING
        assert request_obj.approved_by is None

    def test_escalated_request_is_still_decidable(self):
        gate = ApprovalGate()
        assessment = categorize_drift("CODE_VERSION_DRIFT", "drift-004", "inst-01")
        request_obj = gate.request_approval(assessment, "inst-01")
        request_obj.state = ApprovalState.ESCALATED

        assert gate.reject_request(request_obj.request_id, "console:abc").state == ApprovalState.REJECTED


# ==============================================================================
# INTEGRATION TESTS: Remediation Orchestration
# ==============================================================================


class TestRemediationOrchestration:
    """Orchestration — honest end states, never blocks."""

    def test_safe_drift_without_executor_ends_failed(self, _scratch_home):
        orchestrator = RemediationOrchestrator(plugin_installer=Mock())

        state, result_id = orchestrator.process_drift("PLUGIN_MISSING", "drift-001", "inst-01")

        assert state == RemediationState.FAILED
        assert result_id is None
        kinds = [e.event_type for e in orchestrator.events]
        assert "remediation_not_implemented" in kinds
        assert "remediation_success" not in kinds
        lifecycle = [r["details"]["event"] for r in _events(_scratch_home)
                     if r["event_type"] == "remediation.lifecycle"]
        assert lifecycle == kinds  # every in-process event is on the chain too

    def test_high_risk_drift_returns_immediately_awaiting_approval(self):
        orchestrator = RemediationOrchestrator()

        t0 = time.monotonic()
        state, request_id = orchestrator.process_drift("CODE_VERSION_DRIFT", "drift-004", "inst-01")

        assert time.monotonic() - t0 < 2.0  # used to block up to 60 s in wait_for_approval
        assert state == RemediationState.AWAITING_APPROVAL
        assert request_id.startswith("apr-")

    def test_approved_high_risk_drift_is_not_reported_remediated(self, _scratch_home):
        orchestrator = RemediationOrchestrator()
        _, request_id = orchestrator.process_drift("CODE_VERSION_DRIFT", "drift-004", "inst-01")
        orchestrator.approval_gate.approve_request(request_id, "console:abc")

        state, rid = orchestrator.resume_after_decision(request_id)

        assert state == RemediationState.FAILED
        assert rid == request_id
        assert "remediation_not_implemented" in [e.event_type for e in orchestrator.events]
        assert RemediationState.REMEDIATED.value not in {
            r["details"].get("state") for r in _events(_scratch_home)}

    def test_rejected_and_open_requests(self):
        orchestrator = RemediationOrchestrator()
        _, open_id = orchestrator.process_drift("CODE_VERSION_DRIFT", "drift-a", "inst-01")
        _, rej_id = orchestrator.process_drift("CODE_VERSION_DRIFT", "drift-b", "inst-01")
        orchestrator.approval_gate.reject_request(rej_id, "console:abc")

        assert orchestrator.resume_after_decision(open_id)[0] == RemediationState.AWAITING_APPROVAL
        assert orchestrator.resume_after_decision(rej_id)[0] == RemediationState.BLOCKED
        assert orchestrator.resume_after_decision("apr-unknown")[0] == RemediationState.FAILED

    def test_process_blocked_drift_stays_blocked(self):
        orchestrator = RemediationOrchestrator()

        state, result_id = orchestrator.process_drift("INVALID_CONFIG", "drift-006", "inst-01")

        assert state == RemediationState.BLOCKED

    def test_multi_drift_summary_is_not_blanket_remediated(self, _scratch_home):
        orchestrator = RemediationOrchestrator()
        drifts = [
            ("PLUGIN_MISSING", "drift-001", "inst-01"),
            ("CODE_VERSION_DRIFT", "drift-002", "inst-01"),
            ("CONFIG_OVERRIDE_DRIFT", "drift-003", "inst-02"),
            ("INVALID_CONFIG", "drift-004", "inst-03"),
        ]

        plan, results = orchestrator.orchestrate_multi_drift(drifts)

        assert (plan.total_drifts, plan.safe_drifts, plan.high_risk_drifts, plan.blocked_drifts) == (4, 2, 1, 1)
        assert results["drift-002"][0] == RemediationState.AWAITING_APPROVAL
        assert results["drift-004"][0] == RemediationState.BLOCKED
        assert all(st != RemediationState.REMEDIATED for st, _ in results.values())
        summary = orchestrator.events[-1]
        assert summary.event_type == "multi_drift_orchestrated"
        assert summary.state == RemediationState.FAILED


# ==============================================================================
# ADVERSARIAL TESTS: Error Cases
# ==============================================================================


class TestAdversarialScenarios:

    def test_invalid_approval_request_id_returns_error(self):
        gate = ApprovalGate()
        with pytest.raises(ValueError):
            gate.approve_request("apr-invalid", "console:abc")

    def test_double_approval_rejected(self, _scratch_home):
        gate = ApprovalGate()
        assessment = categorize_drift("CODE_VERSION_DRIFT", "drift-004", "inst-01")
        request_obj = gate.request_approval(assessment, "inst-01")

        gate.approve_request(request_obj.request_id, "console:one")
        with pytest.raises(ValueError):
            gate.approve_request(request_obj.request_id, "console:two")
        decided = [r for r in _events(_scratch_home) if r["event_type"] == "remediation.approval_decided"]
        assert len(decided) == 1

    def test_escalation_after_two_hours_is_audited(self, _scratch_home):
        pager = Mock()
        gate = ApprovalGate(pagerduty_alerter=pager)
        assessment = categorize_drift("CODE_VERSION_DRIFT", "drift-004", "inst-01")
        request_obj = gate.request_approval(assessment, "inst-01")
        request_obj.requested_at = (datetime.utcnow() - timedelta(hours=3)).isoformat()

        result = gate.wait_for_approval(request_obj.request_id, timeout_seconds=0.3, poll_interval_seconds=0.1)

        assert result.state == ApprovalState.ESCALATED
        pager.trigger_incident.assert_called_once()
        assert "remediation.approval_escalated" in [r["event_type"] for r in _events(_scratch_home)]


# ==============================================================================
# PERFORMANCE TESTS
# ==============================================================================


class TestPerformance:

    def test_categorize_1000_drifts_under_1_second(self):
        drifts = [
            ("PLUGIN_MISSING", f"drift-{i}", f"inst-{i % 10}")
            for i in range(1000)
        ]

        start = time.time()
        assessments, summary = categorize_drifts(drifts)
        elapsed = time.time() - start

        assert len(assessments) == 1000
        assert elapsed < 1.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

"""GDPR + EU AI Act compliance modules (d5255c91f) — honest tests.

Adversarial review 2026-09-27: the operator approval gate wrote its "audit
trail" to an unchained ``orchestrator_audit.jsonl`` under ``~/.corvin`` (raw
operator id + reason text, all tenants in one file), and two report generators
issued compliance verdicts from that file with a hash check that never
recomputed a hash. Now: approvals go to THE tenant chain through the core
writer, content-free; the report generators refuse; empty evidence is
``not_measured``. tests/conftest.py isolates CORVIN_HOME per test.
"""

import json
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest

from core.compliance.audit_compliance_report import (
    AuditComplianceChecker,
    generate_compliance_report,
)
from core.compliance.compliance_framework import (
    ComplianceArtifact,
    ComplianceChecklistFactory,
    ComplianceEvent,
    ComplianceReportGenerator,
    GDPRArticle5Validator,
    GDPRArticle6Validator,
)
from core.compliance.operator_approval_system import OperatorApprovalGate
from forge import paths as forge_paths
from forge import security_events


def _chain(tenant_id):
    path = forge_paths.tenant_audit_chain(tenant_id)
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


@pytest.fixture
def gate():
    return OperatorApprovalGate()


@pytest.fixture
def tenant(monkeypatch):
    # write_event refuses a record tagged with a tenant other than the process one
    monkeypatch.setenv("CORVIN_TENANT_ID", "tenant-123")
    return "tenant-123"


class TestApprovalAudit:
    def test_request_is_written_to_the_tenant_chain(self, gate, tenant):
        req = gate.request_approval(tenant, "canary", {"confidence": 0.95})
        recs = [r for r in _chain(tenant) if r["event_type"] == "operator_approval.requested"]
        assert [r["details"]["request_id"] for r in recs] == [req.request_id]
        assert recs[0]["details"]["tenant_id"] == tenant
        ok, problems = security_events.verify_chain(forge_paths.tenant_audit_chain(tenant))
        assert ok, problems
        # nothing is written to the old second trail
        assert not (forge_paths.corvin_home() / "orchestrator_audit.jsonl").exists()

    def test_request_fails_closed_when_chain_write_fails(self, gate, tenant):
        with mock.patch.object(security_events, "write_event", side_effect=OSError("disk")):
            with pytest.raises(RuntimeError, match="Failed to write approval request"):
                gate.request_approval(tenant, "canary", {"confidence": 0.95})

    def test_decision_is_content_free(self, gate, tenant):
        req = gate.request_approval(tenant, "canary", {"confidence": 0.95})
        ok, _ = gate.reject_transition(req.request_id, tenant, "alice@example.com",
                                       reason="Metrics not ready for canary")
        assert ok
        text = forge_paths.tenant_audit_chain(tenant).read_text()
        assert "alice@example.com" not in text  # operator id pseudonymised
        assert "Metrics not ready" not in text  # free text stays out of the chain
        rec = [r for r in _chain(tenant) if r["event_type"] == "operator_approval.decided"][0]
        assert rec["details"]["decision"] == "rejected"
        assert rec["details"]["consent_basis"] == "Art. 7(3)"

    def test_approval_records_consent_basis(self, gate, tenant):
        req = gate.request_approval(tenant, "canary", {"confidence": 0.95})
        ok, _ = gate.approve_transition(req.request_id, tenant, "operator-1")
        assert ok
        rec = [r for r in _chain(tenant) if r["event_type"] == "operator_approval.decided"][0]
        assert rec["details"]["decision"] == "approved"
        assert rec["details"]["consent_basis"] == "Art. 6(1)(f)"


class TestDecidedOnce:
    """A request is decided at most once (was: reject, then approve, both succeeded)."""

    def test_cannot_approve_a_rejected_request(self, gate, tenant):
        req = gate.request_approval(tenant, "canary", {"confidence": 0.95})
        assert gate.reject_transition(req.request_id, tenant, "op", reason="no")[0]
        ok, msg = gate.approve_transition(req.request_id, tenant, "op")
        assert ok is False and "already rejected" in msg
        assert gate.check_approval_status(req.request_id, tenant)[0] == "rejected"

    def test_cannot_approve_twice(self, gate, tenant):
        req = gate.request_approval(tenant, "canary", {"confidence": 0.95})
        assert gate.approve_transition(req.request_id, tenant, "op")[0]
        assert gate.approve_transition(req.request_id, tenant, "op")[0] is False
        decided = [r for r in _chain(tenant) if r["event_type"] == "operator_approval.decided"]
        assert len(decided) == 1


class TestTenantIsolation:
    def test_stores_are_per_tenant(self, gate, monkeypatch):
        monkeypatch.setenv("CORVIN_TENANT_ID", "tenant-a")
        req_a = gate.request_approval("tenant-a", "pilot", {"confidence": 0.9})
        monkeypatch.setenv("CORVIN_TENANT_ID", "tenant-b")
        req_b = gate.request_approval("tenant-b", "pilot", {"confidence": 0.85})
        assert gate.check_approval_status(req_a.request_id, "tenant-a")[0] == "pending"
        assert gate.check_approval_status(req_b.request_id, "tenant-b")[0] == "pending"
        # tenant-b cannot see (or decide) tenant-a's request
        assert gate.check_approval_status(req_a.request_id, "tenant-b")[0] == "not_found"
        assert gate.approve_transition(req_a.request_id, "tenant-b", "op")[0] is False
        assert [r["details"]["request_id"] for r in _chain("tenant-a")] == [req_a.request_id]

    def test_invalid_tenant_rejected(self, gate):
        with pytest.raises(Exception):
            gate.request_approval("../x", "pilot", {})


class TestExpiry:
    def test_expired_request_cannot_be_approved(self, gate, tenant):
        req = gate.request_approval(tenant, "canary", {"confidence": 0.95})
        path = gate._requests_file(tenant)
        rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
        rows[0]["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))
        assert gate.approve_transition(req.request_id, tenant, "op")[0] is False
        assert gate.check_approval_status(req.request_id, tenant)[0] == "expired"
        assert any(r["event_type"] == "operator_approval.expired" for r in _chain(tenant))

    def test_timeout_is_seven_days(self, gate, tenant):
        req = gate.request_approval(tenant, "canary", {"confidence": 0.95})
        delta = datetime.fromisoformat(req.expires_at) - datetime.fromisoformat(req.created_at)
        assert delta == timedelta(days=7)


class TestReportsRefuseFabricatedVerdicts:
    def test_audit_compliance_checker_refuses(self, tmp_path):
        with pytest.raises(NotImplementedError, match="verify_chain"):
            AuditComplianceChecker(tmp_path / "orchestrator_audit.jsonl")
        with pytest.raises(NotImplementedError):
            generate_compliance_report("tenant-123", tmp_path / "orchestrator_audit.jsonl")

    def test_report_generator_refuses(self, tmp_path):
        with pytest.raises(NotImplementedError):
            ComplianceReportGenerator(tmp_path)

    def test_no_evidence_is_not_compliance(self):
        results = ComplianceChecklistFactory.validate_all("tenant-123", [])
        assert results and all(ok is False for ok, _ in results.values())
        assert all("not_measured" in v[0] for _, v in results.values())


class TestComplianceValidators:
    def _ev(self, **kw):
        base = dict(event_id="evt1", artifact_type=ComplianceArtifact.AUDIT_TRAIL,
                    timestamp=datetime.now(timezone.utc).isoformat(), tenant_id="tenant-123",
                    event_type="test", hash="abc123", prev_hash="")
        base.update(kw)
        return ComplianceEvent(**base)

    def test_gdpr_article5_validator(self):
        ok, violations = GDPRArticle5Validator().validate("tenant-123", [self._ev()])
        assert ok and violations == []

    def test_gdpr_article5_flags_cross_tenant_events(self):
        ok, violations = GDPRArticle5Validator().validate(
            "tenant-123", [self._ev(), self._ev(event_id="e2", tenant_id="other")])
        assert ok is False and "Cross-tenant" in violations[0]

    def test_gdpr_article6_validator(self):
        ok, _ = GDPRArticle6Validator().validate("tenant-123", [self._ev(
            artifact_type=ComplianceArtifact.CONSENT_RECORD, event_type="operator_approval",
            details={"consent_basis": "Art. 6(1)(f)"})])
        assert ok

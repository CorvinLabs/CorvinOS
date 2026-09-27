"""
Master orchestration (core/deployment/master_orchestration_blueprint.py) — honest suite.

The module is NOT WIRED (no production caller as of 2026-09-27). These tests
drive its public surface and read the REAL tenant audit chain back
(forge ``write_event`` via ``core.deployment.audit_sink``); nothing here asserts
a fabricated success.

Rewritten in the 2026-09-27 adversarial review. It replaces this file's old
content and ``test_master_orch_findings_complete_e2e.py`` (deleted: a duplicate
of this suite whose "ProofCollector"/aggregator printed "VERIFIED" regardless
of outcome, and whose F015 test implemented the enforcement inside the test).

Findings covered: F001 (day-14 gate), F002 (state persistence, tamper refusal),
F009 (records on the one tenant chain), F010 (LoM hash), F011 (metrics
snapshot), F012 (idempotency), F014 (concurrent audit writes), F015 (premature
canary reverted), F019 (timeout escalation, once), F023 (tenant isolation),
F024 (fail-closed without a measured baseline / on audit failure).
"""

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from core.deployment import audit_sink
from core.deployment.master_orchestration_blueprint import (
    MasterRolloutOrchestrator,
    OperatorApprovalGate,
    OperatorApprovalRecord,
    Phase,
    PhaseGateResult,
    SkillMetrics,
    SkillMode,
)


@pytest.fixture(autouse=True)
def _isolated_runtime(tmp_path, monkeypatch):
    """Each test has its own CORVIN_HOME/HOME — the audit chain is real."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "corvin"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CORVIN_TENANT_ID", raising=False)
    monkeypatch.delenv("VOICE_AUDIT_PATH", raising=False)
    monkeypatch.delenv("FORGE_ROOT", raising=False)
    yield


def _chain(tenant="_default"):
    se, fp = audit_sink._forge()
    path = fp.tenant_audit_chain(tenant)
    if not path.exists():
        return [], (True, []), path
    recs = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return recs, se.verify_chain(path), path


def _events(name, tenant="_default"):
    recs, _, _ = _chain(tenant)
    return [r for r in recs if r["event_type"] == f"deployment.master.{name}"]


def _metrics(phase=Phase.PHASE_1_SHADOW, **kw):
    base = dict(
        agreement_rate=0.99, confidence=0.90, confidence_sigma=0.02,
        latency_p99_ms=100.0, feedback_count=2000,
    )
    base.update(kw)
    return {"os.delegation_router": SkillMetrics(skill_id="os.delegation_router", phase=phase, **base)}


def _run_to_day_14(orch):
    while orch.state.base_state.day_number < 14:
        orch.advance_day(_metrics())


class TestF001Phase1Gate:
    def test_gate_fires_at_day_14_and_is_on_the_chain(self):
        orch = MasterRolloutOrchestrator()
        _run_to_day_14(orch)

        key = OperatorApprovalGate.PHASE_1_TO_2A.value
        assert key in orch.state.operator_approvals
        assert orch.state.base_state.pending_operator_approval
        assert orch.state.base_state.approval_required_for == key
        assert orch.state.base_state.phase == Phase.PHASE_1_SHADOW  # not auto-advanced

        req = _events("operator_approval_requested")
        assert len(req) == 1 and req[0]["details"]["gate"] == key
        # F011: snapshot is on the chain too
        assert req[0]["details"]["agreement_rate"] == pytest.approx(0.99)
        _, (ok, problems), _ = _chain()
        assert ok, problems

    def test_approval_transitions_and_records_pseudonymous_operator(self):
        orch = MasterRolloutOrchestrator()
        _run_to_day_14(orch)
        assert orch.operator_approve(OperatorApprovalGate.PHASE_1_TO_2A, approved_by="alice@example.com")

        st = orch.state.base_state
        assert st.phase == Phase.PHASE_2A_CANARY
        assert st.current_traffic_percentage == 1
        assert all(m == SkillMode.DUAL_WRITE for m in st.skill_states.values())

        granted = _events("operator_approval_granted")
        assert len(granted) == 1
        assert "alice@example.com" not in json.dumps(granted[0])  # operator_ref hash only
        assert len(granted[0]["details"]["operator_ref"]) == 12

    def test_approval_not_applied_when_audit_write_fails(self):
        orch = MasterRolloutOrchestrator()
        _run_to_day_14(orch)
        with patch.object(audit_sink, "emit", side_effect=audit_sink.AuditWriteFailed("down")):
            with pytest.raises(audit_sink.AuditWriteFailed):
                orch.operator_approve(OperatorApprovalGate.PHASE_1_TO_2A, approved_by="op")
        assert orch.state.base_state.phase == Phase.PHASE_1_SHADOW
        rec = orch.state.operator_approvals[OperatorApprovalGate.PHASE_1_TO_2A.value]
        assert rec.decision is None

    def test_rejected_request_cannot_be_approved_later(self):
        orch = MasterRolloutOrchestrator()
        _run_to_day_14(orch)
        assert orch.operator_reject(OperatorApprovalGate.PHASE_1_TO_2A, rejected_by="op", reason="no")
        assert orch.operator_approve(OperatorApprovalGate.PHASE_1_TO_2A, approved_by="op") is False
        assert orch.state.base_state.phase == Phase.PHASE_1_SHADOW

    def test_approve_without_request_is_refused(self):
        orch = MasterRolloutOrchestrator()
        assert orch.operator_approve(OperatorApprovalGate.PHASE_2A_TO_2B, approved_by="op") is False


class TestF002StatePersistence:
    def test_state_round_trips_per_tenant_under_corvin_home(self, tmp_path):
        orch = MasterRolloutOrchestrator()
        # Lives under CORVIN_HOME, per tenant — not ~/.corvin
        assert str(orch.STATE_FILE).startswith(str(tmp_path / "corvin" / "tenants" / "_default"))
        _run_to_day_14(orch)
        assert orch.STATE_FILE.exists()

        orch2 = MasterRolloutOrchestrator()
        assert orch2.state.base_state.day_number == 14
        rec = orch2.state.operator_approvals[OperatorApprovalGate.PHASE_1_TO_2A.value]
        assert rec.agreement_rate_at_approval == pytest.approx(0.99)

    def test_tampered_state_file_is_not_loaded(self):
        orch = MasterRolloutOrchestrator()
        orch.state.base_state.day_number = 10
        assert orch._save_state()
        data = json.loads(orch.STATE_FILE.read_text())
        data["base_state"]["phase"] = "PHASE_2B_SKILL_PRIMARY"  # forged promotion
        orch.STATE_FILE.write_text(json.dumps(data))

        orch2 = MasterRolloutOrchestrator()
        assert orch2.state.base_state.phase == Phase.PHASE_1_SHADOW
        assert orch2.state.base_state.day_number == 1


class TestF009TenantChain:
    def test_every_record_is_on_the_one_tenant_chain(self):
        orch = MasterRolloutOrchestrator()
        for _ in range(3):
            orch.advance_day(_metrics())
        days = _events("day_advanced")
        assert [r["details"]["day"] for r in days] == [2, 3, 4]
        assert [m["hash"] for m in orch.audit_trail] == [r["hash"] for r in _events("day_advanced")]
        ok, issues = orch.verify_audit_chain()
        assert ok, issues
        # no parallel audit file any more
        assert not list(Path(orch.STATE_FILE).parent.glob("*audit*.jsonl"))

    def test_verify_detects_tampering_of_the_real_chain(self):
        orch = MasterRolloutOrchestrator()
        orch.advance_day(_metrics())
        orch.advance_day(_metrics())
        _, _, path = _chain()
        lines = path.read_text().splitlines()
        rec = json.loads(lines[-2])
        rec["details"]["day"] = 99
        lines[-2] = json.dumps(rec)
        path.write_text("\n".join(lines) + "\n")
        ok, issues = orch.verify_audit_chain()
        assert not ok and issues

    def test_undeclared_event_is_refused(self):
        orch = MasterRolloutOrchestrator()
        with pytest.raises(audit_sink.AuditWriteFailed):
            orch._audit_log({"event": "made_up_event"})


class TestF010F012Approval:
    def test_lom_hash_and_idempotent_duplicate(self):
        orch = MasterRolloutOrchestrator()
        _run_to_day_14(orch)
        gate = OperatorApprovalGate.PHASE_1_TO_2A
        assert orch.operator_approve(gate, approved_by="op1")
        rec = orch.state.operator_approvals[gate.value]
        assert len(rec.lom_hash) == 64
        # same approval again: idempotent, no second chain record
        assert orch.operator_approve(gate, approved_by="op1") is True
        assert len(_events("operator_approval_granted")) == 1


class TestF011Snapshot:
    def test_metrics_snapshot_at_request(self):
        orch = MasterRolloutOrchestrator()
        orch._request_operator_approval(
            OperatorApprovalGate.PHASE_1_TO_2A,
            _metrics(agreement_rate=0.98, confidence=0.94, latency_p99_ms=110.0, feedback_count=950),
        )
        rec = orch.state.operator_approvals[OperatorApprovalGate.PHASE_1_TO_2A.value]
        assert rec.agreement_rate_at_approval == pytest.approx(0.98)
        assert rec.confidence_at_approval == pytest.approx(0.94)
        assert rec.latency_p99_at_approval == pytest.approx(110.0)
        assert rec.feedback_count_at_approval == 950

    def test_request_without_metrics_records_not_measured(self):
        orch = MasterRolloutOrchestrator()
        orch._request_operator_approval(OperatorApprovalGate.PHASE_2A_TO_2B)
        rec = orch.state.operator_approvals[OperatorApprovalGate.PHASE_2A_TO_2B.value]
        assert rec.agreement_rate_at_approval is None


class TestF014Concurrency:
    def test_concurrent_audit_writes_keep_the_chain_valid(self):
        orch = MasterRolloutOrchestrator()

        def emit(n):
            for i in range(10):
                orch._audit_log({"event": "automatic_transition", "transition": f"t{n}_{i}"})

        threads = [threading.Thread(target=emit, args=(n,)) for n in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(orch.audit_trail) == 50
        assert len(_events("automatic_transition")) == 50
        ok, issues = orch.verify_audit_chain()
        assert ok, issues


class TestF015PrematureCanary:
    def test_canary_before_day_14_is_reverted(self):
        orch = MasterRolloutOrchestrator()
        st = orch.state.base_state
        st.phase = Phase.PHASE_2A_CANARY
        st.current_traffic_percentage = 1
        st.day_number = 10
        orch.advance_day(_metrics(phase=Phase.PHASE_2A_CANARY))
        assert st.phase == Phase.PHASE_1_SHADOW
        assert st.current_traffic_percentage == 0
        assert all(m == SkillMode.ADVISORY for m in st.skill_states.values())
        assert len(_events("premature_phase_2a_reverted")) == 1


class TestF019Timeout:
    def test_timeout_escalates_once(self):
        orch = MasterRolloutOrchestrator()
        orch.state.operator_approvals[OperatorApprovalGate.PHASE_1_TO_2A.value] = OperatorApprovalRecord(
            gate=OperatorApprovalGate.PHASE_1_TO_2A,
            requested_at=(datetime.now(timezone.utc) - timedelta(days=8)).isoformat(),
        )
        orch._check_approval_timeouts()
        orch._check_approval_timeouts()
        esc = _events("approval_escalated_to_admin")
        assert len(esc) == 1
        assert esc[0]["details"]["gate"] == OperatorApprovalGate.PHASE_1_TO_2A.value


class TestF023TenantIsolation:
    def test_state_is_per_tenant(self, monkeypatch):
        monkeypatch.setenv("CORVIN_TENANT_ID", "tenant_a")
        orch_a = MasterRolloutOrchestrator(tenant_id="tenant_a")
        orch_a.state.base_state.day_number = 10
        assert orch_a._save_state()

        orch_b = MasterRolloutOrchestrator(tenant_id="tenant_b")
        assert orch_b.STATE_FILE != orch_a.STATE_FILE
        assert orch_b.state.base_state.day_number == 1

        # Even pointed at tenant_a's file, tenant_b refuses to load it
        orch_b.STATE_FILE = orch_a.STATE_FILE
        orch_b._load_persisted_state()
        assert orch_b.state.base_state.day_number == 1
        assert orch_b.state.tenant_id == "tenant_b"

    def test_audit_for_foreign_tenant_is_refused_at_the_chokepoint(self):
        # process tenant is _default; a tenant_b record must not be written
        orch = MasterRolloutOrchestrator(tenant_id="tenant_b")
        with pytest.raises(audit_sink.AuditWriteFailed):
            orch._audit_log({"event": "automatic_transition", "transition": "x"})

    def test_tenant_records_land_on_their_own_chain(self, monkeypatch):
        monkeypatch.setenv("CORVIN_TENANT_ID", "tenant_a")
        orch = MasterRolloutOrchestrator(tenant_id="tenant_a")
        orch.advance_day(_metrics())
        assert len(_events("day_advanced", tenant="tenant_a")) == 1
        assert _events("day_advanced", tenant="_default") == []


class TestF024FailClosed:
    def test_weekly_gate_without_measured_baseline_fails(self):
        orch = MasterRolloutOrchestrator()
        orch.state.base_state.phase = Phase.PHASE_2A_CANARY
        assert orch.state.base_state.baseline_latency_p99_ms is None
        orch._evaluate_weekly_gate(3, _metrics(phase=Phase.PHASE_2A_CANARY))
        ev = orch.state.weekly_evaluations[3]
        assert ev.gate_result == PhaseGateResult.FAIL
        assert "not_measured" in ev.reason

    def test_weekly_gate_with_measured_baseline_evaluates(self):
        orch = MasterRolloutOrchestrator()
        _run_to_day_14(orch)  # shadow days record the baseline
        assert orch.state.base_state.baseline_latency_p99_ms == pytest.approx(100.0)
        orch._evaluate_weekly_gate(3, _metrics(phase=Phase.PHASE_2A_CANARY, latency_p99_ms=105.0))
        assert orch.state.weekly_evaluations[3].gate_result == PhaseGateResult.PASS

    def test_advance_day_without_metrics_is_refused(self):
        orch = MasterRolloutOrchestrator()
        with pytest.raises(ValueError):
            orch.advance_day({})
        assert orch.state.base_state.day_number == 1

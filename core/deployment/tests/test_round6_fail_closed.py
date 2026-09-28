"""Adversarial review round 6 — NOT-WIRED deployment modules must fail closed.

Each test names the defect it pins. All of them read the REAL tenant audit
chain (``tenant_audit_chain`` through ``core.deployment.audit_sink``); run them
with an isolated ``CORVIN_HOME``.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from core.deployment import audit_sink
from core.deployment.incident_response_procedures import IncidentDetector, IncidentType
from core.deployment.master_orchestration_blueprint import MasterRolloutOrchestrator
from core.deployment.phase3_rollout_orchestrator import (
    Phase,
    Phase2bActivationGate,
    RolloutOrchestrator,
    SkillMetrics,
    SkillMode,
)
from core.deployment.rollback_automation import (
    RollbackAction,
    RollbackController,
    RollbackEvent,
    RollbackTrigger,
)

HEALTHY = {
    "agreement_rate": 0.995,
    "latency_p99_ms": 100.0,
    "confidence": 0.9,
    "prior_confidence": 0.9,
    "audit_chain_verified": True,
    "tenant_isolation_verified": True,
    "security_checks_pass": True,
}


def _chain(event_type: str) -> list[dict]:
    se, fp = audit_sink._forge()
    chain = fp.tenant_audit_chain("_default")
    if not chain.exists():
        return []
    recs = [json.loads(line) for line in chain.read_text().splitlines() if line.strip()]
    return [r for r in recs if r.get("event_type") == event_type]


def _for(event_type: str, rb_id: str) -> list[dict]:
    return [r for r in _chain(event_type) if r["details"].get("rollback_event_id") == rb_id]


# ---------------------------------------------------------------- item 1


class TestRollbackHasASubject:
    def test_trigger_event_carries_caller_skill_and_phase(self):
        c = RollbackController()
        ev = c.check_all_triggers({**HEALTHY, "audit_chain_verified": False},
                                  phase="PHASE_2A_CANARY", skill_id="os.delegation_router")
        assert ev.trigger == RollbackTrigger.AUDIT_CHAIN_BREAK
        # skill_id was dropped and the phase came from metrics ("unknown")
        assert ev.skill_id == "os.delegation_router"
        assert ev.phase == "PHASE_2A_CANARY"

    def test_manual_rollback_without_skill_writes_no_executed_record(self):
        c = RollbackController()
        ev = c.manual_rollback(operator_id="ops", reason="x", phase="PHASE_2A_CANARY")
        assert ev.executed is False
        assert _for("deployment.rollback_executed", ev.event_id) == []
        failed = _for("deployment.rollback_execution_failed", ev.event_id)
        assert failed and failed[-1]["details"]["error_type"] == "missing_skill_id"
        assert c.rollback_events == []

    def test_failed_revert_is_audited_and_locks_stay(self):
        c = RollbackController()
        ev = RollbackEvent(
            event_id="RB-r6-revert-fail",
            trigger=RollbackTrigger.CORRECTNESS_DROP,
            timestamp=datetime.now(timezone.utc).isoformat(),
            phase="PHASE_R6",
            skill_id="os.delegation_router",
            # REVERT listed FIRST: it used to return before LOCK_PHASE ran.
            actions_taken=[RollbackAction.REVERT_VERSION, RollbackAction.DISABLE_SKILL,
                           RollbackAction.LOCK_PHASE],
            lom="test:r6",
        )
        with patch.object(c, "_execute_version_revert", return_value=False):
            assert c.execute_rollback(ev) is False
        assert ev.executed is False
        assert _for("deployment.rollback_executed", ev.event_id)
        failed = _for("deployment.rollback_execution_failed", ev.event_id)
        assert failed and failed[-1]["details"]["error_type"] == "version_revert_failed"
        # protective actions applied before the revert, and they hold
        assert c.is_phase_locked("PHASE_R6")
        assert "skill:os.delegation_router" in c.get_open_lockdowns()
        # a failed rollback does not arm the cascade guard (a retry may run)
        assert c.last_rollback_time == 0.0

    def test_successful_revert_with_skill(self):
        c = RollbackController()
        ev = c.check_all_triggers({**HEALTHY, "agreement_rate": 0.90},
                                  phase="PHASE_R6_OK", skill_id="os.context_adapter")
        with patch.object(c, "_execute_version_revert", return_value=True) as rv:
            assert c.execute_rollback(ev, version_to_revert="v1") is True
        rv.assert_called_once_with("os.context_adapter", "v1")
        assert ev.executed is True
        assert _for("deployment.rollback_executed", ev.event_id)

    def test_event_ids_unique_across_controllers(self):
        ids = {RollbackController()._next_event_id() for _ in range(50)}
        assert len(ids) == 50


# ---------------------------------------------------------------- item 5


class TestRollbackNotMeasured:
    def test_healthy_metrics_fire_nothing(self):
        assert RollbackController().check_all_triggers(dict(HEALTHY), phase="P") is None

    @pytest.mark.parametrize("missing,trigger", [
        ("agreement_rate", RollbackTrigger.CORRECTNESS_DROP),
        ("latency_p99_ms", RollbackTrigger.LATENCY_SPIKE),
        ("confidence", RollbackTrigger.CONFIDENCE_REGRESSION),
        ("prior_confidence", RollbackTrigger.CONFIDENCE_REGRESSION),
    ])
    def test_missing_metric_holds_escalation(self, missing, trigger):
        m = {k: v for k, v in HEALTHY.items() if k != missing}
        ev = RollbackController().check_all_triggers(m, phase="P")
        assert ev is not None and ev.trigger == trigger
        assert ev.actual_value is None
        assert RollbackAction.HOLD_TRAFFIC in ev.actions_taken
        assert "not measured" in ev.reason

    def test_real_breach_outranks_not_measured(self):
        m = {k: v for k, v in HEALTHY.items() if k != "agreement_rate"}
        m["tenant_isolation_verified"] = False
        ev = RollbackController().check_all_triggers(m, phase="P")
        assert ev.trigger == RollbackTrigger.TENANT_ISOLATION_VIOLATION


# ---------------------------------------------------------------- item 2


def _metrics(p99=100.0, conf=0.9):
    return {
        sid: SkillMetrics(skill_id=sid, agreement_rate=0.995, confidence=conf, latency_p99_ms=p99)
        for sid in ("os.delegation_router", "os.context_adapter")
    }


class TestMasterStateRestart:
    def test_measured_baseline_survives_restart(self, tmp_path):
        m1 = MasterRolloutOrchestrator(state_dir=tmp_path)
        st = m1.base_orch.state
        st.phase = Phase.PHASE_2A_CANARY
        st.day_number, st.week_number = 15, 3
        st.current_traffic_percentage = 1
        st.baseline_latency_p99_ms = 100.0
        st.prior_mean_confidence = 0.9
        m1.base_orch._daily_latency_p99 = [100.0] * 14
        assert m1._save_state()

        m2 = MasterRolloutOrchestrator(state_dir=tmp_path)
        assert m2.state.base_state.baseline_latency_p99_ms == 100.0
        assert m2.state.base_state.prior_mean_confidence == 0.9
        assert m2.base_orch._daily_latency_p99 == [100.0] * 14

        m2.advance_day(_metrics())
        # used to roll back BASELINE_NOT_MEASURED on the first day after restart
        assert m2.state.base_state.phase == Phase.PHASE_2A_CANARY, m2.state.base_state.last_decision_reason

    def test_tampered_samples_are_not_loaded(self, tmp_path):
        m1 = MasterRolloutOrchestrator(state_dir=tmp_path)
        m1.base_orch._daily_latency_p99 = [100.0] * 7
        m1.base_orch.state.baseline_latency_p99_ms = 100.0
        assert m1._save_state()
        data = json.loads(m1.STATE_FILE.read_text())
        data["daily_latency_p99"] = [1.0] * 7
        m1.STATE_FILE.write_text(json.dumps(data))
        m2 = MasterRolloutOrchestrator(state_dir=tmp_path)
        assert m2.base_orch._daily_latency_p99 == []
        assert m2.state.base_state.baseline_latency_p99_ms is None


# ---------------------------------------------------------------- item 3


class TestPhase2bUnmeasured:
    def test_gate_blocks_on_unmeasured_checks(self):
        ok, reasons = Phase2bActivationGate.evaluate_skill_activation(
            "os.delegation_router", SkillMetrics("os.delegation_router", agreement_rate=0.99, confidence=0.95),
            days_at_100_pct=10, days_since_last_rollback=30,
            audit_violations_in_window=0, latency_sigma_ms=1.0,
            unmeasured_checks=["tenant_isolation"],
        )
        assert ok is False
        assert any("tenant_isolation" in r for r in reasons)

    @pytest.mark.parametrize("isolation,expect_primary", [(None, False), (0, True)])
    def test_orchestrator_needs_measured_isolation(self, isolation, expect_primary):
        o = RolloutOrchestrator()
        o.state.phase = Phase.PHASE_2B_SKILL_PRIMARY
        o.state.day_number, o.state.week_number = 75, 11
        o.state.baseline_latency_p99_ms = 100.0
        o.state.prior_mean_confidence = 0.95
        o._daily_latency_p99 = [100.0] * 7
        metrics = {"os.delegation_router": SkillMetrics(
            "os.delegation_router", agreement_rate=0.995, confidence=0.95, latency_p99_ms=100.0)}
        o.advance_day(metrics, audit_violations_in_window=0, tenant_isolation_violations=isolation)
        assert o.state.phase == Phase.PHASE_2B_SKILL_PRIMARY, o.state.last_decision_reason
        is_primary = o.state.skill_states["os.delegation_router"] == SkillMode.PRIMARY
        assert is_primary is expect_primary


# ---------------------------------------------------------------- item 4


class TestIncidentNotMeasured:
    def test_missing_verification_raises_critical_incidents(self):
        inc = IncidentDetector().detect_incidents({"latency_p99_ms": 100.0})
        types = {i.incident_type for i in inc}
        assert IncidentType.AUDIT_CHAIN_BREAK in types
        assert IncidentType.TENANT_ISOLATION_VIOLATION in types
        assert all(i.details.get("measured") is False for i in inc
                   if i.incident_type in (IncidentType.AUDIT_CHAIN_BREAK,
                                          IncidentType.TENANT_ISOLATION_VIOLATION))

    def test_verified_raises_none(self):
        inc = IncidentDetector().detect_incidents({
            "latency_p99_ms": 100.0, "audit_chain_verified": True, "tenant_isolation_verified": True,
        })
        assert inc == []


# ---------------------------------------------------------------- item 6


class TestEcosystemAuditFirst:
    def test_unauditable_tenant_mutates_nothing(self):
        from core.marketplace.ecosystem_monitoring import EcosystemMonitor

        mon = EcosystemMonitor()
        # "acme" is not the process tenant: the forge writer refuses the
        # alert record. Metrics used to be committed before that write.
        with pytest.raises(audit_sink.AuditWriteFailed):
            mon.record_skill_execution("acme", "s1", 10.0, success=False)
        assert "s1" not in mon.metrics.get("acme", {})
        assert ("acme", "s1") not in mon._latencies
        assert mon.get_alerts("acme") == []

    def test_auditable_tenant_commits_metrics_and_alert(self):
        from core.marketplace.ecosystem_monitoring import EcosystemMonitor

        mon = EcosystemMonitor()
        mon.record_skill_execution("_default", "s-r6", 10.0, success=False)
        m = mon.metrics["_default"]["s-r6"]
        assert m.total_executions == 1 and m.failed_executions == 1
        alerts = mon.get_alerts("_default")
        assert [a.category for a in alerts] == ["high_error_rate"]
        recs = [r for r in _chain("marketplace.ecosystem_alert_raised")
                if r["details"].get("alert_id") == alerts[0].alert_id]
        assert recs

"""
ADR Compliance Validation Framework for 12-Week Production Rollout

Validates adherence to key ADRs during production rollout:
- ADR-0206 (Canary Strategy): traffic escalation, agreement rates, latency thresholds
- ADR-2165 (Phase 7 Learning Loop): confidence convergence, feedback signals
- ADR-0186 (Presence Heartbeat): instance ping, geo-tracking, consent signals
- ADR-0369 (Edge Cases & Contingency): rollback scenarios, degradation handling

Generates weekly compliance reports and blocks phase transitions if ADRs are violated.

NOT WIRED: no production caller as of 2026-09-27 (adversarial review). A metric
that was not supplied is NOT MEASURED and its check FAILS — no default baseline,
no default latency/correctness value that reads as a pass. Report records go to
the tenant audit chain through ``core.deployment.audit_sink`` (fail-closed).

Compliance: GDPR (Art. 5/6/30/32), EU AI Act (Art. 5/50), audit-first (ADR-0232/0233)

Adversarial Review Fixes:
- F023: Tenant isolation in all state (tenant_id field, all queries filtered by tenant_id)
- F024-F031: Edge case handling, error handling, compliance verification
"""

from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
import json
import logging
from pathlib import Path
import threading

from . import audit_sink

logger = logging.getLogger(__name__)

audit_sink.register_events({
    "deployment.adr_violation_detected": {"week", "violation_count", "violations"},
    "deployment.weekly_compliance_report": {
        "week", "overall_status", "total_checks", "passed_checks", "failed_checks",
    },
    "deployment.compliance_approved_for_transition": {"week"},
})

# ADRs whose FAIL blocks a phase transition. ADR-0186 carries the geo-consent
# check: a consent violation must block, not merely lower the overall status.
BLOCKING_ADRS = ("ADR-0206", "ADR-0205", "ADR-0369", "ADR-0186")


class ComplianceStatus(Enum):
    """ADR compliance status"""
    PASS = "pass"
    FAIL = "fail"
    WARNING = "warning"


@dataclass
class ADRComplianceCheck:
    """Single ADR compliance check result (F023)"""
    adr_id: str
    check_name: str
    status: ComplianceStatus
    actual_value: float
    threshold: float
    tolerance: float  # Allowable deviation
    message: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    tenant_id: str = "_default"  # F023: Tenant isolation


@dataclass
class WeeklyComplianceReport:
    """Weekly ADR compliance report (F023)"""
    week_number: int
    phase: str
    timestamp: str
    checks: List[ADRComplianceCheck] = field(default_factory=list)
    overall_status: ComplianceStatus = ComplianceStatus.PASS
    blocking_violations: List[str] = field(default_factory=list)
    tenant_id: str = "_default"  # F023: Tenant isolation

    def add_check(self, check: ADRComplianceCheck) -> None:
        """Add compliance check result (F024: edge case handling for empty metrics)"""
        # F024: a check without a value was not measured — it FAILS. Skipping
        # it (the old behaviour) let a report with no data read as PASS.
        if check.actual_value is None or check.threshold is None:
            logger.warning(f"Check {check.adr_id}/{check.check_name} not measured → FAIL")
            check.status = ComplianceStatus.FAIL
            check.message = f"{check.check_name}: not measured"

        self.checks.append(check)

        # Update overall status
        if check.status == ComplianceStatus.FAIL:
            self.overall_status = ComplianceStatus.FAIL
            # Critical ADRs that block phase progression: canary (0206), learning (0205), edge cases (0369)
            if check.adr_id in BLOCKING_ADRS:
                self.blocking_violations.append(f"{check.adr_id}/{check.check_name}")
        elif check.status == ComplianceStatus.WARNING and self.overall_status == ComplianceStatus.PASS:
            self.overall_status = ComplianceStatus.WARNING


class ADRComplianceValidator:
    """
    Validates rollout compliance against key ADRs.

    Each ADR has specific thresholds and validation rules that must be met
    for phase progression to be approved.
    """

    # ADR-0206: Canary Strategy thresholds (per week)
    CANARY_THRESHOLDS = {
        3: {  # Week 3, 1% traffic
            "agreement_rate": 0.99,
            "latency_p99_threshold_pct": 0.10,  # Within 10% of Phase 1 baseline
            "correctness_delta": None,  # Not enforced at low traffic
        },
        4: {  # Week 4, 10% traffic
            "agreement_rate": 0.98,
            "latency_p99_threshold_pct": 0.15,
            "correctness_delta": 0.02,  # Within 2%
        },
        5: {  # Week 5, 50% traffic
            "agreement_rate": 0.97,
            "latency_p99_threshold_pct": 0.20,
            "correctness_delta": 0.02,
        },
        6: {  # Week 6+, 100% traffic
            "agreement_rate": 0.97,
            "latency_p99_threshold_pct": 0.25,
            "correctness_delta": 0.02,
        },
    }

    # ADR-0205: Learning Loop thresholds
    LEARNING_THRESHOLDS = {
        "confidence_convergence_sigma": 0.05,  # σ < 5% indicates convergence
        "feedback_count_minimum": 1000,  # Minimum feedback events per skill
        "convergence_window_days": 7,  # Days to measure convergence over
        "confidence_min_for_activation": 0.85,  # Minimum confidence for skill activation
    }

    # ADR-0186: Presence Heartbeat thresholds
    HEARTBEAT_THRESHOLDS = {
        "ping_cadence_seconds": 300,  # 5-minute heartbeat
        "geo_tracking_ttl_days": 30,  # 30-day retention for tier 1
        "geo_tracking_grid_km": 10,  # 10km grid resolution
        "consent_respected": True,  # Must honor opt-out
    }

    # ADR-0369: Edge Cases thresholds
    EDGE_CASES = {
        "rollback_triggers_armed": 8,  # All 8 triggers must be active
        "degradation_latency_threshold_pct": 0.20,  # 20% max degradation
        "exception_rate_threshold_pct": 0.01,  # Max 1% exceptions
        "audit_chain_verification_passes": True,  # Must verify
    }

    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id  # F023: Tenant isolation
        self.reports: Dict[int, WeeklyComplianceReport] = {}
        self.audit_trail: List[Dict] = []
        self.lock = threading.RLock()  # F024: Thread-safe state

    def validate_adr_0206_canary(
        self,
        week_number: int,
        metrics: Dict[str, float],
        baseline_latency_ms: Optional[float],
    ) -> List[ADRComplianceCheck]:
        """
        Validate ADR-0206: Canary Strategy compliance (F023, F024-F031).

        Returns list of compliance checks (mix of pass/fail/warning).
        F024: Edge case handling for empty metrics
        F025: Error handling for invalid week numbers
        F026: Compliance enforcement for baseline latency
        F023: Tenant isolation in all checks
        """
        checks = []

        # F025: Validate week number
        if week_number < 1 or week_number > 12:
            logger.warning(f"Invalid week number {week_number}, clamping to valid range")
            week_number = max(1, min(week_number, 12))

        # F024: Edge case handling - empty metrics
        if not metrics:
            logger.warning(f"Empty metrics for week {week_number}, creating placeholder checks")
            checks.append(ADRComplianceCheck(
                adr_id="ADR-0206",
                check_name="agreement_rate",
                status=ComplianceStatus.FAIL,
                actual_value=0.0,
                threshold=0.99,
                tolerance=0.01,
                message="No metrics available",
                tenant_id=self.tenant_id,  # F023
            ))
            return checks

        # F026: no measured Phase 1 baseline → the latency check cannot pass.
        # (Substituting a 100 ms default fabricated the comparison.)
        baseline_ok = isinstance(baseline_latency_ms, (int, float)) and baseline_latency_ms > 0
        if not baseline_ok:
            logger.error(f"Baseline latency not measured/invalid: {baseline_latency_ms!r}")

        # Get thresholds for this week
        week_key = min(week_number, 6)  # Week 6+ use week 6 thresholds
        thresholds = self.CANARY_THRESHOLDS.get(week_key, self.CANARY_THRESHOLDS[3])

        # Check 1: Agreement rate
        actual_agreement = metrics.get("agreement_rate", 0.0)
        required_agreement = thresholds["agreement_rate"]

        if actual_agreement >= required_agreement:
            status = ComplianceStatus.PASS
        elif actual_agreement >= required_agreement - 0.01:  # 1% tolerance
            status = ComplianceStatus.WARNING
        else:
            status = ComplianceStatus.FAIL

        checks.append(ADRComplianceCheck(
            adr_id="ADR-0206",
            check_name="agreement_rate",
            status=status,
            actual_value=actual_agreement,
            threshold=required_agreement,
            tolerance=0.01,
            message=f"Agreement rate {actual_agreement:.1%} vs. required {required_agreement:.1%}",
            tenant_id=self.tenant_id,  # F023
        ))

        # Check 2: Latency p99 (missing metric or missing baseline = FAIL)
        max_latency_pct = thresholds["latency_p99_threshold_pct"]
        if "latency_p99_ms" not in metrics or not baseline_ok:
            checks.append(ADRComplianceCheck(
                adr_id="ADR-0206",
                check_name="latency_p99",
                status=ComplianceStatus.FAIL,
                actual_value=float(metrics.get("latency_p99_ms", -1.0)),
                threshold=-1.0,
                tolerance=0.0,
                message=("Latency p99 not measured" if "latency_p99_ms" not in metrics
                         else "Phase 1 baseline latency not measured"),
                tenant_id=self.tenant_id,
            ))
        else:
            actual_latency_ms = metrics["latency_p99_ms"]
            allowed_latency_ms = baseline_latency_ms * (1 + max_latency_pct)

            if actual_latency_ms <= allowed_latency_ms:
                status = ComplianceStatus.PASS
            elif actual_latency_ms <= allowed_latency_ms * 1.1:  # 10% tolerance
                status = ComplianceStatus.WARNING
            else:
                status = ComplianceStatus.FAIL

            checks.append(ADRComplianceCheck(
                adr_id="ADR-0206",
                check_name="latency_p99",
                status=status,
                actual_value=actual_latency_ms,
                threshold=allowed_latency_ms,
                tolerance=allowed_latency_ms * 0.1,
                message=f"Latency p99 {actual_latency_ms:.1f}ms vs. allowed {allowed_latency_ms:.1f}ms",
                tenant_id=self.tenant_id,  # F023
            ))

        # Check 3: Correctness delta (if applicable; missing = FAIL)
        if thresholds["correctness_delta"] is not None and "correctness_delta" not in metrics:
            checks.append(ADRComplianceCheck(
                adr_id="ADR-0206",
                check_name="correctness_delta",
                status=ComplianceStatus.FAIL,
                actual_value=-1.0,
                threshold=thresholds["correctness_delta"],
                tolerance=0.0,
                message="Correctness delta not measured",
                tenant_id=self.tenant_id,
            ))
        elif thresholds["correctness_delta"] is not None:
            actual_delta = metrics["correctness_delta"]
            max_delta = thresholds["correctness_delta"]

            if abs(actual_delta) <= max_delta:
                status = ComplianceStatus.PASS
            elif abs(actual_delta) <= max_delta * 1.2:  # 20% tolerance
                status = ComplianceStatus.WARNING
            else:
                status = ComplianceStatus.FAIL

            checks.append(ADRComplianceCheck(
                adr_id="ADR-0206",
                check_name="correctness_delta",
                status=status,
                actual_value=abs(actual_delta),
                threshold=max_delta,
                tolerance=max_delta * 0.2,
                message=f"Correctness δ {actual_delta:.1%} vs. allowed {max_delta:.1%}",
                tenant_id=self.tenant_id,  # F023
            ))

        return checks

    def validate_adr_0205_learning_loop(
        self,
        metrics: Dict[str, Dict[str, float]],  # per-skill metrics
    ) -> List[ADRComplianceCheck]:
        """
        Validate ADR-0205: Phase 7 Learning Loop compliance (F023, F024).

        Checks confidence convergence, feedback signal emission, parameter optimization.
        F024: Edge case handling for empty metrics
        F023: Tenant isolation in all checks
        """
        checks = []

        # F024: Edge case handling - empty metrics
        if not metrics:
            logger.warning("No learning metrics available, marking as FAIL")
            checks.append(ADRComplianceCheck(
                adr_id="ADR-0205",
                check_name="feedback_count",
                status=ComplianceStatus.FAIL,
                actual_value=0,
                threshold=self.LEARNING_THRESHOLDS["feedback_count_minimum"],
                tolerance=0,
                message="No metrics available",
                tenant_id=self.tenant_id,  # F023
            ))
            return checks

        # Check 1: Feedback count per skill
        for skill_id, skill_metrics in metrics.items():
            if not skill_metrics:
                logger.warning(f"Empty metrics for skill {skill_id}")
                continue

            feedback_count = skill_metrics.get("feedback_count", 0)
            min_feedback = self.LEARNING_THRESHOLDS["feedback_count_minimum"]

            if feedback_count >= min_feedback:
                status = ComplianceStatus.PASS
            elif feedback_count >= min_feedback * 0.8:  # 80% tolerance
                status = ComplianceStatus.WARNING
            else:
                status = ComplianceStatus.FAIL

            checks.append(ADRComplianceCheck(
                adr_id="ADR-2165",
                check_name=f"feedback_count_{skill_id}",
                status=status,
                actual_value=feedback_count,
                threshold=min_feedback,
                tolerance=min_feedback * 0.2,
                message=f"{skill_id}: {feedback_count} feedback events vs. required {min_feedback}",
                tenant_id=self.tenant_id,  # F023
            ))

        # Check 2: Confidence convergence (sigma)
        for skill_id, skill_metrics in metrics.items():
            if not skill_metrics:
                continue

            confidence_sigma = skill_metrics.get("confidence_sigma", 1.0)
            max_sigma = self.LEARNING_THRESHOLDS["confidence_convergence_sigma"]

            if confidence_sigma <= max_sigma:
                status = ComplianceStatus.PASS
            elif confidence_sigma <= max_sigma * 1.5:  # 50% tolerance
                status = ComplianceStatus.WARNING
            else:
                status = ComplianceStatus.FAIL

            checks.append(ADRComplianceCheck(
                adr_id="ADR-2165",
                check_name=f"confidence_convergence_{skill_id}",
                status=status,
                actual_value=confidence_sigma,
                threshold=max_sigma,
                tolerance=max_sigma * 0.5,
                message=f"{skill_id}: confidence σ={confidence_sigma:.3f} vs. required <{max_sigma:.3f}",
                tenant_id=self.tenant_id,  # F023
            ))

        return checks

    def validate_adr_0186_heartbeat(
        self,
        heartbeat_metrics: Dict[str, Any],
    ) -> List[ADRComplianceCheck]:
        """
        Validate ADR-0186: Presence Heartbeat compliance (F023, F024).

        Checks instance ping cadence, geo-tracking tier compliance, consent signals.
        F024: Edge case handling for missing metrics
        F023: Tenant isolation in all checks
        """
        checks = []

        # F024: Edge case handling - missing metrics
        if not heartbeat_metrics:
            logger.warning("No heartbeat metrics available, marking as FAIL")
            checks.append(ADRComplianceCheck(
                adr_id="ADR-0186",
                check_name="ping_cadence",
                status=ComplianceStatus.FAIL,
                actual_value=999999.0,
                threshold=self.HEARTBEAT_THRESHOLDS["ping_cadence_seconds"],
                tolerance=0,
                message="No heartbeat metrics available",
                tenant_id=self.tenant_id,  # F023
            ))
            return checks

        # Check 1: Ping cadence (should be every 5 minutes)
        last_ping_seconds_ago = heartbeat_metrics.get("last_ping_seconds_ago", 999999)
        expected_cadence = self.HEARTBEAT_THRESHOLDS["ping_cadence_seconds"]

        if last_ping_seconds_ago <= expected_cadence * 1.2:  # 20% tolerance
            status = ComplianceStatus.PASS
        elif last_ping_seconds_ago <= expected_cadence * 1.5:
            status = ComplianceStatus.WARNING
        else:
            status = ComplianceStatus.FAIL

        checks.append(ADRComplianceCheck(
            adr_id="ADR-0186",
            check_name="ping_cadence",
            status=status,
            actual_value=last_ping_seconds_ago,
            threshold=expected_cadence,
            tolerance=expected_cadence * 0.2,
            message=f"Last ping {last_ping_seconds_ago}s ago vs. expected ~{expected_cadence}s",
            tenant_id=self.tenant_id,  # F023
        ))

        # Check 2: Geo-tracking consent
        geo_consent_respected = heartbeat_metrics.get("geo_consent_respected", False)

        checks.append(ADRComplianceCheck(
            adr_id="ADR-0186",
            check_name="geo_consent_respected",
            status=ComplianceStatus.PASS if geo_consent_respected else ComplianceStatus.FAIL,
            actual_value=1.0 if geo_consent_respected else 0.0,
            threshold=1.0,
            tolerance=0.0,
            message=f"Geo-tracking consent {'respected' if geo_consent_respected else 'violated'}",
            tenant_id=self.tenant_id,  # F023
        ))

        return checks

    def validate_adr_0369_edge_cases(
        self,
        rollback_metrics: Dict[str, Any],
    ) -> List[ADRComplianceCheck]:
        """
        Validate ADR-0369: Edge Cases & Contingency compliance (F023, F024).

        Checks rollback triggers armed, degradation thresholds, audit chain.
        F024: Edge case handling for missing metrics
        F023: Tenant isolation in all checks
        """
        checks = []

        # F024: Edge case handling - missing metrics
        if not rollback_metrics:
            logger.warning("No rollback metrics available, marking as FAIL")
            checks.append(ADRComplianceCheck(
                adr_id="ADR-0369",
                check_name="rollback_triggers_armed",
                status=ComplianceStatus.FAIL,
                actual_value=0.0,
                threshold=self.EDGE_CASES["rollback_triggers_armed"],
                tolerance=0.0,
                message="No rollback metrics available",
                tenant_id=self.tenant_id,  # F023
            ))
            return checks

        # Check 1: Rollback triggers armed
        triggers_armed = rollback_metrics.get("rollback_triggers_armed", 0)
        required_triggers = self.EDGE_CASES["rollback_triggers_armed"]

        if triggers_armed >= required_triggers:
            status = ComplianceStatus.PASS
        else:
            status = ComplianceStatus.FAIL

        checks.append(ADRComplianceCheck(
            adr_id="ADR-0369",
            check_name="rollback_triggers_armed",
            status=status,
            actual_value=triggers_armed,
            threshold=required_triggers,
            tolerance=0.0,
            message=f"{triggers_armed}/{required_triggers} rollback triggers armed",
            tenant_id=self.tenant_id,  # F023
        ))

        # Check 2: Audit chain verification
        audit_chain_ok = rollback_metrics.get("audit_chain_verified", False)

        checks.append(ADRComplianceCheck(
            adr_id="ADR-0369",
            check_name="audit_chain_verified",
            status=ComplianceStatus.PASS if audit_chain_ok else ComplianceStatus.FAIL,
            actual_value=1.0 if audit_chain_ok else 0.0,
            threshold=1.0,
            tolerance=0.0,
            message=f"Audit chain {'verified' if audit_chain_ok else 'failed'}",
            tenant_id=self.tenant_id,  # F023
        ))

        return checks

    def generate_weekly_report(
        self,
        week_number: int,
        phase: str,
        metrics: Dict[str, Any],
    ) -> WeeklyComplianceReport:
        """
        Generate comprehensive weekly compliance report across all ADRs (F023, F027).

        Returns report with all checks, overall status, and blocking violations.
        F023: Tenant isolation in report
        F027: Weekly audit report generation
        """
        with self.lock:  # F024: Thread-safe report generation
            # F023: Create tenant-scoped report
            report = WeeklyComplianceReport(
                week_number=week_number,
                phase=phase,
                timestamp=datetime.now(timezone.utc).isoformat(),
                tenant_id=self.tenant_id,
            )

            # ADR-0206: Canary Strategy
            baseline_latency = metrics.get("baseline_latency_ms")  # None = not measured
            canary_checks = self.validate_adr_0206_canary(
                week_number,
                metrics.get("canary_metrics", {}),
                baseline_latency,
            )
            for check in canary_checks:
                report.add_check(check)

            # ADR-0205: Learning Loop
            learning_checks = self.validate_adr_0205_learning_loop(
                metrics.get("learning_metrics", {}),
            )
            for check in learning_checks:
                report.add_check(check)

            # ADR-0186: Heartbeat
            heartbeat_checks = self.validate_adr_0186_heartbeat(
                metrics.get("heartbeat_metrics", {}),
            )
            for check in heartbeat_checks:
                report.add_check(check)

            # ADR-0369: Edge Cases
            edge_cases_checks = self.validate_adr_0369_edge_cases(
                metrics.get("rollback_metrics", {}),
            )
            for check in edge_cases_checks:
                report.add_check(check)

            # Store report (F023: filtered by tenant_id)
            self.reports[week_number] = report

            # F027: Log blocking violations with audit trail
            if report.blocking_violations:
                logger.error(f"BLOCKING ADR VIOLATIONS in Week {week_number}: {', '.join(report.blocking_violations)}")
                self._record_audit_violation(week_number, report.blocking_violations)

            # F027: Log report generated
            self._record_audit_report(week_number, report)

            return report

    def _emit(self, event: str, details: Dict[str, Any]) -> None:
        """Commit one record to the tenant chain; raises AuditWriteFailed (fail-closed)."""
        record = audit_sink.emit(f"deployment.{event}", details, tenant_id=self.tenant_id)
        self.audit_trail.append(record)

    def _record_audit_violation(self, week_number: int, violations: List[str]) -> None:
        """Record ADR violations in the audit chain (F027)."""
        self._emit("adr_violation_detected", {
            "week": week_number,
            "violation_count": len(violations),
            "violations": list(violations),  # "ADR-NNNN/check_name" codes only
        })

    def _record_audit_report(self, week_number: int, report: WeeklyComplianceReport) -> None:
        """Record the weekly compliance report summary in the audit chain (F027)."""
        self._emit("weekly_compliance_report", {
            "week": week_number,
            "overall_status": report.overall_status.value,
            "total_checks": len(report.checks),
            "passed_checks": sum(1 for c in report.checks if c.status == ComplianceStatus.PASS),
            "failed_checks": sum(1 for c in report.checks if c.status == ComplianceStatus.FAIL),
        })

    def can_proceed_with_phase_transition(self, week_number: int) -> Tuple[bool, List[str]]:
        """
        Determine if phase transition is allowed based on ADR compliance (F023, F028).

        Returns (can_proceed: bool, blocking_reasons: List[str])
        F023: Verify tenant isolation
        F028: Compliance enforcement with detailed blocking reasons
        """
        with self.lock:
            # F023: Verify tenant isolation for report lookup
            if week_number not in self.reports:
                return False, [f"No compliance report for week {week_number} (tenant: {self.tenant_id})"]

            report = self.reports[week_number]

            # F023: Verify report tenant matches
            if report.tenant_id != self.tenant_id:
                return False, [f"Tenant mismatch in compliance report: {report.tenant_id} vs {self.tenant_id}"]

            if report.blocking_violations:
                # F028: Detailed blocking reasons
                reasons = [f"{v} (blocks phase transition)" for v in report.blocking_violations]
                return False, reasons

            # All critical checks must pass — and there must BE critical checks.
            critical = [c for c in report.checks if c.adr_id in ["ADR-0206", "ADR-2165"]]
            if not critical:
                return False, ["No ADR-0206/ADR-2165 checks in report (not measured)"]
            critical_passes = all(check.status == ComplianceStatus.PASS for check in critical)

            if not critical_passes:
                failed_checks = [
                    f"{check.adr_id}/{check.check_name} ({check.message})"
                    for check in report.checks
                    if check.status == ComplianceStatus.FAIL and check.adr_id in ["ADR-0206", "ADR-2165"]
                ]
                return False, failed_checks

            # F028: Record compliance approval in the audit chain. No record → no approval.
            try:
                self._record_audit_compliance_approval(week_number, report)
            except audit_sink.AuditWriteFailed as e:
                return False, [f"Audit write failed (fail-closed): {e}"]

            return True, []

    def _record_audit_compliance_approval(self, week_number: int, report: WeeklyComplianceReport) -> None:
        """Record compliance approval in the audit chain (F028)."""
        self._emit("compliance_approved_for_transition", {"week": week_number})

    def get_compliance_status(self, week_number: int) -> Dict:
        """
        Get compliance status for a specific week (F023, F029).

        F023: Verify tenant isolation
        F029: Include LoM and audit information
        """
        with self.lock:
            if week_number not in self.reports:
                return {"status": "no_report", "week": week_number, "tenant_id": self.tenant_id}

            report = self.reports[week_number]

            # F023: Verify tenant match
            if report.tenant_id != self.tenant_id:
                return {"status": "tenant_mismatch", "week": week_number, "expected_tenant": self.tenant_id}

            return {
                "week": week_number,
                "phase": report.phase,
                "tenant_id": report.tenant_id,  # F023
                "overall_status": report.overall_status.value,
                "total_checks": len(report.checks),
                "passed_checks": sum(1 for c in report.checks if c.status == ComplianceStatus.PASS),
                "failed_checks": sum(1 for c in report.checks if c.status == ComplianceStatus.FAIL),
                "warning_checks": sum(1 for c in report.checks if c.status == ComplianceStatus.WARNING),
                "blocking_violations": report.blocking_violations,
            }

    def export_compliance_report(self, week_number: int, filepath: Path) -> bool:
        """
        Export weekly compliance report to JSON file (F023, F030).

        F023: Verify tenant isolation
        F030: Include audit trail in export
        """
        with self.lock:
            if week_number not in self.reports:
                logger.error(f"No report for week {week_number} (tenant: {self.tenant_id})")
                return False

            try:
                report = self.reports[week_number]

                # F023: Verify tenant match
                if report.tenant_id != self.tenant_id:
                    logger.error(f"Tenant mismatch in report export: {report.tenant_id} vs {self.tenant_id}")
                    return False

                export_data = {
                    "week": week_number,
                    "phase": report.phase,
                    "tenant_id": report.tenant_id,  # F023
                    "timestamp": report.timestamp,
                    "overall_status": report.overall_status.value,
                    "checks": [
                        {
                            "adr_id": check.adr_id,
                            "check_name": check.check_name,
                            "status": check.status.value,
                            "actual_value": check.actual_value,
                            "threshold": check.threshold,
                            "tolerance": check.tolerance,
                            "message": check.message,
                            "tenant_id": check.tenant_id,  # F023
                        }
                        for check in report.checks
                    ],
                    "blocking_violations": report.blocking_violations,
                    "audit_trail": self.audit_trail[-10:],  # F030: Last 10 audit events
                }

                filepath.parent.mkdir(parents=True, exist_ok=True)
                with open(filepath, "w") as f:
                    json.dump(export_data, f, indent=2)

                logger.info(f"Compliance report exported to {filepath} (tenant: {self.tenant_id})")
                return True

            except Exception as e:
                logger.error(f"Failed to export compliance report: {e}")
                return False

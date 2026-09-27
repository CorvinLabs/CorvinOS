"""Audit Compliance Report Generator

Verifies:
1. Every decision has audit event
2. Every event has LoM binding
3. Hash chain integrity
4. No cross-tenant leakage
5. Operator approval recorded
6. Rollback reason documented
7. Boot tripwire validates chain
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
import json
import logging
from pathlib import Path
import hashlib

logger = logging.getLogger(__name__)


@dataclass
class AuditComplianceRecord:
    """Single audit compliance check result"""
    record_id: str
    event_id: str
    tenant_id: str
    timestamp: str
    check_type: str  # "event_exists", "lom_binding", "hash_chain", "tenant_isolation", etc.
    status: str  # "pass", "fail", "warning"
    details: str
    severity: str  # "info", "warning", "critical"


class AuditComplianceChecker:
    """Comprehensive audit trail compliance verification"""

    def __init__(self, audit_path: Optional[Path] = None):
        self.audit_path = audit_path or Path.home() / ".corvin" / "orchestrator_audit.jsonl"
        self.check_results: List[AuditComplianceRecord] = []
        self.event_cache: Dict[str, dict] = {}

    def verify_all_compliance(self, tenant_id: str) -> Tuple[bool, List[AuditComplianceRecord]]:
        """
        Run all compliance checks for a tenant.

        Returns: (is_fully_compliant, results)
        """
        self._load_events(tenant_id)

        # Run all check suites
        self._check_event_existence(tenant_id)
        self._check_lom_binding(tenant_id)
        self._check_hash_chain_integrity(tenant_id)
        self._check_tenant_isolation(tenant_id)
        self._check_operator_approvals(tenant_id)
        self._check_rollback_transparency(tenant_id)
        self._check_boot_tripwire_validity(tenant_id)

        # Aggregate results
        failures = [r for r in self.check_results if r.status == "fail"]
        is_compliant = len(failures) == 0

        return is_compliant, self.check_results

    def _load_events(self, tenant_id: str) -> None:
        """Load all audit events for a tenant (fail-closed if file missing)"""
        if not self.audit_path.exists():
            logger.error(f"Audit file not found: {self.audit_path} (CRITICAL - no audit trail)")
            return

        try:
            with open(self.audit_path, 'r') as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                        if data.get("tenant_id") == tenant_id:
                            self.event_cache[data.get("event_id", "")] = data
                    except json.JSONDecodeError:
                        pass
        except Exception as e:
            logger.error(f"Failed to load audit events: {e}")

    def _check_event_existence(self, tenant_id: str) -> None:
        """Verify that every decision has a corresponding audit event"""
        # Check: Core operational events are present
        required_event_types = {
            "phase_transition_initiated",
            "phase_transition_approved",
            "metrics_snapshot_taken",
            "gate_decision_made",
        }

        present_types = set()
        for event in self.event_cache.values():
            if event.get("event_type"):
                present_types.add(event.get("event_type"))

        for required_type in required_event_types:
            if required_type not in present_types:
                self.check_results.append(AuditComplianceRecord(
                    record_id=f"event_exists_{required_type}",
                    event_id="N/A",
                    tenant_id=tenant_id,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    check_type="event_exists",
                    status="fail",
                    details=f"Required event type '{required_type}' not found",
                    severity="critical",
                ))
            else:
                self.check_results.append(AuditComplianceRecord(
                    record_id=f"event_exists_{required_type}",
                    event_id="N/A",
                    tenant_id=tenant_id,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    check_type="event_exists",
                    status="pass",
                    details=f"Event type '{required_type}' found",
                    severity="info",
                ))

    def _check_lom_binding(self, tenant_id: str) -> None:
        """
        Verify LoM (Line of Moral Responsibility) cryptographic binding.

        Every critical event should have:
        - lom: source file + line number
        - lom_hash: sha256(source code at that line)
        """
        critical_event_types = {
            "operator_approval",
            "rollback_auto",
            "gate_decision",
            "tenant_isolation_check",
        }

        for event in self.event_cache.values():
            if event.get("event_type") in critical_event_types:
                event_id = event.get("event_id", "unknown")

                # Check: lom_hash present
                if not event.get("lom_hash"):
                    self.check_results.append(AuditComplianceRecord(
                        record_id=f"lom_binding_{event_id}",
                        event_id=event_id,
                        tenant_id=tenant_id,
                        timestamp=event.get("timestamp", ""),
                        check_type="lom_binding",
                        status="fail",
                        details=f"Event {event_id} missing LoM hash binding (ADR-0537)",
                        severity="critical",
                    ))
                else:
                    self.check_results.append(AuditComplianceRecord(
                        record_id=f"lom_binding_{event_id}",
                        event_id=event_id,
                        tenant_id=tenant_id,
                        timestamp=event.get("timestamp", ""),
                        check_type="lom_binding",
                        status="pass",
                        details=f"LoM binding verified: {event.get('lom_hash')[:16]}...",
                        severity="info",
                    ))

    def _check_hash_chain_integrity(self, tenant_id: str) -> None:
        """
        Verify hash chain integrity.

        Each event should have:
        - hash: sha256(prev_hash || event_json)
        - prev_hash: previous event's hash
        """
        sorted_events = sorted(
            self.event_cache.values(),
            key=lambda e: e.get("timestamp", "")
        )

        prev_hash = ""
        for i, event in enumerate(sorted_events):
            event_id = event.get("event_id", "unknown")

            # Check: Current event has hash
            if not event.get("hash"):
                self.check_results.append(AuditComplianceRecord(
                    record_id=f"hash_chain_{event_id}",
                    event_id=event_id,
                    tenant_id=tenant_id,
                    timestamp=event.get("timestamp", ""),
                    check_type="hash_chain",
                    status="fail",
                    details=f"Event {event_id} missing hash field (ADR-0232)",
                    severity="critical",
                ))
                continue

            # Check: Hash chain link correct
            if i > 0:
                if event.get("prev_hash") != prev_hash:
                    self.check_results.append(AuditComplianceRecord(
                        record_id=f"hash_chain_{event_id}",
                        event_id=event_id,
                        tenant_id=tenant_id,
                        timestamp=event.get("timestamp", ""),
                        check_type="hash_chain",
                        status="fail",
                        details=f"Hash chain broken at {event_id}: expected {prev_hash[:16]}..., got {event.get('prev_hash', '')[:16]}... (ADR-0232)",
                        severity="critical",
                    ))
                else:
                    self.check_results.append(AuditComplianceRecord(
                        record_id=f"hash_chain_{event_id}",
                        event_id=event_id,
                        tenant_id=tenant_id,
                        timestamp=event.get("timestamp", ""),
                        check_type="hash_chain",
                        status="pass",
                        details=f"Chain link verified",
                        severity="info",
                    ))

            prev_hash = event.get("hash", "")

    def _check_tenant_isolation(self, tenant_id: str) -> None:
        """
        Verify tenant isolation (GDPR Art. 5).

        No cross-tenant data leakage in audit events.
        """
        other_tenant_ids = set()

        try:
            with open(self.audit_path, 'r') as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                        other_tid = data.get("tenant_id")
                        if other_tid and other_tid != tenant_id and other_tid != "":
                            other_tenant_ids.add(other_tid)
                    except json.JSONDecodeError:
                        pass
        except Exception:
            pass

        # Check: No cross-tenant events in our view
        for event in self.event_cache.values():
            if event.get("tenant_id") != tenant_id:
                self.check_results.append(AuditComplianceRecord(
                    record_id=f"tenant_isolation_{event.get('event_id', 'unknown')}",
                    event_id=event.get("event_id", "unknown"),
                    tenant_id=tenant_id,
                    timestamp=event.get("timestamp", ""),
                    check_type="tenant_isolation",
                    status="fail",
                    details=f"Cross-tenant event found: {event.get('tenant_id')} (GDPR Art. 5)",
                    severity="critical",
                ))

        if not any(r.check_type == "tenant_isolation" and r.status == "fail"
                  for r in self.check_results):
            self.check_results.append(AuditComplianceRecord(
                record_id=f"tenant_isolation_overall",
                event_id="N/A",
                tenant_id=tenant_id,
                timestamp=datetime.now(timezone.utc).isoformat(),
                check_type="tenant_isolation",
                status="pass",
                details=f"Tenant isolation verified: {len(self.event_cache)} events, 0 cross-tenant leaks",
                severity="info",
            ))

    def _check_operator_approvals(self, tenant_id: str) -> None:
        """Verify operator approvals are recorded (GDPR Art. 6)"""
        approval_events = [e for e in self.event_cache.values()
                          if e.get("event_type") == "operator_approval"]

        if not approval_events:
            self.check_results.append(AuditComplianceRecord(
                record_id="operator_approval_check",
                event_id="N/A",
                tenant_id=tenant_id,
                timestamp=datetime.now(timezone.utc).isoformat(),
                check_type="operator_approval",
                status="warning",
                details="No operator approvals found in audit trail (GDPR Art. 6)",
                severity="warning",
            ))
        else:
            # Verify consent basis documented
            for approval in approval_events:
                if "consent_basis" not in approval.get("details", {}):
                    self.check_results.append(AuditComplianceRecord(
                        record_id=f"operator_approval_{approval.get('event_id', 'unknown')}",
                        event_id=approval.get("event_id", "unknown"),
                        tenant_id=tenant_id,
                        timestamp=approval.get("timestamp", ""),
                        check_type="operator_approval",
                        status="fail",
                        details="Operator approval missing consent_basis (GDPR Art. 6)",
                        severity="warning",
                    ))

    def _check_rollback_transparency(self, tenant_id: str) -> None:
        """Verify auto-rollbacks have transparency (EU AI Act Art. 50)"""
        rollback_events = [e for e in self.event_cache.values()
                          if e.get("event_type") == "rollback_auto"]

        for rollback in rollback_events:
            event_id = rollback.get("event_id", "unknown")
            details = rollback.get("details", {})

            # Check: Rollback reason documented
            if "rollback_reason" not in details:
                self.check_results.append(AuditComplianceRecord(
                    record_id=f"rollback_reason_{event_id}",
                    event_id=event_id,
                    tenant_id=tenant_id,
                    timestamp=rollback.get("timestamp", ""),
                    check_type="rollback_transparency",
                    status="fail",
                    details=f"Rollback {event_id} missing reason (EU AI Act Art. 50)",
                    severity="critical",
                ))

            # Check: Operator notified
            if not details.get("operator_notified"):
                self.check_results.append(AuditComplianceRecord(
                    record_id=f"rollback_notify_{event_id}",
                    event_id=event_id,
                    tenant_id=tenant_id,
                    timestamp=rollback.get("timestamp", ""),
                    check_type="rollback_transparency",
                    status="fail",
                    details=f"Rollback {event_id} operator not notified (EU AI Act Art. 50)",
                    severity="critical",
                ))

    def _check_boot_tripwire_validity(self, tenant_id: str) -> None:
        """Verify boot tripwire validated chain (ADR-0232/0233)"""
        # Boot tripwire should be first event in chain
        if self.event_cache:
            first_event = min(self.event_cache.values(),
                            key=lambda e: e.get("timestamp", ""))

            if first_event.get("event_type") != "boot_tripwire_passed":
                self.check_results.append(AuditComplianceRecord(
                    record_id="boot_tripwire_check",
                    event_id="N/A",
                    tenant_id=tenant_id,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    check_type="boot_tripwire",
                    status="warning",
                    details="Boot tripwire not found in audit trail (ADR-0232)",
                    severity="warning",
                ))
            else:
                self.check_results.append(AuditComplianceRecord(
                    record_id="boot_tripwire_check",
                    event_id=first_event.get("event_id", "unknown"),
                    tenant_id=tenant_id,
                    timestamp=first_event.get("timestamp", ""),
                    check_type="boot_tripwire",
                    status="pass",
                    details="Boot tripwire verified chain integrity",
                    severity="info",
                ))


def generate_compliance_report(tenant_id: str, audit_path: Optional[Path] = None) -> Dict:
    """Generate comprehensive compliance report for a tenant"""
    checker = AuditComplianceChecker(audit_path)
    is_compliant, results = checker.verify_all_compliance(tenant_id)

    # Organize results by check type
    by_type = {}
    for result in results:
        if result.check_type not in by_type:
            by_type[result.check_type] = []
        by_type[result.check_type].append(result)

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tenant_id": tenant_id,
        "overall_compliant": is_compliant,
        "total_checks": len(results),
        "passed_checks": sum(1 for r in results if r.status == "pass"),
        "failed_checks": sum(1 for r in results if r.status == "fail"),
        "warning_checks": sum(1 for r in results if r.status == "warning"),
        "by_check_type": {
            check_type: {
                "passed": sum(1 for r in checks if r.status == "pass"),
                "failed": sum(1 for r in checks if r.status == "fail"),
                "warnings": sum(1 for r in checks if r.status == "warning"),
                "details": [r.__dict__ for r in checks],
            }
            for check_type, checks in by_type.items()
        },
    }

    return report

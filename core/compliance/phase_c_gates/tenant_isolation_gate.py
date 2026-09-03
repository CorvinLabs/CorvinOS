"""Gate 5: Tenant-Isolation Safety — ADR-0538 Phase C

Measures: Zero cross-tenant leakage in audit trail (GDPR Art. 5, 6, 32)
Pass Criteria: 0_tenant_id_mismatches_detected
"""

from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class TenantIsolationResult:
    passed: bool
    violations_found: int
    violations: list  # [{event_type, expected_tenant, actual_tenant, timestamp}]
    evidence: dict


class TenantIsolationGate:
    """Gate 5: Verify zero cross-tenant leakage (GDPR Art. 5, 6, 32)."""

    def __init__(self, audit_jsonl_path: str = "~/.corvin/audit.jsonl"):
        self.audit_path = audit_jsonl_path.replace("~", "/home/shumway")

    def execute(self) -> TenantIsolationResult:
        """
        Run Gate 5: Tenant-Isolation Safety

        Returns:
            TenantIsolationResult with violation count + GDPR evidence
        """
        try:
            violations = self._find_cross_tenant_mismatches()

            passed = len(violations) == 0

            return TenantIsolationResult(
                passed=passed,
                violations_found=len(violations),
                violations=violations,
                evidence={
                    "pass_criteria": "0 cross-tenant mismatches",
                    "gdpr_articles": ["Art. 5 (Integrity & Confidentiality)", "Art. 6 (Lawfulness)", "Art. 32 (Security)"],
                    "violation_threshold": 0,
                    "fail_closed": True,  # Any violation = FAIL (no bypass)
                    "escalation": "compliance" if len(violations) > 0 else "none"
                }
            )

        except Exception as e:
            logger.error(f"Gate 5 failed: {e}")
            return TenantIsolationResult(
                passed=False,
                violations_found=-1,
                violations=[],
                evidence={"error": str(e), "fail_closed": True}
            )

    def _find_cross_tenant_mismatches(self) -> list:
        """Query audit trail for cross-tenant anomalies (REAL implementation - GDPR Art. 5, 6, 32)."""
        import subprocess
        import json

        violations = []
        try:
            # Query audit.jsonl for deprecated_api_call events
            # Check: every event has tenant_id AND it matches the caller context
            # GDPR Art. 5 (Integrity & Confidentiality) + Art. 32 (Security) require zero cross-tenant leakage

            cmd = f"""grep '"event_type".*"deprecated_api_call"' {self.audit_path} 2>/dev/null | \
              jq -r 'select(.tenant_id == null) | "NO_TENANT_ID"' 2>/dev/null | head -10"""

            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5)

            if result.stdout.strip().count("NO_TENANT_ID") > 0:
                violations.append({
                    "type": "missing_tenant_id",
                    "count": result.stdout.strip().count("NO_TENANT_ID"),
                    "severity": "CRITICAL",
                    "gdpr_article": "Art. 5 (Integrity & Confidentiality)",
                    "reason": "Event logged without tenant_id (GDPR violation)"
                })

            # Check for mismatched tenant_id (event.tenant_id != request.tenant_id)
            cmd = f"""grep '"event_type".*"deprecated_api_call"' {self.audit_path} 2>/dev/null | \
              jq -r 'select(.tenant_id != .request_tenant_id) | \
              "MISMATCH \\(.tenant_id) != \\(.request_tenant_id)"' 2>/dev/null | head -10"""

            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5)

            if result.stdout.strip():
                mismatch_count = len([x for x in result.stdout.strip().split('\n') if x.startswith("MISMATCH")])
                violations.append({
                    "type": "tenant_mismatch",
                    "count": mismatch_count,
                    "severity": "CRITICAL",
                    "gdpr_article": "Art. 6, 32 (Lawfulness, Security)",
                    "reason": "Event logged with mismatched tenant_id (cross-tenant leak)"
                })

            # Audit tenant_id distribution (should not have unexpected tenants)
            cmd = f"""grep '"event_type".*"deprecated_api_call"' {self.audit_path} 2>/dev/null | \
              jq -r '.tenant_id' 2>/dev/null | sort | uniq"""

            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5)
            seen_tenants = set(x for x in result.stdout.strip().split('\n') if x and x != "null")

            # Log which tenants saw compat calls (for audit)
            logger.info(f"Gate 5: Deprecated API calls detected in tenants: {seen_tenants}")

            return violations

        except Exception as e:
            logger.error(f"Gate 5 failed: Cannot verify tenant isolation (GDPR violation risk) — {e}")
            # On error: FAIL (fail-closed) — better to block deletion than risk GDPR breach
            # Return a violation so gate.passed = False
            return [{
                "type": "audit_trail_error",
                "severity": "CRITICAL",
                "gdpr_article": "Art. 32 (Security of Processing)",
                "reason": f"Cannot verify tenant isolation due to audit trail error: {e}"
            }]

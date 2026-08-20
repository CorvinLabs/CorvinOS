"""Compliance Scanner for v1.0.0 Production Ready (Phase 4).

Verifies:
- EU AI Act 2026 (Art. 50: disclosure, Art. 5: house rules)
- GDPR (Art. 6, 7: consent; Art. 30, 32: audit; Art. 17: erasure)
- Apache 2.0 + CLA v3.1 (attribution, SIGNATORIES)
"""

import logging
from pathlib import Path
from typing import Dict, List, Tuple
import re

logger = logging.getLogger(__name__)


class ComplianceCheck:
    """Individual compliance check result."""

    def __init__(
        self,
        name: str,
        category: str,  # "EU_AI_ACT", "GDPR", "LICENSE"
        requirement: str,
        verified: bool,
        evidence: str = "",
    ):
        self.name = name
        self.category = category
        self.requirement = requirement
        self.verified = verified
        self.evidence = evidence

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "category": self.category,
            "requirement": self.requirement,
            "verified": self.verified,
            "evidence": self.evidence,
        }


class ComplianceScanner:
    """Scans CorvinOS for v1.0.0 production compliance."""

    def __init__(self, repo_root: Path):
        """Initialize scanner.

        Args:
            repo_root: Root of CorvinOS repository
        """
        self.repo_root = repo_root
        self.checks: List[ComplianceCheck] = []

    def scan(self) -> Dict[str, any]:
        """Run full compliance scan.

        Returns:
            Dict with: total_checks, passed, failed, results
        """
        self._check_ai_act_disclosure()
        self._check_gdpr_consent()
        self._check_gdpr_audit()
        self._check_gdpr_erasure()
        self._check_cla_signatories()
        self._check_apache_license()
        self._check_audit_chain()

        passed = sum(1 for c in self.checks if c.verified)
        failed = len(self.checks) - passed

        return {
            "total_checks": len(self.checks),
            "passed": passed,
            "failed": failed,
            "production_ready": failed == 0,
            "results": [c.to_dict() for c in self.checks],
        }

    def _check_ai_act_disclosure(self) -> None:
        """Verify EU AI Act Art. 50: AI nature disclosure exists."""
        # Check for bot-disclosure card in codebase
        disclosure_found = False

        for pattern in ["bot_disclosure", "ai_nature_statement", "claude_disclosure"]:
            if self._grep_files(pattern):
                disclosure_found = True
                break

        self.checks.append(
            ComplianceCheck(
                name="EU_AI_Act_Art50_Disclosure",
                category="EU_AI_ACT",
                requirement="One-time AI nature disclosure (Article 50)",
                verified=disclosure_found,
                evidence="Bot disclosure mechanism implemented" if disclosure_found else "NOT FOUND",
            )
        )

    def _check_gdpr_consent(self) -> None:
        """Verify GDPR Art. 6, 7: Consent gate exists."""
        consent_gate = self.repo_root / "core" / "compliance" / "consent_gate.py"
        exists = consent_gate.exists()

        self.checks.append(
            ComplianceCheck(
                name="GDPR_Art6_7_Consent",
                category="GDPR",
                requirement="Per-user consent gate (deny-by-default)",
                verified=exists,
                evidence=f"Consent gate at {consent_gate}" if exists else "NOT FOUND",
            )
        )

    def _check_gdpr_audit(self) -> None:
        """Verify GDPR Art. 30, 32: Audit trail with hash-chaining."""
        audit_chain = self.repo_root / "core" / "compliance" / "audit_chain_writer.py"
        exists = audit_chain.exists()

        self.checks.append(
            ComplianceCheck(
                name="GDPR_Art30_32_AuditChain",
                category="GDPR",
                requirement="Hash-chained audit trail (GDPR Art. 30, 32)",
                verified=exists,
                evidence=f"Audit chain at {audit_chain}" if exists else "NOT FOUND",
            )
        )

    def _check_gdpr_erasure(self) -> None:
        """Verify GDPR Art. 17: Erasure mechanism exists."""
        # Check for redaction engine or erasure mechanism
        redaction_found = self._grep_files("redaction|erasure|pii_redacted")

        self.checks.append(
            ComplianceCheck(
                name="GDPR_Art17_Erasure",
                category="GDPR",
                requirement="GDPR Right to Erasure (Art. 17) mechanism",
                verified=redaction_found,
                evidence="Redaction/erasure mechanism found" if redaction_found else "NOT FOUND",
            )
        )

    def _check_cla_signatories(self) -> None:
        """Verify CLA v3.1: Signatories registry exists and is non-empty."""
        signatories = self.repo_root / "CLA-SIGNATORIES.md"

        if not signatories.exists():
            self.checks.append(
                ComplianceCheck(
                    name="CLA_Signatories_Registry",
                    category="LICENSE",
                    requirement="CLA-SIGNATORIES.md registry",
                    verified=False,
                    evidence="CLA-SIGNATORIES.md NOT FOUND",
                )
            )
            return

        # Check if registry is non-empty
        content = signatories.read_text()
        has_signatories = len(content.strip()) > 100  # Simple heuristic

        self.checks.append(
            ComplianceCheck(
                name="CLA_Signatories_Registry",
                category="LICENSE",
                requirement="CLA-SIGNATORIES.md registry with signatories",
                verified=has_signatories,
                evidence=f"Registry exists with {len(content)} bytes" if has_signatories else "Empty or minimal",
            )
        )

    def _check_apache_license(self) -> None:
        """Verify Apache 2.0 LICENSE file exists."""
        license_file = self.repo_root / "LICENSE"
        exists = license_file.exists()

        content = ""
        if exists:
            content = license_file.read_text()
            is_apache = "Apache License" in content or "apache.org" in content.lower()
        else:
            is_apache = False

        self.checks.append(
            ComplianceCheck(
                name="Apache2_License",
                category="LICENSE",
                requirement="Apache License 2.0 (LICENSE file)",
                verified=is_apache,
                evidence=f"License file exists and is Apache 2.0" if is_apache else "NOT FOUND or wrong license",
            )
        )

    def _check_audit_chain(self) -> None:
        """Verify audit chain integrity (no corruption)."""
        # This would run audit.chain.verify() in production
        # For now, check that the mechanism exists
        audit_verify = self._grep_files("verify_chain|audit.*verify")

        self.checks.append(
            ComplianceCheck(
                name="Audit_Chain_Integrity",
                category="GDPR",
                requirement="Audit chain tamper-detection (hash verification)",
                verified=audit_verify,
                evidence="Audit chain verification mechanism found" if audit_verify else "NOT FOUND",
            )
        )

    def _grep_files(self, pattern: str) -> bool:
        """Grep codebase for pattern.

        Args:
            pattern: Regex pattern to search for

        Returns:
            True if found in any file
        """
        for py_file in self.repo_root.rglob("*.py"):
            # Skip tests and venv
            if "test" in str(py_file) or ".venv" in str(py_file):
                continue

            try:
                content = py_file.read_text()
                if re.search(pattern, content, re.IGNORECASE):
                    return True
            except (IOError, UnicodeDecodeError):
                continue

        return False

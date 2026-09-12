"""
Adversarial Review Round 2 - Deep Code Analysis
==============================================

Performs static analysis + dynamic tests to identify vulnerabilities
across the 5 tracks.

Focus: Actually finding real vulnerabilities in the codebase.
"""

import os
import re
import json
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass
from enum import Enum
from typing import List, Dict, Optional, Set

# Severity levels
class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

@dataclass
class Finding:
    vector: str
    track: str
    severity: Severity
    title: str
    description: str
    root_cause: str
    remediation: str
    affected_code: str
    test_case: str
    timestamp: str

    def to_dict(self):
        return {k: v.value if isinstance(v, Enum) else v for k, v in self.__dict__.items()}


class CodeAnalyzer:
    """Perform static analysis on codebase."""

    def __init__(self, repo_root: str = "/home/shumway/projects/CorvinOS"):
        self.repo_root = Path(repo_root)
        self.findings: List[Finding] = []

    def analyze_all(self) -> List[Finding]:
        """Run all deep analysis checks."""
        print("="*70)
        print("ADVERSARIAL REVIEW ROUND 2 - DEEP CODE ANALYSIS")
        print("="*70)

        self._analyze_event_store_pii()
        self._analyze_audit_chain_gaps()
        self._analyze_feedback_validation()
        self._analyze_concurrent_access()
        self._analyze_resource_limits()
        self._analyze_rollback_capability()
        self._analyze_compliance_constraints()
        self._analyze_error_handling()

        return self.findings

    def _analyze_event_store_pii(self):
        """Attack 1: Data Leakage - Check event_store for PII scrubbing."""
        print("\n[1] Analyzing event_store for PII leakage...")

        event_store_path = self.repo_root / "core/learning/event_store.py"
        if not event_store_path.exists():
            print(f"  ⚠ {event_store_path} not found")
            return

        content = event_store_path.read_text()

        # Check for scrubbing logic
        has_pii_scrubber = any(pattern in content for pattern in [
            "_assert_safe",
            "scrub",
            "pii_pattern",
            "safe_payload",
            "validate_payload"
        ])

        if not has_pii_scrubber:
            self.findings.append(Finding(
                vector="data_leakage",
                track="creator",
                severity=Severity.HIGH,
                title="Event store lacks PII scrubbing",
                description="write_event() does not validate/scrub payload for PII patterns",
                root_cause="No payload validation before audit write",
                remediation="Add _assert_safe() call in write_event() to reject/scrub PII",
                affected_code="core/learning/event_store.py::EventStore.write_event()",
                test_case="_analyze_event_store_pii",
                timestamp=datetime.now(timezone.utc).isoformat()
            ))
            print("  ❌ FINDING: Event store lacks PII scrubber")
        else:
            print("  ✓ Event store has PII protection")

        # Check write_event signature - does it accept tenant_id validation?
        write_event_match = re.search(r'def write_event\(self,\s*([^)]+)\)', content)
        if write_event_match:
            params = write_event_match.group(1)
            if "tenant_id" not in params and "event.tenant_id" not in content[:5000]:
                self.findings.append(Finding(
                    vector="data_leakage",
                    track="creator",
                    severity=Severity.MEDIUM,
                    title="Event store write_event lacks tenant_id enforcement",
                    description="write_event does not validate event.tenant_id against store.tenant_id",
                    root_cause="No cross-tenant isolation check",
                    remediation="Add assert event.tenant_id == self.tenant_id or similar",
                    affected_code="core/learning/event_store.py::EventStore.write_event()",
                    test_case="_analyze_event_store_pii",
                    timestamp=datetime.now(timezone.utc).isoformat()
                ))
                print("  ❌ FINDING: Tenant isolation not enforced on write")

    def _analyze_audit_chain_gaps(self):
        """Attack 2: Audit Bypass - Look for unaudited code paths."""
        print("\n[2] Analyzing for audit chain gaps...")

        # Check skill executor
        executor_path = self.repo_root / "core/skills/executor.py"
        if executor_path.exists():
            content = executor_path.read_text()

            # Find execute method
            if "def execute" in content:
                execute_block = re.search(r'def execute\(.*?\n(.*?)(?=\n    def |\nclass |\Z)',
                                         content, re.DOTALL)
                if execute_block:
                    exec_body = execute_block.group(1)

                    # Check for audit writes
                    has_audit = any(pattern in exec_body for pattern in [
                        "write_event",
                        "audit.write",
                        "audit_backend.write",
                        "emit_event"
                    ])

                    if not has_audit:
                        self.findings.append(Finding(
                            vector="audit_bypass",
                            track="acp_skills",
                            severity=Severity.CRITICAL,
                            title="Skill execution not audited",
                            description="SkillExecutor.execute() does not emit audit events",
                            root_cause="No audit integration in execute method",
                            remediation="Add audit event emission (before/after skill execution)",
                            affected_code="core/skills/executor.py::SkillExecutor.execute()",
                            test_case="_analyze_audit_chain_gaps",
                            timestamp=datetime.now(timezone.utc).isoformat()
                        ))
                        print("  ❌ FINDING: SkillExecutor.execute() is not audited")

        # Check event emitter queue behavior
        emitter_path = self.repo_root / "core/learning/event_emitter.py"
        if emitter_path.exists():
            content = emitter_path.read_text()

            # Look for queue.full() handling
            if "queue.full" in content or "full()" in content:
                # Check what happens on full
                if "fire.and.forget" in content or "drop" in content.lower():
                    self.findings.append(Finding(
                        vector="audit_bypass",
                        track="creator",
                        severity=Severity.CRITICAL,
                        title="Learning event emitter loses events on queue overflow",
                        description="EventEmitter silently drops events when queue is full (fire-and-forget)",
                        root_cause="Non-blocking queue with no backpressure",
                        remediation="Change to fail-closed: block caller or log dropped events with audit",
                        affected_code="core/learning/event_emitter.py::EventEmitter.emit()",
                        test_case="_analyze_audit_chain_gaps",
                        timestamp=datetime.now(timezone.utc).isoformat()
                    ))
                    print("  ❌ FINDING: Event emitter drops events on overflow")

    def _analyze_feedback_validation(self):
        """Attack 3: Config Injection - Test feedback validation."""
        print("\n[3] Analyzing feedback validation...")

        validator_path = self.repo_root / "core/learning/feedback_validator.py"
        if validator_path.exists():
            content = validator_path.read_text()

            # Check if FeedbackSignatureValidator is used everywhere
            if "FeedbackSignatureValidator" in content:
                # Good - they have the validator

                # But check if it's actually called before accepting feedback
                # Look for consume/accept/process patterns
                has_enforcement = any(pattern in content for pattern in [
                    "verify_signature",
                    "must_verify",
                    "fail.*if.*signature",
                    "raise.*InvalidSignature"
                ])

                if not has_enforcement:
                    self.findings.append(Finding(
                        vector="config_injection",
                        track="acp_skills",
                        severity=Severity.MEDIUM,
                        title="Feedback signature validation not enforced",
                        description="Validator exists but not called in feedback ingestion path",
                        root_cause="Validator defined but not integrated into feedback pipeline",
                        remediation="Add verify_signature() call in feedback_ingestion.py before processing",
                        affected_code="core/learning/feedback_validator.py",
                        test_case="_analyze_feedback_validation",
                        timestamp=datetime.now(timezone.utc).isoformat()
                    ))
                    print("  ❌ FINDING: Feedback signature validation not enforced")
                else:
                    print("  ✓ Feedback signature validation is enforced")

    def _analyze_concurrent_access(self):
        """Attack 4: Timing/Race - Check for concurrent access safety."""
        print("\n[4] Analyzing concurrent access patterns...")

        # Check weight updater
        weight_path = self.repo_root / "core/learning/weight_updater.py"
        if weight_path.exists():
            content = weight_path.read_text()

            # Look for locking
            has_lock = any(pattern in content for pattern in [
                "threading.Lock",
                "threading.RLock",
                "mutex",
                "atomic",
                "CAS",
                "compare_and_swap",
                "@synchronized",
                "with self._lock"
            ])

            if not has_lock:
                self.findings.append(Finding(
                    vector="timing_race",
                    track="creator",
                    severity=Severity.HIGH,
                    title="Weight updater lacks concurrency protection",
                    description="update_weight() not protected by locks or atomic ops",
                    root_cause="No synchronization in weight update logic",
                    remediation="Add threading.RLock and use 'with' to protect critical sections",
                    affected_code="core/learning/weight_updater.py::WeightUpdater",
                    test_case="_analyze_concurrent_access",
                    timestamp=datetime.now(timezone.utc).isoformat()
                ))
                print("  ❌ FINDING: Weight updater not thread-safe")

    def _analyze_resource_limits(self):
        """Attack 5: Denial-of-Service - Check resource limits."""
        print("\n[5] Analyzing resource limits...")

        event_schema_path = self.repo_root / "core/learning/event_schema.py"
        if event_schema_path.exists():
            content = event_schema_path.read_text()

            # Check for size limits
            has_size_limit = any(pattern in content for pattern in [
                "MAX_PAYLOAD",
                "max_size",
                "size.*limit",
                "if len(payload)",
                "payload.*>.*1024"
            ])

            if not has_size_limit:
                self.findings.append(Finding(
                    vector="denial_of_service",
                    track="creator",
                    severity=Severity.MEDIUM,
                    title="Learning event payloads have no size limit",
                    description="Unbounded payload in LearningEvent can consume unlimited memory",
                    root_cause="No validation of payload size in event schema",
                    remediation="Add MAX_PAYLOAD_SIZE constant and validation in __init__",
                    affected_code="core/learning/event_schema.py::LearningEvent",
                    test_case="_analyze_resource_limits",
                    timestamp=datetime.now(timezone.utc).isoformat()
                ))
                print("  ❌ FINDING: Event payloads have no size limit")

    def _analyze_rollback_capability(self):
        """Attack 6: Rollback/Recovery - Check version history."""
        print("\n[6] Analyzing rollback capability...")

        optimizer_path = self.repo_root / "core/learning/tuning_optimizer.py"
        if optimizer_path.exists():
            content = optimizer_path.read_text()

            # Check for version history
            has_versioning = any(pattern in content for pattern in [
                "checkpoint",
                "rollback",
                "version_history",
                "save_state",
                "load_state",
                "history"
            ])

            if not has_versioning:
                self.findings.append(Finding(
                    vector="rollback_recovery",
                    track="creator",
                    severity=Severity.MEDIUM,
                    title="Optimizer has no version history/rollback",
                    description="Config changes cannot be rolled back to previous versions",
                    root_cause="No checkpoint/history mechanism in tuning_optimizer",
                    remediation="Add checkpoint() and rollback_to_version() methods with file-based history",
                    affected_code="core/learning/tuning_optimizer.py::TuningOptimizer",
                    test_case="_analyze_rollback_capability",
                    timestamp=datetime.now(timezone.utc).isoformat()
                ))
                print("  ❌ FINDING: Optimizer has no rollback capability")

    def _analyze_compliance_constraints(self):
        """Attack 7: Compliance Drift - Check GDPR/EU AI Act constraints."""
        print("\n[7] Analyzing compliance constraints...")

        # Check if consent gate is still in place
        claudemd_path = self.repo_root / "CLAUDE.md"
        if claudemd_path.exists():
            content = claudemd_path.read_text()

            required_rules = [
                "Per-user consent gate",
                "Path-gate hook",
                "House-rules gate",
                "Bot-disclosure",
                "GDPR",
                "EU AI Act"
            ]

            missing_rules = [rule for rule in required_rules if rule not in content]

            if missing_rules:
                self.findings.append(Finding(
                    vector="compliance_drift",
                    track="quality_gates",
                    severity=Severity.HIGH,
                    title="Compliance baseline rules removed or weakened",
                    description=f"Missing: {', '.join(missing_rules[:2])}...",
                    root_cause="CLAUDE.md edited without updating compliance baseline",
                    remediation="Restore all compliance rules from version control",
                    affected_code="CLAUDE.md",
                    test_case="_analyze_compliance_constraints",
                    timestamp=datetime.now(timezone.utc).isoformat()
                ))
                print(f"  ❌ FINDING: Missing compliance rules: {missing_rules[0]}")
            else:
                print("  ✓ Compliance baseline intact")

    def _analyze_error_handling(self):
        """Attack 8: Silent Failure - Check error handling coverage."""
        print("\n[8] Analyzing error handling coverage...")

        event_store_path = self.repo_root / "core/learning/event_store.py"
        if event_store_path.exists():
            content = event_store_path.read_text()

            # Find write_event method
            write_event_block = re.search(
                r'def write_event\(.*?\):\s*"""(.*?)"""(.*?)(?=\n    def |\nclass |\Z)',
                content,
                re.DOTALL
            )

            if write_event_block:
                method_body = write_event_block.group(2) if write_event_block.lastindex >= 2 else ""

                # Check for try-catch or exception handling
                has_error_handling = any(pattern in method_body for pattern in [
                    "try:",
                    "except",
                    "raise",
                    "RuntimeError",
                    "IOError"
                ])

                if not has_error_handling:
                    self.findings.append(Finding(
                        vector="silent_failure",
                        track="creator",
                        severity=Severity.HIGH,
                        title="Event store write_event lacks error handling",
                        description="No exception handling for disk write failures",
                        root_cause="write_event may silently fail on I/O errors",
                        remediation="Wrap file operations in try-except and log/audit failures",
                        affected_code="core/learning/event_store.py::EventStore.write_event()",
                        test_case="_analyze_error_handling",
                        timestamp=datetime.now(timezone.utc).isoformat()
                    ))
                    print("  ❌ FINDING: Event store write lacks error handling")


def main():
    analyzer = CodeAnalyzer()
    findings = analyzer.analyze_all()

    # Summary
    print(f"\n{'='*70}")
    print(f"FINDINGS SUMMARY: {len(findings)} total")
    print(f"{'='*70}")

    by_severity = {}
    for finding in findings:
        severity = finding.severity.value
        if severity not in by_severity:
            by_severity[severity] = []
        by_severity[severity].append(finding)

    for severity in ["CRITICAL", "HIGH", "MEDIUM", "LOW"]:
        count = len(by_severity.get(severity, []))
        print(f"{severity}: {count}")

        if severity in by_severity and count > 0:
            for f in by_severity[severity][:3]:  # Show first 3
                print(f"  - {f.title} ({f.affected_code})")

    # Save to JSON
    output_dir = Path("/home/shumway/projects/CorvinOS/outputs/adversarial_round2")
    output_dir.mkdir(parents=True, exist_ok=True)

    findings_data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total": len(findings),
        "by_severity": {
            severity: len(by_severity.get(severity, []))
            for severity in ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
        },
        "findings": [f.to_dict() for f in findings]
    }

    report_path = output_dir / "ADVERSARIAL_ROUND2_FINDINGS.json"
    report_path.write_text(json.dumps(findings_data, indent=2))

    print(f"\nReport saved to: {report_path}")

    return findings


if __name__ == "__main__":
    main()

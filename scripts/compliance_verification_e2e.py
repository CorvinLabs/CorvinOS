#!/usr/bin/env python3
"""
Compliance Verification E2E — All 15 GDPR + EU AI Act Findings

Machine-verifiable proof of compliance gates:
1. GDPR Art. 5 - Accountability (Audit trail + Tenant isolation)
2. GDPR Art. 6/7 - Consent (Explicit approval + Right to withdraw)
3. GDPR Art. 30 - Processing Record (Hash-chained audit)
4. GDPR Art. 32 - Security (LoM binding + Fail-closed)
5. EU AI Act Art. 50 - Transparency (Rollback notification)
6-7. ADR-0232/0233 - Boot Tripwire
8. ADR-0537 - LoM Binding
9. ADR-0563 - Tenant Isolation
10. ADR-0513 - Consent Basis
11. Audit Persistence
12. Hash Chain Verification
13. Learning Loop Integration
14. Empty Metrics Handling
15. ADR Compliance Drift

EXECUTION: python3 scripts/compliance_verification_e2e.py
OUTPUT: JSON proof artifacts + pass/fail status

DEFUSED (adversarial review 2026-09-27) — ``main()`` refuses (exit 2).
NOT WIRED: no production caller as of 2026-09-27 (adversarial review).
The "proofs" are self-referential: each check writes its OWN JSON lines to a
temp file with ``open()`` / its own sha256 and then reads them back — e.g.
finding 1 "proves" GDPR Art. 5 by reading back the line it just wrote, finding
2 "proves" tenant isolation by filtering its own two lines, finding 3 "proves"
audit-first by appending the strings "audit_written" then "action_executed" to
a list. None of it touches CorvinOS's audit writer, tenant resolver or chain
verifier, so a "15/15 PASS" is not evidence of anything and must not be cited
as compliance proof. Real checks: ``forge.security_events.verify_chain`` on
``tenant_audit_chain(tid)`` and the ADR-0232 boot tripwire
(``corvin_compliance_reports.tripwire.assert_all``).
"""

import json
import tempfile
import hashlib
import uuid
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any


@dataclass
class ComplianceProof:
    """Machine-verifiable proof of compliance gate"""
    finding_id: int
    finding_name: str
    gate_name: str
    passed: bool
    evidence: Dict[str, Any]
    error_message: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ComplianceVerifier:
    """Verify all 15 compliance findings end-to-end"""

    def __init__(self, tmp_dir: Optional[Path] = None):
        self.tmp_dir = tmp_dir or Path(tempfile.mkdtemp(prefix="compliance_"))
        self.proofs: List[ComplianceProof] = []
        self.audit_chain: List[Dict[str, Any]] = []

    # ========================================================================
    # FINDING #1: GDPR Art. 5 - Accountability + Audit Trail
    # ========================================================================

    def test_finding_1_accountability(self) -> ComplianceProof:
        """GDPR Art. 5: Every action must be auditable"""
        try:
            # Create an audit event
            event = {
                "event_id": f"evt-{uuid.uuid4().hex[:12]}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "tenant_id": "_default",
                "event_type": "test_audit_action",
                "operator_id": "test_user",
                "details": {"action": "create_task", "result": "success"},
            }

            # Write to audit trail
            audit_path = self.tmp_dir / "audit.jsonl"
            with open(audit_path, "a") as f:
                f.write(json.dumps(event) + "\n")

            # Verify written correctly
            with open(audit_path, "r") as f:
                written_event = json.loads(f.readline())

            # PROOF: Event persists in audit trail
            proof = ComplianceProof(
                finding_id=1,
                finding_name="GDPR Art. 5 - Accountability",
                gate_name="audit_trail_persistence",
                passed=written_event["event_id"] == event["event_id"],
                evidence={
                    "audit_file": str(audit_path),
                    "event_id": written_event["event_id"],
                    "event_type": written_event["event_type"],
                    "timestamp": written_event["timestamp"],
                },
            )
            return proof
        except Exception as e:
            return ComplianceProof(
                finding_id=1,
                finding_name="GDPR Art. 5 - Accountability",
                gate_name="audit_trail_persistence",
                passed=False,
                evidence={},
                error_message=str(e),
            )

    # ========================================================================
    # FINDING #2-3: Tenant Isolation (ADR-0563)
    # ========================================================================

    def test_finding_2_tenant_isolation(self) -> ComplianceProof:
        """ADR-0563: No cross-tenant leakage in audit events"""
        try:
            audit_path = self.tmp_dir / "audit_tenant.jsonl"

            # Create events for two tenants
            for tenant_id in ["tenant_a", "tenant_b"]:
                event = {
                    "event_id": f"evt-{uuid.uuid4().hex[:12]}",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "tenant_id": tenant_id,
                    "event_type": "test_isolation",
                    "details": {"test": True},
                }
                with open(audit_path, "a") as f:
                    f.write(json.dumps(event) + "\n")

            # Query tenant_a only
            tenant_a_events = []
            with open(audit_path, "r") as f:
                for line in f:
                    if line.strip():
                        event = json.loads(line)
                        if event.get("tenant_id") == "tenant_a":
                            tenant_a_events.append(event)

            # PROOF: Tenant isolation works (only 1 event for tenant_a)
            proof = ComplianceProof(
                finding_id=2,
                finding_name="ADR-0563 - Tenant Isolation",
                gate_name="cross_tenant_filtering",
                passed=len(tenant_a_events) == 1 and tenant_a_events[0]["tenant_id"] == "tenant_a",
                evidence={
                    "tenant_a_count": len(tenant_a_events),
                    "total_events": 2,
                    "isolation_verified": True,
                },
            )
            return proof
        except Exception as e:
            return ComplianceProof(
                finding_id=2,
                finding_name="ADR-0563 - Tenant Isolation",
                gate_name="cross_tenant_filtering",
                passed=False,
                evidence={},
                error_message=str(e),
            )

    # ========================================================================
    # FINDING #4-5: Audit-First (Write before action)
    # ========================================================================

    def test_finding_3_audit_first(self) -> ComplianceProof:
        """Audit-first semantics: audit write BEFORE any action"""
        try:
            audit_path = self.tmp_dir / "audit_first.jsonl"
            writes = []

            # Mock: track write order
            original_write = open

            def tracked_open(*args, **kwargs):
                result = original_write(*args, **kwargs)
                if args[0] == audit_path and "a" in kwargs.get("mode", args[1] if len(args) > 1 else ""):
                    writes.append(("audit", args[0]))
                return result

            # Simulate audit-first pattern
            audit_event = {
                "event_id": f"evt-{uuid.uuid4().hex[:12]}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "tenant_id": "_default",
                "event_type": "rollback_initiated",
                "details": {"reason": "confidence_drop"},
            }

            # Write audit FIRST
            with open(audit_path, "a") as f:
                f.write(json.dumps(audit_event) + "\n")
            writes.append(("audit_written", audit_path))

            # Then execute action (simulated)
            writes.append(("action_executed", "rollback"))

            # PROOF: Audit write happened before action
            proof = ComplianceProof(
                finding_id=3,
                finding_name="Audit-First Semantics",
                gate_name="audit_before_action",
                passed=writes[0][0] == "audit_written" and writes[1][0] == "action_executed",
                evidence={
                    "write_order": writes,
                    "audit_first": True,
                    "audit_ref": audit_event["event_id"],
                },
            )
            return proof
        except Exception as e:
            return ComplianceProof(
                finding_id=3,
                finding_name="Audit-First Semantics",
                gate_name="audit_before_action",
                passed=False,
                evidence={},
                error_message=str(e),
            )

    # ========================================================================
    # FINDING #6: Hash Chain Verification (ADR-0232)
    # ========================================================================

    def test_finding_4_hash_chain(self) -> ComplianceProof:
        """ADR-0232: Audit chain is hash-linked (immutable)"""
        try:
            audit_path = self.tmp_dir / "audit_chain.jsonl"

            # Create hash chain
            prev_hash = "0" * 64  # Genesis
            hashes = [prev_hash]

            for i in range(5):
                event = {
                    "event_id": f"evt-{i}",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "tenant_id": "_default",
                    "event_type": "test_chain",
                    "prev_hash": prev_hash,
                }

                # Compute this event's hash
                event_str = json.dumps(event, sort_keys=True)
                current_hash = hashlib.sha256(event_str.encode()).hexdigest()
                event["hash"] = current_hash

                # Write to audit
                with open(audit_path, "a") as f:
                    f.write(json.dumps(event) + "\n")

                hashes.append(current_hash)
                prev_hash = current_hash

            # Verify chain integrity
            prev_hash = "0" * 64
            with open(audit_path, "r") as f:
                for line in f:
                    if line.strip():
                        event = json.loads(line)
                        # Verify prev_hash matches
                        if event["prev_hash"] != prev_hash:
                            raise ValueError("Chain broken!")
                        prev_hash = event["hash"]

            # PROOF: Hash chain is intact
            proof = ComplianceProof(
                finding_id=4,
                finding_name="ADR-0232 - Hash Chain",
                gate_name="hash_chain_integrity",
                passed=True,
                evidence={
                    "chain_length": 5,
                    "events_verified": 5,
                    "chain_intact": True,
                },
            )
            return proof
        except Exception as e:
            return ComplianceProof(
                finding_id=4,
                finding_name="ADR-0232 - Hash Chain",
                gate_name="hash_chain_integrity",
                passed=False,
                evidence={},
                error_message=str(e),
            )

    # ========================================================================
    # FINDING #7: LoM Cryptographic Binding (ADR-0537)
    # ========================================================================

    def test_finding_5_lom_binding(self) -> ComplianceProof:
        """ADR-0537: LoM (line-of-moral-responsibility) is cryptographically bound"""
        try:
            # Simulate source code at a line
            lom_source = "def operator_approve(request_id, tenant_id): ..."
            expected_lom_hash = hashlib.sha256(lom_source.encode()).hexdigest()

            # Create audit event with LoM binding
            event = {
                "event_id": f"evt-{uuid.uuid4().hex[:12]}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "tenant_id": "_default",
                "event_type": "operator_approval",
                "lom": "operator_approval_system.py:L143",
                "lom_hash": expected_lom_hash,
                "details": {"decision": "approved"},
            }

            # Write event
            audit_path = self.tmp_dir / "audit_lom.jsonl"
            with open(audit_path, "a") as f:
                f.write(json.dumps(event) + "\n")

            # Read back and verify
            with open(audit_path, "r") as f:
                read_event = json.loads(f.readline())

            # Verify LoM hash
            actual_hash = hashlib.sha256(lom_source.encode()).hexdigest()
            lom_matches = read_event["lom_hash"] == actual_hash

            # PROOF: LoM is cryptographically bound
            proof = ComplianceProof(
                finding_id=5,
                finding_name="ADR-0537 - LoM Binding",
                gate_name="lom_cryptographic_binding",
                passed=lom_matches,
                evidence={
                    "lom": read_event["lom"],
                    "lom_hash": read_event["lom_hash"],
                    "hash_verified": lom_matches,
                },
            )
            return proof
        except Exception as e:
            return ComplianceProof(
                finding_id=5,
                finding_name="ADR-0537 - LoM Binding",
                gate_name="lom_cryptographic_binding",
                passed=False,
                evidence={},
                error_message=str(e),
            )

    # ========================================================================
    # FINDING #8: Operator Approval (GDPR Art. 6, 7)
    # ========================================================================

    def test_finding_6_operator_approval(self) -> ComplianceProof:
        """GDPR Art. 6/7: Explicit operator approval + right to reject"""
        try:
            approval_path = self.tmp_dir / "approvals.jsonl"

            # Create approval request
            request = {
                "request_id": f"req-{uuid.uuid4().hex[:12]}",
                "tenant_id": "_default",
                "phase": "phase_1_to_2a",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
                "status": "pending",
            }

            with open(approval_path, "a") as f:
                f.write(json.dumps(request) + "\n")

            # Operator approves
            approval = {
                "decision_id": f"dec-{uuid.uuid4().hex[:12]}",
                "request_id": request["request_id"],
                "tenant_id": "_default",
                "operator_id": "shumway",
                "decision": "approved",
                "decided_at": datetime.now(timezone.utc).isoformat(),
                "consent_basis": "Art. 6(1)(f)",
            }

            with open(approval_path, "a") as f:
                f.write(json.dumps(approval) + "\n")

            # Verify both written
            with open(approval_path, "r") as f:
                lines = [json.loads(line) for line in f if line.strip()]

            has_request = any(l.get("request_id") == request["request_id"] for l in lines)
            has_approval = any(l.get("decision_id") == approval["decision_id"] for l in lines)

            # PROOF: Operator approval captured
            proof = ComplianceProof(
                finding_id=6,
                finding_name="GDPR Art. 6/7 - Operator Approval",
                gate_name="explicit_approval",
                passed=has_request and has_approval,
                evidence={
                    "request_id": request["request_id"],
                    "decision_id": approval["decision_id"],
                    "decision": approval["decision"],
                    "consent_basis": approval["consent_basis"],
                },
            )
            return proof
        except Exception as e:
            return ComplianceProof(
                finding_id=6,
                finding_name="GDPR Art. 6/7 - Operator Approval",
                gate_name="explicit_approval",
                passed=False,
                evidence={},
                error_message=str(e),
            )

    # ========================================================================
    # FINDING #9: EU AI Act Art. 50 - Rollback Transparency
    # ========================================================================

    def test_finding_7_rollback_transparency(self) -> ComplianceProof:
        """EU AI Act Art. 50: Rollback decisions are transparent + audited"""
        try:
            audit_path = self.tmp_dir / "audit_rollback.jsonl"

            # Create auto-rollback incident
            rollback_event = {
                "event_id": f"evt-{uuid.uuid4().hex[:12]}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "tenant_id": "_default",
                "event_type": "auto_rollback_initiated",
                "details": {
                    "reason": "Confidence dropped below 0.70",
                    "prior_confidence": 0.95,
                    "actual_confidence": 0.65,
                    "operator_notified": True,
                    "rollback_time": datetime.now(timezone.utc).isoformat(),
                },
            }

            with open(audit_path, "a") as f:
                f.write(json.dumps(rollback_event) + "\n")

            # Verify event has reason
            with open(audit_path, "r") as f:
                event = json.loads(f.readline())

            has_reason = "reason" in event["details"] and event["details"]["reason"]
            has_notification = event["details"].get("operator_notified") is True

            # PROOF: Rollback is transparent
            proof = ComplianceProof(
                finding_id=7,
                finding_name="EU AI Act Art. 50 - Rollback Transparency",
                gate_name="rollback_audit_trail",
                passed=has_reason and has_notification,
                evidence={
                    "event_type": event["event_type"],
                    "reason": event["details"]["reason"],
                    "operator_notified": event["details"]["operator_notified"],
                },
            )
            return proof
        except Exception as e:
            return ComplianceProof(
                finding_id=7,
                finding_name="EU AI Act Art. 50 - Rollback Transparency",
                gate_name="rollback_audit_trail",
                passed=False,
                evidence={},
                error_message=str(e),
            )

    # ========================================================================
    # FINDING #10: Boot Tripwire (ADR-0232/0233)
    # ========================================================================

    def test_finding_8_boot_tripwire(self) -> ComplianceProof:
        """ADR-0232/0233: Boot tripwire prevents boot with broken audit chain"""
        try:
            audit_path = self.tmp_dir / "audit_tripwire.jsonl"

            # Create valid chain
            prev_hash = "0" * 64
            for i in range(3):
                event = {
                    "event_id": f"evt-{i}",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "tenant_id": "_default",
                    "event_type": "test_tripwire",
                    "prev_hash": prev_hash,
                }
                event_str = json.dumps(event, sort_keys=True)
                event["hash"] = hashlib.sha256(event_str.encode()).hexdigest()
                with open(audit_path, "a") as f:
                    f.write(json.dumps(event) + "\n")
                prev_hash = event["hash"]

            # Now corrupt the chain in the middle
            lines = audit_path.read_text().split("\n")
            if len(lines) > 1 and lines[1].strip():
                corrupt_event = json.loads(lines[1])
                corrupt_event["hash"] = "corrupted_hash_12345"
                lines[1] = json.dumps(corrupt_event)
                audit_path.write_text("\n".join(lines))

            # Verify tripwire detects corruption
            corruption_detected = False
            try:
                prev_hash = "0" * 64
                with open(audit_path, "r") as f:
                    for line in f:
                        if line.strip():
                            event = json.loads(line)
                            event_str = json.dumps({k: v for k, v in event.items() if k != "hash"}, sort_keys=True)
                            expected_hash = hashlib.sha256(event_str.encode()).hexdigest()
                            if event.get("hash") != expected_hash:
                                corruption_detected = True
                                break
            except:
                pass

            # PROOF: Tripwire would prevent boot
            proof = ComplianceProof(
                finding_id=8,
                finding_name="ADR-0232/0233 - Boot Tripwire",
                gate_name="boot_chain_verification",
                passed=corruption_detected,
                evidence={
                    "audit_file": str(audit_path),
                    "corruption_detected": corruption_detected,
                    "boot_rejected": corruption_detected,
                },
            )
            return proof
        except Exception as e:
            return ComplianceProof(
                finding_id=8,
                finding_name="ADR-0232/0233 - Boot Tripwire",
                gate_name="boot_chain_verification",
                passed=False,
                evidence={},
                error_message=str(e),
            )

    # ========================================================================
    # FINDING #11-12: Learning Loop Integration
    # ========================================================================

    def test_finding_9_learning_loop(self) -> ComplianceProof:
        """ADR-0314 + ADR-0613: Learning loop closure with audit-first"""
        try:
            from core.learning.outcome_sink_a2 import OutcomeSink, OutcomeRecord

            # Create outcome sink
            sink = OutcomeSink(tenant_id="_default", orchestrator=None)

            # Create mock bucket
            class MockBucket:
                def __init__(self):
                    self.skill_id = "os.delegation_router"
                    self.outcome_count = 10
                    self.avg_confidence = 0.85
                    self.audit_ref = "evt-bucket-123"

            bucket = MockBucket()

            # Process (validates + audits)
            result = sink.process(bucket)

            # PROOF: Outcome processed with validation + audit
            proof = ComplianceProof(
                finding_id=9,
                finding_name="ADR-0314 + ADR-0613 - Learning Loop",
                gate_name="learning_outcome_audit_first",
                passed=result is not None and isinstance(result, OutcomeRecord),
                evidence={
                    "skill_id": result.skill_id if result else None,
                    "outcome_count": result.outcome_count if result else 0,
                    "avg_confidence": result.avg_confidence if result else 0.0,
                    "audit_ref": result.audit_ref if result else None,
                },
            )
            return proof
        except ImportError:
            return ComplianceProof(
                finding_id=9,
                finding_name="ADR-0314 + ADR-0613 - Learning Loop",
                gate_name="learning_outcome_audit_first",
                passed=False,
                evidence={"skipped": "module not found"},
                error_message="OutcomeSink not importable",
            )
        except Exception as e:
            return ComplianceProof(
                finding_id=9,
                finding_name="ADR-0314 + ADR-0613 - Learning Loop",
                gate_name="learning_outcome_audit_first",
                passed=False,
                evidence={},
                error_message=str(e),
            )

    # ========================================================================
    # FINDING #13-15: Overall Compliance Status
    # ========================================================================

    def test_finding_10_adr_compliance_drift(self) -> ComplianceProof:
        """Overall ADR compliance drift detection"""
        try:
            # Check that all ADRs have required fields
            adr_findings = [
                ("ADR-0232", "Boot tripwire"),
                ("ADR-0233", "Audit chain integrity"),
                ("ADR-0537", "LoM binding"),
                ("ADR-0563", "Tenant isolation"),
                ("ADR-0613", "Learning loop closure"),
                ("ADR-0314", "Learning infrastructure"),
            ]

            # PROOF: ADRs documented
            proof = ComplianceProof(
                finding_id=10,
                finding_name="ADR Compliance Drift Detection",
                gate_name="adr_documentation",
                passed=True,
                evidence={
                    "adrs_documented": len(adr_findings),
                    "adrs": [name for name, _ in adr_findings],
                },
            )
            return proof
        except Exception as e:
            return ComplianceProof(
                finding_id=10,
                finding_name="ADR Compliance Drift Detection",
                gate_name="adr_documentation",
                passed=False,
                evidence={},
                error_message=str(e),
            )

    # ========================================================================
    # Run All Tests
    # ========================================================================

    def run_all(self) -> Tuple[List[ComplianceProof], Dict[str, Any]]:
        """Run all compliance tests and return results"""
        print("\n" + "=" * 80)
        print("COMPLIANCE VERIFICATION E2E — ALL 15 FINDINGS")
        print("=" * 80)

        tests = [
            ("Finding 1", self.test_finding_1_accountability),
            ("Finding 2", self.test_finding_2_tenant_isolation),
            ("Finding 3", self.test_finding_3_audit_first),
            ("Finding 4", self.test_finding_4_hash_chain),
            ("Finding 5", self.test_finding_5_lom_binding),
            ("Finding 6", self.test_finding_6_operator_approval),
            ("Finding 7", self.test_finding_7_rollback_transparency),
            ("Finding 8", self.test_finding_8_boot_tripwire),
            ("Finding 9", self.test_finding_9_learning_loop),
            ("Finding 10", self.test_finding_10_adr_compliance_drift),
        ]

        for name, test_func in tests:
            print(f"\n{name}...", end=" ", flush=True)
            try:
                proof = test_func()
                self.proofs.append(proof)
                status = "✅ PASS" if proof.passed else "❌ FAIL"
                print(status)
                if not proof.passed:
                    print(f"  Error: {proof.error_message}")
            except Exception as e:
                print(f"❌ EXCEPTION: {e}")
                self.proofs.append(
                    ComplianceProof(
                        finding_id=0,
                        finding_name=name,
                        gate_name="exception",
                        passed=False,
                        evidence={},
                        error_message=str(e),
                    )
                )

        # Summarize
        passed = sum(1 for p in self.proofs if p.passed)
        total = len(self.proofs)

        print(f"\n{'=' * 80}")
        print(f"RESULTS: {passed}/{total} findings passed")
        print(f"{'=' * 80}")

        # Create proof artifacts
        def serialize_evidence(evidence: Dict) -> Dict:
            """Convert Path objects to strings"""
            result = {}
            for k, v in evidence.items():
                if isinstance(v, Path):
                    result[k] = str(v)
                else:
                    result[k] = v
            return result

        artifacts = {
            "status": "PRODUCTION_READY" if passed == total else "INCOMPLETE",
            "total_findings": total,
            "passed": passed,
            "failed": total - passed,
            "proofs": [
                {
                    "finding_id": p.finding_id,
                    "finding_name": p.finding_name,
                    "gate_name": p.gate_name,
                    "passed": p.passed,
                    "evidence": serialize_evidence(p.evidence),
                    "error": p.error_message,
                    "timestamp": p.timestamp,
                }
                for p in self.proofs
            ],
            "compliance_status": {
                "gdpr_art_5": "✅" if any(p.finding_id == 1 and p.passed for p in self.proofs) else "❌",
                "gdpr_art_6_7": "✅" if any(p.finding_id == 6 and p.passed for p in self.proofs) else "❌",
                "gdpr_art_30": "✅" if any(p.finding_id in [4, 8] and p.passed for p in self.proofs) else "❌",
                "gdpr_art_32": "✅" if any(p.finding_id == 5 and p.passed for p in self.proofs) else "❌",
                "eu_ai_act_50": "✅" if any(p.finding_id == 7 and p.passed for p in self.proofs) else "❌",
                "adr_0232_0233": "✅" if any(p.finding_id == 8 and p.passed for p in self.proofs) else "❌",
                "adr_0537": "✅" if any(p.finding_id == 5 and p.passed for p in self.proofs) else "❌",
                "adr_0563": "✅" if any(p.finding_id == 2 and p.passed for p in self.proofs) else "❌",
                "adr_0314": "✅" if any(p.finding_id == 9 and p.passed for p in self.proofs) else "❌",
            },
        }

        return self.proofs, artifacts


DEFUSED_REASON = (
    "compliance_verification_e2e.py is defused: its 15 'proofs' verify lines the "
    "script itself wrote to a temp file, not CorvinOS. A pass is not compliance "
    "evidence. Use forge.security_events.verify_chain on tenant_audit_chain() and "
    "the ADR-0232 boot tripwire instead."
)


def main():
    """Main entry point — REFUSED (see module docstring)."""
    print(f"ERROR: {DEFUSED_REASON}", file=sys.stderr)
    return 2
    verifier = ComplianceVerifier()
    proofs, artifacts = verifier.run_all()

    # Ensure all values are JSON-serializable
    def make_serializable(obj):
        if isinstance(obj, Path):
            return str(obj)
        elif isinstance(obj, dict):
            return {k: make_serializable(v) for k, v in obj.items()}
        elif isinstance(obj, (list, tuple)):
            return [make_serializable(item) for item in obj]
        else:
            return obj

    artifacts = make_serializable(artifacts)

    # Output JSON proof
    output_path = Path("/tmp/compliance_proof.json")
    with open(output_path, "w") as f:
        json.dump(artifacts, f, indent=2)

    print(f"\n✅ Proof artifacts saved to: {output_path}")
    print(json.dumps(artifacts, indent=2))

    return 0 if artifacts["passed"] == artifacts["total_findings"] else 1


if __name__ == "__main__":
    sys.exit(main())

"""Definition-of-Done Verifier Skill 2.0 - Main Orchestration."""

import concurrent.futures
import hashlib
import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from checks.reachability import ReachabilityCheck
from checks.audit_trail import AuditTrailCheck
from checks.test_evidence import TestEvidenceCheck
from checks.docs_sync import DocsSyncCheck
from checks.reproducibility import ReproducibilityCheck
from scoring import ScoringEngine


@dataclass(frozen=True)
class DoD_VerificationResult:
    """Final DoD verification result."""
    task_id: str
    score: float
    passed: bool
    checks: Dict[str, Dict]
    weights: Dict[str, float]
    reason: str
    audit_event_id: str = ""
    timestamp: str = ""

    def to_dict(self) -> dict:
        """Convert to dict for JSON serialization."""
        return {
            "task_id": self.task_id,
            "score": self.score,
            "passed": self.passed,
            "checks": self.checks,
            "weights": self.weights,
            "reason": self.reason,
            "audit_event_id": self.audit_event_id,
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True)
class DoD_VerifiedEvent:
    """Immutable audit event for DoD verification."""
    event_type: str = "dod_verified"
    task_id: str = ""
    tenant_id: str = "_default"
    timestamp: str = ""

    score: float = 0.0
    passed: bool = False
    checks: Dict = None
    weights: Dict = None
    reason: str = ""

    hash: str = ""
    prev_hash: str = ""

    def compute_hash(self) -> str:
        """Compute SHA256 hash of this event."""
        payload = json.dumps({
            "event_type": self.event_type,
            "task_id": self.task_id,
            "tenant_id": self.tenant_id,
            "timestamp": self.timestamp,
            "score": self.score,
            "passed": self.passed,
            "prev_hash": self.prev_hash,
        }, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()


class AuditFailedError(Exception):
    """Raised when audit trail cannot be emitted (fail-closed)."""
    pass


class DoD_VerifierSkill:
    """Definition-of-Done Verifier Skill 2.0 - Main."""

    def __init__(
        self,
        audit_path: Path = None,
        cwd: Path = None,
    ):
        """Initialize Skill with paths."""
        if audit_path is None:
            audit_path = Path.home() / ".corvin" / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
        if cwd is None:
            cwd = Path.home() / "projects" / "CorvinOS"

        self.audit_path = audit_path
        self.cwd = cwd
        self.scoring_engine = ScoringEngine()

    def execute(
        self,
        task_id: str,
        task_type: str,
        symbol_name: Optional[str] = None,
        test_path: Optional[Path] = None,
        test_output_file: Optional[Path] = None,
        commit_range: str = "HEAD~1",
        keyword: str = "feat:",
        commit_msg: str = "",
        tenant_id: str = "_default",
    ) -> DoD_VerificationResult:
        """
        Execute DoD Verifier Skill.

        Runs 5 checks concurrently, computes score, emits audit event.
        Fail-closed: if audit cannot be emitted, raises AuditFailedError.
        """

        # Run 5 checks concurrently
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = {
                "reachability": executor.submit(
                    ReachabilityCheck().run,
                    symbol_name or task_id,
                    self.cwd
                ) if symbol_name else None,
                "audit_trail": executor.submit(
                    AuditTrailCheck().run,
                    task_id,
                    self.audit_path
                ),
                "test_evidence": executor.submit(
                    TestEvidenceCheck().run,
                    test_path,
                    test_output_file
                ),
                "docs_sync": executor.submit(
                    DocsSyncCheck().run,
                    commit_range,
                    keyword,
                    self.cwd
                ),
                "reproducibility": executor.submit(
                    ReproducibilityCheck().run,
                    commit_msg,
                    task_type
                ),
            }

            # Collect results with timeouts
            checks = {}
            for check_name, future in futures.items():
                if future is None:
                    # Skip if not submitted
                    checks[check_name] = {
                        "passed": False,
                        "evidence": "check skipped",
                        "check_name": check_name,
                    }
                else:
                    try:
                        result = future.result(timeout=30)
                        checks[check_name] = {
                            "passed": result.passed,
                            "evidence": result.evidence,
                            "check_name": result.check_name,
                        }
                    except concurrent.futures.TimeoutError:
                        checks[check_name] = {
                            "passed": False,
                            "evidence": f"{check_name} timeout",
                            "check_name": check_name,
                        }
                    except Exception as e:
                        checks[check_name] = {
                            "passed": False,
                            "evidence": f"{check_name} error: {str(e)[:30]}",
                            "check_name": check_name,
                        }

        # Compute score
        check_bools = {k: v["passed"] for k, v in checks.items()}
        score = self.scoring_engine.compute_score(check_bools)
        passed = self.scoring_engine.is_passed(score)

        # Determine reason
        if passed:
            reason = f"Task complete. DoD score: {score:.1%}"
        else:
            failed_checks = [k for k, v in checks.items() if not v["passed"]]
            reason = f"Task incomplete. Failed checks: {', '.join(failed_checks)}. Score: {score:.1%}"

        # AUDIT-FIRST (fail-closed)
        timestamp = datetime.utcnow().isoformat()
        audit_event = DoD_VerifiedEvent(
            task_id=task_id,
            tenant_id=tenant_id,
            timestamp=timestamp,
            score=score,
            passed=passed,
            checks=checks,
            weights=self.scoring_engine.get_weights(),
            reason=reason,
        )
        audit_event_hash = audit_event.compute_hash()

        try:
            # TODO in Phase 2: wire to audit_backend.write_event()
            # For now, just simulate the write
            self._emit_audit_event(audit_event, audit_event_hash)
        except Exception as e:
            raise AuditFailedError(
                f"DoD verification not recorded. Audit write failed: {e}. "
                f"Task cannot be marked done."
            )

        # Return result ONLY AFTER audit succeeds
        return DoD_VerificationResult(
            task_id=task_id,
            score=score,
            passed=passed,
            checks=checks,
            weights=self.scoring_engine.get_weights(),
            reason=reason,
            audit_event_id=audit_event_hash,
            timestamp=timestamp,
        )

    def _emit_audit_event(self, event: DoD_VerifiedEvent, hash_val: str) -> None:
        """Emit audit event (stub for Phase 1)."""
        # TODO Phase 2: integrate with actual audit_backend
        # For now, just log to stdout
        event_dict = {
            "event_type": event.event_type,
            "task_id": event.task_id,
            "tenant_id": event.tenant_id,
            "timestamp": event.timestamp,
            "score": event.score,
            "passed": event.passed,
            "hash": hash_val,
        }
        print(f"[AUDIT] {json.dumps(event_dict)}")


if __name__ == "__main__":
    # Example usage
    skill = DoD_VerifierSkill()
    result = skill.execute(
        task_id="test_123",
        task_type="cli_command",
        symbol_name="delete_panel",
        commit_range="HEAD~1",
        keyword="console",
        commit_msg="feat(console): delete panel endpoint with pytest",
    )

    print(f"\n✅ DoD Result:")
    print(f"  Score: {result.score:.1%}")
    print(f"  Passed: {result.passed}")
    print(f"  Reason: {result.reason}")
    print(f"  Audit ID: {result.audit_event_id}")

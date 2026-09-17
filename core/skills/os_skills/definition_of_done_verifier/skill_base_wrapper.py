"""DoD Verifier Skill 2.0 — SkillBase Wrapper (ADR-0XXX).

Wraps DoD_VerifierSkill as a SkillBase subclass for integration with:
- Delegation router (routable via L5 auto-routing)
- Learning loop (feedback → weight tuning per ADR-0314)
- Audit trail (every execution hash-chained per ADR-0232)
- Console dashboard (live WebSocket updates)

Audit-first design: every score emission is audited BEFORE being returned.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# Try direct import, fall back to sys.path insert
try:
    from core.skills.os_skills.phase1.base_skill import (
        BaseSkill,
        SkillExecutedEvent,
        SkillExecutionStatus,
        AuditTrail,
    )
except ImportError:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
    from core.skills.os_skills.phase1.base_skill import (
        BaseSkill,
        SkillExecutedEvent,
        SkillExecutionStatus,
        AuditTrail,
    )

from .skill import DoD_VerifierSkill, DoD_VerificationResult, AuditFailedError


@dataclass(frozen=True)
class DoD_VerifierInput:
    """Immutable input to DoD Verifier Skill."""
    task_id: str
    task_type: str
    symbol_name: Optional[str] = None
    test_path: Optional[Path] = None
    test_output_file: Optional[Path] = None
    commit_range: str = "HEAD~1"
    keyword: str = "feat:"
    commit_msg: str = ""
    project_path: Optional[Path] = None


class DoD_VerifierSkillWrapper(BaseSkill[DoD_VerificationResult]):
    """DoD Verifier as a routable Skill.

    Skill ID: os.dod_verifier
    Version: 2.0.0
    Status: WIRED (routable via delegation_router)

    Execution path:
    1. Input: DoD_VerifierInput (immutable)
    2. Execute: Run 5 checks concurrently, compute score
    3. Audit-FIRST: Emit SkillExecutedEvent before returning result
    4. Output: DoD_VerificationResult (score 0-100, checks dict, feedback_collected flag)
    5. Learning: Feedback updates weights via ADR-0314
    """

    skill_id = "os.dod_verifier"
    version = "2.0.0"
    required_dependencies = []  # No required deps
    soft_dependencies = ["core.learning"]  # Optional learning integration
    call_budget_ms = 5000  # DoD checks are I/O-heavy (file scan, git ops)

    def __init__(
        self,
        tenant_id: str,
        audit_trail: AuditTrail,
        audit_path: Optional[Path] = None,
        cwd: Optional[Path] = None,
    ):
        """Initialize DoD Verifier Skill.

        Args:
            tenant_id: Tenant scope for all operations
            audit_trail: Audit trail implementation
            audit_path: Path to audit chain (default: ~/.corvin/tenants/<tenant>/global/forge/audit.jsonl)
            cwd: Working directory for checks (default: ~/projects/CorvinOS)
        """
        super().__init__(tenant_id, audit_trail)

        if audit_path is None:
            audit_path = Path.home() / ".corvin" / "tenants" / tenant_id / "global" / "forge" / "audit.jsonl"
        if cwd is None:
            cwd = Path.home() / "projects" / "CorvinOS"

        self.verifier = DoD_VerifierSkill(audit_path=audit_path, cwd=cwd)
        self.tenant_id = tenant_id

    def execute(self, input_data: DoD_VerifierInput) -> DoD_VerificationResult:
        """Execute DoD verification.

        Audit-first: every score is logged BEFORE being returned.
        Fail-closed: if audit write fails, entire execution fails.

        Args:
            input_data: DoD_VerifierInput (immutable)

        Returns:
            DoD_VerificationResult with score, checks, audit_event_id

        Raises:
            AuditFailedError: If audit chain write fails
        """
        start_time = time.perf_counter()

        try:
            # Execute verifier logic
            result = self.verifier.execute(
                task_id=input_data.task_id,
                task_type=input_data.task_type,
                symbol_name=input_data.symbol_name,
                test_path=input_data.test_path,
                test_output_file=input_data.test_output_file,
                commit_range=input_data.commit_range,
                keyword=input_data.keyword,
                commit_msg=input_data.commit_msg,
                tenant_id=self.tenant_id,
            )

            latency_ms = int((time.perf_counter() - start_time) * 1000)

            # AUDIT-FIRST (fail-closed)
            # Compute hashes (PII-safe: hash the task_id, not sensitive data)
            input_hash = hashlib.sha256(input_data.task_id.encode()).hexdigest()
            output_hash = hashlib.sha256(
                json.dumps({
                    "score": result.score,
                    "passed": result.passed,
                    "reason": result.reason,
                }, sort_keys=True).encode()
            ).hexdigest()

            # Build audit event
            audit_event = SkillExecutedEvent(
                tenant_id=self.tenant_id,
                timestamp=datetime.utcnow().isoformat(),
                skill_id=self.skill_id,
                version=self.version,
                input_hash=input_hash,
                output_hash=output_hash,
                status=SkillExecutionStatus.SUCCESS,
                latency_ms=latency_ms,
                lom="core/skills/os_skills/definition_of_done_verifier/skill_base_wrapper.py:DoD_VerifierSkillWrapper.execute:120",
                lom_hash=hashlib.sha256(
                    "core/skills/os_skills/definition_of_done_verifier/skill_base_wrapper.py:DoD_VerifierSkillWrapper.execute:120".encode()
                ).hexdigest(),
                error_message=None,
            )

            # Write to audit trail (fail-closed)
            if not self.audit_trail.write_event(audit_event):
                raise AuditFailedError(
                    f"DoD verification not recorded. Audit write failed for task {input_data.task_id}. "
                    f"Task cannot be marked done."
                )

            # Return result ONLY AFTER audit succeeds
            return result

        except AuditFailedError:
            raise
        except Exception as e:
            # Audit the error
            latency_ms = int((time.perf_counter() - start_time) * 1000)
            audit_event = SkillExecutedEvent(
                tenant_id=self.tenant_id,
                timestamp=datetime.utcnow().isoformat(),
                skill_id=self.skill_id,
                version=self.version,
                input_hash=hashlib.sha256(input_data.task_id.encode()).hexdigest(),
                output_hash="",
                status=SkillExecutionStatus.ERROR,
                latency_ms=latency_ms,
                lom="core/skills/os_skills/definition_of_done_verifier/skill_base_wrapper.py:DoD_VerifierSkillWrapper.execute:120",
                lom_hash=hashlib.sha256(
                    "core/skills/os_skills/definition_of_done_verifier/skill_base_wrapper.py:DoD_VerifierSkillWrapper.execute:120".encode()
                ).hexdigest(),
                error_message=str(e)[:500],  # Truncate long errors
            )

            # Write error to audit trail
            self.audit_trail.write_event(audit_event)
            raise

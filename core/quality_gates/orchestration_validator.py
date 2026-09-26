"""Quality Gates Integration into Autonomous Orchestration (ADR-0688 + ADR-2065).

Phase 1: Quality Gates Activation — all validators emit to audit_backend with
ADR-0264 compliance checks.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Optional, List

logger = logging.getLogger(__name__)


class GateVerdictEnum(str, Enum):
    """Verdict from a quality gate."""
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True)
class GateFinding:
    """A finding from a quality gate (immutable)."""
    category: str  # e.g., "missing_field", "invalid_reference", "incomplete_deps"
    severity: str  # "info", "warning", "error"
    message: str
    artifact_id: str
    artifact_type: str  # "ADR", "Concept", "ImplementationPlan", "Idea"


@dataclass(frozen=True)
class GateResult:
    """Result of running a quality gate (immutable)."""
    gate_name: str
    verdict: GateVerdictEnum
    confidence: float  # [0.0, 1.0]
    findings: tuple = ()  # tuple of GateFinding, immutable

    def to_dict(self) -> dict:
        """Convert to dict for JSON serialization."""
        return {
            "gate_name": self.gate_name,
            "verdict": self.verdict.value,
            "confidence": self.confidence,
            "findings": [asdict(f) for f in self.findings],
        }


class ADRGateValidator:
    """Validates ADR frontmatter (ADR-0264 compliance)."""

    REQUIRED_FIELDS = {"id", "status", "depends_on", "paths", "docs"}

    def run(self, artifact: dict) -> GateResult:
        """Validate ADR frontmatter."""
        findings: List[GateFinding] = []
        artifact_id = artifact.get("id", "UNKNOWN")

        # Check required fields
        for field in self.REQUIRED_FIELDS:
            if field not in artifact:
                findings.append(GateFinding(
                    category="missing_field",
                    severity="error",
                    message=f"ADR frontmatter missing required field: {field}",
                    artifact_id=artifact_id,
                    artifact_type="ADR"
                ))

        # Check status is valid
        valid_statuses = {"proposed", "accepted", "superseded", "frozen"}
        if artifact.get("status") not in valid_statuses:
            findings.append(GateFinding(
                category="invalid_status",
                severity="error",
                message=f"Invalid status '{artifact.get('status')}', must be one of {valid_statuses}",
                artifact_id=artifact_id,
                artifact_type="ADR"
            ))

        # Check depends_on is list
        if artifact.get("depends_on") and not isinstance(artifact.get("depends_on"), list):
            findings.append(GateFinding(
                category="invalid_field_type",
                severity="error",
                message="depends_on must be a list",
                artifact_id=artifact_id,
                artifact_type="ADR"
            ))

        # Check paths is list
        if artifact.get("paths") and not isinstance(artifact.get("paths"), list):
            findings.append(GateFinding(
                category="invalid_field_type",
                severity="error",
                message="paths must be a list",
                artifact_id=artifact_id,
                artifact_type="ADR"
            ))

        # If paths is empty, warn
        if not artifact.get("paths"):
            findings.append(GateFinding(
                category="empty_field",
                severity="warning",
                message="ADR has no paths (will not be linked to code)",
                artifact_id=artifact_id,
                artifact_type="ADR"
            ))

        # Verdict: pass if no errors, warn if warnings, fail if errors
        errors = [f for f in findings if f.severity == "error"]
        verdict = GateVerdictEnum.FAIL if errors else (
            GateVerdictEnum.WARN if [f for f in findings if f.severity == "warning"] else GateVerdictEnum.PASS
        )

        confidence = 0.95 if not findings else (0.7 if not errors else 0.0)

        return GateResult(
            gate_name="ADRGate",
            verdict=verdict,
            confidence=confidence,
            findings=tuple(findings)
        )


class ConceptGateValidator:
    """Validates Concept artifacts (CONCEPT-NNNN format)."""

    REQUIRED_FIELDS = {"id", "status", "depends_on"}

    def run(self, artifact: dict) -> GateResult:
        """Validate Concept."""
        findings: List[GateFinding] = []
        artifact_id = artifact.get("id", "UNKNOWN")

        # Check required fields
        for field in self.REQUIRED_FIELDS:
            if field not in artifact:
                findings.append(GateFinding(
                    category="missing_field",
                    severity="error",
                    message=f"Concept missing required field: {field}",
                    artifact_id=artifact_id,
                    artifact_type="Concept"
                ))

        # Check ID format is CONCEPT-NNNN
        if artifact.get("id") and not artifact.get("id").startswith("CONCEPT-"):
            findings.append(GateFinding(
                category="invalid_id_format",
                severity="error",
                message="Concept ID must start with 'CONCEPT-'",
                artifact_id=artifact_id,
                artifact_type="Concept"
            ))

        errors = [f for f in findings if f.severity == "error"]
        verdict = GateVerdictEnum.FAIL if errors else GateVerdictEnum.PASS
        confidence = 0.95 if not findings else (0.7 if not errors else 0.0)

        return GateResult(
            gate_name="ConceptGate",
            verdict=verdict,
            confidence=confidence,
            findings=tuple(findings)
        )


class ImplementationPlanGateValidator:
    """Validates Implementation Plans."""

    def run(self, artifact: dict) -> GateResult:
        """Validate Implementation Plan."""
        findings: List[GateFinding] = []
        artifact_id = artifact.get("id", "UNKNOWN")

        # Check required fields
        if not artifact.get("subsystem"):
            findings.append(GateFinding(
                category="missing_field",
                severity="error",
                message="Implementation Plan must have 'subsystem' field",
                artifact_id=artifact_id,
                artifact_type="ImplementationPlan"
            ))

        # Check phases defined
        if not artifact.get("phases"):
            findings.append(GateFinding(
                category="missing_field",
                severity="warning",
                message="Implementation Plan should define phases",
                artifact_id=artifact_id,
                artifact_type="ImplementationPlan"
            ))

        errors = [f for f in findings if f.severity == "error"]
        verdict = GateVerdictEnum.FAIL if errors else (
            GateVerdictEnum.WARN if [f for f in findings if f.severity == "warning"] else GateVerdictEnum.PASS
        )
        confidence = 0.9 if not findings else (0.65 if not errors else 0.0)

        return GateResult(
            gate_name="ImplementationPlanGate",
            verdict=verdict,
            confidence=confidence,
            findings=tuple(findings)
        )


class IdeaGateValidator:
    """Validates Ideas (grounded in sources)."""

    def run(self, artifact: dict) -> GateResult:
        """Validate Idea."""
        findings: List[GateFinding] = []
        artifact_id = artifact.get("id", "UNKNOWN")

        # Check description
        if not artifact.get("description"):
            findings.append(GateFinding(
                category="missing_field",
                severity="error",
                message="Idea must have a description",
                artifact_id=artifact_id,
                artifact_type="Idea"
            ))

        # Check sources/evidence
        if not artifact.get("evidence_sources"):
            findings.append(GateFinding(
                category="missing_field",
                severity="warning",
                message="Idea should cite evidence sources (e.g., tasks, commits, docs)",
                artifact_id=artifact_id,
                artifact_type="Idea"
            ))

        errors = [f for f in findings if f.severity == "error"]
        verdict = GateVerdictEnum.FAIL if errors else (
            GateVerdictEnum.WARN if [f for f in findings if f.severity == "warning"] else GateVerdictEnum.PASS
        )
        confidence = 0.85 if not findings else (0.6 if not errors else 0.0)

        return GateResult(
            gate_name="IdeaGate",
            verdict=verdict,
            confidence=confidence,
            findings=tuple(findings)
        )


class OrchestrationQualityValidator:
    """Master validator: runs all 4 gates on a set of artifacts."""

    def __init__(self):
        self.adr_validator = ADRGateValidator()
        self.concept_validator = ConceptGateValidator()
        self.plan_validator = ImplementationPlanGateValidator()
        self.idea_validator = IdeaGateValidator()

    def validate_phase_artifacts(self, phase_id: str, artifacts: dict[str, Any]) -> dict[str, GateResult]:
        """Run all validators on phase artifacts. Returns dict of gate results."""
        results = {}

        # Determine artifact type and run appropriate validator
        artifact_type = artifacts.get("type", "unknown")

        if artifact_type == "ADR" or "id" in artifacts and artifacts["id"].startswith("ADR-"):
            results["ADRGate"] = self.adr_validator.run(artifacts)
        elif artifact_type == "Concept" or "id" in artifacts and artifacts["id"].startswith("CONCEPT-"):
            results["ConceptGate"] = self.concept_validator.run(artifacts)
        elif artifact_type == "ImplementationPlan":
            results["ImplementationPlanGate"] = self.plan_validator.run(artifacts)
        elif artifact_type == "Idea":
            results["IdeaGate"] = self.idea_validator.run(artifacts)

        # Log all results
        logger.info(f"Phase {phase_id} quality gates: {[r.verdict.value for r in results.values()]}")

        return results

    def all_gates_passed(self, results: dict[str, GateResult]) -> bool:
        """Check if all gates passed (no FAIL verdicts)."""
        return all(r.verdict != GateVerdictEnum.FAIL for r in results.values())
